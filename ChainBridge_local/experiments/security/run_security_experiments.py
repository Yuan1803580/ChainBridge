from __future__ import annotations

import argparse
import csv
import hashlib
import json
import os
import re
import subprocess
import sys
from dataclasses import asdict, dataclass
from datetime import datetime, timezone
from pathlib import Path
from time import perf_counter
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen


ROOT = Path(__file__).resolve().parents[2]
SENSITIVE = re.compile(r"(api.?key|token|password|secret|private.?key)", re.I)


@dataclass
class Result:
    scenario_id: str
    name: str
    passed: bool
    duration_ms: int
    evidence: str
    error: str = ""


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


def redact(text: str) -> str:
    lines = []
    for line in text.splitlines():
        if SENSITIVE.search(line):
            key = line.split("=", 1)[0].split(":", 1)[0]
            lines.append(f"{key}=<redacted>")
        else:
            lines.append(line)
    return "\n".join(lines)


def command_case(
    scenario_id: str,
    name: str,
    command: list[str],
    cwd: Path,
    log_path: Path,
) -> Result:
    started = perf_counter()
    process = subprocess.run(
        command,
        cwd=cwd,
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        env={**os.environ, "PYTHONDONTWRITEBYTECODE": "1"},
        check=False,
    )
    output = redact(process.stdout + "\n" + process.stderr)
    log_path.write_text(output, encoding="utf-8")
    return Result(
        scenario_id=scenario_id,
        name=name,
        passed=process.returncode == 0,
        duration_ms=int((perf_counter() - started) * 1000),
        evidence=log_path.name,
        error="" if process.returncode == 0 else f"exit_code={process.returncode}",
    )


def http_case(scenario_id: str, name: str, url: str, expected: set[int]) -> Result:
    started = perf_counter()
    status_code = 0
    try:
        with urlopen(Request(url, headers={"Accept": "application/json"}), timeout=5) as response:
            status_code = response.status
    except HTTPError as exc:
        status_code = exc.code
    except URLError as exc:
        return Result(scenario_id, name, False, int((perf_counter() - started) * 1000), url, str(exc.reason))
    return Result(scenario_id, name, status_code in expected, int((perf_counter() - started) * 1000), url, f"status={status_code}")


def wallet_checkpoint(path: Path, expected_address: str | None) -> Result:
    started = perf_counter()
    if not path.exists():
        return Result("S14", "requester_wallet_checkpoint", False, 0, str(path), "keystore_missing")
    value = json.loads(path.read_text(encoding="utf-8-sig"))
    address = str(value.get("address") or "").removeprefix("0x").lower()
    expected = (expected_address or address).removeprefix("0x").lower()
    normalized_fields = {str(field).lower() for field in value}
    encrypted = (
        "crypto" in normalized_fields
        and "version" in normalized_fields
        and "privatekey" not in normalized_fields
    )
    fingerprint = hashlib.sha256(path.read_bytes()).hexdigest()
    return Result(
        "S14",
        "requester_wallet_checkpoint",
        encrypted and bool(address) and address == expected,
        int((perf_counter() - started) * 1000),
        f"address=0x{address};sha256={fingerprint};encrypted={encrypted}",
    )


def main() -> int:
    started_at = utc_now()
    parser = argparse.ArgumentParser()
    parser.add_argument("--mode", choices=("offline", "live-isolated"), default="offline")
    parser.add_argument("--runtime-dir", type=Path)
    parser.add_argument("--confirm-isolated", action="store_true")
    parser.add_argument("--api-url", default="http://127.0.0.1:8000")
    parser.add_argument("--requester-keystore", type=Path)
    parser.add_argument("--expected-requester-address")
    args = parser.parse_args()

    run_id = datetime.now().strftime("security-%Y%m%d-%H%M%S")
    output = ROOT / "experiments" / "results" / "security" / run_id
    output.mkdir(parents=True, exist_ok=False)
    results: list[Result] = []

    python = sys.executable
    signer_temp = output / "signer-pytest-temp"
    platform_temp = output / "platform-pytest-temp"
    results.append(command_case(
        "S01-S07", "signer_policy_suite",
        [python, "-m", "pytest", "-q", "--basetemp", str(signer_temp), "tests"],
        ROOT / "services" / "agent-signer", output / "signer-policy.log",
    ))
    results.append(command_case(
        "S08-S11", "worker_reliability_suite",
        [
            "node",
            "--test",
            *[
                str(path.relative_to(ROOT))
                for path in sorted((ROOT / "services" / "chain-worker" / "test").glob("*.test.ts"))
            ],
        ],
        ROOT, output / "worker-reliability.log",
    ))
    results.append(command_case(
        "S01-S05", "platform_security_regression",
        [
            python,
            "-m",
            "pytest",
            "-q",
            "--basetemp",
            str(platform_temp),
            "tests/test_jcs_canonicalization.py",
            "tests/test_workflow_recovery.py",
            "tests/test_crosschain_workflow.py",
        ],
        ROOT / "apps" / "api", output / "platform-security.log",
    ))
    if args.requester_keystore:
        results.append(wallet_checkpoint(args.requester_keystore.resolve(), args.expected_requester_address))

    if args.mode == "live-isolated":
        marker = args.runtime_dir / ".isolated-security-experiment" if args.runtime_dir else None
        if not args.confirm_isolated or marker is None or not marker.exists():
            raise SystemExit("Live mode requires --confirm-isolated and an isolated runtime marker")
        results.append(http_case("LIVE-01", "api_health", f"{args.api_url.rstrip('/')}/api/v1/health", {200}))

    with (output / "results.csv").open("w", newline="", encoding="utf-8-sig") as target:
        writer = csv.DictWriter(target, fieldnames=Result.__dataclass_fields__.keys())
        writer.writeheader()
        writer.writerows(asdict(item) for item in results)
    summary = {
        "runId": run_id,
        "startedAt": started_at,
        "mode": args.mode,
        "sourceVersion": "chainbridge-v1-hardening",
        "passed": sum(item.passed for item in results),
        "failed": sum(not item.passed for item in results),
        "results": [asdict(item) for item in results],
    }
    (output / "run.json").write_text(json.dumps(summary, ensure_ascii=False, indent=2), encoding="utf-8")
    print(output)
    return 0 if summary["failed"] == 0 else 1


if __name__ == "__main__":
    raise SystemExit(main())
