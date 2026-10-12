from __future__ import annotations

import asyncio
import json
from pathlib import Path

import httpx
import pytest
from fastapi.testclient import TestClient

from app.main import (
    OpenAICompatibleTaskInterpreter,
    PlatformClient,
    TaskInterpreter,
    _validate_proposal_market_contract,
    build_app,
)


class FakePlatform:
    def __init__(self) -> None:
        self.created: dict | None = None
        self.planned: dict | None = None

    async def health(self):
        return {"status": "ok"}

    async def planning_context(self):
        return {
            "currency": "USDC",
            "availableCapabilities": [
                {
                    "capability": "research",
                    "lowestQuote": "4.2",
                    "eligibleProviders": 2,
                    "liveProviders": 2,
                    "commonOutputFields": ["score", "sources", "summary"],
                },
                {
                    "capability": "verification",
                    "lowestQuote": "6.8",
                    "eligibleProviders": 2,
                    "liveProviders": 2,
                    "commonOutputFields": ["report", "verified"],
                },
            ],
            "rules": ["Use an exact capability value from availableCapabilities."],
        }

    async def plan(self, payload):
        self.planned = payload
        return {
            "plannerVersion": "LLM_DAG_PROPOSAL_V1+CORE_VALIDATOR_V1",
            "decisionHash": "0x" + "11" * 32,
            "nodes": [
                {
                    "nodeKey": "N1",
                    "title": "Research",
                    "description": "Collect evidence",
                    "capability": "research",
                    "dependsOn": [],
                    "acceptanceCriteria": {"requiredFields": ["summary", "score"]},
                    "inputPayload": {},
                    "reward": "4.20",
                    "riskLevel": "LOW",
                    "reviewPolicyVersion": "RISK_REVIEW_V2",
                    "requiredApprovals": 0,
                    "challengePeriodSeconds": 0,
                    "samplingRateBps": 0,
                    "reviewBudget": "0",
                    "maxAttempts": 3,
                    "riskAssessment": {
                        "score": 15,
                        "riskLevel": "LOW",
                        "reasons": ["checks required fields"],
                        "assignmentMode": "AUTO",
                    },
                    "candidates": [],
                    "criteriaHash": "0x" + "22" * 32,
                }
            ],
        }

    async def create_workflow(self, payload):
        self.created = payload
        return {"id": "workflow-1", "status": "RUNNING"}

    async def workflow(self, workflow_id):
        return {
            "id": workflow_id,
            "referenceCode": "WF-TEST",
            "status": "COMPLETED",
            "budget": "20",
            "paidAmount": "4.2",
            "platformFee": "1.1",
            "refundedAmount": "14.7",
            "nodes": [
                {
                    "nodeKey": "N1",
                    "title": "Research",
                    "status": "SETTLED",
                    "currentAttempt": {
                        "id": "attempt-1",
                        "agentKey": "Agent-B1",
                        "output": {"summary": "done"},
                    },
                }
            ],
        }

    async def coordinate(self, workflow_id, max_actions):
        return {"workflowId": workflow_id, "actions": [{"action": "waiting_for_agent"}]}


def client(tmp_path: Path, monkeypatch) -> tuple[TestClient, FakePlatform]:
    monkeypatch.setenv("APP_ENV", "test")
    monkeypatch.setenv("COORDINATOR_ALLOW_INSECURE_LOCAL", "true")
    platform = FakePlatform()
    application = build_app(
        platform_client=platform,
        interpreter=TaskInterpreter(),
        state_path=tmp_path / "coordinator.json",
    )
    return TestClient(application), platform


def test_chat_creates_validated_draft_without_creating_workflow(tmp_path, monkeypatch):
    test_client, platform = client(tmp_path, monkeypatch)
    response = test_client.post(
        "/chat",
        json={"message": "Research cross-chain settlement safety", "budget": "20.00"},
    )
    assert response.status_code == 200
    body = response.json()
    assert body["status"] == "DRAFT"
    assert body["draft"]["nodes"][0]["nodeKey"] == "N1"
    assert "riskLevel" not in body["draft"]["nodes"][0]
    assert "requiredApprovals" not in body["draft"]["nodes"][0]
    assert "candidates" not in body["draft"]["nodes"][0]
    assert platform.planned is not None
    assert len(platform.planned["proposedNodes"]) == 2
    assert platform.planned["proposalVersion"] == "LLM_DAG_PROPOSAL_V1"
    assert body["marketContext"]["availableCapabilities"][0]["capability"] == "research"
    assert platform.created is None


def test_market_contract_rejects_fields_not_supported_by_live_agents():
    proposal = {
        "nodes": [{
            "nodeKey": "N1",
            "capability": "research",
            "acceptanceCriteria": {
                "requiredFields": ["comprehensiveSummary", "documentSources"],
            },
        }]
    }
    market = {
        "availableCapabilities": [{
            "capability": "research",
            "commonOutputFields": ["summary", "score", "sources"],
        }]
    }

    with pytest.raises(ValueError, match="comprehensiveSummary"):
        _validate_proposal_market_contract(proposal, market)


def test_market_contract_accepts_shared_manifest_fields():
    proposal = {
        "nodes": [{
            "nodeKey": "N1",
            "capability": "research",
            "acceptanceCriteria": {
                "requiredFields": ["summary", "score", "sources"],
                "rules": ["Cite the retrieved project evidence."],
            },
        }]
    }
    market = {
        "availableCapabilities": [{
            "capability": "research",
            "commonOutputFields": ["summary", "score", "sources"],
        }]
    }

    assert _validate_proposal_market_contract(proposal, market) is proposal


def test_planning_context_uses_intersection_of_live_provider_output_schemas():
    class ManifestPlatform(PlatformClient):
        async def _request(self, method, path, body=None):
            assert method == "GET"
            assert path == "/api/v1/workflow-agents"
            common = {
                "active": True,
                "admissionStatus": "APPROVED",
                "chainStatus": "READY",
                "availability": "ONLINE",
                "capacityAvailable": True,
                "gasReady": True,
                "credentialStatus": "ACTIVE",
                "capabilities": ["research"],
                "priceFloor": "4.20",
                "matchable": True,
            }
            return {
                "items": [
                    {
                        **common,
                        "metadata": {
                            "verification": {
                                "manifest": {
                                    "capabilities": [{
                                        "capabilityId": "research",
                                        "outputSchema": {
                                            "type": "object",
                                            "required": ["summary", "score", "sources"],
                                            "properties": {
                                                "summary": {"type": "string"},
                                                "score": {"type": "integer"},
                                                "sources": {"type": "array"},
                                            },
                                            "additionalProperties": True,
                                        },
                                    }]
                                }
                            }
                        },
                    },
                    {
                        **common,
                        "metadata": {
                            "verification": {
                                "manifest": {
                                    "capabilities": [{
                                        "capabilityId": "research",
                                        "outputSchema": {
                                            "type": "object",
                                            "required": ["summary", "score"],
                                            "properties": {
                                                "summary": {"type": "string"},
                                                "score": {"type": "integer"},
                                            },
                                            "additionalProperties": True,
                                        },
                                    }]
                                }
                            }
                        },
                    },
                    {
                        **common,
                        "matchable": False,
                        "metadata": {},
                    },
                ]
            }

    context = asyncio.run(ManifestPlatform().planning_context())
    research = context["availableCapabilities"][0]

    assert research["commonOutputFields"] == ["score", "summary"]
    assert research["outputContractsKnown"] == 2
    assert research["outputContractsUnknown"] == 0


def test_confirm_is_idempotent_and_never_accepts_a_private_key(tmp_path, monkeypatch):
    test_client, platform = client(tmp_path, monkeypatch)
    draft = test_client.post(
        "/chat",
        json={"message": "Research cross-chain settlement safety", "budget": "20.00"},
    ).json()
    path = f"/conversations/{draft['conversationId']}/workflows"
    first = test_client.post(path, json={"fundingMode": "RESERVED"})
    second = test_client.post(path, json={"fundingMode": "RESERVED"})
    assert first.status_code == 200
    assert second.status_code == 200
    assert platform.created is not None
    assert "privateKey" not in platform.created
    assert platform.created["fundingMode"] == "RESERVED"


def test_onchain_confirmation_requires_only_a_payer_address(tmp_path, monkeypatch):
    test_client, _ = client(tmp_path, monkeypatch)
    draft = test_client.post(
        "/chat",
        json={"message": "Research cross-chain settlement safety", "budget": "20.00"},
    ).json()
    response = test_client.post(
        f"/conversations/{draft['conversationId']}/workflows",
        json={"fundingMode": "ONCHAIN"},
    )
    assert response.status_code == 422


def configure_openai_compatible(monkeypatch, api_mode: str) -> None:
    monkeypatch.setenv("COORDINATOR_OPENAI_API_KEY", "test-key")
    monkeypatch.setenv("COORDINATOR_OPENAI_BASE_URL", "https://llm.example.test/v1")
    monkeypatch.setenv("COORDINATOR_OPENAI_MODEL", "test-model")
    monkeypatch.setenv("COORDINATOR_OPENAI_API_MODE", api_mode)
    monkeypatch.setenv("COORDINATOR_OPENAI_MAX_RETRIES", "0")


def proposed_nodes() -> list[dict]:
    return [
        {
            "nodeKey": "N1",
            "title": "Collect evidence",
            "description": "Find and structure relevant evidence.",
            "capability": "research",
            "dependsOn": [],
            "acceptanceCriteria": {"requiredFields": ["summary"]},
            "inputPayload": {"topic": "settlement"},
            "suggestedReward": "4.20",
            "maxAttempts": 2,
            "rationale": "Creates an evidence base.",
            "riskLevel": "LOW",
            "chainId": 1,
            "homeWallet": "0x" + "f" * 40,
        },
        {
            "nodeKey": "N2",
            "title": "Verify evidence",
            "description": "Check the evidence and produce a final report.",
            "capability": "verification",
            "dependsOn": ["N1"],
            "acceptanceCriteria": {"requiredFields": ["verified", "report"]},
            "inputPayload": {"evidence": "N1.output"},
            "suggestedReward": "6.80",
            "maxAttempts": 3,
            "rationale": "Separates production from verification.",
        },
    ]


def test_openai_compatible_responses_interpreter(monkeypatch):
    configure_openai_compatible(monkeypatch, "responses")

    def handler(request: httpx.Request) -> httpx.Response:
        assert request.url.path == "/v1/responses"
        assert request.headers["authorization"] == "Bearer test-key"
        request_body = json.loads(request.content)
        assert request_body["model"] == "test-model"
        prompt = json.loads(request_body["input"][1]["content"])
        assert prompt["capabilityMarket"] == {}
        return httpx.Response(
            200,
            json={
                "output": [
                    {
                        "type": "message",
                        "content": [
                            {
                                "type": "output_text",
                                "text": json.dumps(
                                        {
                                            "title": "Settlement research",
                                            "objective": "Research node-level cross-chain settlement safety.",
                                            "inputPayload": {"topic": "settlement"},
                                            "nodes": proposed_nodes(),
                                            "explanation": "Research first, then verify.",
                                        }
                                ),
                            }
                        ],
                    }
                ]
            },
        )

    interpreter = OpenAICompatibleTaskInterpreter(transport=httpx.MockTransport(handler))
    result = asyncio.run(interpreter.interpret("Research settlement safety", {"topic": "settlement"}))
    assert result["title"] == "Settlement research"
    assert result["inputPayload"] == {"topic": "settlement"}
    assert len(result["nodes"]) == 2
    assert "riskLevel" not in result["nodes"][0]
    assert "chainId" not in result["nodes"][0]
    assert "homeWallet" not in result["nodes"][0]


def test_openai_compatible_chat_completions_interpreter(monkeypatch):
    configure_openai_compatible(monkeypatch, "chat_completions")

    def handler(request: httpx.Request) -> httpx.Response:
        assert request.url.path == "/v1/chat/completions"
        request_body = json.loads(request.content)
        prompt = json.loads(request_body["messages"][1]["content"])
        assert prompt["budget"] == "20.00"
        return httpx.Response(
            200,
            json={
                "choices": [
                    {
                        "message": {
                            "content": "```json\n"
                            + json.dumps(
                                    {
                                        "title": "Agent recovery study",
                                        "objective": "Compare Agent retry and replacement mechanisms.",
                                        "inputPayload": {},
                                        "nodes": proposed_nodes(),
                                        "explanation": "Use separate research and verification nodes.",
                                    }
                            )
                            + "\n```"
                        }
                    }
                ]
            },
        )

    interpreter = OpenAICompatibleTaskInterpreter(transport=httpx.MockTransport(handler))
    result = asyncio.run(interpreter.interpret("Compare Agent recovery mechanisms", {}))
    assert result["objective"] == "Compare Agent retry and replacement mechanisms."


def test_refine_replaces_unconfirmed_draft_without_creating_workflow(tmp_path, monkeypatch):
    test_client, platform = client(tmp_path, monkeypatch)
    draft = test_client.post(
        "/chat",
        json={"message": "Research cross-chain settlement safety", "budget": "20.00"},
    ).json()
    refined = test_client.post(
        f"/conversations/{draft['conversationId']}/refine",
        json={"message": "Also require an independently verified final report."},
    )
    assert refined.status_code == 200, refined.text
    assert "Revision requested" in refined.json()["draft"]["objective"]
    assert len(refined.json()["messages"]) == 4
    assert platform.created is None


def test_summary_is_read_only_and_exposes_final_output(tmp_path, monkeypatch):
    test_client, _ = client(tmp_path, monkeypatch)
    draft = test_client.post(
        "/chat",
        json={"message": "Research cross-chain settlement safety", "budget": "20.00"},
    ).json()
    test_client.post(
        f"/conversations/{draft['conversationId']}/workflows",
        json={"fundingMode": "RESERVED"},
    )
    summary = test_client.get(f"/conversations/{draft['conversationId']}/summary")
    assert summary.status_code == 200
    assert summary.json()["completed"] is True
    assert summary.json()["finalOutput"] == {"summary": "done"}


def test_unknown_coordinator_provider_is_rejected(monkeypatch):
    monkeypatch.setenv("COORDINATOR_LLM_PROVIDER", "unknown-provider")
    with pytest.raises(ValueError, match="Unsupported COORDINATOR_LLM_PROVIDER"):
        build_app()


def test_coordinator_exposes_agent_manifest_identity_and_test_plan(tmp_path, monkeypatch):
    test_client, _ = client(tmp_path, monkeypatch)
    manifest_response = test_client.get("/manifest")
    assert manifest_response.status_code == 200
    manifest = manifest_response.json()
    assert manifest["agentType"] == "COORDINATOR"
    assert manifest["did"].startswith("did:local:")
    assert manifest["capabilities"][0]["capabilityId"] == "workflow_coordination"
    assert manifest["coordination"]["coreValidated"] is True

    test_plan = test_client.post(
        "/test-plan",
        json={
            "message": "Research cross-chain settlement safety",
            "budget": "20.00",
        },
    )
    assert test_plan.status_code == 200, test_plan.text
    body = test_plan.json()
    assert body["agentId"] == manifest["agentId"]
    assert body["did"] == manifest["did"]
    assert body["agentType"] == "COORDINATOR"
    assert body["proposalValid"] is True
    assert body["outputHash"].startswith("sha256:")
    assert body["draft"]["inputPayload"]["_coordinator"]["did"] == manifest["did"]


def test_coordinate_uses_execution_timeout(monkeypatch):
    monkeypatch.setenv("COORDINATOR_PLATFORM_TIMEOUT_SECONDS", "30")
    monkeypatch.setenv("COORDINATOR_EXECUTION_TIMEOUT_SECONDS", "2200")

    class RecordingPlatform(PlatformClient):
        def __init__(self) -> None:
            super().__init__()
            self.request: dict | None = None

        async def _request(self, method, path, body=None, *, timeout_seconds=None):
            self.request = {
                "method": method,
                "path": path,
                "body": body,
                "timeoutSeconds": timeout_seconds,
            }
            return {"status": "ok"}

    platform = RecordingPlatform()
    result = asyncio.run(platform.coordinate("workflow-1", 5))

    assert result == {"status": "ok"}
    assert platform.timeout == 30
    assert platform.request == {
        "method": "POST",
        "path": "/api/v1/workflows/workflow-1/coordinate",
        "body": {"maxActions": 5},
        "timeoutSeconds": 2200,
    }
