from __future__ import annotations

import argparse
import csv
import hashlib
import json
import shutil
import statistics
import subprocess
import sys
import time
import uuid
from dataclasses import asdict, dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


ROOT = Path(__file__).resolve().parents[2]
RESULTS_ROOT = ROOT / "experiments" / "results" / "functional"
LATEST_PROTOCOL_RESULT = ROOT / ".runtime" / "experiment-v2" / "results" / "latest.json"
HARDHAT_STAGE = ROOT / ".runtime" / "hardhat-stage"
HARDHAT_CMD = HARDHAT_STAGE / "node_modules" / ".bin" / "hardhat.cmd"
CONTRACT_TEST = "test/ExperimentProtocolV2.test.ts"


SCENARIOS: dict[str, dict[str, str]] = {
    "F1": {
        "name": "single_node_success",
        "mode": "contract",
        "evidence": "settles only after event relay, keeps failed swaps in the Vault, and preserves all balances",
        "expected": "A one-node workflow reaches SETTLED, pays once, and preserves balances.",
    },
    "F2": {
        "name": "dependent_dag_success",
        "mode": "protocol",
        "evidence": "two_nodes_settled",
        "expected": "Both dependent DAG nodes settle in dependency order.",
    },
    "F3": {
        "name": "agent_timeout_recovery",
        "mode": "protocol",
        "evidence": "failed_attempt_replaced",
        "expected": "The failed attempt receives no payout and a new Agent attempt settles.",
    },
    "F4": {
        "name": "review_failure_recovery_gate",
        "mode": "contract",
        "evidence": "does not create a settlement for a failed coordinator review",
        "expected": "A failed review cannot create a settlement and its reservation remains recoverable.",
    },
    "F5": {
        "name": "relay_crash_recovery",
        "mode": "protocol",
        "evidence": "crash_recovered_without_duplicate",
        "expected": "Relay resumes after enqueue crash without message loss or duplicate payment.",
    },
    "F6": {
        "name": "payout_failure_recovery",
        "mode": "contract",
        "evidence": "settles only after event relay, keeps failed swaps in the Vault, and preserves all balances",
        "expected": "Failed payout remains in the Vault and settles after payout terms are corrected.",
    },
    "F7": {
        "name": "late_attempt_rejection",
        "mode": "contract",
        "evidence": "rejects a delayed older attempt after a newer assignment has arrived",
        "expected": "A delayed superseded attempt is rejected and cannot replace the current attempt.",
    },
}


@dataclass
class RunRecord:
    run_id: str
    suite_id: str
    scenario_id: str
    scenario_name: str
    round: int
    mode: str
    started_at: str
    completed_at: str
    duration_ms: int
    passed: bool
    expected: str
    actual: str
    evidence_path: str
    protocol_run_id: str
    transaction_count: int | None
    total_gas: int | None
    budget: int | None
    source_spent: int | None
    refund: int | None
    funds_conserved: bool | None
    duplicate_payment_count: int | None
    stale_attempt_rejected: bool | None
    error: str


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


def redact(text: str) -> str:
    redacted = []
    for line in text.splitlines():
        lowered = line.lower()
        if any(marker in lowered for marker in ("api_key=", "private_key=", "password=", "secret=", "token=")):
            name = line.split("=", 1)[0]
            redacted.append(f"{name}=<redacted>")
        else:
            redacted.append(line)
    return "\n".join(redacted)


def run_command(command: list[str], cwd: Path, log_path: Path, timeout_seconds: int) -> tuple[int, int, str]:
    started = time.perf_counter()
    log_path.parent.mkdir(parents=True, exist_ok=True)
    process_log = log_path.with_suffix(".process.log")
    try:
        with process_log.open("w", encoding="utf-8", errors="replace") as target:
            target.write(f"COMMAND: {' '.join(command)}\n\nOUTPUT:\n")
            target.flush()
            completed = subprocess.run(
                command,
                cwd=cwd,
                text=True,
                encoding="utf-8",
                errors="replace",
                stdout=target,
                stderr=subprocess.STDOUT,
                timeout=timeout_seconds,
                check=False,
            )
        output = redact(process_log.read_text(encoding="utf-8", errors="replace"))
        return completed.returncode, round((time.perf_counter() - started) * 1000), output
    except subprocess.TimeoutExpired as exc:
        partial = process_log.read_text(encoding="utf-8", errors="replace") if process_log.exists() else ""
        output = redact(f"COMMAND TIMEOUT AFTER {timeout_seconds}s\n\n{partial}")
        return 124, round((time.perf_counter() - started) * 1000), output
    finally:
        try:
            process_log.unlink(missing_ok=True)
        except PermissionError:
            pass


def write_log(path: Path, value: str) -> None:
    path.write_text(value, encoding="utf-8")


def int_value(value: Any) -> int | None:
    if value is None or value == "":
        return None
    return int(value)


def evaluate_protocol(scenario_id: str, result: dict[str, Any]) -> tuple[bool, str]:
    workflow = result.get("workflow", {})
    funds = result.get("funds", {})
    faults = result.get("faultEvidence", {})
    node_one = workflow.get("nodeOne", {})
    node_two = workflow.get("nodeTwo", {})
    failed = node_two.get("failedAttempt", {})
    successful = node_two.get("successfulAttempt", {})
    if scenario_id == "F2":
        passed = node_one.get("status") == "SETTLED" and successful.get("status") == "SETTLED" and funds.get("conserved") is True
        return passed, f"nodeOne={node_one.get('status')};nodeTwo={successful.get('status')};fundsConserved={funds.get('conserved')}"
    if scenario_id == "F3":
        passed = failed.get("payout") == "0" and successful.get("status") == "SETTLED" and failed.get("attemptId") != successful.get("attemptId")
        return passed, f"failedPayout={failed.get('payout')};replacement={successful.get('status')};attemptChanged={failed.get('attemptId') != successful.get('attemptId')}"
    if scenario_id == "F5":
        passed = faults.get("crashAfterEnqueueRecovered") is True and faults.get("relayRestartDidNotDuplicate") is True
        return passed, f"crashRecovered={faults.get('crashAfterEnqueueRecovered')};noDuplicate={faults.get('relayRestartDidNotDuplicate')}"
    raise ValueError(f"Unsupported protocol scenario: {scenario_id}")


def prepare_contract_stage(log_path: Path) -> None:
    command = [
        "powershell.exe", "-NoProfile", "-ExecutionPolicy", "Bypass", "-File",
        str(ROOT / "scripts" / "local" / "invoke-hardhat-isolated.ps1"), "-Command", "compile",
    ]
    code, _, output = run_command(command, ROOT, log_path, 300)
    write_log(log_path, output)
    if code != 0 or not HARDHAT_CMD.exists():
        raise RuntimeError(f"Could not prepare isolated Hardhat stage; see {log_path}")


def run_contract_case(suite_id: str, scenario_id: str, round_number: int, output_dir: Path) -> RunRecord:
    scenario = SCENARIOS[scenario_id]
    run_id = str(uuid.uuid4())
    started_at = utc_now()
    log_path = output_dir / "raw.log"
    command = [
        "powershell.exe", "-NoProfile", "-ExecutionPolicy", "Bypass", "-Command",
        f"& '{HARDHAT_CMD}' test nodejs --no-compile -- '{CONTRACT_TEST}'",
    ]
    code, duration_ms, output = run_command(command, HARDHAT_STAGE, log_path, 180)
    write_log(log_path, output)
    evidence_found = scenario["evidence"] in output
    passed = code == 0 and evidence_found and "4 passing" in output
    actual = f"exitCode={code};evidenceFound={evidence_found};contractSuitePassing={'4 passing' in output}"
    completed_at = utc_now()
    return RunRecord(
        run_id, suite_id, scenario_id, scenario["name"], round_number, "isolated_contract_evm",
        started_at, completed_at, duration_ms, passed, scenario["expected"], actual,
        str(log_path.relative_to(ROOT)), "", None, None, None, None, None, None, None, None,
        "" if passed else actual,
    )


def run_protocol_case(suite_id: str, scenario_id: str, round_number: int, output_dir: Path) -> RunRecord:
    scenario = SCENARIOS[scenario_id]
    run_id = str(uuid.uuid4())
    started_at = utc_now()
    log_path = output_dir / "raw.log"
    cleanup_command = [
        "powershell.exe", "-NoProfile", "-ExecutionPolicy", "Bypass", "-File",
        str(ROOT / "scripts" / "experiment-v2" / "cleanup-hardhat-orphans.ps1"),
    ]
    cleanup_code, _, cleanup_output = run_command(cleanup_command, ROOT, output_dir / "cleanup.log", 60)
    write_log(output_dir / "cleanup.log", cleanup_output)
    if cleanup_code != 0:
        raise RuntimeError(f"Could not clean verified experiment-chain orphans; see {output_dir / 'cleanup.log'}")
    command = [
        "powershell.exe", "-NoProfile", "-ExecutionPolicy", "Bypass", "-File",
        str(ROOT / "scripts" / "experiment-v2" / "run-hardhat.ps1"),
    ]
    code, duration_ms, output = run_command(command, ROOT, log_path, 600)
    write_log(log_path, output)
    protocol: dict[str, Any] = {}
    error = ""
    if code == 0 and LATEST_PROTOCOL_RESULT.exists():
        protocol = json.loads(LATEST_PROTOCOL_RESULT.read_text(encoding="utf-8-sig"))
        shutil.copy2(LATEST_PROTOCOL_RESULT, output_dir / "protocol-result.json")
        passed, actual = evaluate_protocol(scenario_id, protocol)
    else:
        passed = False
        actual = f"exitCode={code};protocolResultExists={LATEST_PROTOCOL_RESULT.exists()}"
        error = actual
    metrics = protocol.get("metrics", {})
    transactions = metrics.get("transactions", [])
    funds = protocol.get("funds", {})
    faults = protocol.get("faultEvidence", {})
    total_gas = sum(int(item.get("gasUsed", 0)) for item in transactions) if transactions else None
    completed_at = utc_now()
    return RunRecord(
        run_id, suite_id, scenario_id, scenario["name"], round_number, "four_chain_protocol",
        started_at, completed_at, duration_ms, passed and code == 0, scenario["expected"], actual,
        str((output_dir / "protocol-result.json").relative_to(ROOT)), str(protocol.get("runId", "")),
        int_value(metrics.get("measuredTransactions")), total_gas, int_value(funds.get("budget")),
        int_value(funds.get("sourceSpent")), int_value(funds.get("refund")), funds.get("conserved"),
        0 if faults.get("relayRestartDidNotDuplicate") is True else None,
        faults.get("staleAttemptRejected"), error if error else ("" if passed else actual),
    )


def write_csv(path: Path, rows: list[dict[str, Any]], fields: list[str]) -> None:
    with path.open("w", newline="", encoding="utf-8-sig") as target:
        writer = csv.DictWriter(target, fieldnames=fields)
        writer.writeheader()
        writer.writerows(rows)


def summarize(records: list[RunRecord]) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for scenario_id in SCENARIOS:
        subset = [item for item in records if item.scenario_id == scenario_id]
        durations = [item.duration_ms for item in subset]
        rows.append({
            "scenario_id": scenario_id,
            "scenario_name": SCENARIOS[scenario_id]["name"],
            "runs": len(subset),
            "passed": sum(item.passed for item in subset),
            "failed": sum(not item.passed for item in subset),
            "pass_rate": round(sum(item.passed for item in subset) / len(subset), 4) if subset else 0,
            "average_duration_ms": round(statistics.mean(durations), 2) if durations else None,
            "median_duration_ms": round(statistics.median(durations), 2) if durations else None,
            "min_duration_ms": min(durations) if durations else None,
            "max_duration_ms": max(durations) if durations else None,
        })
    return rows


def source_fingerprint() -> str:
    digest = hashlib.sha256()
    for relative in (
        "scripts/experiment-v2-acceptance.ts",
        "test/ExperimentProtocolV2.test.ts",
        "services/chain-worker/src/experiment-v2-relay.ts",
    ):
        path = ROOT / relative
        digest.update(relative.encode())
        digest.update(path.read_bytes())
    return digest.hexdigest()


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--rounds", type=int, default=3)
    parser.add_argument("--scenarios", default=",".join(SCENARIOS))
    args = parser.parse_args()
    if args.rounds < 1:
        raise SystemExit("--rounds must be at least 1")
    scenario_ids = [value.strip().upper() for value in args.scenarios.split(",") if value.strip()]
    unknown = [value for value in scenario_ids if value not in SCENARIOS]
    if unknown:
        raise SystemExit(f"Unknown scenarios: {', '.join(unknown)}")

    suite_id = datetime.now().strftime("functional-%Y%m%d-%H%M%S")
    suite_dir = RESULTS_ROOT / suite_id
    suite_dir.mkdir(parents=True, exist_ok=False)
    prepare_contract_stage(suite_dir / "prepare-contract-stage.log")
    records: list[RunRecord] = []
    for scenario_id in scenario_ids:
        for round_number in range(1, args.rounds + 1):
            output_dir = suite_dir / "runs" / scenario_id / f"round-{round_number:02d}"
            output_dir.mkdir(parents=True, exist_ok=False)
            print(f"[{scenario_id}] round {round_number}/{args.rounds}", flush=True)
            try:
                if SCENARIOS[scenario_id]["mode"] == "protocol":
                    record = run_protocol_case(suite_id, scenario_id, round_number, output_dir)
                else:
                    record = run_contract_case(suite_id, scenario_id, round_number, output_dir)
            except Exception as exc:
                now = utc_now()
                record = RunRecord(
                    str(uuid.uuid4()), suite_id, scenario_id, SCENARIOS[scenario_id]["name"], round_number,
                    SCENARIOS[scenario_id]["mode"], now, now, 0, False, SCENARIOS[scenario_id]["expected"],
                    "runner_exception", "", "", None, None, None, None, None, None, None, None,
                    f"{type(exc).__name__}: {exc}",
                )
            records.append(record)
            (output_dir / "run.json").write_text(json.dumps(asdict(record), ensure_ascii=False, indent=2), encoding="utf-8")

    stop_command = [
        "powershell.exe", "-NoProfile", "-ExecutionPolicy", "Bypass", "-File",
        str(ROOT / "scripts" / "experiment-v2" / "stop-hardhat.ps1"),
    ]
    stop_code, _, stop_output = run_command(stop_command, ROOT, suite_dir / "stop-hardhat.log", 60)
    write_log(suite_dir / "stop-hardhat.log", stop_output)

    run_rows = [asdict(item) for item in records]
    write_csv(suite_dir / "runs.csv", run_rows, list(RunRecord.__dataclass_fields__))
    summary_rows = summarize(records)
    write_csv(suite_dir / "summary.csv", summary_rows, list(summary_rows[0]))
    summary = {
        "suite_id": suite_id,
        "created_at": utc_now(),
        "source_fingerprint_sha256": source_fingerprint(),
        "rounds_per_scenario": args.rounds,
        "total_runs": len(records),
        "passed": sum(item.passed for item in records),
        "failed": sum(not item.passed for item in records),
        "all_passed": all(item.passed for item in records),
        "cleanup_passed": stop_code == 0,
        "scenarios": summary_rows,
    }
    (suite_dir / "summary.json").write_text(json.dumps(summary, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps({"result_dir": str(suite_dir), **summary}, ensure_ascii=False), flush=True)
    return 0 if summary["all_passed"] else 1


if __name__ == "__main__":
    sys.exit(main())
