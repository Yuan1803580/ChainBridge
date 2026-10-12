from __future__ import annotations

from fastapi.testclient import TestClient

from .conftest import auth_headers


PROOF_B = "0x" + "b" * 64
PROOF_C = "0x" + "c" * 64
PROOF_D = "0x" + "d" * 64


def _create(client: TestClient) -> tuple[dict, dict[str, str]]:
    requester = auth_headers(client, "campus-requester", "wf-create")
    response = client.post(
        "/api/v1/workflows",
        headers=requester,
        json={
            "title": "Local V1 cross-chain evidence workflow",
            "objective": "Research the supplied evidence, verify it, and publish an auditable report.",
            "budget": "20.00",
            "platformFee": "1.10",
            "inputPayload": {"topic": "dynamic multi-chain agents"},
        },
    )
    assert response.status_code == 201, response.text
    return response.json(), requester


def _coordinate(client: TestClient, workflow_id: str, headers: dict[str, str], key: str) -> dict:
    request_headers = {**headers, "Idempotency-Key": key}
    response = client.post(
        f"/api/v1/workflows/{workflow_id}/coordinate",
        headers=request_headers,
        json={"maxActions": 1},
    )
    assert response.status_code == 200, response.text
    return response.json()


def _node(workflow: dict, key: str) -> dict:
    return next(item for item in workflow["nodes"] if item["nodeKey"] == key)


def test_local_v1_success_failure_retry_stale_rejection_and_balanced_ledger(client: TestClient):
    workflow, requester = _create(client)
    workflow_id = workflow["id"]
    assert workflow["referenceCode"] == "WF-1001"
    assert workflow["budget"] == "20"
    assert _node(workflow, "N1")["status"] == "READY"
    assert _node(workflow, "N2")["status"] == "BLOCKED"

    dispatched_b = _coordinate(client, workflow_id, requester, "coord-b-01")
    n1 = _node(dispatched_b["workflow"], "N1")
    attempt_b = n1["currentAttempt"]
    assert attempt_b["agentKey"] == "Agent-B1"
    assert attempt_b["chainId"] == 31338
    assert attempt_b["routeVersion"] == 1
    assert attempt_b["settlementAsset"] == "USDC"
    assert attempt_b["payoutAsset"] == "USDC"
    assert attempt_b["payoutRate"] == "1"
    assert attempt_b["payoutStatus"] == "NOT_REQUIRED"

    result_b = client.post(
        f"/api/v1/workflows/{workflow_id}/nodes/N1/attempts/{attempt_b['id']}/results",
        headers=auth_headers(client, "workflow-research-b", "result-b-01"),
        json={
            "output": {"summary": "Structured evidence", "score": 92},
            "proofHash": PROOF_B,
            "gasCost": "0.00011",
            "transactionHash": "0x" + "1" * 64,
        },
    )
    assert result_b.status_code == 202, result_b.text
    assert result_b.json()["accepted"] is True

    reviewed_b = _coordinate(client, workflow_id, requester, "review-b-01")
    assert _node(reviewed_b["workflow"], "N1")["status"] == "SUCCEEDED"
    reviewed_attempt = _node(reviewed_b["workflow"], "N1")["currentAttempt"]
    assert reviewed_attempt["qualityStatus"] == "PROVISIONAL"
    assert reviewed_attempt["qualityPolicyVersion"] == "NODE_RESULT_QUALITY_V1"
    assert reviewed_attempt["reportedScore"] == "92"
    assert reviewed_attempt["qualityBreakdown"]["reportedScore"]["includedInQuality"] is False
    # A business result is not enough to unlock a dependent node.
    assert _node(reviewed_b["workflow"], "N2")["status"] == "BLOCKED"

    settled_b = _coordinate(client, workflow_id, requester, "settle-b-01")
    assert _node(settled_b["workflow"], "N1")["status"] == "SETTLED"
    settled_attempt = _node(settled_b["workflow"], "N1")["currentAttempt"]
    assert settled_attempt["qualityStatus"] == "FINAL"
    assert settled_attempt["qualityEvidenceHash"].startswith("0x")
    assert settled_b["workflow"]["qualitySummary"]["finalScoredAttempts"] == 1
    assert _node(settled_b["workflow"], "N2")["status"] == "READY"
    profiles_after_b = client.get("/api/v1/workflow-agents").json()["items"]
    reputation_b = next(item for item in profiles_after_b if item["agentKey"] == "Agent-B1")
    assert reputation_b["trustScore"] == 93
    assert reputation_b["successRateBps"] == 8000
    assert reputation_b["metadata"]["reputation"]["successfulAttempts"] == 1

    dispatched_c = _coordinate(client, workflow_id, requester, "coord-c-01")
    attempt_c = _node(dispatched_c["workflow"], "N2")["currentAttempt"]
    assert attempt_c["agentKey"] == "Agent-C1"
    assert attempt_c["chainId"] == 31339
    n2_match_round = _node(dispatched_c["workflow"], "N2")["matchRounds"][0]
    assert [candidate["agentKey"] for candidate in n2_match_round["candidates"]] == ["Agent-C1", "Agent-D1"]
    assert [candidate["rank"] for candidate in n2_match_round["candidates"]] == [1, 2]

    result_c = client.post(
        f"/api/v1/workflows/{workflow_id}/nodes/N2/attempts/{attempt_c['id']}/results",
        headers=auth_headers(client, "workflow-verification-c", "result-c-01"),
        json={
            "output": {"verified": False, "report": "Proof is well-formed but the claim is false"},
            "proofHash": PROOF_C,
            "gasCost": "0.00012",
            "transactionHash": "0x" + "2" * 64,
        },
    )
    assert result_c.status_code == 202, result_c.text

    reviewed_c = _coordinate(client, workflow_id, requester, "review-c-01")
    n2_after_c = _node(reviewed_c["workflow"], "N2")
    assert n2_after_c["status"] == "RETRYING"
    assert n2_after_c["reservations"][0]["status"] == "RELEASED"
    profiles_after_c = client.get("/api/v1/workflow-agents").json()["items"]
    reputation_c = next(item for item in profiles_after_c if item["agentKey"] == "Agent-C1")
    assert reputation_c["trustScore"] == 83
    assert reputation_c["successRateBps"] == 6000
    assert reputation_c["metadata"]["reputation"]["failureReasonCounts"] == [
        {"reasonCode": "AGENT_RESULT_REJECTED", "count": 1},
    ]

    dispatched_d = _coordinate(client, workflow_id, requester, "coord-d-01")
    attempt_d = _node(dispatched_d["workflow"], "N2")["currentAttempt"]
    assert attempt_d["id"] != attempt_c["id"]
    assert attempt_d["attemptNumber"] == 2
    assert attempt_d["agentKey"] == "Agent-D1"
    assert attempt_d["chainId"] == 31340
    candidate_status = {
        candidate["agentKey"]: candidate["status"]
        for candidate in _node(dispatched_d["workflow"], "N2")["matchRounds"][0]["candidates"]
    }
    assert candidate_status == {"Agent-C1": "FAILED", "Agent-D1": "SELECTED"}

    late_c = client.post(
        f"/api/v1/workflows/{workflow_id}/nodes/N2/attempts/{attempt_c['id']}/results",
        headers=auth_headers(client, "workflow-verification-c", "late-c-01"),
        json={"output": {"verified": True, "report": "late replay"}, "proofHash": PROOF_C},
    )
    assert late_c.status_code == 202, late_c.text
    assert late_c.json() == {"accepted": False, "code": "STALE_ATTEMPT", "attemptId": attempt_c["id"]}

    result_d = client.post(
        f"/api/v1/workflows/{workflow_id}/nodes/N2/attempts/{attempt_d['id']}/results",
        headers=auth_headers(client, "workflow-verification-d", "result-d-01"),
        json={
            "output": {"verified": True, "report": "Audited final report"},
            "proofHash": PROOF_D,
            "gasCost": "0.00013",
            "transactionHash": "0x" + "3" * 64,
        },
    )
    assert result_d.status_code == 202, result_d.text
    _coordinate(client, workflow_id, requester, "review-d-01")
    _coordinate(client, workflow_id, requester, "settle-d-01")
    completed = _coordinate(client, workflow_id, requester, "complete-01")
    final = completed["workflow"]

    assert final["status"] == "COMPLETED"
    assert final["paidAmount"] == "11"
    assert final["platformFee"] == "1.1"
    assert final["refundedAmount"] == "7.9"
    assert final["ledgerSummary"]["balanced"] is True
    assert {entry["assetKind"] for entry in final["ledger"]} == {"TOKEN", "GAS"}
    assert any(event["eventType"] == "STALE_ATTEMPT_REJECTED" for event in final["events"])
    assert any(event["eventType"] == "REVIEW_FAILED" for event in final["events"])

    token_entries = [entry for entry in final["ledger"] if entry["assetKind"] == "TOKEN"]
    assert sum(float(entry["amount"]) for entry in token_entries if entry["entryType"] == "PAYOUT") == 11.0
    assert next(entry for entry in token_entries if entry["entryType"] == "REFUND")["amount"] == "7.9"


def test_routes_use_only_the_approved_local_chain_ids(client: TestClient):
    response = client.get("/api/v1/chain-routes")
    assert response.status_code == 200
    routes = response.json()["items"]
    assert {route["sourceChainId"] for route in routes} == {31337}
    assert {route["destinationChainId"] for route in routes} == {31338, 31339, 31340}


def test_custom_workflow_rejects_dependency_cycles(client: TestClient):
    headers = auth_headers(client, "campus-requester", "cycle-01")
    response = client.post(
        "/api/v1/workflows",
        headers=headers,
        json={
            "title": "Cyclic workflow should fail",
            "objective": "Verify that the coordinator rejects cyclic dependency definitions.",
            "budget": "3.00",
            "platformFee": "0.20",
            "nodes": [
                {
                    "nodeKey": "N1",
                    "title": "First",
                    "capability": "research",
                    "dependsOn": ["N2"],
                    "reward": "1.00",
                },
                {
                    "nodeKey": "N2",
                    "title": "Second",
                    "capability": "verification",
                    "dependsOn": ["N1"],
                    "reward": "1.00",
                },
            ],
        },
    )
    assert response.status_code == 422
    assert "cycle" in response.json()["error"]["message"].lower()


def test_core_validates_and_enriches_llm_dag_proposal(client: TestClient):
    headers = auth_headers(client, "campus-requester", "llm-dag-plan")
    response = client.post(
        "/api/v1/workflow-plans",
        headers=headers,
        json={
            "objective": "Research evidence and independently verify the final result.",
            "budget": "20.00",
            "platformFee": "1.10",
            "proposalVersion": "LLM_DAG_PROPOSAL_V1",
            "proposedNodes": [
                {
                    "nodeKey": "N1",
                    "title": "Collect evidence",
                    "description": "Structure source evidence.",
                    "capability": "research",
                    "acceptanceCriteria": {"requiredFields": ["summary"]},
                    "suggestedReward": "4.20",
                    "maxAttempts": 2,
                    "rationale": "Evidence must exist before verification.",
                },
                {
                    "nodeKey": "N2",
                    "title": "Verify evidence",
                    "description": "Verify the structured evidence.",
                    "capability": "verification",
                    "dependsOn": ["N1"],
                    "acceptanceCriteria": {"requiredFields": ["verified"]},
                    "suggestedReward": "6.80",
                    "rationale": "Separate verification reduces unchecked settlement risk.",
                },
            ],
        },
    )
    assert response.status_code == 200, response.text
    plan = response.json()
    assert plan["plannerVersion"] == "LLM_DAG_PROPOSAL_V1+CORE_VALIDATOR_V1"
    assert plan["validation"]["validatorVersion"] == "CORE_VALIDATOR_V1"
    assert [node["nodeKey"] for node in plan["nodes"]] == ["N1", "N2"]
    assert plan["nodes"][0]["riskAssessment"]["assignmentMode"] == "AUTO"
    assert "marketQuote" in plan["nodes"][0]
    assert "candidates" in plan["nodes"][0]


def test_core_rejects_cyclic_llm_dag_proposal(client: TestClient):
    response = client.post(
        "/api/v1/workflow-plans",
        headers=auth_headers(client, "campus-requester", "llm-cycle-plan"),
        json={
            "objective": "Reject a cyclic DAG before any workflow can be created.",
            "budget": "20.00",
            "proposedNodes": [
                {"nodeKey": "N1", "title": "First", "capability": "research", "dependsOn": ["N2"], "suggestedReward": "1.00"},
                {"nodeKey": "N2", "title": "Second", "capability": "verification", "dependsOn": ["N1"], "suggestedReward": "1.00"},
            ],
        },
    )
    assert response.status_code == 422
    assert "cycle" in response.json()["error"]["message"].lower()


def test_core_rejects_llm_dag_budget_overflow(client: TestClient):
    response = client.post(
        "/api/v1/workflow-plans",
        headers=auth_headers(client, "campus-requester", "llm-budget-plan"),
        json={
            "objective": "Reject a proposed DAG whose node rewards exceed the budget.",
            "budget": "5.00",
            "platformFee": "1.10",
            "proposedNodes": [
                {"nodeKey": "N1", "title": "First", "capability": "research", "suggestedReward": "4.00"},
                {"nodeKey": "N2", "title": "Second", "capability": "verification", "suggestedReward": "2.00"},
            ],
        },
    )
    assert response.status_code == 422
    assert "exceed" in response.json()["error"]["message"].lower()


def test_core_does_not_accept_security_fields_from_llm_proposal(client: TestClient):
    response = client.post(
        "/api/v1/workflow-plans",
        headers=auth_headers(client, "campus-requester", "llm-security-fields"),
        json={
            "objective": "Reject an LLM proposal that tries to choose its own settlement authority.",
            "budget": "20.00",
            "proposedNodes": [
                {
                    "nodeKey": "N1",
                    "title": "Research",
                    "capability": "research",
                    "suggestedReward": "4.20",
                    "riskLevel": "LOW",
                    "chainId": 31340,
                    "agentId": "attacker-agent",
                    "homeWallet": "0x" + "f" * 40,
                }
            ],
        },
    )
    assert response.status_code == 422


def test_failed_primary_waits_for_directory_agent_and_recovers_with_new_ranked_round(client: TestClient):
    workflow, requester = _create(client)
    workflow_id = workflow["id"]

    dispatched = _coordinate(client, workflow_id, requester, "research-primary-dispatch")
    n1 = _node(dispatched["workflow"], "N1")
    attempt_b = n1["currentAttempt"]
    assert [item["agentKey"] for item in n1["matchRounds"][0]["candidates"]] == ["Agent-B1"]
    assert n1["matchRounds"][0]["candidates"][0]["status"] == "SELECTED"

    rejected_result = client.post(
        f"/api/v1/workflows/{workflow_id}/nodes/N1/attempts/{attempt_b['id']}/results",
        headers=auth_headers(client, "workflow-research-b", "research-primary-result"),
        json={
            "output": {"score": 10},
            "proofHash": PROOF_B,
        },
    )
    assert rejected_result.status_code == 202, rejected_result.text
    reviewed = _coordinate(client, workflow_id, requester, "research-primary-review")
    reviewed_n1 = _node(reviewed["workflow"], "N1")
    assert reviewed_n1["status"] == "RETRYING"
    assert reviewed_n1["failureIncidents"][0]["reasonCode"] == "AGENT_RESULT_REJECTED"
    assert reviewed_n1["failureIncidents"][0]["responsibleParty"] == "AGENT"

    waiting = _coordinate(client, workflow_id, requester, "research-waiting")
    waiting_n1 = _node(waiting["workflow"], "N1")
    assert waiting["actions"][0]["action"] == "waiting_for_agent"
    assert waiting_n1["status"] == "WAITING_FOR_AGENT"
    assert waiting["workflow"]["status"] == "RUNNING"
    assert waiting_n1["reservations"][0]["status"] == "RELEASED"

    agents = client.get("/api/v1/agents?limit=100").json()["items"]
    fallback_agent = next(item for item in agents if item["slug"] == "code-review")
    registered = client.post(
        "/api/v1/workflow-agents",
        headers=auth_headers(client, "platform-admin", "register-research-fallback"),
        json={
            "agentId": fallback_agent["id"],
            "agentKey": "Agent-D2",
            "chainId": 31340,
            "capabilities": ["research", "extraction"],
            "priority": 15,
            "priceFloor": "4.20",
            "successRateBps": 9300,
            "trustScore": 89,
            "endpointLatencyMs": 220,
            "availability": "ONLINE",
            "capacityAvailable": True,
            "gasReady": True,
            "metadata": {"localV1": True},
            "credentialStatus": "ACTIVE",
            "credentialRef": "internal-test-agent-d2",
            "credentialFingerprint": "sha256:test-agent-d2",
        },
    )
    assert registered.status_code == 201, registered.text

    recovered = _coordinate(client, workflow_id, requester, "research-fallback-dispatch")
    recovered_n1 = _node(recovered["workflow"], "N1")
    attempt_d2 = recovered_n1["currentAttempt"]
    assert recovered["actions"][0]["action"] == "node_dispatched"
    assert recovered_n1["status"] == "ASSIGNED"
    assert attempt_d2["attemptNumber"] == 2
    assert attempt_d2["agentKey"] == "Agent-D2"
    assert attempt_d2["chainId"] == 31340
    assert attempt_d2["id"] != attempt_b["id"]
    assert len(recovered_n1["matchRounds"]) == 2
    assert recovered_n1["matchRounds"][1]["candidates"][0]["status"] == "SELECTED"


def test_timeout_allows_one_bounded_retry_with_the_same_agent_when_enabled(client: TestClient, monkeypatch):
    monkeypatch.setenv("AGENT_TIMEOUT_MAX_ATTEMPTS_PER_AGENT", "2")
    workflow, requester = _create(client)
    workflow_id = workflow["id"]

    dispatched = _coordinate(client, workflow_id, requester, "timeout-dispatch-1")
    first_attempt = _node(dispatched["workflow"], "N1")["currentAttempt"]
    assert first_attempt["agentKey"] == "Agent-B1"

    failed = client.post(
        f"/api/v1/workflows/{workflow_id}/nodes/N1/attempts/{first_attempt['id']}/failures",
        headers=auth_headers(client, "platform-admin", "timeout-failure-1"),
        json={"reasonCode": "AGENT_TIMEOUT", "detail": "Runner request timed out"},
    )
    assert failed.status_code == 200, failed.text

    retried = _coordinate(client, workflow_id, requester, "timeout-dispatch-2")
    second_attempt = _node(retried["workflow"], "N1")["currentAttempt"]
    assert retried["actions"][0]["action"] == "node_dispatched"
    assert second_attempt["attemptNumber"] == 2
    assert second_attempt["agentKey"] == "Agent-B1"
    assert second_attempt["id"] != first_attempt["id"]

    failed_again = client.post(
        f"/api/v1/workflows/{workflow_id}/nodes/N1/attempts/{second_attempt['id']}/failures",
        headers=auth_headers(client, "platform-admin", "timeout-failure-2"),
        json={"reasonCode": "AGENT_TIMEOUT", "detail": "Runner timed out again"},
    )
    assert failed_again.status_code == 200, failed_again.text

    exhausted = _coordinate(client, workflow_id, requester, "timeout-no-third-dispatch")
    assert exhausted["actions"][0]["action"] == "workflow_failed_refunding"
    assert _node(exhausted["workflow"], "N1")["status"] == "FAILED"


def test_onchain_protocol_gates_funding_route_proof_and_destination_settlement(client: TestClient):
    requester = auth_headers(client, "campus-requester", "onchain-create")
    payer = "0x" + "1" * 40
    created = client.post(
        "/api/v1/workflows",
        headers=requester,
        json={
            "title": "On-chain gated local V1 workflow",
            "objective": "Prove that every protocol confirmation gates the next Coordinator transition.",
            "budget": "20.00",
            "platformFee": "1.10",
            "fundingMode": "ONCHAIN",
            "payerAddress": payer,
        },
    )
    assert created.status_code == 201, created.text
    workflow = created.json()
    workflow_id = workflow["id"]
    assert workflow["status"] == "AWAITING_FUNDING"
    assert workflow["fundingStatus"] == "AWAITING_CONFIRMATION"
    blocked = _coordinate(client, workflow_id, requester, "onchain-before-funding")
    assert blocked["actions"][0]["action"] == "funding_required"
    assert not _node(blocked["workflow"], "N1")["attempts"]

    funded = client.post(
        f"/api/v1/workflows/{workflow_id}/funding-confirmations",
        headers={**requester, "Idempotency-Key": "onchain-funding"},
        json={
            "walletAddress": payer,
            "termsHash": workflow["termsHash"],
            "approveTxHash": "0x" + "c" * 64,
            "lockTxHash": "0x" + "a" * 64,
            "sourceGasTxHash": "0x" + "b" * 64,
            "sourceGasAmount": "0.01",
            "deploymentId": "test-deployment-001",
            "verificationMode": "TEST",
        },
    )
    assert funded.status_code == 200, funded.text
    assert funded.json()["workflow"]["fundingStatus"] == "CONFIRMED"

    admin = auth_headers(client, "platform-admin", "protocol-job-list")
    protocol_jobs = client.get("/api/v1/protocol-jobs?status=&limit=100", headers=admin).json()["items"]
    register_job = next(item for item in protocol_jobs if item["workflowId"] == workflow_id and item["jobType"] == "REGISTER_WORKFLOW")
    running = client.patch(
        f"/api/v1/protocol-jobs/{register_job['id']}",
        headers={**admin, "Idempotency-Key": "protocol-job-running"},
        json={"status": "RUNNING"},
    )
    assert running.status_code == 200, running.text
    assert running.json()["attempts"] == 1
    checkpointed = client.patch(
        f"/api/v1/protocol-jobs/{register_job['id']}",
        headers={**admin, "Idempotency-Key": "protocol-job-checkpoint"},
        json={"status": "RUNNING", "transactionHash": "0x" + "9" * 64},
    )
    assert checkpointed.status_code == 200, checkpointed.text
    assert checkpointed.json()["attempts"] == 1
    completed_job = client.patch(
        f"/api/v1/protocol-jobs/{register_job['id']}",
        headers={**admin, "Idempotency-Key": "protocol-job-completed"},
        json={"status": "COMPLETED"},
    )
    assert completed_job.status_code == 200, completed_job.text
    assert completed_job.json()["attempts"] == 1

    deployment_queued = _coordinate(client, workflow_id, requester, "onchain-deployment")
    assert deployment_queued["actions"][0]["action"] == "route_deployment_queued"
    assert _node(deployment_queued["workflow"], "N1")["status"] == "WAITING_FOR_ROUTE_DEPLOYMENT"
    deployments = client.get("/api/v1/route-deployments?status=QUEUED").json()["items"]
    assert len(deployments) == 1
    deployment = deployments[0]
    admin = auth_headers(client, "platform-admin", "complete-route-b")
    completed_deployment = client.post(
        f"/api/v1/route-deployments/{deployment['id']}/complete",
        headers=admin,
        json={
            "status": "READY",
            "contracts": {
                "resultProofGatewayA": "0x" + "2" * 40,
                "remoteWorkBoard": "0x" + "3" * 40,
                "dynamicSettlementReceiver": "0x" + "4" * 40,
                "destinationToken": "0x" + "5" * 40,
                "payoutToken": "0x" + "6" * 40,
                "payoutSwap": "0x" + "7" * 40,
            },
            "transactionHashes": {"deploy": "0x" + "c" * 64},
            "codeHashes": {"remoteWorkBoard": "0x" + "d" * 64},
            "manifestHash": "0x" + "e" * 64,
        },
    )
    assert completed_deployment.status_code == 200, completed_deployment.text

    dispatched = _coordinate(client, workflow_id, requester, "onchain-dispatch-b")
    attempt = _node(dispatched["workflow"], "N1")["currentAttempt"]
    assert attempt["agentKey"] == "Agent-B1"
    assert attempt["settlementAsset"] == "USDC"
    assert attempt["payoutAsset"] == "BTK"
    assert attempt["payoutTokenAddress"] == "0x" + "6" * 40
    assert attempt["payoutDecimals"] == 18
    assert attempt["payoutRate"] == "2"
    assert attempt["payoutSlippageBps"] == 100
    assert attempt["payoutStatus"] == "PENDING"
    protocol_jobs = client.get("/api/v1/protocol-jobs?status=&limit=100", headers=admin).json()["items"]
    dispatch_job = next(item for item in protocol_jobs if item["attemptId"] == attempt["id"] and item["jobType"] == "DISPATCH_ASSIGNMENT")
    dispatch_running = client.patch(
        f"/api/v1/protocol-jobs/{dispatch_job['id']}",
        headers={**admin, "Idempotency-Key": "dispatch-job-running"},
        json={"status": "RUNNING"},
    )
    assert dispatch_running.status_code == 200, dispatch_running.text
    recovered_jobs = client.post(
        "/api/v1/protocol-jobs/recover-stale?staleAfterSeconds=0",
        headers={**admin, "Idempotency-Key": "recover-stale-dispatch-job"},
    )
    assert recovered_jobs.status_code == 200, recovered_jobs.text
    assert dispatch_job["id"] in recovered_jobs.json()["recoveredJobIds"]
    reclaimed = client.patch(
        f"/api/v1/protocol-jobs/{dispatch_job['id']}",
        headers={**admin, "Idempotency-Key": "dispatch-job-reclaimed"},
        json={"status": "RUNNING"},
    )
    assert reclaimed.status_code == 200, reclaimed.text
    assert reclaimed.json()["attempts"] == 2
    dispatch_completed = client.patch(
        f"/api/v1/protocol-jobs/{dispatch_job['id']}",
        headers={**admin, "Idempotency-Key": "dispatch-job-completed"},
        json={"status": "COMPLETED", "transactionHash": "0x" + "8" * 64},
    )
    assert dispatch_completed.status_code == 200, dispatch_completed.text
    dispatch_messages = client.get("/api/v1/cross-chain-messages").json()["items"]
    dispatch_message = next(item for item in dispatch_messages if item["attemptId"] == attempt["id"] and item["messageType"] == "DISPATCH")
    assert dispatch_message["status"] == "DELIVERED"
    assert dispatch_message["transactionHash"] == "0x" + "8" * 64
    result = client.post(
        f"/api/v1/workflows/{workflow_id}/nodes/N1/attempts/{attempt['id']}/results",
        headers=auth_headers(client, "workflow-research-b", "onchain-result-b"),
        json={
            "output": {"summary": "Relayed evidence", "score": 95},
            "proofHash": PROOF_B,
            "transactionHash": "0x" + "6" * 64,
            "gasCost": "0.0001",
        },
    )
    assert result.status_code == 202, result.text
    assert result.json()["code"] == "RESULT_PROOF_PENDING"
    pending = client.get(f"/api/v1/workflows/{workflow_id}").json()
    pending_attempt = _node(pending, "N1")["currentAttempt"]
    assert pending_attempt["status"] == "RESULT_PROOF_PENDING"
    assert _coordinate(client, workflow_id, requester, "onchain-proof-not-here")["actions"][0]["action"] == "idle"

    proof = client.post(
        f"/api/v1/workflows/{workflow_id}/nodes/N1/attempts/{attempt['id']}/result-proof-confirmations",
        headers=auth_headers(client, "platform-admin", "onchain-proof-b"),
        json={
            "messageId": pending_attempt["resultMessageId"],
            "transactionHash": "0x" + "7" * 64,
            "resultHash": pending_attempt["resultHash"],
            "proofHash": PROOF_B,
            "gasCost": "0.0001",
        },
    )
    assert proof.status_code == 200, proof.text
    proof_workflow = proof.json()["workflow"]
    gas_fees = {
        item["category"]: item
        for item in proof_workflow["feeLedger"]
        if item["attemptId"] == attempt["id"]
    }
    assert gas_fees["AGENT_RESULT_SUBMISSION_GAS"]["finalResponsibleParty"] == "AGENT"
    assert gas_fees["RESULT_PROOF_RELAY_FEE"]["finalResponsibleParty"] == "USER_SOURCE_GAS_DEPOSIT"
    gas_entries = [
        item for item in proof_workflow["ledger"]
        if item["attemptId"] == attempt["id"] and item["assetKind"] == "GAS"
    ]
    assert {item["entryType"] for item in gas_entries} == {"RESULT_SUBMISSION_GAS", "RELAY_GAS"}
    assert next(item for item in gas_entries if item["entryType"] == "RESULT_SUBMISSION_GAS")["chainId"] == 31338
    assert next(item for item in gas_entries if item["entryType"] == "RELAY_GAS")["chainId"] == 31337
    reviewed = _coordinate(client, workflow_id, requester, "onchain-review-b")
    assert _node(reviewed["workflow"], "N1")["status"] == "SUCCEEDED"
    waiting_for_review_chain = _coordinate(client, workflow_id, requester, "onchain-review-chain-wait")
    assert waiting_for_review_chain["actions"][0]["action"] == "review_onchain_pending"
    protocol_jobs = client.get("/api/v1/protocol-jobs?status=&limit=100", headers=admin).json()["items"]
    review_sync_job = next(
        item
        for item in reversed(protocol_jobs)
        if item["attemptId"] == attempt["id"]
        and item["jobType"] == "SYNC_REVIEW_V2"
        and item["payload"]["expectedStatus"] == "APPROVED"
    )
    client.patch(
        f"/api/v1/protocol-jobs/{review_sync_job['id']}",
        headers={**admin, "Idempotency-Key": "review-sync-running"},
        json={"status": "RUNNING"},
    )
    review_sync_completed = client.patch(
        f"/api/v1/protocol-jobs/{review_sync_job['id']}",
        headers={**admin, "Idempotency-Key": "review-sync-completed"},
        json={"status": "COMPLETED", "transactionHash": "0x" + "5" * 64},
    )
    assert review_sync_completed.status_code == 200, review_sync_completed.text
    settlement = _coordinate(client, workflow_id, requester, "onchain-settlement-b")
    settlement_attempt = _node(settlement["workflow"], "N1")["currentAttempt"]
    assert settlement["actions"][0]["action"] == "settlement_pending"
    assert settlement_attempt["settlementStatus"] == "QUEUED"
    assert settlement_attempt["payoutStatus"] == "QUOTED"
    assert settlement_attempt["payoutQuoteAmount"] == "8.4"
    assert settlement_attempt["payoutMinAmount"] == "8.316"
    assert settlement_attempt["payoutQuoteExpiresAt"] is not None
    assert _node(settlement["workflow"], "N2")["status"] == "BLOCKED"

    wrong_wallet = client.post(
        f"/api/v1/workflows/{workflow_id}/nodes/N1/attempts/{attempt['id']}/settlement-confirmations",
        headers=auth_headers(client, "platform-admin", "onchain-settlement-wrong-wallet"),
        json={
            "messageId": settlement_attempt["settlementMessageId"],
            "destinationTxHash": "0x" + "8" * 64,
            "sourceConfirmTxHash": "0x" + "9" * 64,
            "receivedAmount": "4.2",
            "homeWallet": "0x" + "f" * 40,
            "settlementFee": "0.0002",
        },
    )
    assert wrong_wallet.status_code == 422

    conversion_confirmation = {
        "messageId": settlement_attempt["settlementMessageId"],
        "destinationTxHash": "0x" + "8" * 64,
        "sourceConfirmTxHash": "0x" + "9" * 64,
        "receivedAmount": "4.2",
        "homeWallet": attempt["homeWallet"],
        "settlementFee": "0.0002",
        "settlementAsset": "USDC",
        "payoutAsset": "BTK",
        "payoutTokenAddress": "0x" + "6" * 40,
        "payoutAmount": "8.400000000000000000",
        "minPayoutAmount": "8.316000000000000000",
        "payoutRate": "2.000000000000",
        "payoutTxHash": "0x" + "8" * 64,
    }
    for suffix, field, value in (
        ("token", "payoutTokenAddress", "0x" + "f" * 40),
        ("rate", "payoutRate", "3"),
        ("amount", "payoutAmount", "8.3"),
    ):
        invalid_confirmation = {**conversion_confirmation, field: value}
        rejected = client.post(
            f"/api/v1/workflows/{workflow_id}/nodes/N1/attempts/{attempt['id']}/settlement-confirmations",
            headers=auth_headers(client, "platform-admin", f"onchain-settlement-wrong-{suffix}"),
            json=invalid_confirmation,
        )
        assert rejected.status_code == 422, rejected.text

    confirmed = client.post(
        f"/api/v1/workflows/{workflow_id}/nodes/N1/attempts/{attempt['id']}/settlement-confirmations",
        headers=auth_headers(client, "platform-admin", "onchain-settlement-confirm-b"),
        json=conversion_confirmation,
    )
    assert confirmed.status_code == 200, confirmed.text
    final = confirmed.json()["workflow"]
    assert _node(final, "N1")["status"] == "SETTLED"
    assert _node(final, "N2")["status"] == "READY"
    assert any(item["category"] == "SETTLEMENT_CCIP_FEE" for item in final["feeLedger"])
    assert any(
        item["entryType"] == "PAYOUT_SWAP" and item["assetSymbol"] == "BTK" and item["amount"] == "8.4"
        for item in final["ledger"]
    )


def test_failed_workflow_automatically_refunds_model_budget(client: TestClient):
    requester = auth_headers(client, "campus-requester", "failed-refund-create")
    created = client.post(
        "/api/v1/workflows",
        headers=requester,
        json={
            "title": "Exhausted workflow refund",
            "objective": "Reject the only attempt and return every remaining model-reserved token.",
            "budget": "5.00",
            "platformFee": "0.50",
            "nodes": [{
                "nodeKey": "N1",
                "title": "Single failing research node",
                "capability": "research",
                "reward": "4.20",
                "maxAttempts": 1,
                "acceptanceCriteria": {"requiredFields": ["summary"], "minScore": 80},
            }],
        },
    )
    assert created.status_code == 201, created.text
    workflow_id = created.json()["id"]
    dispatched = _coordinate(client, workflow_id, requester, "failed-refund-dispatch")
    attempt = _node(dispatched["workflow"], "N1")["currentAttempt"]
    submitted = client.post(
        f"/api/v1/workflows/{workflow_id}/nodes/N1/attempts/{attempt['id']}/results",
        headers=auth_headers(client, "workflow-research-b", "failed-refund-result"),
        json={"output": {"score": 1}, "proofHash": PROOF_B},
    )
    assert submitted.status_code == 202, submitted.text
    reviewed = _coordinate(client, workflow_id, requester, "failed-refund-review")
    assert reviewed["workflow"]["status"] == "FAILED"
    refunding = _coordinate(client, workflow_id, requester, "failed-refund-start")
    assert refunding["workflow"]["status"] == "FAILED_REFUNDING"
    refunded = _coordinate(client, workflow_id, requester, "failed-refund-complete")["workflow"]
    assert refunded["status"] == "FAILED_REFUNDED"
    assert refunded["refundedAmount"] == "5"
    assert all(item["status"] != "RESERVED" for node in refunded["nodes"] for item in node["reservations"])
