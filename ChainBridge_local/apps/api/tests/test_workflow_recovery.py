from __future__ import annotations

import threading
from types import SimpleNamespace
from unittest.mock import Mock

import pytest
from fastapi import HTTPException
from fastapi.testclient import TestClient
from sqlalchemy import func, select

from app import workflow_api
from app.models import Agent, IdempotencyRecord
from app.coordinator import _excluded_attempted_agent_keys
from app.services import save_idempotent_response
from app.workflow_api import (
    _agent_invocation_timeout_seconds,
    _agent_task_deadline_seconds,
    _workflow_agent_input,
)
from app.workflow_recovery import workflow_coordination_guard, workflow_coordination_lock

from .conftest import auth_headers


def _node(workflow: dict, node_key: str) -> dict:
    return next(node for node in workflow["nodes"] if node["nodeKey"] == node_key)


def test_any_platform_failure_allows_same_agent_recovery_attempt():
    attempt = SimpleNamespace(
        agent_key="Agent-D1",
        status="FAILED",
        failure_reason_code="WORKER_FAILURE",
        failure_responsible_party="PLATFORM",
    )
    session = SimpleNamespace(scalars=lambda _query: [attempt])

    excluded = _excluded_attempted_agent_keys(
        session,
        SimpleNamespace(id="node-platform-retry"),
    )

    assert excluded == set()


def test_overlapping_workflow_coordination_is_rejected_without_waiting():
    workflow_id = "workflow-lock-test"
    acquired = threading.Event()
    release = threading.Event()

    def hold_lock() -> None:
        with workflow_coordination_lock(workflow_id):
            acquired.set()
            release.wait(timeout=2)

    thread = threading.Thread(target=hold_lock)
    thread.start()
    assert acquired.wait(timeout=1)
    try:
        guard = workflow_coordination_guard(workflow_id)
        with pytest.raises(HTTPException) as exc_info:
            next(guard)
    finally:
        release.set()
        thread.join(timeout=1)

    assert exc_info.value.status_code == 409


def test_workflow_coordination_guard_can_exit_on_another_worker_thread():
    workflow_id = "workflow-cross-thread-lock-test"
    guard = workflow_coordination_guard(workflow_id)
    entered = threading.Event()
    exited = threading.Event()
    errors: list[BaseException] = []

    def enter_guard() -> None:
        try:
            next(guard)
            entered.set()
        except BaseException as exc:  # pragma: no cover - asserted below
            errors.append(exc)

    def exit_guard() -> None:
        try:
            guard.close()
            exited.set()
        except BaseException as exc:  # pragma: no cover - asserted below
            errors.append(exc)

    enter_thread = threading.Thread(target=enter_guard)
    enter_thread.start()
    enter_thread.join(timeout=1)
    assert entered.is_set()

    exit_thread = threading.Thread(target=exit_guard)
    exit_thread.start()
    exit_thread.join(timeout=1)

    assert exited.is_set()
    assert errors == []

    with workflow_coordination_lock(workflow_id, blocking=False) as acquired:
        assert acquired is True


def test_coordinate_idempotency_records_are_bounded(client: TestClient, monkeypatch):
    monkeypatch.setenv("WORKFLOW_COORDINATE_IDEMPOTENCY_LIMIT", "2")
    route = "dynamic_workflows.retention-test.coordinate"

    with client.app.state.session_factory() as session:
        actor = session.scalar(select(Agent).where(Agent.did == "did:local:campus-requester"))
        assert actor is not None
        for index in range(3):
            save_idempotent_response(
                session,
                actor.id,
                route,
                f"retention-{index}",
                {"maxActions": 1},
                {"actions": [], "workflow": {"id": "retention-test", "version": index}},
            )
            session.commit()

        count = session.scalar(
            select(func.count())
            .select_from(IdempotencyRecord)
            .where(IdempotencyRecord.agent_id == actor.id, IdempotencyRecord.route == route)
        )

    assert count == 2


def test_workflow_list_summary_omits_detail_only_evidence(client: TestClient):
    requester = auth_headers(client, "campus-requester", "summary-list-create")
    created = client.post(
        "/api/v1/workflows",
        headers=requester,
        json={
            "title": "Summary list workflow",
            "objective": "Return a lightweight workflow list item.",
            "budget": "5.00",
            "platformFee": "0.50",
            "nodes": [{
                "nodeKey": "N1",
                "title": "Research",
                "capability": "research",
                "reward": "4.00",
            }],
        },
    )
    assert created.status_code == 201, created.text

    response = client.get("/api/v1/workflows?view=summary&limit=100")

    assert response.status_code == 200, response.text
    item = next(value for value in response.json()["items"] if value["id"] == created.json()["id"])
    assert item["events"] == []
    assert item["ledger"] == []
    assert item["protocolJobs"] == []
    assert item["nodes"][0]["attempts"] == []
    assert item["nodeCount"] == 1


def test_agent_timeout_and_offer_deadline_are_configurable(monkeypatch):
    monkeypatch.setenv("AGENT_INVOCATION_TIMEOUT_SECONDS", "2100")
    monkeypatch.setenv("AGENT_TASK_DEADLINE_SECONDS", "2400")

    assert _agent_invocation_timeout_seconds() == 2100
    assert _agent_task_deadline_seconds() == 2400

    monkeypatch.setenv("AGENT_TASK_DEADLINE_SECONDS", "1800")
    assert _agent_task_deadline_seconds() == 2160


def test_agent_input_contains_the_node_contract_and_dependencies():
    dependency = SimpleNamespace(output_payload={"summary": "prior result"})
    session = Mock()
    session.scalar.return_value = dependency
    workflow = SimpleNamespace(
        id="workflow-test",
        objective="Complete the whole workflow",
        input_payload={"language": "zh-CN"},
    )
    node = SimpleNamespace(
        node_key="N3",
        title="Verify claims",
        description="Verify three material claims against primary sources.",
        capability="verification",
        input_payload={"verificationFocus": ["evidence quality"]},
        acceptance_criteria={"requiredFields": ["summary", "sources"]},
        reward="4.70",
        risk_level="HIGH",
        depends_on=["N2"],
    )

    payload = _workflow_agent_input(session, workflow, node)

    assert payload["objective"] == node.description
    assert payload["dependencies"] == {"N2": dependency.output_payload}
    assert payload["input"]["language"] == "zh-CN"
    assert payload["input"]["_node"] == {
        "nodeKey": "N3",
        "title": "Verify claims",
        "description": node.description,
        "capability": "verification",
        "input": node.input_payload,
        "acceptanceCriteria": node.acceptance_criteria,
        "reward": "4.70",
        "riskLevel": "HIGH",
    }


def test_recovery_scheduler_switches_agent_and_creates_only_one_new_attempt(
    client: TestClient,
    monkeypatch,
):
    requester = auth_headers(client, "campus-requester", "recovery-create")
    created = client.post(
        "/api/v1/workflows",
        headers=requester,
        json={
            "title": "Verification recovery",
            "objective": "Verify one result and recover by switching Agents after a timeout.",
            "budget": "10.00",
            "platformFee": "1.00",
            "nodes": [{
                "nodeKey": "N1",
                "title": "Verify result",
                "capability": "verification",
                "reward": "7.00",
                "maxAttempts": 3,
                "acceptanceCriteria": {"requiredFields": ["summary"], "minScore": 80},
            }],
        },
    )
    assert created.status_code == 201, created.text
    workflow_id = created.json()["id"]

    first = client.post(
        f"/api/v1/workflows/{workflow_id}/coordinate",
        headers={**requester, "Idempotency-Key": "recovery-dispatch-1"},
        json={"maxActions": 1},
    )
    assert first.status_code == 200, first.text
    first_attempt = _node(first.json()["workflow"], "N1")["currentAttempt"]
    assert first_attempt["agentKey"] == "Agent-C1"

    failed = client.post(
        f"/api/v1/workflows/{workflow_id}/nodes/N1/attempts/{first_attempt['id']}/failures",
        headers=auth_headers(client, "platform-admin", "recovery-timeout"),
        json={"reasonCode": "AGENT_TIMEOUT", "detail": "Runner request timed out"},
    )
    assert failed.status_code == 200, failed.text
    failed_node = _node(failed.json()["workflow"], "N1")
    assert failed_node["status"] == "RETRYING"
    assert failed_node["waitingDiagnostics"]["matchableAgentCount"] == 1
    assert failed_node["waitingDiagnostics"]["remainingAttempts"] == 2

    scheduler = client.app.state.workflow_recovery
    first_scan = scheduler.run_once()
    assert any(
        item["workflowId"] == workflow_id and item["action"] == "node_dispatched"
        for item in first_scan
    )
    recovered = client.get(f"/api/v1/workflows/{workflow_id}").json()
    recovered_node = _node(recovered, "N1")
    assert len(recovered_node["attempts"]) == 2
    assert recovered_node["currentAttempt"]["agentKey"] == "Agent-D1"
    assert recovered_node["currentAttempt"]["id"] != first_attempt["id"]

    scheduler.run_once()
    unchanged = client.get(f"/api/v1/workflows/{workflow_id}").json()
    assert len(_node(unchanged, "N1")["attempts"]) == 2

    invoked_attempts: list[str] = []
    monkeypatch.setenv("AUTO_INVOKE_AGENTS", "true")

    def invoke_existing(_session, _workflow, _node_value, attempt):
        invoked_attempts.append(attempt.id)
        return {
            "action": "agent_result_submitted",
            "agentKey": attempt.agent_key,
            "attemptId": attempt.id,
            "resultHash": "0x" + "12" * 32,
        }

    monkeypatch.setattr(workflow_api, "_invoke_reserved_attempt", invoke_existing)
    resumed = client.post(
        f"/api/v1/workflows/{workflow_id}/coordinate",
        headers={**requester, "Idempotency-Key": "recovery-invoke-existing"},
        json={"maxActions": 1},
    )

    assert resumed.status_code == 200, resumed.text
    expected_attempt_id = _node(unchanged, "N1")["currentAttempt"]["id"]
    assert invoked_attempts == [expected_attempt_id]
    assert resumed.json()["actions"][-1]["action"] == "agent_result_submitted"


def test_timeout_fallback_retries_the_latest_recovered_agent(
    client: TestClient,
    monkeypatch,
):
    monkeypatch.setenv("AGENT_TIMEOUT_MAX_ATTEMPTS_PER_AGENT", "2")
    requester = auth_headers(client, "campus-requester", "fallback-create")
    created = client.post(
        "/api/v1/workflows",
        headers=requester,
        json={
            "title": "Transient timeout fallback",
            "objective": "Verify one result despite transient Runner restarts.",
            "budget": "10.00",
            "platformFee": "1.00",
            "nodes": [{
                "nodeKey": "N1",
                "title": "Verify result",
                "capability": "verification",
                "reward": "7.00",
                "maxAttempts": 3,
                "acceptanceCriteria": {"requiredFields": ["summary"]},
            }],
        },
    )
    workflow_id = created.json()["id"]

    first = client.post(
        f"/api/v1/workflows/{workflow_id}/coordinate",
        headers={**requester, "Idempotency-Key": "fallback-dispatch-1"},
        json={"maxActions": 1},
    ).json()
    first_attempt = _node(first["workflow"], "N1")["currentAttempt"]
    assert first_attempt["agentKey"] == "Agent-C1"
    client.post(
        f"/api/v1/workflows/{workflow_id}/nodes/N1/attempts/{first_attempt['id']}/failures",
        headers=auth_headers(client, "platform-admin", "fallback-fail-1"),
        json={"reasonCode": "AGENT_TIMEOUT", "detail": "first Runner timed out"},
    )

    scheduler = client.app.state.workflow_recovery
    scheduler.run_once()
    second = client.get(f"/api/v1/workflows/{workflow_id}").json()
    second_attempt = _node(second, "N1")["currentAttempt"]
    assert second_attempt["agentKey"] == "Agent-D1"
    client.post(
        f"/api/v1/workflows/{workflow_id}/nodes/N1/attempts/{second_attempt['id']}/failures",
        headers=auth_headers(client, "platform-admin", "fallback-fail-2"),
        json={"reasonCode": "AGENT_TIMEOUT", "detail": "replacement Runner restarted"},
    )

    scheduler.run_once()
    recovered = client.get(f"/api/v1/workflows/{workflow_id}").json()
    node = _node(recovered, "N1")

    assert len(node["attempts"]) == 3
    assert node["currentAttempt"]["agentKey"] == "Agent-D1"
    assert node["currentAttempt"]["attemptNumber"] == 3


def test_manual_resume_returns_waiting_diagnostics(client: TestClient):
    requester = auth_headers(client, "campus-requester", "manual-resume-create")
    created = client.post(
        "/api/v1/workflows",
        headers=requester,
        json={
            "title": "Unavailable capability",
            "objective": "Wait until an Agent with a new capability is registered.",
            "budget": "5.00",
            "platformFee": "0.50",
            "nodes": [{
                "nodeKey": "N1",
                "title": "Specialized task",
                "capability": "not_registered_capability",
                "reward": "4.00",
                "maxAttempts": 3,
                "acceptanceCriteria": {"requiredFields": ["summary"]},
            }],
        },
    )
    workflow_id = created.json()["id"]
    resumed = client.post(
        f"/api/v1/workflows/{workflow_id}/resume",
        headers={**requester, "Idempotency-Key": "manual-resume"},
        json={"maxActions": 4},
    )
    assert resumed.status_code == 200, resumed.text
    node = _node(resumed.json()["workflow"], "N1")
    assert resumed.json()["actions"][-1]["action"] == "waiting_for_agent"
    assert node["waitingDiagnostics"]["matchableAgentCount"] == 0
    assert node["waitingDiagnostics"]["waitingReason"] == "NO_AGENT_WITH_REQUIRED_CAPABILITY"
