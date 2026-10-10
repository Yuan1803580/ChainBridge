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
import httpx
from eth_account import Account
from cryptography.hazmat.primitives import hashes, serialization
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


def _control_headers(secret: str, method: str, path: str, payload: dict[str, object]) -> dict[str, str]:
    timestamp = str(int(datetime.now(timezone.utc).timestamp()))
    nonce = f"control-{hashlib.sha256((path + timestamp).encode()).hexdigest()}"
    body = canonical_json(payload).decode()
    signed = "\n".join((method, timestamp, nonce, path, body))
    signature = "sha256=" + hmac.new(secret.encode(), signed.encode(), hashlib.sha256).hexdigest()
    return {
        "X-Runner-Timestamp": timestamp,
        "X-Runner-Nonce": nonce,
        "X-Runner-Signature": signature,
    }


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


def test_runtime_llm_configuration_is_protected_and_never_returns_api_key(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    outbound_requests: list[httpx.Request] = []

    def deepseek_handler(request: httpx.Request) -> httpx.Response:
        outbound_requests.append(request)
        return httpx.Response(
            200,
            json={
                "choices": [{
                    "message": {
                        "content": json.dumps({
                            "summary": "DeepSeek-compatible research result",
                            "score": 91,
                            "sources": ["https://evidence.example/test"],
                        }),
                    },
                }],
                "usage": {"prompt_tokens": 12, "completion_tokens": 8},
            },
        )

    real_async_client = httpx.AsyncClient
    transport = httpx.MockTransport(deepseek_handler)
    monkeypatch.setattr(
        "app.providers.httpx.AsyncClient",
        lambda **kwargs: real_async_client(transport=transport, **kwargs),
    )
    monkeypatch.setenv("AGENT_RUNNER_API_TOKEN", "runtime-config-token")
    with TestClient(build_app()) as protected_client:
        assert protected_client.get("/runtime/llm-config").status_code == 401
        headers = {"Authorization": "Bearer runtime-config-token"}
        configured = protected_client.post(
            "/runtime/llm-config",
            headers=headers,
            json={
                "provider": "deepseek",
                "apiKey": "deepseek-secret-value",
                "model": "deepseek-chat",
                "baseUrl": "https://deepseek.test",
            },
        )
        assert configured.status_code == 200, configured.text
        assert configured.json()["configured"] is True
        assert configured.json()["provider"] == "deepseek"
        assert "secret" not in configured.text.lower()
        assert "apiKey" not in configured.text

        tested = protected_client.post("/runtime/llm-config/test", headers=headers)
        assert tested.status_code == 200, tested.text
        assert tested.json()["ok"] is True
        assert len(outbound_requests) == 1
        request = outbound_requests[0]
        assert str(request.url) == "https://deepseek.test/chat/completions"
        assert request.headers["Authorization"] == "Bearer deepseek-secret-value"
        request_body = json.loads(request.content)
        assert request_body["model"] == "deepseek-chat"
        assert request_body["response_format"] == {"type": "json_object"}

        status_response = protected_client.get("/runtime/llm-config", headers=headers)
        assert status_response.status_code == 200
        assert "deepseek-secret-value" not in status_response.text

        local = protected_client.post(
            "/runtime/llm-config",
            headers=headers,
            json={"provider": "mock", "model": "ignored", "baseUrl": "https://api.deepseek.com"},
        )
        assert local.status_code == 200, local.text
        assert local.json()["configured"] is False


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


def test_managed_runtime_provision_invoke_and_transaction_verification(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path,
) -> None:
    runner_token = "managed-runner-api-token"
    control_secret = "managed-runner-control-secret"
    worker_secret = "managed-worker-intent-secret"
    identity_key = ec.generate_private_key(ec.SECP256K1())
    identity_public = "0x" + identity_key.public_key().public_bytes(
        serialization.Encoding.X962,
        serialization.PublicFormat.UncompressedPoint,
    ).hex()
    wallet = Account.create()
    monkeypatch.setenv("RUNNER_PROFILE", "managed")
    monkeypatch.setenv("APP_ENV", "test")
    monkeypatch.setenv("AGENT_RUNNER_API_TOKEN", runner_token)
    monkeypatch.setenv("AGENT_RUNNER_ALLOW_INSECURE_LOCAL", "false")
    monkeypatch.setenv("RUNNER_CONTROL_HMAC_SECRET", control_secret)
    monkeypatch.setenv("WORKER_INTENT_HMAC_SECRET", worker_secret)
    monkeypatch.setenv("AGENT_RUNNER_DATABASE_URL", f"sqlite:///{tmp_path / 'runner.db'}")
    monkeypatch.setenv("MANAGED_SIGNER_TOKEN", "agent-specific-signer-token")
    monkeypatch.delenv("AGENT_HOME_WALLET_PRIVATE_KEYS_JSON", raising=False)
    monkeypatch.delenv("AGENT_WALLET_POLICIES_JSON", raising=False)

    provisioning_id = "provisioning-managed-openclaw-01"
    path = f"/internal/v1/runtimes/{provisioning_id}"
    provision = {
        "runtimeId": "runtime-managed-openclaw-01",
        "slug": "managed-openclaw",
        "did": "did:agent:managed-openclaw:testnet",
        "publicKeyHex": identity_public,
        "homeWallet": wallet.address,
        "homeChainId": 421614,
        "routeKey": "SEPOLIA_TO_ARBITRUM_SEPOLIA_CCIP_BNM_V1",
        "routeVersion": 1,
        "capabilities": [{
            "capabilityId": "research",
            "pricingModel": "fixed",
            "basePrice": "1",
            "currency": "CCIP-BNM",
            "estimatedDurationSeconds": 60,
            "inputSchema": {"type": "object", "required": ["text"], "properties": {"text": {"type": "string"}}},
            "outputSchema": {"type": "object", "required": ["summary"], "properties": {"summary": {"type": "string"}}},
            "exampleInput": {"text": "test"},
            "schemaHash": "sha256:" + "11" * 32,
        }],
        "name": "Managed OpenClaw",
        "description": "Managed test runtime.",
        "version": "1.0.0",
        "executorType": "llm",
        "executorConfig": {
            "credentialRef": "env:DEEPSEEK_API_KEY",
            "model": "deepseek-chat",
            "baseUrl": "https://api.deepseek.com",
        },
        "signerUrl": "http://127.0.0.1:8020",
        "signerCredentialRef": "env:MANAGED_SIGNER_TOKEN",
        "platformRegistration": {
            "platformUrl": "http://127.0.0.1:8000",
            "profileId": "00000000-0000-0000-0000-000000000002",
            "heartbeatIntervalSeconds": 30,
        },
    }

    async def fake_execute(runtime, payload, capability_id=None):
        assert runtime.executor_config["credentialRef"] == "env:DEEPSEEK_API_KEY"
        return {"summary": str(payload["text"]).upper()}

    def sign_runtime_result(_self, payload):
        signature = identity_key.sign(canonical_json(payload), ec.ECDSA(hashes.SHA256()))
        return {"signature": base64.urlsafe_b64encode(signature).rstrip(b"=").decode()}

    def sign_transaction(_self, payload, _worker_signature):
        nonce = int(payload["observedPendingNonce"])
        signed = wallet.sign_transaction({
            "type": 2,
            "chainId": int(payload["chainId"]),
            "to": payload["to"],
            "data": payload["data"],
            "value": int(payload["value"]),
            "nonce": nonce,
            "gas": int(payload["gasLimit"]),
            "maxFeePerGas": int(payload["maxFeePerGas"]),
            "maxPriorityFeePerGas": int(payload["maxPriorityFeePerGas"]),
        })
        return {
            "assignedNonce": nonce,
            "rawTransaction": signed.raw_transaction.hex(),
            "transactionHash": signed.hash.hex(),
            "status": "SIGNED",
        }

    def sign_identity_message(_self, message):
        signature = identity_key.sign(message.encode(), ec.ECDSA(hashes.SHA256()))
        return {"signature": base64.urlsafe_b64encode(signature).rstrip(b"=").decode()}

    observed_heartbeats: list[tuple[str, dict[str, object]]] = []

    class FakeHeartbeatResponse:
        def __init__(self, body):
            self.body = body

        def raise_for_status(self):
            return None

        def json(self):
            return self.body

    class FakeHeartbeatClient:
        def __init__(self, *args, **kwargs):
            pass

        async def __aenter__(self):
            return self

        async def __aexit__(self, exc_type, exc, traceback):
            return False

        async def post(self, url, json):
            observed_heartbeats.append((url, json))
            if url.endswith("/heartbeat-challenge"):
                return FakeHeartbeatResponse({
                    "challengeId": "00000000-0000-0000-0000-000000000003",
                    "message": "Agent heartbeat challenge\nnonce=managed-test",
                })
            return FakeHeartbeatResponse({"accepted": True, "matchable": True})

    monkeypatch.setattr("app.main.execute_runtime", fake_execute)
    monkeypatch.setattr("app.main.SidecarClient.sign_runtime_result", sign_runtime_result)
    monkeypatch.setattr("app.main.SidecarClient.sign_transaction", sign_transaction)
    monkeypatch.setattr("app.main.SidecarClient.sign_identity_message", sign_identity_message)
    monkeypatch.setattr("app.main.httpx.AsyncClient", FakeHeartbeatClient)
    with TestClient(build_app()) as managed:
        created = managed.put(path, json=provision, headers=_control_headers(control_secret, "PUT", path, provision))
        assert created.status_code == 200, created.text
        assert created.json()["status"] == "PROVISIONED"
        assert created.json()["heartbeatConfigured"] is True
        assert managed.app.state.platform_registrations["managed-openclaw"]["profileId"] == provision["platformRegistration"]["profileId"]
        manifest = managed.get("/agents/managed-openclaw/manifest").json()
        assert manifest["did"] == provision["did"]
        assert manifest["capabilities"][0]["capabilityId"] == "research"

        runner_headers = {"Authorization": f"Bearer {runner_token}"}
        heartbeat_registration = managed.post(
            "/agents/managed-openclaw/platform-registration",
            headers=runner_headers,
            json=provision["platformRegistration"],
        )
        assert heartbeat_registration.status_code == 200, heartbeat_registration.text
        assert heartbeat_registration.json()["heartbeat"]["matchable"] is True
        assert observed_heartbeats[1][1]["signature"]
        invoked = managed.post(
            "/agents/managed-openclaw/invoke",
            headers=runner_headers,
            json={"requestId": "managed-request-01", "input": {"text": "deepseek result"}},
        )
        assert invoked.status_code == 200, invoked.text
        assert invoked.json()["output"] == {"summary": "DEEPSEEK RESULT"}

        now = datetime.now(timezone.utc)
        intent = {
            "intentId": "intent-managed-transaction-01",
            "requestId": "request-managed-transaction-01",
            "runtimeId": provision["runtimeId"],
            "did": provision["did"],
            "purpose": "submit_result",
            "chainId": 421614,
            "to": "0x" + "12" * 20,
            "data": "0x12345678",
            "value": "0",
            "gasLimit": 180000,
            "maxFeePerGas": "2000000000",
            "maxPriorityFeePerGas": "100000000",
            "observedPendingNonce": 7,
            "observedBlockNumber": 123,
            "observedAt": now.isoformat().replace("+00:00", "Z"),
            "routeKey": provision["routeKey"],
            "routeVersion": 1,
            "policyVersion": "agent-wallet-v1",
            "expiresAt": (now + timedelta(minutes=1)).isoformat().replace("+00:00", "Z"),
        }
        worker_signature = "sha256=" + hmac.new(worker_secret.encode(), canonical_json(intent), hashlib.sha256).hexdigest()
        signed = managed.post(
            "/agents/managed-openclaw/wallet-transactions/sign",
            headers={**runner_headers, "X-Worker-Intent-Signature": worker_signature},
            json=intent,
        )
        assert signed.status_code == 200, signed.text
        assert signed.json()["assignedNonce"] == 7
        assert Account.recover_transaction(signed.json()["rawTransaction"]).lower() == wallet.address.lower()
