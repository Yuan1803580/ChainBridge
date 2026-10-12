from __future__ import annotations

import argparse
import csv
import json
import os
import time
import uuid
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path
from typing import Any
from urllib.error import HTTPError, URLError
from urllib.request import ProxyHandler, Request, build_opener, urlopen


ROOT_DIR = Path(__file__).resolve().parents[2]
DEFAULT_RESULTS_DIR = ROOT_DIR / "experiments" / "results" / "real-agents"

AGENTS = {
    "kimi": {
        "agentKey": "workflow-research-kimi-external-v2",
        "profileId": "0e21ec66-c7e2-468b-811c-20c979b60c8e",
    },
    "codex": {
        "agentKey": "workflow-multimodal-research-codex-c",
        "profileId": "42b60d1d-a446-43f0-8343-3fba99a1525a",
    },
    "delivery": {
        "agentKey": "workflow-verification-delivery-d",
        "profileId": "43aa8494-8610-44ba-a3ca-b6303086c0fc",
    },
}

RUN_FIELDS = [
    "runId",
    "scenarioId",
    "workflowId",
    "referenceCode",
    "passed",
    "workflowStatus",
    "nodeCount",
    "settledNodeCount",
    "attemptCount",
    "failedAttemptCount",
    "agentCount",
    "agentKeys",
    "workflowDurationMs",
    "runnerDurationMs",
    "paidAmount",
    "refundedAmount",
    "platformFee",
    "ledgerBalanced",
    "payoutEntryCount",
    "duplicatePayment",
    "qualityPolicyVersion",
    "qualityStatus",
    "qualityScore",
    "completionRate",
    "reviewPassRate",
    "averageMatchingTrust",
    "averageFinalAttemptQuality",
    "failureReason",
    "llmMode",
    "workflowFile",
]

def _probe_health(url: str) -> dict[str, Any] | None:
    opener = build_opener(ProxyHandler({}))
    try:
        with opener.open(Request(url, headers={"Accept": "application/json"}), timeout=8) as response:
            value = json.loads(response.read().decode("utf-8-sig"))
    except (HTTPError, URLError, ValueError, OSError):
        return None
    return value if isinstance(value, dict) else None


def collect_llm_mode(
    api_url: str,
    *,
    coordinator_url: str = "",
    runner_token: str = "",
) -> dict[str, Any]:
    """Record whether the inference layer of this suite ran on a real model or a stub.

    Every exported evidence file must be able to prove on its own whether the
    node outputs were produced by a real LLM or by the deterministic mock
    provider. A stub is a legitimate local fallback, but only when it is
    labelled; an unlabelled stub makes the evidence misleading.
    """
    coordinator = coordinator_url or os.getenv("COORDINATOR_URL", "http://127.0.0.1:8300")
    components: list[dict[str, Any]] = []

    coordinator_health = _probe_health(f"{coordinator.rstrip('/')}/health")
    components.append(
        {
            "component": "coordinator",
            "provider": (coordinator_health or {}).get("provider"),
            "model": (coordinator_health or {}).get("model"),
            "reachable": coordinator_health is not None,
        }
    )

    runners: dict[str, str] = {}
    directory = _probe_health(f"{api_url.rstrip('/')}/api/v1/workflow-agents")
    for item in (directory or {}).get("items", []) if isinstance(directory, dict) else []:
        if not isinstance(item, dict):
            continue
        if str(item.get("agentType") or "EXECUTOR").upper() != "EXECUTOR":
            continue
        endpoint = str((item.get("agent") or {}).get("endpoint") or "")
        if not endpoint.startswith("http"):
            continue
        parts = endpoint.split("/agents/", 1)
        if len(parts) != 2:
            continue
        runners[parts[0].rstrip("/")] = str(item.get("agentKey") or "")

    for base in sorted(runners):
        health = _probe_health(f"{base}/health")
        components.append(
            {
                "component": "runner",
                "agentKey": runners[base],
                "endpoint": base,
                "provider": health.get("llmProvider") if health else None,
                "reachable": health is not None,
            }
        )

    known = [item for item in components if item["reachable"] and item.get("provider")]
    providers = sorted({str(item["provider"]).lower() for item in known})
    if not known:
        mode = "unknown"
    elif providers == ["mock"]:
        mode = "mock"
    elif "mock" in providers:
        mode = "mixed"
    else:
        mode = "real"
    return {
        "mode": mode,
        "providers": providers,
        "components": components,
        "statement": (
            "Node outputs were produced by deterministic stub providers, not by a real LLM. "
            "Only protocol-layer conclusions (identity, heartbeat, matching, cross-chain "
            "settlement) are supported by this evidence."
            if mode == "mock"
            else "Node outputs were produced by real LLM providers."
            if mode == "real"
            else "Some components used stub providers and others used real LLM providers. "
            "Per-component detail is listed in components."
            if mode == "mixed"
            else "Inference mode could not be determined because no service was reachable."
        ),
    }


ATTEMPT_FIELDS = [
    "scenarioId",
    "workflowId",
    "referenceCode",
    "nodeKey",
    "nodeId",
    "attemptId",
    "attemptNumber",
    "agentKey",
    "chainId",
    "status",
    "failureReasonCode",
    "failureResponsibleParty",
    "createdAt",
    "submittedAt",
    "reviewedAt",
    "settledAt",
    "executionMs",
    "reviewMs",
    "settlementMs",
    "reviewStatus",
    "settlementStatus",
    "resultHash",
    "reportedScore",
    "matchingTrustScore",
    "matchingTrustSource",
    "qualityPolicyVersion",
    "qualityStatus",
    "qualityScore",
    "completionScore",
    "reviewScore",
    "matchingTrustComponentScore",
    "qualityEvidenceHash",
]


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


def parse_time(value: str | None) -> datetime | None:
    if not value:
        return None
    return datetime.fromisoformat(value.replace("Z", "+00:00"))


def elapsed_ms(start: str | None, end: str | None) -> int | None:
    start_time = parse_time(start)
    end_time = parse_time(end)
    if start_time is None or end_time is None:
        return None
    return round((end_time - start_time).total_seconds() * 1000)


def write_json(path: Path, value: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value, ensure_ascii=False, indent=2), encoding="utf-8")


def write_csv(path: Path, rows: list[dict[str, Any]], fields: list[str]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8-sig", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=fields, extrasaction="ignore")
        writer.writeheader()
        writer.writerows(rows)


class PlatformClient:
    def __init__(self, api_url: str) -> None:
        self.api_url = api_url.rstrip("/")
        self.requester_token = self.login("did:local:campus-requester")
        self.admin_token = self.login("did:local:platform-admin")

    def request(
        self,
        method: str,
        path: str,
        payload: dict[str, Any] | None = None,
        *,
        token: str | None = None,
        timeout: float = 30,
    ) -> dict[str, Any]:
        body = None if payload is None else json.dumps(payload).encode("utf-8")
        headers = {"Accept": "application/json"}
        if body is not None:
            headers["Content-Type"] = "application/json"
        if token:
            headers["Authorization"] = f"Bearer {token}"
        if method.upper() != "GET":
            headers["Idempotency-Key"] = str(uuid.uuid4())
        request = Request(
            f"{self.api_url}{path}",
            data=body,
            headers=headers,
            method=method.upper(),
        )
        try:
            with urlopen(request, timeout=timeout) as response:
                value = json.loads(response.read().decode("utf-8-sig"))
        except HTTPError as exc:
            detail = exc.read().decode("utf-8", errors="replace")
            raise RuntimeError(f"{method} {path} failed ({exc.code}): {detail}") from exc
        except URLError as exc:
            raise RuntimeError(f"{method} {path} failed: {exc}") from exc
        if not isinstance(value, dict):
            raise RuntimeError(f"{method} {path} returned a non-object response")
        return value

    def login(self, did: str) -> str:
        response = self.request("POST", "/api/v1/auth/dev-login", {"did": did})
        token = response.get("accessToken")
        if not isinstance(token, str) or not token:
            raise RuntimeError(f"Development login did not return a token for {did}")
        return token

    def health(self) -> dict[str, Any]:
        return self.request("GET", "/api/v1/health")

    def workflow(self, workflow_id: str) -> dict[str, Any]:
        return self.request("GET", f"/api/v1/workflows/{workflow_id}")

    def create_workflow(self, payload: dict[str, Any]) -> dict[str, Any]:
        return self.request(
            "POST",
            "/api/v1/workflows",
            payload,
            token=self.requester_token,
        )

    def coordinate(self, workflow_id: str, max_actions: int = 1) -> dict[str, Any]:
        return self.request(
            "POST",
            f"/api/v1/workflows/{workflow_id}/coordinate",
            {"maxActions": max_actions},
            token=self.requester_token,
            timeout=2300,
        )

    def profiles(self) -> list[dict[str, Any]]:
        response = self.request("GET", "/api/v1/workflow-agents")
        return [item for item in response.get("items", []) if isinstance(item, dict)]

    def patch_profile(self, profile_id: str, payload: dict[str, Any]) -> dict[str, Any]:
        last_error: RuntimeError | None = None
        for attempt in range(1, 21):
            try:
                return self.request(
                    "PATCH",
                    f"/api/v1/workflow-agents/{profile_id}",
                    payload,
                    token=self.admin_token,
                )
            except RuntimeError as exc:
                last_error = exc
                message = str(exc).lower()
                transient = (
                    "(500)" in message
                    or "(503)" in message
                    or "database is locked" in message
                    or "temporarily unavailable" in message
                )
                if not transient or attempt == 20:
                    raise
                time.sleep(min(0.1 * (2 ** (attempt - 1)), 2.0))
        raise last_error or RuntimeError("Agent profile update failed")

    def fail_attempt(
        self,
        workflow_id: str,
        node_id: str,
        attempt_id: str,
        reason_code: str,
        detail: str,
    ) -> dict[str, Any]:
        return self.request(
            "POST",
            f"/api/v1/workflows/{workflow_id}/nodes/{node_id}/attempts/{attempt_id}/failures",
            {"reasonCode": reason_code, "detail": detail},
            token=self.admin_token,
        )


class CapacityController:
    def __init__(self, client: PlatformClient) -> None:
        self.client = client
        profiles = {item.get("agentKey"): item for item in client.profiles()}
        self.snapshot: dict[str, dict[str, Any]] = {}
        for config in AGENTS.values():
            profile = profiles.get(config["agentKey"])
            if profile is None:
                raise RuntimeError(f"Agent profile is missing: {config['agentKey']}")
            config["profileId"] = str(profile["id"])
            self.snapshot[str(profile["id"])] = {
                "capabilities": list(profile.get("capabilities") or []),
                "capacityAvailable": bool(profile.get("capacityAvailable")),
                "availability": str(profile.get("availability") or "ONLINE"),
                "trustScore": int(profile.get("trustScore") or 0),
                "successRateBps": int(profile.get("successRateBps") or 0),
                "priceFloor": str(profile.get("priceFloor") or "0"),
                "endpointLatencyMs": int(profile.get("endpointLatencyMs") or 0),
            }

    def configure_capabilities(self, capabilities: dict[str, list[str]]) -> None:
        if set(capabilities) != set(AGENTS):
            raise ValueError("Capability controls must include kimi, codex, and delivery")
        for name, config in AGENTS.items():
            self.client.patch_profile(
                config["profileId"],
                {
                    "capabilities": capabilities[name],
                },
            )
        self.wait_until_online()

    def wait_until_online(
        self,
        *,
        timeout_seconds: float = 75,
        max_heartbeat_age_seconds: float = 45,
    ) -> None:
        deadline = time.monotonic() + timeout_seconds
        latest_state: dict[str, dict[str, Any]] = {}
        while time.monotonic() < deadline:
            profiles = {item.get("agentKey"): item for item in self.client.profiles()}
            now = datetime.now(timezone.utc)
            latest_state = {}
            all_ready = True
            for name, config in AGENTS.items():
                profile = profiles.get(config["agentKey"]) or {}
                heartbeat = parse_time(profile.get("lastHeartbeatAt"))
                heartbeat_age = None if heartbeat is None else (now - heartbeat).total_seconds()
                signed_heartbeat = ((profile.get("metadata") or {}).get("lastSignedHeartbeat"))
                ready = bool(
                    profile.get("availability") == "ONLINE"
                    and profile.get("capacityAvailable") is True
                    and heartbeat_age is not None
                    and heartbeat_age <= max_heartbeat_age_seconds
                    and isinstance(signed_heartbeat, dict)
                )
                latest_state[name] = {
                    "availability": profile.get("availability"),
                    "capacityAvailable": profile.get("capacityAvailable"),
                    "heartbeatAgeSeconds": None if heartbeat_age is None else round(heartbeat_age, 2),
                    "signedHeartbeat": isinstance(signed_heartbeat, dict),
                }
                all_ready = all_ready and ready
            if all_ready:
                return
            time.sleep(0.5)
        raise RuntimeError(f"Real Agents did not become heartbeat-ready: {latest_state}")

    def restore(self) -> None:
        for profile_id, state in self.snapshot.items():
            self.client.patch_profile(profile_id, state)


def node_payload(
    node_key: str,
    title: str,
    description: str,
    capability: str,
    reward: str,
    *,
    depends_on: list[str] | None = None,
    required_fields: list[str] | None = None,
    input_payload: dict[str, Any] | None = None,
) -> dict[str, Any]:
    return {
        "nodeKey": node_key,
        "title": title,
        "description": description,
        "capability": capability,
        "dependsOn": depends_on or [],
        "acceptanceCriteria": {
            "requiredFields": required_fields or ["summary", "score", "sources"],
            "rules": [
                "Return a concise result that directly answers the objective.",
                "Do not invent sources or completed artifacts.",
            ],
        },
        "inputPayload": input_payload or {},
        "reward": reward,
        "riskLevel": "LOW",
        "reviewPolicyVersion": "RISK_REVIEW_V2",
        "requiredApprovals": 0,
        "challengePeriodSeconds": 0,
        "samplingRateBps": 0,
        "reviewBudget": "0",
        "maxAttempts": 3,
    }


def workflow_payload(
    scenario_id: str,
    title: str,
    objective: str,
    nodes: list[dict[str, Any]],
    budget: str,
) -> dict[str, Any]:
    return {
        "title": title,
        "objective": objective,
        "budget": budget,
        "platformFee": "0.50",
        "currency": "USDC",
        "sourceChainId": 31337,
        "inputPayload": {"experimentScenario": scenario_id, "language": "zh-CN"},
        "nodes": nodes,
        "fundingMode": "RESERVED",
    }


def first_unsettled_node(workflow: dict[str, Any]) -> dict[str, Any] | None:
    for node in workflow.get("nodes", []):
        if node.get("status") != "SETTLED":
            return node
    return None


def drive_workflow(
    client: PlatformClient,
    capacity: CapacityController,
    workflow_id: str,
    desired_agents: dict[str, str],
    *,
    timeout_seconds: float = 2200,
) -> tuple[dict[str, Any], list[dict[str, Any]]]:
    trace: list[dict[str, Any]] = []
    deadline = time.monotonic() + timeout_seconds
    step = 0
    while time.monotonic() < deadline:
        step += 1
        workflow = client.workflow(workflow_id)
        if workflow.get("status") in {"COMPLETED", "FAILED", "CANCELLED"}:
            return workflow, trace
        node = first_unsettled_node(workflow)
        if node is None:
            time.sleep(0.5)
            continue
        desired_agent = desired_agents[str(node["nodeKey"])]
        response: dict[str, Any] | None = None
        started = time.perf_counter()
        for lock_attempt in range(1, 41):
            try:
                response = client.coordinate(workflow_id, max_actions=1)
                break
            except RuntimeError as exc:
                if "(409)" not in str(exc) or "coordination is already in progress" not in str(exc):
                    raise
                time.sleep(min(0.25 * lock_attempt, 2.0))
        if response is None:
            raise RuntimeError(f"Workflow {workflow_id} coordination lock did not become available")
        trace.append(
            {
                "step": step,
                "nodeKey": node["nodeKey"],
                "desiredAgent": desired_agent,
                "durationMs": round((time.perf_counter() - started) * 1000),
                "actions": response.get("actions", []),
                "workflowStatus": response.get("workflow", {}).get("status"),
            }
        )
        if not response.get("actions"):
            time.sleep(0.5)
    workflow = client.workflow(workflow_id)
    node_states = {
        str(node.get("nodeKey")): {
            "nodeStatus": node.get("status"),
            "attemptStatus": (node.get("currentAttempt") or {}).get("status"),
        }
        for node in workflow.get("nodes", [])
    }
    raise RuntimeError(
        f"Workflow {workflow_id} did not terminate within {timeout_seconds} seconds: {node_states}"
    )


def run_real_f1(
    client: PlatformClient,
    capacity: CapacityController,
    existing_workflow_id: str | None,
) -> tuple[dict[str, Any], list[dict[str, Any]], str]:
    if existing_workflow_id:
        return client.workflow(existing_workflow_id), [], "existing-workflow"
    capacity.configure_capabilities(
        {
            "kimi": ["research"],
            "codex": ["verification"],
            "delivery": ["academic_research"],
        }
    )
    created = client.create_workflow(
        workflow_payload(
            "REAL-F1",
            "REAL-F1 single-node real Agent success",
            "Use one real Agent to produce a source-backed summary and complete node settlement.",
            [
                node_payload(
                    "N1",
                    "Produce a source-backed recovery summary",
                    "Summarize three mechanisms that make node-level workflow recovery safe.",
                    "academic_research",
                    "4.20",
                    input_payload={"language": "zh-CN", "maxSources": 3},
                )
            ],
            "8.00",
        )
    )
    workflow, trace = drive_workflow(
        client,
        capacity,
        str(created["id"]),
        {"N1": "delivery"},
    )
    return workflow, trace, "new-workflow"


def run_real_f2(
    client: PlatformClient,
    capacity: CapacityController,
    existing_workflow_id: str | None,
) -> tuple[dict[str, Any], list[dict[str, Any]], str]:
    capacity.configure_capabilities(
        {
            "kimi": ["research"],
            "codex": ["verification"],
            "delivery": ["markdown_document"],
        }
    )
    if existing_workflow_id:
        workflow = client.workflow(existing_workflow_id)
        if workflow.get("status") in {"COMPLETED", "FAILED", "CANCELLED"}:
            return workflow, [], "existing-workflow"
        workflow, trace = drive_workflow(
            client,
            capacity,
            existing_workflow_id,
            {"N1": "kimi", "N2": "codex", "N3": "delivery"},
        )
        return workflow, trace, "continued-existing-workflow"

    created = client.create_workflow(
        workflow_payload(
            "REAL-F2",
            "REAL-F2 three-Agent dependent DAG",
            "Research, verify, and package a short report through three dependent real Agent nodes.",
            [
                node_payload(
                    "N1",
                    "Research node-level recovery",
                    "Use project evidence to identify three node-level failure recovery mechanisms.",
                    "research",
                    "4.20",
                    input_payload={"language": "zh-CN", "topic": "node-level workflow recovery"},
                ),
                node_payload(
                    "N2",
                    "Verify the research result",
                    "Verify the upstream claims and identify any unsupported statement.",
                    "verification",
                    "4.20",
                    depends_on=["N1"],
                    input_payload={"language": "zh-CN", "verificationMode": "evidence"},
                ),
                node_payload(
                    "N3",
                    "Create the final Markdown report",
                    "Create a concise Markdown report from the verified upstream findings.",
                    "markdown_document",
                    "4.20",
                    depends_on=["N2"],
                    required_fields=[
                        "summary",
                        "score",
                        "sources",
                        "verified",
                        "findings",
                        "artifacts",
                    ],
                    input_payload={"language": "zh-CN", "filename": "real-f2-recovery-report.md"},
                ),
            ],
            "15.00",
        )
    )
    workflow, trace = drive_workflow(
        client,
        capacity,
        str(created["id"]),
        {"N1": "kimi", "N2": "codex", "N3": "delivery"},
    )
    return workflow, trace, "new-workflow"


def wait_for_dispatched_attempt(
    client: PlatformClient,
    workflow_id: str,
    *,
    timeout_seconds: float = 30,
) -> tuple[dict[str, Any], dict[str, Any]]:
    deadline = time.monotonic() + timeout_seconds
    while time.monotonic() < deadline:
        workflow = client.workflow(workflow_id)
        node = workflow["nodes"][0]
        attempt = node.get("currentAttempt")
        if isinstance(attempt, dict) and attempt.get("status") == "DISPATCHED":
            return node, attempt
        time.sleep(0.5)
    raise RuntimeError(f"Workflow {workflow_id} did not create a DISPATCHED attempt")


def run_real_f3(
    client: PlatformClient,
    capacity: CapacityController,
    existing_workflow_id: str | None,
) -> tuple[dict[str, Any], list[dict[str, Any]], str]:
    if existing_workflow_id:
        return client.workflow(existing_workflow_id), [], "existing-workflow"
    capacity.configure_capabilities(
        {
            "kimi": ["experiment_standby"],
            "codex": ["research"],
            "delivery": ["markdown_document"],
        }
    )
    created = client.create_workflow(
        workflow_payload(
            "REAL-F3",
            "REAL-F3 timeout and Agent replacement",
            "Inject a timeout into the first real Agent attempt, then recover with a new Agent attempt.",
            [
                node_payload(
                    "N1",
                    "Recoverable research task",
                    "Summarize why stable nodeId and independent attemptId prevent duplicate settlement.",
                    "research",
                    "4.20",
                    input_payload={"language": "zh-CN", "topic": "attempt recovery"},
                )
            ],
            "8.00",
        )
    )
    workflow_id = str(created["id"])
    node, first_attempt = wait_for_dispatched_attempt(client, workflow_id)
    if first_attempt.get("agentKey") != AGENTS["codex"]["agentKey"]:
        raise RuntimeError(f"REAL-F3 first attempt used unexpected Agent: {first_attempt.get('agentKey')}")

    capacity.configure_capabilities(
        {
            "kimi": ["research"],
            "codex": ["verification"],
            "delivery": ["markdown_document"],
        }
    )
    client.fail_attempt(
        workflow_id,
        str(node["id"]),
        str(first_attempt["id"]),
        "AGENT_TIMEOUT",
        "Controlled REAL-F3 fault injection before Agent invocation.",
    )
    workflow, trace = drive_workflow(
        client,
        capacity,
        workflow_id,
        {"N1": "kimi"},
    )
    trace.insert(
        0,
        {
            "step": 0,
            "nodeKey": "N1",
            "desiredAgent": "codex",
            "durationMs": None,
            "actions": [{"action": "inject_timeout", "attemptId": first_attempt["id"]}],
            "workflowStatus": "RUNNING",
        },
    )
    return workflow, trace, "new-workflow-with-controlled-timeout"


def attempt_rows(scenario_id: str, workflow: dict[str, Any]) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for node in workflow.get("nodes", []):
        for attempt in node.get("attempts", []):
            breakdown = attempt.get("qualityBreakdown") or {}
            components = breakdown.get("components") or {}
            rows.append(
                {
                    "scenarioId": scenario_id,
                    "workflowId": workflow.get("id"),
                    "referenceCode": workflow.get("referenceCode"),
                    "nodeKey": node.get("nodeKey"),
                    "nodeId": node.get("id"),
                    "attemptId": attempt.get("id"),
                    "attemptNumber": attempt.get("attemptNumber"),
                    "agentKey": attempt.get("agentKey"),
                    "chainId": attempt.get("chainId"),
                    "status": attempt.get("status"),
                    "failureReasonCode": attempt.get("failureReasonCode"),
                    "failureResponsibleParty": attempt.get("failureResponsibleParty"),
                    "createdAt": attempt.get("createdAt"),
                    "submittedAt": attempt.get("submittedAt"),
                    "reviewedAt": attempt.get("reviewedAt"),
                    "settledAt": attempt.get("settledAt"),
                    "executionMs": elapsed_ms(attempt.get("createdAt"), attempt.get("submittedAt")),
                    "reviewMs": elapsed_ms(attempt.get("submittedAt"), attempt.get("reviewedAt")),
                    "settlementMs": elapsed_ms(attempt.get("reviewedAt"), attempt.get("settledAt")),
                    "reviewStatus": attempt.get("reviewStatus"),
                    "settlementStatus": attempt.get("settlementStatus"),
                    "resultHash": attempt.get("resultHash"),
                    "reportedScore": attempt.get("reportedScore"),
                    "matchingTrustScore": attempt.get("matchingTrustScore"),
                    "matchingTrustSource": attempt.get("matchingTrustSource"),
                    "qualityPolicyVersion": attempt.get("qualityPolicyVersion"),
                    "qualityStatus": attempt.get("qualityStatus"),
                    "qualityScore": attempt.get("qualityScore"),
                    "completionScore": (components.get("completion") or {}).get("score"),
                    "reviewScore": (components.get("review") or {}).get("score"),
                    "matchingTrustComponentScore": (components.get("matchingTrust") or {}).get("score"),
                    "qualityEvidenceHash": attempt.get("qualityEvidenceHash"),
                }
            )
    return rows


def scenario_errors(scenario_id: str, workflow: dict[str, Any]) -> list[str]:
    errors: list[str] = []
    nodes = workflow.get("nodes", [])
    attempts = [attempt for node in nodes for attempt in node.get("attempts", [])]
    payouts = [entry for entry in workflow.get("ledger", []) if entry.get("entryType") == "PAYOUT"]
    payout_counts = Counter(str(entry.get("attemptId")) for entry in payouts)
    if workflow.get("status") != "COMPLETED":
        errors.append(f"workflow status is {workflow.get('status')}")
    if not workflow.get("ledgerSummary", {}).get("balanced"):
        errors.append("ledger is not balanced")
    if any(node.get("status") != "SETTLED" for node in nodes):
        errors.append("not every node is SETTLED")
    if any(count > 1 for count in payout_counts.values()):
        errors.append("an attempt has more than one PAYOUT entry")

    if scenario_id == "REAL-F1":
        if len(nodes) != 1 or len(attempts) != 1 or len(payouts) != 1:
            errors.append("REAL-F1 requires one node, one attempt, and one payout")
        if attempts and attempts[0].get("agentKey") != AGENTS["delivery"]["agentKey"]:
            errors.append("REAL-F1 did not use the Delivery Agent")
    elif scenario_id == "REAL-F2":
        expected = {
            "N1": AGENTS["kimi"]["agentKey"],
            "N2": AGENTS["codex"]["agentKey"],
            "N3": AGENTS["delivery"]["agentKey"],
        }
        actual = {
            str(node.get("nodeKey")): (node.get("currentAttempt") or {}).get("agentKey")
            for node in nodes
        }
        if len(nodes) != 3 or len(payouts) != 3:
            errors.append("REAL-F2 requires three settled nodes and three payouts")
        if actual != expected:
            errors.append(f"REAL-F2 Agent assignment mismatch: {actual}")
    elif scenario_id == "REAL-F3":
        ordered = sorted(attempts, key=lambda item: int(item.get("attemptNumber") or 0))
        if len(nodes) != 1 or len(ordered) < 2 or len(payouts) != 1:
            errors.append("REAL-F3 requires one node, at least two attempts, and one payout")
        elif (
            ordered[0].get("agentKey") != AGENTS["codex"]["agentKey"]
            or ordered[0].get("failureReasonCode") != "AGENT_TIMEOUT"
            or ordered[-1].get("agentKey") != AGENTS["kimi"]["agentKey"]
            or ordered[-1].get("status") != "SETTLED"
        ):
            errors.append("REAL-F3 timeout and replacement evidence is incomplete")
    return errors


def build_run_row(
    scenario_id: str,
    workflow: dict[str, Any],
    runner_duration_ms: int,
    workflow_file: Path,
    errors: list[str],
) -> dict[str, Any]:
    nodes = workflow.get("nodes", [])
    attempts = [attempt for node in nodes for attempt in node.get("attempts", [])]
    agent_keys = sorted({str(item.get("agentKey")) for item in attempts if item.get("agentKey")})
    payouts = [entry for entry in workflow.get("ledger", []) if entry.get("entryType") == "PAYOUT"]
    payout_counts = Counter(str(entry.get("attemptId")) for entry in payouts)
    quality = workflow.get("qualitySummary") or {}
    return {
        "runId": str(uuid.uuid4()),
        "scenarioId": scenario_id,
        "workflowId": workflow.get("id"),
        "referenceCode": workflow.get("referenceCode"),
        "passed": not errors,
        "workflowStatus": workflow.get("status"),
        "nodeCount": len(nodes),
        "settledNodeCount": sum(node.get("status") == "SETTLED" for node in nodes),
        "attemptCount": len(attempts),
        "failedAttemptCount": sum(item.get("status") == "FAILED" for item in attempts),
        "agentCount": len(agent_keys),
        "agentKeys": ";".join(agent_keys),
        "workflowDurationMs": elapsed_ms(workflow.get("createdAt"), workflow.get("completedAt")),
        "runnerDurationMs": runner_duration_ms,
        "paidAmount": workflow.get("paidAmount"),
        "refundedAmount": workflow.get("refundedAmount"),
        "platformFee": workflow.get("platformFee"),
        "ledgerBalanced": bool(workflow.get("ledgerSummary", {}).get("balanced")),
        "payoutEntryCount": len(payouts),
        "duplicatePayment": any(count > 1 for count in payout_counts.values()),
        "qualityPolicyVersion": quality.get("policyVersion"),
        "qualityStatus": quality.get("qualityStatus"),
        "qualityScore": quality.get("qualityScore"),
        "completionRate": quality.get("completionRate"),
        "reviewPassRate": quality.get("reviewPassRate"),
        "averageMatchingTrust": quality.get("averageMatchingTrust"),
        "averageFinalAttemptQuality": quality.get("averageFinalAttemptQuality"),
        "failureReason": "; ".join(errors),
        "workflowFile": str(workflow_file),
    }


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Run real Agent functional experiments and export CSV data.")
    parser.add_argument("--api-url", default=os.getenv("EXPERIMENT_API_URL", "http://127.0.0.1:8200"))
    parser.add_argument("--output-root", type=Path, default=DEFAULT_RESULTS_DIR)
    parser.add_argument(
        "--scenarios",
        default="REAL-F1,REAL-F2,REAL-F3",
        help="Comma-separated subset of REAL-F1,REAL-F2,REAL-F3.",
    )
    parser.add_argument(
        "--real-f1-workflow-id",
        default=os.getenv("REAL_F1_WORKFLOW_ID"),
        help="Collect an already completed REAL-F1 workflow instead of creating a new one.",
    )
    parser.add_argument(
        "--real-f2-workflow-id",
        default=os.getenv("REAL_F2_WORKFLOW_ID"),
        help="Continue or collect an existing REAL-F2 workflow instead of creating a new one.",
    )
    parser.add_argument(
        "--real-f3-workflow-id",
        default=os.getenv("REAL_F3_WORKFLOW_ID"),
        help="Collect an already completed REAL-F3 workflow instead of creating a new one.",
    )
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    scenarios = [item.strip().upper() for item in args.scenarios.split(",") if item.strip()]
    unknown = sorted(set(scenarios) - {"REAL-F1", "REAL-F2", "REAL-F3"})
    if unknown:
        raise SystemExit(f"Unknown scenarios: {', '.join(unknown)}")

    suite_id = f"real-agents-{datetime.now().strftime('%Y%m%d-%H%M%S')}"
    suite_dir = args.output_root.resolve() / suite_id
    suite_dir.mkdir(parents=True, exist_ok=False)
    started_at = utc_now()
    client = PlatformClient(args.api_url)
    capacity = CapacityController(client)
    run_rows: list[dict[str, Any]] = []
    all_attempt_rows: list[dict[str, Any]] = []
    failures: list[dict[str, str]] = []

    runners = {
        "REAL-F1": lambda: run_real_f1(client, capacity, args.real_f1_workflow_id),
        "REAL-F2": lambda: run_real_f2(client, capacity, args.real_f2_workflow_id),
        "REAL-F3": lambda: run_real_f3(client, capacity, args.real_f3_workflow_id),
    }

    try:
        write_json(suite_dir / "platform-health.json", client.health())
        for scenario_id in scenarios:
            scenario_dir = suite_dir / scenario_id
            scenario_dir.mkdir(parents=True, exist_ok=True)
            scenario_started_at = utc_now()
            started = time.perf_counter()
            try:
                workflow, trace, source = runners[scenario_id]()
                runner_duration_ms = round((time.perf_counter() - started) * 1000)
                workflow_file = scenario_dir / "workflow.json"
                write_json(workflow_file, workflow)
                write_json(scenario_dir / "coordination-trace.json", trace)
                errors = scenario_errors(scenario_id, workflow)
                row = build_run_row(
                    scenario_id,
                    workflow,
                    runner_duration_ms,
                    workflow_file,
                    errors,
                )
                write_json(
                    scenario_dir / "run.json",
                    {
                        **row,
                        "source": source,
                        "startedAt": scenario_started_at,
                        "finishedAt": utc_now(),
                        "acceptanceErrors": errors,
                    },
                )
                run_rows.append(row)
                all_attempt_rows.extend(attempt_rows(scenario_id, workflow))
            except Exception as exc:
                runner_duration_ms = round((time.perf_counter() - started) * 1000)
                failure = {
                    "scenarioId": scenario_id,
                    "error": f"{type(exc).__name__}: {exc}",
                }
                failures.append(failure)
                write_json(
                    scenario_dir / "run.json",
                    {
                        "runId": str(uuid.uuid4()),
                        "scenarioId": scenario_id,
                        "passed": False,
                        "startedAt": scenario_started_at,
                        "finishedAt": utc_now(),
                        "runnerDurationMs": runner_duration_ms,
                        **failure,
                    },
                )
                run_rows.append(
                    {
                        "runId": str(uuid.uuid4()),
                        "scenarioId": scenario_id,
                        "passed": False,
                        "runnerDurationMs": runner_duration_ms,
                        "failureReason": failure["error"],
                    }
                )
    finally:
        try:
            capacity.restore()
        except Exception as exc:
            failures.append({"scenarioId": "CLEANUP", "error": f"{type(exc).__name__}: {exc}"})

    llm_mode = collect_llm_mode(args.api_url)
    for row in run_rows:
        row["llmMode"] = llm_mode["mode"]
    write_csv(suite_dir / "runs.csv", run_rows, RUN_FIELDS)
    write_csv(suite_dir / "attempts.csv", all_attempt_rows, ATTEMPT_FIELDS)
    write_json(suite_dir / "llm-mode.json", llm_mode)
    summary_rows = []
    for scenario_id in scenarios:
        items = [row for row in run_rows if row.get("scenarioId") == scenario_id]
        passed = sum(bool(row.get("passed")) for row in items)
        durations = [int(row["runnerDurationMs"]) for row in items if row.get("runnerDurationMs") is not None]
        summary_rows.append(
            {
                "scenarioId": scenario_id,
                "runs": len(items),
                "passed": passed,
                "failed": len(items) - passed,
                "passRate": passed / len(items) if items else 0,
                "averageRunnerDurationMs": round(sum(durations) / len(durations), 2) if durations else None,
            }
        )
    write_csv(
        suite_dir / "summary.csv",
        summary_rows,
        ["scenarioId", "runs", "passed", "failed", "passRate", "averageRunnerDurationMs"],
    )
    all_passed = bool(run_rows) and all(bool(row.get("passed")) for row in run_rows) and not failures
    summary = {
        "suiteId": suite_id,
        "startedAt": started_at,
        "finishedAt": utc_now(),
        "apiUrl": args.api_url,
        "llmMode": llm_mode,
        "totalRuns": len(run_rows),
        "passed": sum(bool(row.get("passed")) for row in run_rows),
        "failed": sum(not bool(row.get("passed")) for row in run_rows),
        "allPassed": all_passed,
        "failures": failures,
        "scenarios": summary_rows,
    }
    write_json(suite_dir / "summary.json", summary)
    (args.output_root.resolve() / "LATEST.txt").write_text(str(suite_dir), encoding="utf-8")
    print(json.dumps({**summary, "outputDirectory": str(suite_dir)}, ensure_ascii=False, indent=2))
    return 0 if all_passed else 1


if __name__ == "__main__":
    raise SystemExit(main())
