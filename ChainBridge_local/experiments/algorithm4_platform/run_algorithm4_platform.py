from __future__ import annotations

import argparse
import base64
import csv
import hashlib
import hmac
import json
import os
import socket
import statistics
import subprocess
import sys
import threading
import time
import uuid
from dataclasses import asdict, dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import httpx
import rfc8785


PROJECT_ROOT = Path(__file__).resolve().parents[2]
CHECKPOINTS = (5, 10, 15, 20, 25, 30)
RUNNER_TOKEN = "algorithm4-platform-runner-token"
BASELINE_PROFILES = {
    "Agent-B1": {"trustScore": 92, "successRateBps": 9600, "availability": "ONLINE", "capacityAvailable": True},
    "Agent-C1": {"trustScore": 90, "successRateBps": 9400, "availability": "ONLINE", "capacityAvailable": True},
    "Agent-D1": {"trustScore": 88, "successRateBps": 9200, "availability": "ONLINE", "capacityAvailable": True},
}


def utc_now() -> datetime:
    return datetime.now(timezone.utc)


def iso(value: datetime | None = None) -> str:
    return (value or utc_now()).isoformat()


def parse_time(value: str) -> datetime:
    return datetime.fromisoformat(value.replace("Z", "+00:00"))


def free_port() -> int:
    with socket.socket() as listener:
        listener.bind(("127.0.0.1", 0))
        return int(listener.getsockname()[1])


def wait_http(url: str, *, timeout: float = 30.0) -> dict[str, Any]:
    deadline = time.monotonic() + timeout
    last_error = ""
    while time.monotonic() < deadline:
        try:
            response = httpx.get(url, timeout=1.0)
            if response.status_code == 200:
                return response.json()
            last_error = f"HTTP {response.status_code}"
        except httpx.HTTPError as exc:
            last_error = str(exc)
        time.sleep(0.05)
    raise RuntimeError(f"Service did not become ready at {url}: {last_error}")


@dataclass
class RecoveryRun:
    suite_id: str
    run_id: str
    round: int
    variant: str
    workflow_id: str
    final_workflow_id: str
    node_id: str
    failed_attempt_id: str
    replacement_attempt_id: str
    failed_agent_key: str
    replacement_agent_key: str
    fault_injected_at: str
    failure_event_at: str
    replacement_event_at: str
    fault_detection_ms: float
    rematch_ms: float
    workflow_completion_ms: float
    task_success: bool
    recovery_completed: bool
    duplicate_payment_count: int
    duplicate_payment: bool
    predecessor_retained: bool
    old_reservation_status: str
    ledger_balanced: bool
    funds_conserved: bool
    reexecuted_predecessor_count: int
    database_path: str
    error: str


@dataclass
class AttackRun:
    suite_id: str
    run_id: str
    round: int
    variant: str
    workflow_id: str
    node_id: str
    failed_attempt_id: str
    replacement_attempt_id: str
    same_agent_reselected: bool
    attack_a_http_status: int
    attack_a_success: bool
    attack_b_http_status: int
    attack_b_success: bool
    attack_c_http_status: int
    attack_c_success: bool
    predecessor_retained: bool
    current_attempt_preserved: bool
    recovery_completed_after_attacks: bool
    duplicate_payment_count: int
    ledger_balanced: bool
    database_path: str
    error: str


class ServiceProcess:
    def __init__(self, name: str, command: list[str], cwd: Path, env: dict[str, str], log_dir: Path):
        self.name = name
        self.command = command
        self.cwd = cwd
        self.env = env
        self.log_dir = log_dir
        self.process: subprocess.Popen[bytes] | None = None
        self.stdout = None
        self.stderr = None

    def start(self) -> None:
        self.log_dir.mkdir(parents=True, exist_ok=True)
        self.stdout = (self.log_dir / f"{self.name}.stdout.log").open("ab")
        self.stderr = (self.log_dir / f"{self.name}.stderr.log").open("ab")
        creation_flags = subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0
        self.process = subprocess.Popen(
            self.command,
            cwd=self.cwd,
            env=self.env,
            stdout=self.stdout,
            stderr=self.stderr,
            creationflags=creation_flags,
        )

    def stop(self) -> None:
        process = self.process
        if process is not None and process.poll() is None:
            process.terminate()
            try:
                process.wait(timeout=5)
            except subprocess.TimeoutExpired:
                process.kill()
                process.wait(timeout=5)
        self.process = None
        if self.stdout is not None:
            self.stdout.close()
            self.stdout = None
        if self.stderr is not None:
            self.stderr.close()
            self.stderr = None


class PlatformTopology:
    def __init__(self, suite_id: str, variant: str, runtime_root: Path):
        self.suite_id = suite_id
        self.variant = variant
        self.runtime_dir = runtime_root / variant.lower()
        self.runtime_dir.mkdir(parents=True, exist_ok=True)
        self.database_path = self.runtime_dir / "algorithm4-platform.db"
        self.api_port = free_port()
        self.runner_ports = {"B": free_port(), "C": free_port(), "D": free_port()}
        self.api: ServiceProcess | None = None
        self.runners: dict[str, ServiceProcess] = {}
        self.client: httpx.Client | None = None
        self.tokens: dict[str, str] = {}
        self.profile_ids: dict[str, str] = {}

    @property
    def api_base(self) -> str:
        return f"http://127.0.0.1:{self.api_port}/api/v1"

    def _base_env(self) -> dict[str, str]:
        env = dict(os.environ)
        env.update({
            "APP_ENV": "test",
            "LOCAL_PLATFORM_RUNTIME_ID": f"{self.suite_id}-{self.variant.lower()}",
            "DEV_IDENTITY_SEED": "agent-market-demo",
            "ALGORITHM4_EXPERIMENT_MODE": "true",
            "AGENT_RUNNER_API_TOKEN": RUNNER_TOKEN,
            "LLM_PROVIDER": "mock",
        })
        return env

    def _runner(self, key: str, *, delay: float) -> ServiceProcess:
        spec = {
            "B": ("runner-b", "workflow-research-b"),
            "C": ("runner-c", "workflow-verification-c"),
            "D": ("runner-d", "workflow-verification-d"),
        }[key]
        env = self._base_env()
        env.update({
            "AGENT_RUNNER_ID": spec[0],
            "AGENT_RUNNER_SLUGS": spec[1],
            "AGENT_RUNNER_ALLOW_INSECURE_LOCAL": "false",
            "ALGORITHM4_INVOKE_DELAY_SECONDS": str(delay),
            "AGENT_CHAT_DB_PATH": str(self.runtime_dir / f"{spec[0]}-chat.db"),
            "AGENT_PLATFORM_REGISTRATION_STORE_PATH": str(self.runtime_dir / f"{spec[0]}-registrations.json"),
        })
        return ServiceProcess(
            spec[0],
            [sys.executable, "-m", "uvicorn", "app.main:app", "--host", "127.0.0.1", "--port", str(self.runner_ports[key])],
            PROJECT_ROOT / "services" / "agent-runner",
            env,
            self.runtime_dir / "logs",
        )

    def start_runner(self, key: str, *, delay: float = 0.0) -> None:
        self.stop_runner(key)
        runner = self._runner(key, delay=delay)
        runner.start()
        self.runners[key] = runner
        health = wait_http(f"http://127.0.0.1:{self.runner_ports[key]}/health")
        if health.get("runnerId") != f"runner-{key.lower()}":
            raise RuntimeError(f"Runner {key} identity mismatch: {health}")

    def stop_runner(self, key: str) -> None:
        runner = self.runners.pop(key, None)
        if runner is not None:
            runner.stop()

    def start(self) -> None:
        if self.database_path.exists():
            self.database_path.unlink()
        self.start_runner("B")
        self.start_runner("C", delay=15.0)
        self.start_runner("D")
        endpoints = {
            "workflow-research-b": f"http://127.0.0.1:{self.runner_ports['B']}",
            "workflow-verification-c": f"http://127.0.0.1:{self.runner_ports['C']}",
            "workflow-verification-d": f"http://127.0.0.1:{self.runner_ports['D']}",
        }
        env = self._base_env()
        env.update({
            "DATABASE_URL": f"sqlite:///{self.database_path.as_posix()}",
            "LOCAL_PROTOCOL_RUNTIME_DIR": str(self.runtime_dir),
            "AGENT_RUNNER_ENDPOINTS_JSON": json.dumps(endpoints, separators=(",", ":")),
            "AUTO_INVOKE_AGENTS": "true",
            "REQUIRE_SIGNED_AGENT_RESULTS": "true",
            "AGENT_INVOCATION_TIMEOUT_SECONDS": "30",
            "AGENT_TASK_DEADLINE_SECONDS": "90",
            "WORKFLOW_RECOVERY_ENABLED": "false",
            "SETTLEMENT_MODE": "mock",
            "MATCH_ALLOW_SEEDED_LOCAL_AGENTS": "true",
            "ALGORITHM4_DISABLE_STALE_ATTEMPT_GUARD": "true" if self.variant == "ATTEMPT_GUARD_DISABLED" else "false",
            "ALGORITHM4_DISABLE_AGENT_REUSE_GUARD": "false",
            "ALGORITHM4_DISABLE_RESERVATION_RELEASE": "true" if self.variant == "RESERVATION_RELEASE_DISABLED" else "false",
        })
        self.api = ServiceProcess(
            "api",
            [sys.executable, "-m", "uvicorn", "app.main:app", "--host", "127.0.0.1", "--port", str(self.api_port)],
            PROJECT_ROOT / "apps" / "api",
            env,
            self.runtime_dir / "logs",
        )
        self.api.start()
        health = wait_http(f"{self.api_base}/health")
        if health.get("database") != "connected":
            raise RuntimeError(f"API database health failed: {health}")
        self.client = httpx.Client(base_url=self.api_base, timeout=30.0)
        for slug in ("campus-requester", "platform-admin", "workflow-research-b", "workflow-verification-c", "workflow-verification-d"):
            response = self.client.post("/auth/dev-login", json={"did": f"did:local:{slug}"})
            response.raise_for_status()
            self.tokens[slug] = response.json()["accessToken"]
        profiles = self.get("/workflow-agents")["items"]
        self.profile_ids = {item["agentKey"]: item["id"] for item in profiles if item["agentKey"] in BASELINE_PROFILES}
        if set(self.profile_ids) != set(BASELINE_PROFILES):
            raise RuntimeError(f"Seeded profile set is incomplete: {self.profile_ids}")

    def stop(self) -> None:
        if self.client is not None:
            self.client.close()
            self.client = None
        if self.api is not None:
            self.api.stop()
            self.api = None
        for key in tuple(self.runners):
            self.stop_runner(key)

    def headers(self, slug: str, idempotency_key: str | None = None) -> dict[str, str]:
        values = {"Authorization": f"Bearer {self.tokens[slug]}"}
        if idempotency_key:
            values["Idempotency-Key"] = idempotency_key
        return values

    def get(self, path: str) -> dict[str, Any]:
        assert self.client is not None
        response = self.client.get(path)
        response.raise_for_status()
        return response.json()

    def post(self, path: str, body: dict[str, Any], slug: str, key: str | None = None) -> httpx.Response:
        assert self.client is not None
        return self.client.post(path, json=body, headers=self.headers(slug, key))

    def patch_profile(self, agent_key: str, values: dict[str, Any], key: str) -> None:
        assert self.client is not None
        response = self.client.patch(
            f"/workflow-agents/{self.profile_ids[agent_key]}",
            json=values,
            headers=self.headers("platform-admin", key),
        )
        response.raise_for_status()

    def reset_profiles(self, prefix: str) -> None:
        for agent_key, values in BASELINE_PROFILES.items():
            self.patch_profile(agent_key, values, f"{prefix}-{agent_key.lower()}-{uuid.uuid4().hex}")


def require(response: httpx.Response, status: int, label: str) -> dict[str, Any]:
    if response.status_code != status:
        raise RuntimeError(f"{label}: expected {status}, got {response.status_code}: {response.text}")
    return response.json()


def node(workflow: dict[str, Any], key: str) -> dict[str, Any]:
    return next(item for item in workflow["nodes"] if item["nodeKey"] == key)


def create_workflow(topology: PlatformTopology, prefix: str, *, max_attempts: int = 3) -> dict[str, Any]:
    payload = {
        "title": f"Algorithm 4 platform experiment {prefix}",
        "objective": "Execute a two-node DAG and recover the dependent node after an injected Agent Runner failure.",
        "budget": "20.00",
        "platformFee": "1.10",
        "fundingMode": "RESERVED",
        "inputPayload": {"isExperiment": True, "suiteId": topology.suite_id, "runId": prefix, "scenarioId": topology.variant},
        "nodes": [
            {
                "nodeKey": "N1", "title": "Prepare evidence", "description": "Prepare deterministic evidence.",
                "capability": "research", "dependsOn": [], "acceptanceCriteria": {"requiredFields": ["summary", "score"]},
                "inputPayload": {"experiment": "algorithm4"}, "reward": "4.20", "riskLevel": "LOW", "maxAttempts": 3,
            },
            {
                "nodeKey": "N2", "title": "Verify evidence", "description": "Verify N1 evidence and recover after failure.",
                "capability": "verification", "dependsOn": ["N1"], "acceptanceCriteria": {"requiredFields": ["verified", "report"]},
                "inputPayload": {"experiment": "algorithm4"}, "reward": "6.80", "riskLevel": "LOW", "maxAttempts": max_attempts,
            },
        ],
    }
    return require(topology.post("/workflows", payload, "campus-requester", f"{prefix}-create-{uuid.uuid4().hex}"), 201, "create workflow")


def coordinate(topology: PlatformTopology, workflow_id: str, prefix: str) -> dict[str, Any]:
    return require(
        topology.post(f"/workflows/{workflow_id}/coordinate", {"maxActions": 1}, "campus-requester", f"{prefix}-{uuid.uuid4().hex}"),
        200,
        prefix,
    )


def coordinate_until(topology: PlatformTopology, workflow_id: str, target: set[str], prefix: str, limit: int = 10) -> dict[str, Any]:
    snapshot = topology.get(f"/workflows/{workflow_id}")
    for index in range(limit):
        if snapshot["status"] in target:
            return snapshot
        snapshot = coordinate(topology, workflow_id, f"{prefix}-{index}")["workflow"]
    raise RuntimeError(f"Workflow {workflow_id} did not reach {target}; current={snapshot['status']}")


def finish_node(topology: PlatformTopology, workflow_id: str, node_key: str, prefix: str) -> dict[str, Any]:
    reviewed = coordinate(topology, workflow_id, f"{prefix}-review")["workflow"]
    if node(reviewed, node_key)["status"] != "SUCCEEDED":
        raise RuntimeError(f"{node_key} did not pass review: {node(reviewed, node_key)['status']}")
    settled = coordinate(topology, workflow_id, f"{prefix}-settle")["workflow"]
    if node(settled, node_key)["status"] != "SETTLED":
        raise RuntimeError(f"{node_key} did not settle: {node(settled, node_key)['status']}")
    return settled


def prepare_n1(topology: PlatformTopology, workflow: dict[str, Any], prefix: str) -> dict[str, Any]:
    workflow_id = workflow["id"]
    dispatched = coordinate(topology, workflow_id, f"{prefix}-dispatch-n1")["workflow"]
    attempt = node(dispatched, "N1")["currentAttempt"]
    if attempt["agentKey"] != "Agent-B1" or attempt["status"] != "RESULT_SUBMITTED":
        raise RuntimeError(f"N1 was not executed by B: {attempt}")
    settled = finish_node(topology, workflow_id, "N1", f"{prefix}-n1")
    if node(settled, "N2")["status"] != "READY":
        raise RuntimeError("N2 unlocked before or after the wrong N1 state")
    return settled


def inject_c_failure(
    topology: PlatformTopology,
    workflow_id: str,
    prefix: str,
) -> tuple[dict[str, Any], str, datetime, dict[str, Any], dict[str, Any]]:
    result: dict[str, Any] = {}
    error: list[BaseException] = []

    def invoke() -> None:
        try:
            result.update(coordinate(topology, workflow_id, f"{prefix}-dispatch-c"))
        except BaseException as exc:
            error.append(exc)

    thread = threading.Thread(target=invoke, daemon=True)
    thread.start()
    state_url = f"http://127.0.0.1:{topology.runner_ports['C']}/experiment/state"
    deadline = time.monotonic() + 15
    failed_attempt_id = ""
    accepted_offer: dict[str, Any] = {}
    active_invocation: dict[str, Any] = {}
    while time.monotonic() < deadline:
        try:
            state = httpx.get(state_url, timeout=0.5).json()
            offer = next((item for item in state.get("acceptedOffers", []) if item.get("workflowId") == workflow_id), None)
            invocation = next(
                (item for item in state.get("activeInvocations", []) if item.get("workflowId") == workflow_id),
                None,
            )
            if offer and invocation:
                failed_attempt_id = str(offer["attemptId"])
                accepted_offer = dict(offer)
                active_invocation = dict(invocation)
                break
        except (httpx.HTTPError, ValueError):
            pass
        time.sleep(0.01)
    if not failed_attempt_id:
        raise RuntimeError("C Runner never accepted the injected-failure task offer")
    fault_at = utc_now()
    topology.stop_runner("C")
    thread.join(timeout=35)
    if thread.is_alive():
        raise RuntimeError("Coordinate request did not return after C Runner termination")
    if error:
        raise RuntimeError(f"Coordinate request failed outside the platform state machine: {error[0]}")
    workflow = result["workflow"]
    attempt = next(item for item in node(workflow, "N2")["attempts"] if item["id"] == failed_attempt_id)
    if attempt["failureReasonCode"] != "AGENT_TIMEOUT" or node(workflow, "N2")["status"] not in {"RETRYING", "FAILED"}:
        raise RuntimeError(f"C termination was not classified as AGENT_TIMEOUT: {attempt}")
    return workflow, failed_attempt_id, fault_at, accepted_offer, active_invocation


def rematch_d(topology: PlatformTopology, workflow_id: str, prefix: str) -> tuple[dict[str, Any], dict[str, Any], bool]:
    recovered = coordinate(topology, workflow_id, f"{prefix}-rematch-d")["workflow"]
    replacement = node(recovered, "N2")["currentAttempt"]
    if replacement["agentKey"] != "Agent-D1" or replacement["status"] != "RESULT_SUBMITTED":
        raise RuntimeError(f"N2 was not recovered by D: {replacement}")
    return recovered, replacement, replacement["agentKey"] == "Agent-C1"


def finish_recovered(topology: PlatformTopology, workflow_id: str, prefix: str) -> dict[str, Any]:
    finish_node(topology, workflow_id, "N2", f"{prefix}-n2")
    final = coordinate(topology, workflow_id, f"{prefix}-complete")["workflow"]
    if final["status"] != "COMPLETED":
        raise RuntimeError(f"Recovered workflow did not complete: {final['status']}")
    return final


def event(workflow: dict[str, Any], event_type: str, *, attempt_id: str | None = None, node_id: str | None = None) -> dict[str, Any]:
    matches = [
        item for item in workflow["events"]
        if item["eventType"] == event_type
        and (attempt_id is None or item.get("attemptId") == attempt_id)
        and (node_id is None or item.get("nodeId") == node_id)
    ]
    if not matches:
        raise RuntimeError(f"Missing event {event_type} attempt={attempt_id} node={node_id}")
    return matches[-1]


def payout_count(workflow: dict[str, Any]) -> int:
    return sum(item["entryType"] == "PAYOUT" for item in workflow["ledger"])


def reservation_status(workflow: dict[str, Any], attempt_id: str) -> str:
    for workflow_node in workflow["nodes"]:
        for reservation in workflow_node.get("reservations", []):
            if reservation["attemptId"] == attempt_id:
                return str(reservation["status"])
    return "MISSING"


def save_evidence(output_dir: Path, variant: str, round_number: int, name: str, value: dict[str, Any]) -> None:
    path = output_dir / "evidence" / variant.lower() / f"round-{round_number:02d}-{name}.json"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value, ensure_ascii=False, indent=2), encoding="utf-8")


def empty_recovery(suite_id: str, round_number: int, variant: str, database: Path, error: BaseException) -> RecoveryRun:
    return RecoveryRun(suite_id, str(uuid.uuid4()), round_number, variant, "", "", "", "", "", "", "", "", "", "", 0, 0, 0, False, False, 0, False, False, "UNKNOWN", False, False, 0, str(database), f"{type(error).__name__}: {error}")


def run_recovery_round(topology: PlatformTopology, output_dir: Path, round_number: int) -> RecoveryRun:
    variant = topology.variant
    run_id = str(uuid.uuid4())
    prefix = f"{variant.lower()}-{round_number}-{run_id[:8]}"
    try:
        topology.reset_profiles(prefix)
        topology.start_runner("C", delay=15.0)
        workflow = create_workflow(topology, prefix)
        prepare_n1(topology, workflow, prefix)
        failed, failed_attempt_id, fault_at, _, _ = inject_c_failure(topology, workflow["id"], prefix)
        failed_attempt = next(item for item in node(failed, "N2")["attempts"] if item["id"] == failed_attempt_id)
        node_id = node(failed, "N2")["id"]
        recovered, replacement, _ = rematch_d(topology, workflow["id"], prefix)
        final = finish_recovered(topology, workflow["id"], prefix)
        failure_event = event(final, "FAILURE_CLASSIFIED", attempt_id=failed_attempt_id)
        replacement_event = event(final, "NODE_DISPATCHED", attempt_id=replacement["id"])
        first_dispatch = event(final, "NODE_DISPATCHED", node_id=node(final, "N1")["id"])
        completed_event = event(final, "WORKFLOW_COMPLETED")
        payouts = payout_count(final)
        old_status = reservation_status(final, failed_attempt_id)
        save_evidence(output_dir, variant, round_number, "workflow", final)
        return RecoveryRun(
            topology.suite_id, run_id, round_number, variant, final["id"], final["id"], node_id,
            failed_attempt_id, replacement["id"], failed_attempt["agentKey"], replacement["agentKey"],
            iso(fault_at), failure_event["createdAt"], replacement_event["createdAt"],
            round(max(0.0, (parse_time(failure_event["createdAt"]) - fault_at).total_seconds() * 1000), 4),
            round(max(0.0, (parse_time(replacement_event["createdAt"]) - parse_time(failure_event["createdAt"])).total_seconds() * 1000), 4),
            round((parse_time(completed_event["createdAt"]) - parse_time(first_dispatch["createdAt"])).total_seconds() * 1000, 4),
            final["status"] == "COMPLETED", final["status"] == "COMPLETED", max(0, payouts - 2), payouts > 2,
            node(final, "N1")["status"] == "SETTLED", old_status, bool(final["ledgerSummary"]["balanced"]),
            bool(final["ledgerSummary"]["balanced"]) and old_status == "RELEASED" and payouts == 2,
            0, str(topology.database_path), "",
        )
    except BaseException as exc:
        return empty_recovery(topology.suite_id, round_number, variant, topology.database_path, exc)


def run_full_restart_round(topology: PlatformTopology, output_dir: Path, round_number: int) -> RecoveryRun:
    variant = "FULL_WORKFLOW_RESTART"
    run_id = str(uuid.uuid4())
    prefix = f"full-restart-{round_number}-{run_id[:8]}"
    try:
        topology.reset_profiles(prefix)
        topology.start_runner("C", delay=15.0)
        original = create_workflow(topology, f"{prefix}-original", max_attempts=1)
        prepare_n1(topology, original, f"{prefix}-original")
        failed, failed_attempt_id, fault_at, _, _ = inject_c_failure(topology, original["id"], f"{prefix}-original")
        original_final = coordinate_until(topology, original["id"], {"FAILED_REFUNDED"}, f"{prefix}-refund", limit=4)
        topology.patch_profile("Agent-C1", {"availability": "OFFLINE"}, f"{prefix}-c-offline-{uuid.uuid4().hex}")
        replacement_workflow = create_workflow(topology, f"{prefix}-replacement")
        prepare_n1(topology, replacement_workflow, f"{prefix}-replacement")
        dispatched_d = coordinate(topology, replacement_workflow["id"], f"{prefix}-replacement-dispatch-d")["workflow"]
        replacement_attempt = node(dispatched_d, "N2")["currentAttempt"]
        if replacement_attempt["agentKey"] != "Agent-D1":
            raise RuntimeError(f"Full restart replacement did not choose D: {replacement_attempt}")
        final = finish_recovered(topology, replacement_workflow["id"], f"{prefix}-replacement")
        failure_event = event(original_final, "FAILURE_CLASSIFIED", attempt_id=failed_attempt_id)
        replacement_event = event(final, "NODE_DISPATCHED", attempt_id=replacement_attempt["id"])
        first_dispatch = event(original_final, "NODE_DISPATCHED", node_id=node(original_final, "N1")["id"])
        completed_event = event(final, "WORKFLOW_COMPLETED")
        payouts = payout_count(original_final) + payout_count(final)
        save_evidence(output_dir, variant, round_number, "original-workflow", original_final)
        save_evidence(output_dir, variant, round_number, "replacement-workflow", final)
        return RecoveryRun(
            topology.suite_id, run_id, round_number, variant, original_final["id"], final["id"], node(original_final, "N2")["id"],
            failed_attempt_id, replacement_attempt["id"], "Agent-C1", "Agent-D1", iso(fault_at), failure_event["createdAt"], replacement_event["createdAt"],
            round(max(0.0, (parse_time(failure_event["createdAt"]) - fault_at).total_seconds() * 1000), 4),
            0.0,
            round((parse_time(completed_event["createdAt"]) - parse_time(first_dispatch["createdAt"])).total_seconds() * 1000, 4),
            final["status"] == "COMPLETED", final["status"] == "COMPLETED", max(0, payouts - 2), payouts > 2,
            node(original_final, "N1")["status"] == "SETTLED", reservation_status(original_final, failed_attempt_id),
            bool(original_final["ledgerSummary"]["balanced"] and final["ledgerSummary"]["balanced"]), False, 1,
            str(topology.database_path), "",
        )
    except BaseException as exc:
        return empty_recovery(topology.suite_id, round_number, variant, topology.database_path, exc)


def result_submission(topology: PlatformTopology, workflow_id: str, node_key: str, attempt: dict[str, Any], slug: str, label: str) -> httpx.Response:
    output = {"summary": label, "score": 92} if node_key == "N1" else {"verified": True, "report": label}
    return topology.post(
        f"/workflows/{workflow_id}/nodes/{node_key}/attempts/{attempt['id']}/results",
        {"output": output, "proofHash": "0x" + "9" * 64, "resultMessageId": f"attack-{uuid.uuid4().hex}"},
        slug,
        f"attack-{uuid.uuid4().hex}",
    )


def signed_late_result(
    topology: PlatformTopology,
    offer: dict[str, Any],
    invocation: dict[str, Any],
) -> dict[str, Any]:
    topology.start_runner("C", delay=0.0)
    unsigned_offer = dict(offer)
    unsigned_offer.pop("offerId", None)
    unsigned_offer = {"offerId": offer["offerId"], **unsigned_offer}
    signature = base64.urlsafe_b64encode(
        hmac.new(RUNNER_TOKEN.encode("utf-8"), rfc8785.dumps(unsigned_offer), hashlib.sha256).digest()
    ).rstrip(b"=").decode("ascii")
    headers = {"Authorization": f"Bearer {RUNNER_TOKEN}"}
    runner_base = f"http://127.0.0.1:{topology.runner_ports['C']}/agents/workflow-verification-c"
    with httpx.Client(timeout=30.0) as client:
        accepted_response = client.post(
            f"{runner_base}/task-offers",
            headers=headers,
            json={**unsigned_offer, "coordinatorSignature": signature},
        )
        accepted_response.raise_for_status()
        invoke_response = client.post(f"{runner_base}/invoke", headers=headers, json=invocation)
        invoke_response.raise_for_status()
        return invoke_response.json()


def signed_result_submission(
    topology: PlatformTopology,
    workflow_id: str,
    node_key: str,
    attempt: dict[str, Any],
    runtime_envelope: dict[str, Any],
) -> httpx.Response:
    return topology.post(
        f"/workflows/{workflow_id}/nodes/{node_key}/attempts/{attempt['id']}/results",
        {
            "output": runtime_envelope["output"],
            "proofHash": "0x" + "9" * 64,
            "resultMessageId": f"late-{uuid.uuid4().hex}",
            "runtimeEnvelope": runtime_envelope,
        },
        "workflow-verification-c",
        f"late-{uuid.uuid4().hex}",
    )


def accepted(response: httpx.Response) -> bool:
    if response.status_code != 202:
        return False
    try:
        return response.json().get("accepted") is True
    except ValueError:
        return False


def run_attack_round(topology: PlatformTopology, output_dir: Path, round_number: int) -> AttackRun:
    variant = topology.variant
    run_id = str(uuid.uuid4())
    prefix = f"{variant.lower()}-{round_number}-{run_id[:8]}"
    try:
        topology.reset_profiles(prefix)
        topology.start_runner("C", delay=15.0)
        workflow = create_workflow(topology, prefix)
        prepare_n1(topology, workflow, prefix)
        failed, failed_attempt_id, _, failed_offer, failed_invocation = inject_c_failure(
            topology, workflow["id"], prefix
        )
        failed_attempt = next(item for item in node(failed, "N2")["attempts"] if item["id"] == failed_attempt_id)
        recovered, replacement, same_agent_reselected = rematch_d(topology, workflow["id"], prefix)
        attempt_n1 = node(recovered, "N1")["currentAttempt"]
        late_envelope = signed_late_result(topology, failed_offer, failed_invocation)
        attack_a = signed_result_submission(
            topology, workflow["id"], "N2", failed_attempt, late_envelope
        )
        attack_b = topology.post(
            f"/workflows/{workflow['id']}/nodes/N2/attempts/{failed_attempt_id}/settlement-confirmations",
            {
                "messageId": f"fake-{uuid.uuid4().hex}", "destinationTxHash": "0x" + "9" * 64,
                "sourceConfirmTxHash": "0x" + "8" * 64, "receivedAmount": "6.80",
                "homeWallet": failed_attempt["homeWallet"], "settlementFee": "0", "settlementAsset": "USDC",
            },
            "platform-admin",
            f"{prefix}-attack-b-{uuid.uuid4().hex}",
        )
        attack_c = result_submission(topology, workflow["id"], "N1", attempt_n1, "workflow-research-b", "repeat predecessor result")
        attacked = topology.get(f"/workflows/{workflow['id']}")
        predecessor_retained = node(attacked, "N1")["status"] == "SETTLED"
        current_preserved = node(attacked, "N2")["currentAttemptId"] == replacement["id"]
        recovery_completed = False
        final = attacked
        try:
            final = finish_recovered(topology, workflow["id"], f"{prefix}-post-attack")
            recovery_completed = final["status"] == "COMPLETED"
        except BaseException:
            final = topology.get(f"/workflows/{workflow['id']}")
        save_evidence(output_dir, variant, round_number, "workflow", final)
        payouts = payout_count(final)
        return AttackRun(
            topology.suite_id, run_id, round_number, variant, workflow["id"], node(final, "N2")["id"], failed_attempt_id,
            replacement["id"], same_agent_reselected, attack_a.status_code, accepted(attack_a), attack_b.status_code,
            attack_b.status_code < 300, attack_c.status_code, accepted(attack_c), predecessor_retained, current_preserved,
            recovery_completed, max(0, payouts - 2), bool(final["ledgerSummary"]["balanced"]), str(topology.database_path), "",
        )
    except BaseException as exc:
        return AttackRun(topology.suite_id, run_id, round_number, variant, "", "", "", "", False, 0, False, 0, False, 0, False, False, False, False, 0, False, str(topology.database_path), f"{type(exc).__name__}: {exc}")


def mean_bool(items: list[Any], field: str) -> float:
    return sum(bool(getattr(item, field)) for item in items) / len(items) if items else 0.0


def mean_value(items: list[Any], field: str) -> float:
    return statistics.mean(float(getattr(item, field)) for item in items) if items else 0.0


def checkpoint_rows(recovery: list[RecoveryRun], attacks: list[AttackRun]) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for checkpoint in CHECKPOINTS:
        for variant in ("NFRDA", "FULL_WORKFLOW_RESTART", "RESERVATION_RELEASE_DISABLED"):
            subset = [item for item in recovery if item.variant == variant and item.round <= checkpoint]
            rows.append({
                "checkpoint": checkpoint, "experiment": "RECOVERY", "variant": variant, "sample_count": len(subset),
                "fault_detection_mean_ms": round(mean_value(subset, "fault_detection_ms"), 4),
                "rematch_mean_ms": round(mean_value(subset, "rematch_ms"), 4),
                "workflow_completion_mean_ms": round(mean_value(subset, "workflow_completion_ms"), 4),
                "task_success_rate": round(mean_bool(subset, "task_success"), 4),
                "recovery_completion_rate": round(mean_bool(subset, "recovery_completed"), 4),
                "duplicate_payment_rate": round(mean_bool(subset, "duplicate_payment"), 4),
                "predecessor_retention_rate": round(mean_bool(subset, "predecessor_retained"), 4),
                "funds_conservation_rate": round(mean_bool(subset, "funds_conserved"), 4),
                "same_agent_reselection_rate": "", "attack_a_success_rate": "", "attack_b_success_rate": "", "attack_c_success_rate": "",
            })
        for variant in ("NORMAL_GUARDS", "ATTEMPT_GUARD_DISABLED"):
            subset = [item for item in attacks if item.variant == variant and item.round <= checkpoint]
            rows.append({
                "checkpoint": checkpoint, "experiment": "GUARD_ATTACKS", "variant": variant, "sample_count": len(subset),
                "fault_detection_mean_ms": "", "rematch_mean_ms": "", "workflow_completion_mean_ms": "", "task_success_rate": "",
                "recovery_completion_rate": round(mean_bool(subset, "recovery_completed_after_attacks"), 4),
                "duplicate_payment_rate": round(sum(item.duplicate_payment_count > 0 for item in subset) / len(subset), 4) if subset else 0,
                "predecessor_retention_rate": round(mean_bool(subset, "predecessor_retained"), 4),
                "funds_conservation_rate": round(mean_bool(subset, "ledger_balanced"), 4),
                "same_agent_reselection_rate": round(mean_bool(subset, "same_agent_reselected"), 4),
                "attack_a_success_rate": round(mean_bool(subset, "attack_a_success"), 4),
                "attack_b_success_rate": round(mean_bool(subset, "attack_b_success"), 4),
                "attack_c_success_rate": round(mean_bool(subset, "attack_c_success"), 4),
            })
    return rows


def write_csv(path: Path, rows: list[dict[str, Any]]) -> None:
    if not rows:
        return
    with path.open("w", newline="", encoding="utf-8-sig") as target:
        writer = csv.DictWriter(target, fieldnames=list(rows[0]))
        writer.writeheader()
        writer.writerows(rows)


def group_summary(recovery: list[RecoveryRun], attacks: list[AttackRun]) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for variant in ("NFRDA", "FULL_WORKFLOW_RESTART", "RESERVATION_RELEASE_DISABLED"):
        subset = [item for item in recovery if item.variant == variant]
        result[variant] = {
            "runs": len(subset), "passed": sum(not item.error for item in subset),
            "averageFaultDetectionMs": round(mean_value(subset, "fault_detection_ms"), 4),
            "averageRematchMs": round(mean_value(subset, "rematch_ms"), 4),
            "averageCompletionMs": round(mean_value(subset, "workflow_completion_ms"), 4),
            "recoveryCompletionRate": round(mean_bool(subset, "recovery_completed"), 4),
            "duplicatePaymentRate": round(mean_bool(subset, "duplicate_payment"), 4),
            "predecessorRetentionRate": round(mean_bool(subset, "predecessor_retained"), 4),
            "fundsConservationRate": round(mean_bool(subset, "funds_conserved"), 4),
        }
    for variant in ("NORMAL_GUARDS", "ATTEMPT_GUARD_DISABLED"):
        subset = [item for item in attacks if item.variant == variant]
        result[variant] = {
            "runs": len(subset), "passed": sum(not item.error for item in subset),
            "sameAgentReselectionRate": round(mean_bool(subset, "same_agent_reselected"), 4),
            "attackASuccessRate": round(mean_bool(subset, "attack_a_success"), 4),
            "attackBSuccessRate": round(mean_bool(subset, "attack_b_success"), 4),
            "attackCSuccessRate": round(mean_bool(subset, "attack_c_success"), 4),
            "predecessorRetentionRate": round(mean_bool(subset, "predecessor_retained"), 4),
            "recoveryCompletionRate": round(mean_bool(subset, "recovery_completed_after_attacks"), 4),
        }
    return result


def run_variant(suite_id: str, variant: str, rounds: int, runtime_root: Path, output_dir: Path) -> tuple[list[RecoveryRun], list[AttackRun]]:
    topology = PlatformTopology(suite_id, variant, runtime_root)
    recovery: list[RecoveryRun] = []
    attacks: list[AttackRun] = []
    try:
        topology.start()
        for round_number in range(1, rounds + 1):
            print(f"[{variant}] {round_number}/{rounds}", flush=True)
            if variant == "FULL_WORKFLOW_RESTART":
                recovery.append(run_full_restart_round(topology, output_dir, round_number))
            elif variant in {"NFRDA", "RESERVATION_RELEASE_DISABLED"}:
                recovery.append(run_recovery_round(topology, output_dir, round_number))
            else:
                attacks.append(run_attack_round(topology, output_dir, round_number))
    finally:
        topology.stop()
    return recovery, attacks


def main() -> int:
    parser = argparse.ArgumentParser(description="Run Algorithm 4 through deployed HTTP platform services.")
    parser.add_argument("--rounds", type=int, default=30)
    parser.add_argument("--variants", default="NFRDA,FULL_WORKFLOW_RESTART,RESERVATION_RELEASE_DISABLED,NORMAL_GUARDS,ATTEMPT_GUARD_DISABLED")
    parser.add_argument("--output-root", type=Path, default=Path.home() / "Documents" / "Codex" / "2026-07-16" / "wo" / "outputs" / "algorithm4-platform-experiment")
    parser.add_argument("--runtime-root", type=Path, default=PROJECT_ROOT / ".runtime" / "algorithm4-platform")
    args = parser.parse_args()
    if args.rounds < 1 or args.rounds > 30:
        raise SystemExit("--rounds must be between 1 and 30")
    suite_id = f"algorithm4-platform-{datetime.now().strftime('%Y%m%d-%H%M%S')}"
    output_dir = args.output_root.resolve() / "results" / suite_id
    runtime_root = args.runtime_root.resolve() / suite_id
    output_dir.mkdir(parents=True, exist_ok=True)
    started_at = iso()
    recovery: list[RecoveryRun] = []
    attacks: list[AttackRun] = []
    for variant in [item.strip().upper() for item in args.variants.split(",") if item.strip()]:
        variant_recovery, variant_attacks = run_variant(suite_id, variant, args.rounds, runtime_root, output_dir)
        recovery.extend(variant_recovery)
        attacks.extend(variant_attacks)
        write_csv(output_dir / "recovery_rounds.partial.csv", [asdict(item) for item in recovery])
        write_csv(output_dir / "attack_rounds.partial.csv", [asdict(item) for item in attacks])
    recovery_rows = [asdict(item) for item in recovery]
    attack_rows = [asdict(item) for item in attacks]
    checkpoints = checkpoint_rows(recovery, attacks)
    write_csv(output_dir / "recovery_rounds.csv", recovery_rows)
    write_csv(output_dir / "attack_rounds.csv", attack_rows)
    write_csv(output_dir / "checkpoints.csv", checkpoints)
    errors = [item.error for item in [*recovery, *attacks] if item.error]
    summary = {
        "schemaVersion": 2, "suiteId": suite_id, "startedAt": started_at, "finishedAt": iso(),
        "mode": "Deployed FastAPI API + Coordinator Core + independent HTTP Mock Agent Runners + file SQLite + mock settlement",
        "evidenceLevel": "PLATFORM_LEVEL", "roundsPerVariant": args.rounds,
        "checkpoints": [point for point in CHECKPOINTS if point <= args.rounds],
        "platformSource": str(PROJECT_ROOT), "requesterWalletUsed": False, "realFundsUsed": False,
        "normalWorkflowDatabaseUsed": False, "experimentRuntimeRoot": str(runtime_root),
        "groups": group_summary(recovery, attacks), "errors": errors,
    }
    (output_dir / "summary.json").write_text(json.dumps(summary, ensure_ascii=False, indent=2), encoding="utf-8")
    (output_dir / "README.md").write_text(
        "# Algorithm 4 platform-level experiment\n\n"
        "- Every round used deployed HTTP services and independent Mock Agent Runner processes.\n"
        "- C Runner failure was injected after task-offer acceptance and classified by the API as AGENT_TIMEOUT.\n"
        "- Databases and workflow evidence are isolated from the normal platform and Requester wallet.\n"
        "- Checkpoints are cumulative at 5, 10, 15, 20, 25 and 30 actual rounds per variant.\n",
        encoding="utf-8",
    )
    (args.output_root.resolve() / "LATEST.txt").write_text(str(output_dir), encoding="utf-8")
    print(json.dumps({"resultDir": str(output_dir), "errors": len(errors)}, ensure_ascii=False), flush=True)
    return 0 if not errors else 1


if __name__ == "__main__":
    raise SystemExit(main())
