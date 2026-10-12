from __future__ import annotations

import base64
import hashlib
import hmac
import json
import os
from datetime import datetime, timedelta, timezone

os.environ.setdefault("APP_ENV", "test")
os.environ.setdefault("AGENT_RUNNER_ALLOW_INSECURE_LOCAL", "true")

import pytest
from eth_account import Account
from eth_account.messages import encode_defunct
from cryptography.hazmat.primitives import hashes
from cryptography.hazmat.primitives.asymmetric import ec
from fastapi.testclient import TestClient

from app.identity import canonical_json, load_runtime_identities
from app.main import build_app


@pytest.fixture()
def client() -> TestClient:
    return TestClient(build_app())


def _decode(value: str) -> bytes:
    return base64.urlsafe_b64decode(value + "=" * (-len(value) % 4))


def _signed_offer(token: str) -> dict[str, object]:
    offer: dict[str, object] = {
        "offerId": "offer-12345678",
        "workflowId": "workflow-123",
        "nodeId": "node-123",
        "attemptId": "attempt-123",
        "attemptNumber": 1,
        "reservationId": "reservation-123",
        "capabilityId": "research",
        "criteriaHash": "0x" + "11" * 32,
        "reward": "4.20",
        "settlementAsset": "USDC",
        "payoutAsset": "BTK",
        "deadline": (datetime.now(timezone.utc) + timedelta(minutes=5)).isoformat().replace("+00:00", "Z"),
        "nonce": "nonce-1234567890123456",
    }
    digest = hmac.new(token.encode(), canonical_json(offer), hashlib.sha256).digest()
    offer["coordinatorSignature"] = base64.urlsafe_b64encode(digest).rstrip(b"=").decode()
    return offer


def test_lists_all_demo_agents(client: TestClient) -> None:
    response = client.get("/agents")
    assert response.status_code == 200
    assert {item["agentId"] for item in response.json()["items"]} == {
        "text-summary",
        "data-statistics",
        "hash-audit",
        "code-review",
        "workflow-research-b",
        "workflow-verification-c",
        "workflow-verification-d",
    }
    assert all(
        item["capabilities"][0]["basePrice"] for item in response.json()["items"]
    )


def test_runner_can_isolate_one_agent(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("AGENT_RUNNER_SLUGS", "workflow-research-b")
    monkeypatch.setenv("AGENT_RUNNER_ID", "runner-b")
    isolated = TestClient(build_app())
    health = isolated.get("/health")
    assert health.status_code == 200
    assert health.json()["runnerId"] == "runner-b"
    assert health.json()["agentSlugs"] == ["workflow-research-b"]
    assert isolated.get("/agents/workflow-verification-c/health").status_code == 404


def test_research_agent_persists_conversation_and_returns_sources(
    monkeypatch: pytest.MonkeyPatch, tmp_path
) -> None:
    knowledge = tmp_path / "project.md"
    knowledge.write_text(
        "# Settlement\n\nChain C failure releases the reservation before settlement.\n",
        encoding="utf-8",
    )
    monkeypatch.setenv("LLM_PROVIDER", "mock")
    monkeypatch.setenv("AGENT_RUNNER_SLUGS", "workflow-research-b")
    monkeypatch.setenv("AGENT_RUNNER_ID", "runner-b-test")
    monkeypatch.setenv("AGENT_CHAT_DB_PATH", str(tmp_path / "chat.db"))
    monkeypatch.setenv("AGENT_KNOWLEDGE_PATHS", str(knowledge))

    with TestClient(build_app()) as kimi_client:
        manifest = kimi_client.get(
            "/agents/workflow-research-b/manifest"
        )
        assert manifest.status_code == 200
        assert manifest.json()["conversation"]["enabled"] is True
        assert "read_project_context" in {item["name"] for item in manifest.json()["tools"]}

        created = kimi_client.post(
            "/agents/workflow-research-b/chat/sessions",
            json={"ownerId": "local-user", "title": "Failure handling"},
        )
        assert created.status_code == 200, created.text
        session_id = created.json()["sessionId"]

        first = kimi_client.post(
            "/agents/workflow-research-b/chat",
            json={"sessionId": session_id, "message": "How is Chain C failure handled?"},
        )
        assert first.status_code == 200, first.text
        assert "charset=utf-8" in first.headers["content-type"].lower()
        assert first.json()["provider"] == "mock"
        assert first.json()["sources"][0]["path"].endswith("project.md")
        assert first.json()["toolInvocations"][0]["name"] == "read_project_context"
        assert first.json()["toolInvocations"][0]["ok"] is True

        second = kimi_client.post(
            "/agents/workflow-research-b/chat",
            json={"sessionId": session_id, "message": "Does it require rollback?"},
        )
        assert second.status_code == 200, second.text

        stored = kimi_client.get(
            f"/agents/workflow-research-b/chat/sessions/{session_id}"
        )
        assert stored.status_code == 200
        assert [item["role"] for item in stored.json()["messages"]] == [
            "user",
            "assistant",
            "user",
            "assistant",
        ]

        deleted = kimi_client.delete(
            f"/agents/workflow-research-b/chat/sessions/{session_id}"
        )
        assert deleted.status_code == 200
        assert kimi_client.get(
            f"/agents/workflow-research-b/chat/sessions/{session_id}"
        ).status_code == 404


def test_non_conversational_agent_rejects_chat(client: TestClient) -> None:
    # workflow-research-b is conversational, so this asserts against a
    # verification Agent that has no chat_system_prompt.
    response = client.post(
        "/agents/workflow-verification-c/chat/sessions",
        json={"ownerId": "local-user"},
    )
    assert response.status_code == 409


@pytest.mark.parametrize(
    ("slug", "payload", "assertion"),
    [
        (
            "text-summary",
            {
                "text": "First sentence. Second sentence is longer.",
                "maxLength": 20,
                "language": "en",
            },
            lambda output: output["summary"] == "First sentence.",
        ),
        (
            "data-statistics",
            {"numbers": [1, 2, 3, 4], "metrics": ["mean", "median", "stdev"]},
            lambda output: output["metrics"]["mean"] == 2.5,
        ),
        (
            "hash-audit",
            {"content": "abc", "algorithm": "sha256"},
            lambda output: (
                output["digest"]
                == "ba7816bf8f01cfea414140de5dae2223b00361a396177a9cb410ff61f20015ad"
            ),
        ),
        (
            "code-review",
            {
                "code": "def run(value):\n    return eval(value)",
                "language": "python",
                "focus": "security",
            },
            lambda output: output["findings"][0]["severity"] == "high",
        ),
    ],
)
def test_invokes_agents_and_signs_results(client, slug, payload, assertion) -> None:
    response = client.post(
        f"/agents/{slug}/invoke",
        json={"requestId": "request-123", "input": payload},
    )
    assert response.status_code == 200
    result = response.json()
    assert assertion(result["output"])
    assert (
        result["outputHash"]
        == "sha256:" + hashlib.sha256(canonical_json(result["output"])).hexdigest()
    )

    signed_payload = {
        field: result[field]
        for field in (
            "agentId",
            "capabilityId",
            "completedAt",
            "did",
            "outputHash",
            "requestId",
        )
    }
    compressed_key = _decode(result["signature"]["publicKeyMultibase"][1:])
    public_key = ec.EllipticCurvePublicKey.from_encoded_point(
        ec.SECP256K1(), compressed_key
    )
    public_key.verify(
        _decode(result["signature"]["value"]),
        canonical_json(signed_payload),
        ec.ECDSA(hashes.SHA256()),
    )


def test_rejects_schema_violation(client: TestClient) -> None:
    response = client.post(
        "/agents/data-statistics/invoke",
        json={"input": {"numbers": [], "metrics": ["unknown"]}},
    )
    assert response.status_code == 422
    assert response.json()["detail"]["code"] == "invalid_agent_input"


def test_rejects_non_finite_statistics(client: TestClient) -> None:
    response = client.post(
        "/agents/data-statistics/invoke",
        content=b'{"input":{"numbers":[1,NaN],"metrics":["mean"]}}',
        headers={"Content-Type": "application/json"},
    )
    assert response.status_code == 422
    assert response.json()["detail"]["code"] == "agent_execution_error"


def test_chinese_summary_splits_on_normal_punctuation(client: TestClient) -> None:
    response = client.post(
        "/agents/text-summary/invoke",
        json={
            "input": {
                "text": "这是第一句话。这是明显更长的第二句话！",
                "maxLength": 8,
                "language": "zh",
            }
        },
    )
    assert response.status_code == 200
    assert response.json()["output"]["summary"] == "这是第一句话。"


def test_unknown_agent_is_404(client: TestClient) -> None:
    assert client.get("/agents/missing/manifest").status_code == 404


def test_invoke_token_can_be_required(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("AGENT_RUNNER_API_TOKEN", "test-token")
    with TestClient(build_app()) as protected_client:
        payload = {"input": {"content": "abc", "algorithm": "sha256"}}
        assert (
            protected_client.post("/agents/hash-audit/invoke", json=payload).status_code
            == 401
        )
        assert (
            protected_client.post(
                "/agents/hash-audit/invoke",
                json=payload,
                headers={"Authorization": "Bearer test-token"},
            ).status_code
            == 200
        )


def test_signed_task_offer_binds_context_and_is_idempotent(monkeypatch: pytest.MonkeyPatch) -> None:
    token = "runner-test-token"
    monkeypatch.setenv("AGENT_RUNNER_API_TOKEN", token)
    with TestClient(build_app()) as protected_client:
        offer = _signed_offer(token)
        headers = {"Authorization": f"Bearer {token}"}
        accepted = protected_client.post(
            "/agents/workflow-research-b/task-offers",
            json=offer,
            headers=headers,
        )
        assert accepted.status_code == 200
        invocation = {
            "requestId": offer["offerId"],
            "offerId": offer["offerId"],
            "workflowId": offer["workflowId"],
            "nodeId": offer["nodeId"],
            "attemptId": offer["attemptId"],
            "attemptNumber": offer["attemptNumber"],
            "reservationId": offer["reservationId"],
            "deadline": offer["deadline"],
            "nonce": offer["nonce"],
            "input": {"objective": "Review cross-chain evidence", "input": {}, "dependencies": {}},
        }
        first = protected_client.post(
            "/agents/workflow-research-b/invoke", json=invocation, headers=headers
        )
        second = protected_client.post(
            "/agents/workflow-research-b/invoke", json=invocation, headers=headers
        )
        assert first.status_code == 200
        assert second.status_code == 200
        assert second.json() == first.json()
        assert first.json()["reservationId"] == offer["reservationId"]


def test_task_offer_rejects_bad_hmac_and_context_replay(monkeypatch: pytest.MonkeyPatch) -> None:
    token = "runner-test-token"
    monkeypatch.setenv("AGENT_RUNNER_API_TOKEN", token)
    headers = {"Authorization": f"Bearer {token}"}
    with TestClient(build_app()) as protected_client:
        bad_offer = _signed_offer(token)
        bad_offer["coordinatorSignature"] = "x" * 43
        assert protected_client.post(
            "/agents/workflow-research-b/task-offers", json=bad_offer, headers=headers
        ).status_code == 401

        offer = _signed_offer(token)
        assert protected_client.post(
            "/agents/workflow-research-b/task-offers", json=offer, headers=headers
        ).status_code == 200
        mismatched = {
            "requestId": offer["offerId"],
            "offerId": offer["offerId"],
            "workflowId": offer["workflowId"],
            "nodeId": offer["nodeId"],
            "attemptId": "attempt-different",
            "attemptNumber": offer["attemptNumber"],
            "reservationId": offer["reservationId"],
            "deadline": offer["deadline"],
            "nonce": offer["nonce"],
            "input": {"objective": "Review cross-chain evidence", "input": {}, "dependencies": {}},
        }
        assert protected_client.post(
            "/agents/workflow-research-b/invoke", json=mismatched, headers=headers
        ).status_code == 409


def test_invoke_requires_token_by_default(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("AGENT_RUNNER_API_TOKEN", raising=False)
    monkeypatch.delenv("AGENT_RUNNER_ALLOW_INSECURE_LOCAL", raising=False)
    with TestClient(build_app()) as protected_client:
        response = protected_client.post(
            "/agents/hash-audit/invoke",
            json={"input": {"content": "abc", "algorithm": "sha256"}},
        )
    assert response.status_code == 503
    assert "AGENT_RUNNER_API_TOKEN" in response.json()["detail"]


def test_injected_private_key_is_not_exposed(monkeypatch: pytest.MonkeyPatch) -> None:
    private_hex = "01".zfill(64)
    monkeypatch.setenv(
        "AGENT_RUNTIME_PRIVATE_KEYS_JSON", json.dumps({"hash-audit": private_hex})
    )
    with TestClient(build_app()) as injected_client:
        manifest = injected_client.get("/agents/hash-audit/manifest").json()
        assert private_hex not in json.dumps(manifest)


def test_agent_runner_signs_only_policy_bound_home_chain_transactions(monkeypatch: pytest.MonkeyPatch) -> None:
    token = "runner-wallet-token"
    private_key = "0x" + "01".zfill(64)
    wallet = Account.from_key(private_key).address
    contract = "0x" + "22" * 20
    attempt_id = "33333333-3333-3333-3333-333333333333"
    attempt_hash = "0x" + "33" * 16 + "00" * 16
    selector = "0x12345678"
    monkeypatch.setenv("AGENT_RUNNER_API_TOKEN", token)
    monkeypatch.setenv("AGENT_HOME_WALLET_PRIVATE_KEYS_JSON", json.dumps({"workflow-research-b": private_key}))
    monkeypatch.setenv("AGENT_WALLET_POLICIES_JSON", json.dumps({
        "workflow-research-b": {
            "chainId": 31338,
            "homeWallet": wallet,
            "allowedContracts": {contract.lower(): [selector]},
            "maxValueWei": "0",
            "maxGas": 200000,
            "maxGasPriceWei": "100000000000",
        }
    }))
    body = {
        "chainId": 31338,
        "to": contract,
        "data": selector + attempt_hash[2:] + "00" * 32,
        "valueWei": "0",
        "nonce": 0,
        "gas": 100000,
        "gasPriceWei": "1000000000",
        "attemptId": attempt_id,
        "attemptIdBytes32": attempt_hash,
        "requestNonce": "0x" + "44" * 32,
    }
    headers = {"Authorization": f"Bearer {token}"}
    with TestClient(build_app()) as wallet_client:
        signed = wallet_client.post(
            "/agents/workflow-research-b/wallet-transactions/sign",
            headers=headers,
            json=body,
        )
        assert signed.status_code == 200, signed.text
        assert signed.json()["signer"].lower() == wallet.lower()
        assert Account.recover_transaction(signed.json()["rawTransaction"]).lower() == wallet.lower()
        replay = wallet_client.post(
            "/agents/workflow-research-b/wallet-transactions/sign",
            headers=headers,
            json=body,
        )
        assert replay.status_code == 409
        forbidden = wallet_client.post(
            "/agents/workflow-research-b/wallet-transactions/sign",
            headers=headers,
            json={**body, "to": "0x" + "55" * 20, "requestNonce": "0x" + "66" * 32},
        )
        assert forbidden.status_code == 422
        wrong_chain = wallet_client.post(
            "/agents/workflow-research-b/wallet-transactions/sign",
            headers=headers,
            json={**body, "chainId": 31339, "requestNonce": "0x" + "67" * 32},
        )
        assert wrong_chain.status_code == 422
        wrong_selector = wallet_client.post(
            "/agents/workflow-research-b/wallet-transactions/sign",
            headers=headers,
            json={**body, "data": "0x87654321" + attempt_hash[2:], "requestNonce": "0x" + "68" * 32},
        )
        assert wrong_selector.status_code == 422
        wrong_attempt = wallet_client.post(
            "/agents/workflow-research-b/wallet-transactions/sign",
            headers=headers,
            json={**body, "attemptId": "44444444-4444-4444-4444-444444444444", "requestNonce": "0x" + "69" * 32},
        )
        assert wrong_attempt.status_code == 422
        excessive_gas = wallet_client.post(
            "/agents/workflow-research-b/wallet-transactions/sign",
            headers=headers,
            json={**body, "gas": 200001, "requestNonce": "0x" + "70" * 32},
        )
        assert excessive_gas.status_code == 422


def test_agent_runner_signs_only_its_onboarding_wallet_challenge(monkeypatch: pytest.MonkeyPatch) -> None:
    token = "runner-wallet-ownership-token"
    private_key = "0x" + "01".zfill(64)
    wallet = Account.from_key(private_key).address
    monkeypatch.setenv("AGENT_RUNNER_API_TOKEN", token)
    monkeypatch.setenv("AGENT_RUNNER_SLUGS", "workflow-research-b")
    monkeypatch.setenv(
        "AGENT_HOME_WALLET_PRIVATE_KEYS_JSON",
        json.dumps({"workflow-research-b": private_key}),
    )
    monkeypatch.setenv("AGENT_WALLET_POLICIES_JSON", json.dumps({
        "workflow-research-b": {
            "chainId": 31338,
            "homeWallet": wallet,
        }
    }))
    message = "\n".join([
        "Agent onboarding ownership challenge",
        "profileId=00000000-0000-0000-0000-000000000001",
        "agentKey=workflow-research-b",
        "did=did:local:workflow-research-b",
        f"homeWallet={wallet}",
        "chainId=31338",
        "nonce=local-test-nonce",
        "expiresAt=2030-01-01T00:00:00Z",
    ])
    headers = {"Authorization": f"Bearer {token}"}
    with TestClient(build_app()) as wallet_client:
        info = wallet_client.get("/agents/workflow-research-b/wallet", headers=headers)
        assert info.status_code == 200
        assert info.json()["address"].lower() == wallet.lower()
        signed = wallet_client.post(
            "/agents/workflow-research-b/wallet-signatures",
            headers=headers,
            json={"message": message},
        )
        assert signed.status_code == 200, signed.text
        recovered = Account.recover_message(
            encode_defunct(text=message),
            signature=signed.json()["signature"],
        )
        assert recovered.lower() == wallet.lower()
        arbitrary = wallet_client.post(
            "/agents/workflow-research-b/wallet-signatures",
            headers=headers,
            json={"message": "Sign this arbitrary payment instruction"},
        )
        assert arbitrary.status_code == 422
        wrong_wallet = wallet_client.post(
            "/agents/workflow-research-b/wallet-signatures",
            headers=headers,
            json={"message": message.replace(wallet, "0x" + "22" * 20)},
        )
        assert wrong_wallet.status_code == 422


def test_development_seed_produces_stable_distinct_dids(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("APP_ENV", "development")
    monkeypatch.setenv("DEV_IDENTITY_SEED", "local-test-seed")
    first = load_runtime_identities(["text-summary", "hash-audit"])
    second = load_runtime_identities(["text-summary", "hash-audit"])
    assert first["text-summary"].did == second["text-summary"].did
    assert first["text-summary"].did != first["hash-audit"].did


def test_marketplace_seed_matches_fixed_did_and_public_key(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("APP_ENV", "development")
    monkeypatch.setenv("DEV_IDENTITY_SEED", "agent-market-demo")
    monkeypatch.delenv("DEV_DID_PREFIX", raising=False)
    monkeypatch.delenv("AGENT_RUNTIME_PRIVATE_KEYS_JSON", raising=False)
    identity = load_runtime_identities(["text-summary"])["text-summary"]
    assert identity.did == "did:local:text-summary"
    assert identity.public_key_hex == (
        "0x04e0219eb18dad34ee0e730544c81d8b92c1d1b36bd2b2d2adf760985956c7fcb1"
        "c7241af1843c7ca87f63731abe5781329a7b3866b3aeb8bd5e2991b5d22a1306"
    )
    with TestClient(build_app()) as seeded_client:
        manifest = seeded_client.get("/agents/text-summary/manifest").json()
        assert manifest["did"] == identity.did
        assert manifest["verificationMethod"]["publicKeyHex"] == identity.public_key_hex


def test_production_requires_explicit_keys(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("APP_ENV", "production")
    monkeypatch.setenv("DEV_IDENTITY_SEED", "must-not-be-used-in-production")
    monkeypatch.delenv("AGENT_RUNTIME_PRIVATE_KEYS_JSON", raising=False)
    with pytest.raises(ValueError, match="Production identity key"):
        load_runtime_identities(["text-summary"])


def test_runner_registers_and_renews_signed_platform_heartbeat(monkeypatch: pytest.MonkeyPatch) -> None:
    token = "runner-heartbeat-token"
    monkeypatch.setenv("AGENT_RUNNER_API_TOKEN", token)
    observed: list[tuple[str, dict[str, object]]] = []

    class FakeResponse:
        def __init__(self, body: dict[str, object]):
            self.body = body

        def raise_for_status(self) -> None:
            return None

        def json(self) -> dict[str, object]:
            return self.body

    class FakeAsyncClient:
        def __init__(self, *args, **kwargs):
            pass

        async def __aenter__(self):
            return self

        async def __aexit__(self, exc_type, exc, traceback):
            return False

        async def post(self, url: str, json: dict[str, object]):
            observed.append((url, json))
            if url.endswith("/heartbeat-challenge"):
                return FakeResponse({
                    "challengeId": "00000000-0000-0000-0000-000000000001",
                    "message": "Agent heartbeat challenge\nnonce=test",
                })
            return FakeResponse({"accepted": True, "matchable": True})

    monkeypatch.setattr("app.main.httpx.AsyncClient", FakeAsyncClient)
    headers = {"Authorization": f"Bearer {token}"}
    with TestClient(build_app()) as protected_client:
        response = protected_client.post(
            "/agents/workflow-research-b/platform-registration",
            headers=headers,
            json={
                "platformUrl": "http://127.0.0.1:8000",
                "profileId": "00000000-0000-0000-0000-000000000002",
                "heartbeatIntervalSeconds": 30,
            },
        )
        assert response.status_code == 200, response.text
        assert response.json()["heartbeat"]["matchable"] is True
        status_response = protected_client.get(
            "/agents/workflow-research-b/platform-registration",
            headers=headers,
        )
        assert status_response.status_code == 200
        assert status_response.json()["lastError"] is None

    assert observed[0][0].endswith("/heartbeat-challenge")
    heartbeat_payload = observed[1][1]
    assert heartbeat_payload["challengeId"] == "00000000-0000-0000-0000-000000000001"
    assert isinstance(heartbeat_payload["signature"], str)
    assert len(str(heartbeat_payload["signature"])) > 16


def test_runner_restores_persisted_platform_registration_after_restart(
    monkeypatch: pytest.MonkeyPatch, tmp_path
) -> None:
    token = "runner-persistent-heartbeat-token"
    store = tmp_path / "platform-registrations.json"
    monkeypatch.setenv("AGENT_RUNNER_API_TOKEN", token)
    monkeypatch.setenv("AGENT_RUNNER_SLUGS", "workflow-research-b")
    monkeypatch.setenv("AGENT_PLATFORM_REGISTRATION_STORE_PATH", str(store))
    monkeypatch.delenv("AGENT_PLATFORM_REGISTRATIONS_JSON", raising=False)
    observed: list[tuple[str, dict[str, object]]] = []

    class FakeResponse:
        def __init__(self, body: dict[str, object]):
            self.body = body

        def raise_for_status(self) -> None:
            return None

        def json(self) -> dict[str, object]:
            return self.body

    class FakeAsyncClient:
        def __init__(self, *args, **kwargs):
            pass

        async def __aenter__(self):
            return self

        async def __aexit__(self, exc_type, exc, traceback):
            return False

        async def post(self, url: str, json: dict[str, object]):
            observed.append((url, json))
            if url.endswith("/heartbeat-challenge"):
                return FakeResponse({
                    "challengeId": "00000000-0000-0000-0000-000000000011",
                    "message": "Agent heartbeat challenge\nnonce=persisted",
                })
            return FakeResponse({"accepted": True, "matchable": True})

    monkeypatch.setattr("app.main.httpx.AsyncClient", FakeAsyncClient)
    headers = {"Authorization": f"Bearer {token}"}
    registration = {
        "platformUrl": "http://127.0.0.1:8000",
        "profileId": "00000000-0000-0000-0000-000000000012",
        "heartbeatIntervalSeconds": 30,
    }

    with TestClient(build_app()) as first_runner:
        response = first_runner.post(
            "/agents/workflow-research-b/platform-registration",
            headers=headers,
            json=registration,
        )
        assert response.status_code == 200, response.text

    persisted = json.loads(store.read_text(encoding="utf-8"))
    assert persisted["workflow-research-b"]["profileId"] == registration["profileId"]
    assert "lastHeartbeatResult" not in persisted["workflow-research-b"]

    observed.clear()
    with TestClient(build_app()) as restarted_runner:
        restored = restarted_runner.get(
            "/agents/workflow-research-b/platform-registration",
            headers=headers,
        )
        assert restored.status_code == 200, restored.text
        assert restored.json()["profileId"] == registration["profileId"]
        assert restored.json()["lastError"] is None

    assert observed[0][0].endswith("/heartbeat-challenge")
    assert observed[1][0].endswith("/heartbeat")
