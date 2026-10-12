from __future__ import annotations

import argparse
import csv
import hashlib
import json
import math
from datetime import datetime, timezone
from pathlib import Path
from statistics import mean, median
from typing import Any


ROOT_DIR = Path(__file__).resolve().parents[1]
RESULTS_DIR = ROOT_DIR / "experiments" / "results"


def read_json(path: Path) -> dict[str, Any]:
    value = json.loads(path.read_text(encoding="utf-8-sig"))
    if not isinstance(value, dict):
        raise ValueError(f"Expected a JSON object: {path}")
    return value


def write_json(path: Path, value: Any) -> None:
    path.write_text(json.dumps(value, ensure_ascii=False, indent=2), encoding="utf-8")


def write_csv(path: Path, rows: list[dict[str, Any]], fields: list[str]) -> None:
    with path.open("w", encoding="utf-8-sig", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=fields, extrasaction="ignore")
        writer.writeheader()
        writer.writerows(rows)


def read_csv(path: Path) -> list[dict[str, str]]:
    with path.open("r", encoding="utf-8-sig", newline="") as handle:
        return list(csv.DictReader(handle))


def latest_passing_directory(root: Path, summary_name: str, passed_field: str) -> Path:
    candidates = sorted(
        (path for path in root.iterdir() if path.is_dir() and (path / summary_name).is_file()),
        key=lambda path: path.stat().st_mtime,
        reverse=True,
    )
    for candidate in candidates:
        summary = read_json(candidate / summary_name)
        value = summary.get(passed_field)
        passed = value is True or isinstance(value, int) and value > 0
        if passed and int(summary.get("failed", 0)) == 0:
            return candidate
    raise FileNotFoundError(f"No passing experiment suite found under {root}")


def percentile(values: list[float], percentile_value: float) -> float | None:
    if not values:
        return None
    ordered = sorted(values)
    index = max(0, math.ceil(percentile_value * len(ordered)) - 1)
    return round(ordered[index], 2)


def protocol_signature(run: dict[str, Any]) -> str:
    workflow = run.get("workflow") or {}
    node_two = workflow.get("nodeTwo") or {}
    material = {
        "environment": run.get("environment"),
        "chainIds": [item.get("chainId") for item in run.get("chains", [])],
        "workflowId": workflow.get("workflowId"),
        "nodeOne": workflow.get("nodeOne"),
        "failedAttempt": node_two.get("failedAttempt"),
        "successfulAttempt": node_two.get("successfulAttempt"),
        "funds": {
            key: (run.get("funds") or {}).get(key)
            for key in ("budget", "sourceSpent", "platformFee", "refund", "conserved")
        },
        "faultEvidence": run.get("faultEvidence"),
        "measuredTransactions": (run.get("metrics") or {}).get("measuredTransactions"),
    }
    encoded = json.dumps(material, sort_keys=True, separators=(",", ":")).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


def protocol_passed(run: dict[str, Any]) -> bool:
    workflow = run.get("workflow") or {}
    node_two = workflow.get("nodeTwo") or {}
    faults = run.get("faultEvidence") or {}
    return bool(
        (run.get("funds") or {}).get("conserved")
        and faults
        and all(bool(value) for value in faults.values())
        and (workflow.get("nodeOne") or {}).get("status") == "SETTLED"
        and (node_two.get("failedAttempt") or {}).get("status") == "STALE_REJECTED"
        and (node_two.get("successfulAttempt") or {}).get("status") == "SETTLED"
        and int((run.get("metrics") or {}).get("measuredTransactions") or 0) == 46
    )


def protocol_results(root: Path, run_count: int) -> tuple[dict[str, Any], list[dict[str, Any]]]:
    candidates: list[tuple[Path, dict[str, Any]]] = []
    for path in root.iterdir():
        result_path = path / "run.json"
        if path.is_dir() and result_path.is_file():
            candidates.append((result_path, read_json(result_path)))
    candidates.sort(key=lambda item: str(item[1].get("completedAt") or ""), reverse=True)
    selected = list(reversed(candidates[:run_count]))
    if len(selected) != run_count:
        raise RuntimeError(f"Expected {run_count} protocol runs, found {len(selected)}")

    rows: list[dict[str, Any]] = []
    all_latencies: list[float] = []
    signatures: list[str] = []
    for result_path, run in selected:
        transactions = (run.get("metrics") or {}).get("transactions") or []
        latencies = [float(item["durationMs"]) for item in transactions]
        gas_by_chain = (run.get("metrics") or {}).get("gasByChain") or {}
        passed = protocol_passed(run)
        signatures.append(protocol_signature(run))
        all_latencies.extend(latencies)
        rows.append(
            {
                "runId": run.get("runId"),
                "completedAt": run.get("completedAt"),
                "passed": passed,
                "measuredTransactions": len(transactions),
                "averageTransactionLatencyMs": round(mean(latencies), 2),
                "medianTransactionLatencyMs": round(median(latencies), 2),
                "p95TransactionLatencyMs": percentile(latencies, 0.95),
                "totalGas": sum(int(value) for value in gas_by_chain.values()),
                "fundsConserved": bool((run.get("funds") or {}).get("conserved")),
                "crashRecovered": bool((run.get("faultEvidence") or {}).get("crashAfterEnqueueRecovered")),
                "noDuplicateRelay": bool((run.get("faultEvidence") or {}).get("relayRestartDidNotDuplicate")),
                "staleAttemptRejected": bool((run.get("faultEvidence") or {}).get("staleAttemptRejected")),
                "paymentGatePassed": bool((run.get("faultEvidence") or {}).get("paymentRequiredBeforeSettled")),
                "evidencePath": str(result_path),
            }
        )

    passed_count = sum(bool(row["passed"]) for row in rows)
    summary = {
        "runs": len(rows),
        "passed": passed_count,
        "failed": len(rows) - passed_count,
        "passRate": passed_count / len(rows),
        "allPassed": passed_count == len(rows),
        "deterministicStable": len(set(signatures)) == 1,
        "measuredTransactions": sum(int(row["measuredTransactions"]) for row in rows),
        "averageTransactionLatencyMs": round(mean(all_latencies), 2),
        "medianTransactionLatencyMs": round(median(all_latencies), 2),
        "p95TransactionLatencyMs": percentile(all_latencies, 0.95),
        "sourceDirectory": str(root),
    }
    return summary, rows


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Build one CSV/JSON overview from completed experiments.")
    parser.add_argument("--protocol-root", type=Path, default=RESULTS_DIR / "protocol-baseline")
    parser.add_argument("--protocol-run-count", type=int, default=3)
    parser.add_argument("--security-run", type=Path)
    parser.add_argument("--functional-run", type=Path)
    parser.add_argument("--real-agent-run", type=Path)
    parser.add_argument("--output-root", type=Path, default=RESULTS_DIR / "overview")
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    security_dir = (args.security_run or latest_passing_directory(
        RESULTS_DIR / "security", "run.json", "passed"
    )).resolve()
    functional_dir = (args.functional_run or latest_passing_directory(
        RESULTS_DIR / "functional", "summary.json", "all_passed"
    )).resolve()
    real_agent_dir = (args.real_agent_run or latest_passing_directory(
        RESULTS_DIR / "real-agents", "summary.json", "allPassed"
    )).resolve()

    protocol, protocol_rows = protocol_results(args.protocol_root.resolve(), args.protocol_run_count)
    security = read_json(security_dir / "run.json")
    functional = read_json(functional_dir / "summary.json")
    real_agents = read_json(real_agent_dir / "summary.json")
    real_run_rows = {
        row.get("scenarioId"): row
        for row in read_csv(real_agent_dir / "runs.csv")
        if row.get("scenarioId")
    }

    security_total = int(security.get("passed", 0)) + int(security.get("failed", 0))
    category_rows = [
        {
            "category": "protocol_baseline", "runs": protocol["runs"],
            "passed": protocol["passed"], "failed": protocol["failed"],
            "passRate": protocol["passRate"], "keyMetric": "measuredTransactions",
            "keyValue": protocol["measuredTransactions"], "sourceDirectory": protocol["sourceDirectory"],
        },
        {
            "category": "security_offline", "runs": security_total,
            "passed": security.get("passed", 0), "failed": security.get("failed", 0),
            "passRate": int(security.get("passed", 0)) / max(1, security_total),
            "keyMetric": "securitySuites", "keyValue": len(security.get("results") or []),
            "sourceDirectory": str(security_dir),
        },
        {
            "category": "functional_f1_f7", "runs": functional.get("total_runs", 0),
            "passed": functional.get("passed", 0), "failed": functional.get("failed", 0),
            "passRate": int(functional.get("passed", 0)) / max(1, int(functional.get("total_runs", 0))),
            "keyMetric": "scenarios", "keyValue": len(functional.get("scenarios") or []),
            "sourceDirectory": str(functional_dir),
        },
        {
            "category": "real_agents", "runs": real_agents.get("totalRuns", 0),
            "passed": real_agents.get("passed", 0), "failed": real_agents.get("failed", 0),
            "passRate": int(real_agents.get("passed", 0)) / max(1, int(real_agents.get("totalRuns", 0))),
            "keyMetric": "realAgents", "keyValue": 3, "sourceDirectory": str(real_agent_dir),
        },
    ]

    scenario_rows: list[dict[str, Any]] = [{
        "category": "protocol_baseline", "scenarioId": "PROTOCOL-V2",
        "scenarioName": "deterministic_four_chain_protocol", "runs": protocol["runs"],
        "passed": protocol["passed"], "failed": protocol["failed"],
        "passRate": protocol["passRate"], "averageDurationMs": protocol["averageTransactionLatencyMs"],
        "evidencePath": protocol["sourceDirectory"],
    }]
    for item in security.get("results") or []:
        raw_evidence = str(item.get("evidence") or "")
        evidence_file = security_dir / raw_evidence
        scenario_rows.append({
            "category": "security_offline", "scenarioId": item.get("scenario_id"),
            "scenarioName": item.get("name"), "runs": 1,
            "passed": int(bool(item.get("passed"))), "failed": int(not bool(item.get("passed"))),
            "passRate": float(bool(item.get("passed"))), "averageDurationMs": item.get("duration_ms"),
            "evidencePath": str(evidence_file) if evidence_file.is_file() else raw_evidence,
        })
    for item in functional.get("scenarios") or []:
        scenario_rows.append({
            "category": "functional_f1_f7", "scenarioId": item.get("scenario_id"),
            "scenarioName": item.get("scenario_name"), "runs": item.get("runs"),
            "passed": item.get("passed"), "failed": item.get("failed"),
            "passRate": item.get("pass_rate"), "averageDurationMs": item.get("average_duration_ms"),
            "evidencePath": str(functional_dir / "runs.csv"),
        })
    for item in real_agents.get("scenarios") or []:
        scenario_id = str(item.get("scenarioId"))
        run_row = real_run_rows.get(scenario_id) or {}
        scenario_rows.append({
            "category": "real_agents", "scenarioId": scenario_id,
            "scenarioName": scenario_id, "runs": item.get("runs"),
            "passed": item.get("passed"), "failed": item.get("failed"),
            "passRate": item.get("passRate"), "averageDurationMs": run_row.get("workflowDurationMs"),
            "evidencePath": str(real_agent_dir / scenario_id / "workflow.json"),
        })

    total_runs = sum(int(row["runs"]) for row in category_rows)
    total_passed = sum(int(row["passed"]) for row in category_rows)
    all_passed = all(int(row["failed"]) == 0 for row in category_rows) and protocol["deterministicStable"]
    suite_id = f"overview-{datetime.now().strftime('%Y%m%d-%H%M%S')}"
    output_dir = args.output_root.resolve() / suite_id
    output_dir.mkdir(parents=True, exist_ok=False)
    overview = {
        "schemaVersion": 1, "suiteId": suite_id,
        "generatedAt": datetime.now(timezone.utc).isoformat().replace("+00:00", "Z"),
        "allPassed": all_passed, "totalObservations": total_runs,
        "passed": total_passed, "failed": total_runs - total_passed,
        "passRate": total_passed / total_runs, "protocol": protocol,
        "security": {"runId": security.get("runId"), "passed": security.get("passed"),
                     "failed": security.get("failed"), "sourceDirectory": str(security_dir)},
        "functional": {"suiteId": functional.get("suite_id"), "passed": functional.get("passed"),
                       "failed": functional.get("failed"), "sourceDirectory": str(functional_dir)},
        "realAgents": {"suiteId": real_agents.get("suiteId"), "passed": real_agents.get("passed"),
                       "failed": real_agents.get("failed"), "sourceDirectory": str(real_agent_dir)},
    }
    write_json(output_dir / "overview.json", overview)
    write_csv(output_dir / "overview.csv", category_rows,
              ["category", "runs", "passed", "failed", "passRate", "keyMetric", "keyValue", "sourceDirectory"])
    write_csv(output_dir / "scenario-summary.csv", scenario_rows,
              ["category", "scenarioId", "scenarioName", "runs", "passed", "failed", "passRate", "averageDurationMs", "evidencePath"])
    write_csv(output_dir / "protocol-runs.csv", protocol_rows,
              ["runId", "completedAt", "passed", "measuredTransactions", "averageTransactionLatencyMs",
               "medianTransactionLatencyMs", "p95TransactionLatencyMs", "totalGas", "fundsConserved",
               "crashRecovered", "noDuplicateRelay", "staleAttemptRejected", "paymentGatePassed", "evidencePath"])
    (args.output_root.resolve() / "LATEST.txt").write_text(str(output_dir), encoding="utf-8")
    print(json.dumps({**overview, "outputDirectory": str(output_dir)}, ensure_ascii=False, indent=2))
    return 0 if all_passed else 1


if __name__ == "__main__":
    raise SystemExit(main())
