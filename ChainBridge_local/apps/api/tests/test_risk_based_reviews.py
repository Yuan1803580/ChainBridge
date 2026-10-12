from __future__ import annotations

from datetime import datetime, timedelta, timezone
import hashlib

from eth_account import Account
from eth_account.messages import encode_typed_data
from fastapi.testclient import TestClient

from .conftest import auth_headers
from app.security import derive_demo_private_key


PROOF = "0x" + "a" * 64
EVIDENCE_1 = "0x" + "b" * 64
EVIDENCE_2 = "0x" + "c" * 64
CHALLENGE_EVIDENCE = "0x" + "d" * 64


def _node(workflow: dict, key: str = "N1") -> dict:
    return next(item for item in workflow["nodes"] if item["nodeKey"] == key)


def _coordinate(client: TestClient, workflow_id: str, headers: dict[str, str], key: str) -> dict:
    response = client.post(
        f"/api/v1/workflows/{workflow_id}/coordinate",
        headers={**headers, "Idempotency-Key": key},
        json={"maxActions": 1},
    )
    assert response.status_code == 200, response.text
    return response.json()


def _create_risk_workflow(client: TestClient, risk_level: str) -> tuple[dict, dict[str, str]]:
    requester = auth_headers(client, "campus-requester", f"create-{risk_level.lower()}")
    node = {
        "nodeKey": "N1",
        "title": f"{risk_level.title()} risk research",
        "capability": "research",
        "reward": "4.20",
        "maxAttempts": 1,
        "riskLevel": risk_level,
        "reviewBudget": "0.30",
        "acceptanceCriteria": {"requiredFields": ["summary"], "minScore": 80},
    }
    if risk_level == "MEDIUM":
        node.update({"samplingRateBps": 10000, "requiredApprovals": 1})
    if risk_level == "HIGH":
        node.update({"requiredApprovals": 2, "challengePeriodSeconds": 60})
    response = client.post(
        "/api/v1/workflows",
        headers=requester,
        json={
            "title": f"{risk_level.title()} risk review workflow",
            "objective": "Exercise the immutable risk-based review policy before any settlement starts.",
            "budget": "6.00",
            "platformFee": "0.50",
            "nodes": [node],
        },
    )
    assert response.status_code == 201, response.text
    return response.json(), requester


def _dispatch_and_submit(client: TestClient, workflow: dict, requester: dict[str, str], key: str) -> dict:
    workflow_id = workflow["id"]
    dispatched = _coordinate(client, workflow_id, requester, f"dispatch-{key}")
    attempt = _node(dispatched["workflow"])["currentAttempt"]
    response = client.post(
        f"/api/v1/workflows/{workflow_id}/nodes/N1/attempts/{attempt['id']}/results",
        headers=auth_headers(client, "workflow-research-b", f"result-{key}"),
        json={"output": {"summary": "Auditable evidence", "score": 95}, "proofHash": PROOF},
    )
    assert response.status_code == 202, response.text
    return attempt


def _private_key(slug: str) -> bytes:
    value = derive_demo_private_key(slug).private_numbers().private_value
    return value.to_bytes(32, "big")


def _nonce(label: str) -> str:
    return "0x" + hashlib.sha256(label.encode()).hexdigest()


def _signed_review(
    client: TestClient,
    workflow_id: str,
    attempt_id: str,
    slug: str,
    *,
    decision: str,
    evidence_hash: str,
    notes: str = "",
    label: str,
):
    headers = auth_headers(client, slug, label)
    deadline = datetime.now(timezone.utc) + timedelta(minutes=5)
    nonce = _nonce(label)
    unsigned = {
        "decision": decision,
        "evidenceHash": evidence_hash,
        "notes": notes,
        "nonce": nonce,
        "deadline": deadline.isoformat(),
    }
    payload = client.post(
        f"/api/v1/workflows/{workflow_id}/nodes/N1/attempts/{attempt_id}/review-approval-payload",
        headers=headers,
        json=unsigned,
    )
    assert payload.status_code == 200, payload.text
    signature = "0x" + Account.sign_message(
        encode_typed_data(full_message=payload.json()["typedData"]),
        private_key=_private_key(slug),
    ).signature.hex().removeprefix("0x")
    return client.post(
        f"/api/v1/workflows/{workflow_id}/nodes/N1/attempts/{attempt_id}/review-approvals",
        headers=headers,
        json={
            **unsigned,
            "reviewerWallet": payload.json()["reviewerWallet"],
            "signature": signature,
            "signatureScheme": "EIP712",
        },
    )


def _signed_arbitration_vote(
    client: TestClient,
    workflow_id: str,
    attempt_id: str,
    challenge_id: str,
    slug: str,
    *,
    resolution: str,
    reason: str,
    label: str,
):
    headers = auth_headers(client, slug, label)
    deadline = datetime.now(timezone.utc) + timedelta(minutes=5)
    nonce = _nonce(label)
    unsigned = {
        "resolution": resolution,
        "reason": reason,
        "nonce": nonce,
        "deadline": deadline.isoformat(),
    }
    payload = client.post(
        f"/api/v1/workflows/{workflow_id}/nodes/N1/attempts/{attempt_id}/review-challenges/{challenge_id}/arbitration-vote-payload",
        headers=headers,
        json=unsigned,
    )
    assert payload.status_code == 200, payload.text
    signature = "0x" + Account.sign_message(
        encode_typed_data(full_message=payload.json()["typedData"]),
        private_key=_private_key(slug),
    ).signature.hex().removeprefix("0x")
    return client.post(
        f"/api/v1/workflows/{workflow_id}/nodes/N1/attempts/{attempt_id}/review-challenges/{challenge_id}/arbitration-votes",
        headers=headers,
        json={
            **unsigned,
            "voterWallet": payload.json()["voterWallet"],
            "signature": signature,
            "signatureScheme": "EIP712",
        },
    )


def test_coordinator_assigns_auditable_risk_from_node_difficulty(client: TestClient):
    response = client.post(
        "/api/v1/workflow-plans",
        headers=auth_headers(client, "campus-requester", "auto-risk-plan"),
        json={
            "objective": "Classify each planned node by difficulty before selecting its review policy.",
            "budget": "20.00",
            "platformFee": "1.10",
        },
    )
    assert response.status_code == 200, response.text
    nodes = {item["nodeKey"]: item for item in response.json()["nodes"]}
    assert nodes["N1"]["riskLevel"] == "LOW"
    assert nodes["N1"]["riskAssessment"]["assignmentMode"] == "AUTO"
    assert nodes["N2"]["riskLevel"] == "MEDIUM"
    assert nodes["N2"]["riskAssessment"]["assignmentMode"] == "AUTO"
    assert nodes["N2"]["riskAssessment"]["score"] >= 30
    assert nodes["N2"]["samplingRateBps"] == 0


def test_medium_risk_sample_waits_for_independent_reviewer_before_settlement(client: TestClient):
    workflow, requester = _create_risk_workflow(client, "MEDIUM")
    workflow_id = workflow["id"]
    attempt = _dispatch_and_submit(client, workflow, requester, "medium")

    routed = _coordinate(client, workflow_id, requester, "review-medium")
    node = _node(routed["workflow"])
    assert routed["actions"][0]["action"] == "independent_review_required"
    assert node["status"] == "REVIEW_PENDING"
    assert node["riskLevel"] == "MEDIUM"
    assert node["riskAssignmentMode"] == "MANUAL"
    assert node["reviewPolicyHash"].startswith("0x")
    assert node["currentAttempt"]["reviewStatus"] == "INDEPENDENT_REVIEW_PENDING"
    assert node["currentAttempt"]["reviewSampled"] is True

    assigned_agent_cannot_review = client.post(
        f"/api/v1/workflows/{workflow_id}/nodes/N1/attempts/{attempt['id']}/review-approvals",
        headers=auth_headers(client, "workflow-research-b", "self-review-medium"),
        json={"decision": "PASS", "evidenceHash": EVIDENCE_1},
    )
    assert assigned_agent_cannot_review.status_code == 403

    approved = _signed_review(
        client,
        workflow_id,
        attempt["id"],
        "code-review",
        decision="PASS",
        evidence_hash=EVIDENCE_1,
        notes="Independent sample passed",
        label="approve-medium",
    )
    assert approved.status_code == 200, approved.text
    approved_node = _node(approved.json()["workflow"])
    assert approved.json()["action"] == "review_passed"
    assert approved_node["status"] == "SUCCEEDED"
    assert approved_node["currentAttempt"]["reviewStatus"] == "PASSED"
    assert len(approved_node["currentAttempt"]["reviewApprovals"]) == 1

    settled = _coordinate(client, workflow_id, requester, "settle-medium")
    assert _node(settled["workflow"])["status"] == "SETTLED"


def test_high_risk_requires_two_approvals_and_blocks_payment_during_dispute(client: TestClient):
    workflow, requester = _create_risk_workflow(client, "HIGH")
    workflow_id = workflow["id"]
    attempt = _dispatch_and_submit(client, workflow, requester, "high")

    routed = _coordinate(client, workflow_id, requester, "review-high")
    assert _node(routed["workflow"])["currentAttempt"]["reviewStatus"] == "APPROVALS_PENDING"

    first = _signed_review(
        client,
        workflow_id,
        attempt["id"],
        "code-review",
        decision="PASS",
        evidence_hash=EVIDENCE_1,
        label="approve-high-1",
    )
    assert first.status_code == 200, first.text
    assert first.json()["action"] == "review_approval_recorded"
    assert first.json()["approvalCount"] == 1

    second = _signed_review(
        client,
        workflow_id,
        attempt["id"],
        "platform-admin",
        decision="PASS",
        evidence_hash=EVIDENCE_2,
        label="approve-high-2",
    )
    assert second.status_code == 200, second.text
    assert second.json()["action"] == "challenge_period_opened"
    second_node = _node(second.json()["workflow"])
    assert second_node["status"] == "CHALLENGE_PERIOD"
    assert second_node["currentAttempt"]["reviewStatus"] == "CHALLENGE_PERIOD"
    assert second_node["currentAttempt"]["settlementStatus"] == "NONE"

    challenged = client.post(
        f"/api/v1/workflows/{workflow_id}/nodes/N1/attempts/{attempt['id']}/review-challenges",
        headers=requester,
        json={"reason": "The evidence omits a material source", "evidenceHash": CHALLENGE_EVIDENCE},
    )
    assert challenged.status_code == 200, challenged.text
    challenge_id = challenged.json()["challengeId"]
    disputed_node = _node(challenged.json()["workflow"])
    assert disputed_node["status"] == "DISPUTED"
    assert disputed_node["currentAttempt"]["settlementStatus"] == "NONE"

    blocked = _coordinate(client, workflow_id, requester, "settlement-blocked-by-dispute")
    assert blocked["actions"][0]["action"] == "idle"
    assert _node(blocked["workflow"])["status"] == "DISPUTED"

    direct_admin_resolution = client.post(
        f"/api/v1/workflows/{workflow_id}/nodes/N1/attempts/{attempt['id']}/review-challenges/{challenge_id}/resolve",
        headers=auth_headers(client, "platform-admin", "resolve-high"),
        json={"resolution": "REJECT_RESULT", "reason": "A single admin must not decide this dispute"},
    )
    assert direct_admin_resolution.status_code == 409

    first_vote = _signed_arbitration_vote(
        client,
        workflow_id,
        attempt["id"],
        challenge_id,
        "arbitrator-one",
        resolution="REJECT_RESULT",
        reason="The source is materially incomplete",
        label="arbitration-high-1",
    )
    assert first_vote.status_code == 200, first_vote.text
    assert first_vote.json()["action"] == "arbitration_vote_recorded"
    assert _node(first_vote.json()["workflow"])["status"] == "DISPUTED"

    resolved = _signed_arbitration_vote(
        client,
        workflow_id,
        attempt["id"],
        challenge_id,
        "arbitrator-two",
        resolution="REJECT_RESULT",
        reason="Independent reproduction confirms the missing source",
        label="arbitration-high-2",
    )
    assert resolved.status_code == 200, resolved.text
    final = resolved.json()["workflow"]
    assert final["status"] == "FAILED"
    assert _node(final)["status"] == "FAILED"
    assert _node(final)["reservations"][0]["status"] == "RELEASED"
    assert final["paidAmount"] == "0"
