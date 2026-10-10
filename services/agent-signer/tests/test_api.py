from __future__ import annotations

import hashlib
import hmac
import json
import os
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timedelta, timezone

from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric import ec
from eth_account import Account
from eth_utils import is_checksum_address, keccak
from fastapi.testclient import TestClient

os.environ["APP_ENV"] = "test"

from app.main import TransactionIntent, build_app  # noqa: E402
from app.security import canonical_json  # noqa: E402


TOKEN = "agent-specific-test-token-value"
WORKER_SECRET = "worker-intent-test-secret"
RUNTIME_ID = "runtime-openclaw-01"
DID = "did:agent:openclaw-research-01:local"
ROUTE = "SEPOLIA_TO_ARBITRUM_SEPOLIA_CCIP_BNM_V1"
BOARD = "0x" + "12" * 20
SELECTOR = "0x" + keccak(text="submitResult(bytes32,bytes32,bytes32,bytes32)")[:4].hex()


def _bytes32(byte: str) -> str:
    return "0x" + byte * 64


def _setup(tmp_path):
    identity = ec.generate_private_key(ec.SECP256K1())
    identity_path = tmp_path / "identity.pem"
    identity_path.write_bytes(identity.private_bytes(
        serialization.Encoding.PEM,
        serialization.PrivateFormat.PKCS8,
        serialization.NoEncryption(),
    ))
    wallet = Account.create()
    wallet_path = tmp_path / "wallet.txt"
    wallet_path.write_text(wallet.key.hex(), encoding="utf-8")
    config_path = tmp_path / "signer.json"
    config_path.write_text(json.dumps({"agents": [{
        "runtimeId": RUNTIME_ID,
        "agentSlug": RUNTIME_ID,
        "did": DID,
        "homeWallet": wallet.address,
        "identityKeyFile": str(identity_path),
        "walletKeyFile": str(wallet_path),
        "credentialSha256": hashlib.sha256(TOKEN.encode()).hexdigest(),
        "routeKey": ROUTE,
        "routeVersion": 1,
        "allowedContracts": [BOARD],
        "maxGasLimit": 500000,
        "maxFeePerGas": "100000000000",
        "maxPriorityFeePerGas": "5000000000",
        "maxTotalFeeWei": "50000000000000000",
    }]}), encoding="utf-8")
    os.environ["AGENT_SIGNER_CONFIG_FILE"] = str(config_path)
    os.environ["AGENT_SIGNER_DATABASE_URL"] = f"sqlite:///{tmp_path / 'signer.db'}"
    os.environ["WORKER_INTENT_HMAC_SECRET"] = WORKER_SECRET
    os.environ.pop("SIDECAR_STORAGE_KEY", None)
    return TestClient(build_app()), wallet


def _worker_headers(payload: dict) -> dict[str, str]:
    signature = hmac.new(WORKER_SECRET.encode(), canonical_json(payload), hashlib.sha256).hexdigest()
    return {"Authorization": f"Bearer {TOKEN}", "X-Worker-Intent-Signature": f"sha256={signature}"}


def _headers(payload: dict) -> dict[str, str]:
    TransactionIntent.model_validate(payload)
    return _worker_headers(payload)


def _agent() -> dict[str, str]:
    return {"runtimeId": RUNTIME_ID, "did": DID}


def _canonical(client: TestClient, attempt="0x" + "22" * 32):
    payload = {
        **_agent(), "workflowId": "workflow-1", "attemptId": attempt,
        "resultMessageId": "0x" + "11" * 32,
        "resultHash": "0x" + "33" * 32,
        "proofHash": "0x" + "44" * 32,
        "routeKey": ROUTE, "routeVersion": 1,
    }
    response = client.post("/v1/sign/canonical-result", json=payload, headers={"Authorization": f"Bearer {TOKEN}"})
    assert response.status_code == 200, response.text
    return payload


def _intent(canonical: dict, request_id: str, observed_nonce: int) -> dict:
    args = "".join(canonical[field][2:] for field in ("resultMessageId", "attemptId", "resultHash", "proofHash"))
    now = datetime.now(timezone.utc)
    return {
        **_agent(), "intentId": f"intent-{request_id}", "requestId": request_id,
        "purpose": "submit_result", "chainId": 421614, "to": BOARD,
        "data": SELECTOR + args, "value": "0", "gasLimit": 180000,
        "maxFeePerGas": "2000000000", "maxPriorityFeePerGas": "100000000",
        "observedPendingNonce": observed_nonce, "observedBlockNumber": 123,
        "observedAt": now.isoformat(), "routeKey": ROUTE, "routeVersion": 1,
        "policyVersion": "agent-wallet-v1", "expiresAt": (now + timedelta(minutes=1)).isoformat(),
    }


def test_zero_rpc_signing_and_idempotency(tmp_path):
    client, wallet = _setup(tmp_path)
    assert client.get("/health").json()["rpcConnected"] is False
    canonical = _canonical(client)
    intent = _intent(canonical, "request-0001", 7)
    response = client.post("/v1/sign/transaction", json=intent, headers=_headers(intent))
    assert response.status_code == 200, response.text
    signed = response.json()
    assert signed["assignedNonce"] == 7
    assert Account.recover_transaction(signed["rawTransaction"]).lower() == wallet.address.lower()
    replay = client.post("/v1/sign/transaction", json=intent, headers=_headers(intent)).json()
    assert replay["rawTransaction"] == signed["rawTransaction"]
    assert replay["assignedNonce"] == 7
    changed = {**intent, "observedPendingNonce": 8}
    rejected = client.post("/v1/sign/transaction", json=changed, headers=_headers(changed))
    assert rejected.status_code == 409
    assert "different transaction intent" in rejected.text


def test_concurrent_requests_reserve_distinct_nonces(tmp_path):
    client, _ = _setup(tmp_path)
    requests = []
    for index in range(4):
        canonical = _canonical(client, "0x" + f"{index + 1:02x}" * 32)
        requests.append(_intent(canonical, f"request-{index:04d}", 3))
    def sign(payload):
        result = client.post("/v1/sign/transaction", json=payload, headers=_headers(payload))
        assert result.status_code == 200, result.text
        return result.json()["assignedNonce"]
    with ThreadPoolExecutor(max_workers=4) as pool:
        nonces = list(pool.map(sign, requests))
    assert sorted(nonces) == [3, 4, 5, 6]


def test_rejects_wrong_result_and_fee_over_limit(tmp_path):
    client, _ = _setup(tmp_path)
    canonical = _canonical(client)
    intent = _intent(canonical, "request-wrong", 0)
    intent["data"] = intent["data"][:-64] + "55" * 32
    response = client.post("/v1/sign/transaction", json=intent, headers=_headers(intent))
    assert response.status_code == 422
    intent = _intent(canonical, "request-fee", 0)
    intent["maxFeePerGas"] = "100000000001"
    response = client.post("/v1/sign/transaction", json=intent, headers=_headers(intent))
    assert response.status_code == 422


def test_replace_and_cancel_reuse_original_nonce_and_reject_confirmed_transactions(tmp_path):
    client, wallet = _setup(tmp_path)
    canonical = _canonical(client)
    original = _intent(canonical, "request-original", 4)
    signed_original = client.post("/v1/sign/transaction", json=original, headers=_headers(original)).json()

    now = datetime.now(timezone.utc)
    replacement = {
        **original,
        "intentId": "intent-request-replace",
        "requestId": "request-replace",
        "purpose": "replace",
        "maxFeePerGas": "2250000000",
        "maxPriorityFeePerGas": "112500000",
        "observedAt": now.isoformat(),
        "expiresAt": (now + timedelta(minutes=1)).isoformat(),
        "replacesRequestId": original["requestId"],
        "originalStatus": "PENDING",
    }
    signed_replacement_response = client.post("/v1/sign/transaction", json=replacement, headers=_headers(replacement))
    assert signed_replacement_response.status_code == 200, signed_replacement_response.text
    signed_replacement = signed_replacement_response.json()
    assert signed_replacement["assignedNonce"] == signed_original["assignedNonce"] == 4
    assert Account.recover_transaction(signed_replacement["rawTransaction"]).lower() == wallet.address.lower()

    invalid = {**replacement, "intentId": "intent-request-invalid", "requestId": "request-invalid", "data": "0x"}
    invalid_response = client.post("/v1/sign/transaction", json=invalid, headers=_headers(invalid))
    assert invalid_response.status_code == 422

    cancel = {
        **replacement,
        "intentId": "intent-request-cancel",
        "requestId": "request-cancel",
        "purpose": "cancel",
        "to": wallet.address,
        "data": "0x",
        "gasLimit": 21000,
        "maxFeePerGas": "2531250000",
        "maxPriorityFeePerGas": "126562500",
        "replacesRequestId": replacement["requestId"],
        "originalStatus": "DROPPED",
    }
    signed_cancel_response = client.post("/v1/sign/transaction", json=cancel, headers=_headers(cancel))
    assert signed_cancel_response.status_code == 200, signed_cancel_response.text
    assert signed_cancel_response.json()["assignedNonce"] == 4

    status = {
        **_agent(),
        "status": "CONFIRMED",
        "transactionHash": signed_replacement["transactionHash"],
        "observedPendingNonce": 5,
        "observedBlockNumber": 150,
        "observedAt": datetime.now(timezone.utc).isoformat(),
    }
    confirmed = client.post(
        f"/v1/transactions/{replacement['requestId']}/chain-status",
        json=status,
        headers=_worker_headers(status),
    )
    assert confirmed.status_code == 200, confirmed.text
    after_confirm = {**cancel, "intentId": "intent-after-confirm", "requestId": "request-after-confirm"}
    rejected = client.post("/v1/sign/transaction", json=after_confirm, headers=_headers(after_confirm))
    assert rejected.status_code == 409


def test_identity_prefix_is_enforced(tmp_path):
    client, _ = _setup(tmp_path)
    headers = {"Authorization": f"Bearer {TOKEN}"}
    denied = client.post("/v1/sign/identity-message", json={**_agent(), "message": "Please sign arbitrary data that is definitely long enough"}, headers=headers)
    assert denied.status_code == 422
    allowed = client.post("/v1/sign/identity-message", json={**_agent(), "message": "Agent heartbeat challenge\nnonce=1234567890"}, headers=headers)
    assert allowed.status_code == 200
    login = client.post(
        "/v1/sign/identity-message",
        json={**_agent(), "message": "Agent Market DID Authentication\nDID: did:agent:test\nNonce: 1234567890"},
        headers=headers,
    )
    assert login.status_code == 200


def _provision_client(tmp_path, monkeypatch):
    config_path = tmp_path / "managed-signer.json"
    key_dir = tmp_path / "keys"
    monkeypatch.setenv("AGENT_SIGNER_CONFIG_FILE", str(config_path))
    monkeypatch.setenv("SIDECAR_KEY_DIR", str(key_dir))
    monkeypatch.setenv("SIDECAR_PROVISIONING_TOKEN", "dedicated-provisioning-token")
    monkeypatch.setenv("AGENT_SIGNER_DATABASE_URL", f"sqlite:///{tmp_path / 'provision-signer.db'}")
    monkeypatch.setenv("WORKER_INTENT_HMAC_SECRET", WORKER_SECRET)
    monkeypatch.delenv("SIDECAR_STORAGE_KEY", raising=False)
    return TestClient(build_app()), config_path, key_dir


def _provision_payload(runtime_id="runtime-self-service-01"):
    return {
        "runtimeId": runtime_id,
        "agentSlug": "self-service-01",
        "did": "did:agent:self-service-01:testnet",
        "homeChainId": 421614,
        "routeKey": ROUTE,
        "routeVersion": 1,
        "allowedContracts": [BOARD],
    }


def test_provision_generates_private_files_without_returning_secrets(tmp_path, monkeypatch):
    client, config_path, key_dir = _provision_client(tmp_path, monkeypatch)
    payload = _provision_payload()
    denied = client.post("/v1/provision", json=payload, headers={"Authorization": "Bearer wrong-token"})
    assert denied.status_code == 401
    remote_client = TestClient(client.app, client=("203.0.113.10", 50000))
    remote = remote_client.post(
        "/v1/provision",
        json=payload,
        headers={"Authorization": "Bearer dedicated-provisioning-token"},
    )
    assert remote.status_code == 403

    created = client.post(
        "/v1/provision",
        json=payload,
        headers={"Authorization": "Bearer dedicated-provisioning-token"},
    )
    assert created.status_code == 201, created.text
    public = created.json()
    assert public["runtimeId"] == payload["runtimeId"]
    assert public["identityPublicKey"].startswith("es256k:04")
    assert is_checksum_address(public["homeWallet"])
    assert public["signerCredentialRef"].startswith("file:")
    serialized = json.dumps(public)
    assert "credentialSha256" not in serialized
    assert "identityKeyFile" not in serialized
    assert "walletKeyFile" not in serialized
    assert "dedicated-provisioning-token" not in serialized

    stored = json.loads(config_path.read_text(encoding="utf-8"))["agents"]
    assert len(stored) == 1
    assert stored[0]["homeWallet"] == public["homeWallet"]
    assert stored[0]["homeChainId"] == 421614
    credential_path = key_dir / payload["runtimeId"] / "signer.credential"
    credential = credential_path.read_text(encoding="utf-8").strip()
    assert hashlib.sha256(credential.encode()).hexdigest() == stored[0]["credentialSha256"]

    replay = client.post(
        "/v1/provision",
        json=payload,
        headers={"Authorization": "Bearer dedicated-provisioning-token"},
    )
    assert replay.status_code == 201, replay.text
    assert replay.json()["idempotent"] is True
    assert replay.json()["homeWallet"] == public["homeWallet"]
    assert len(json.loads(config_path.read_text(encoding="utf-8"))["agents"]) == 1

    signed = client.post(
        "/v1/sign/identity-message",
        json={
            "runtimeId": payload["runtimeId"],
            "did": payload["did"],
            "message": "Agent heartbeat challenge\nnonce=1234567890",
        },
        headers={"Authorization": f"Bearer {credential}"},
    )
    assert signed.status_code == 200, signed.text


def test_provision_rejects_path_traversal_conflicts_and_is_concurrently_idempotent(tmp_path, monkeypatch):
    client, config_path, _ = _provision_client(tmp_path, monkeypatch)
    traversal = client.post(
        "/v1/provision",
        json=_provision_payload("../outside"),
        headers={"Authorization": "Bearer dedicated-provisioning-token"},
    )
    assert traversal.status_code == 422

    payload = _provision_payload("runtime-concurrent-01")
    def provision():
        return client.post(
            "/v1/provision",
            json=payload,
            headers={"Authorization": "Bearer dedicated-provisioning-token"},
        )
    with ThreadPoolExecutor(max_workers=4) as pool:
        responses = list(pool.map(lambda _: provision(), range(4)))
    assert all(response.status_code == 201 for response in responses)
    assert len({response.json()["homeWallet"] for response in responses}) == 1
    assert len(json.loads(config_path.read_text(encoding="utf-8"))["agents"]) == 1

    conflict_payload = {**payload, "did": "did:agent:another-self-service:testnet"}
    conflict = client.post(
        "/v1/provision",
        json=conflict_payload,
        headers={"Authorization": "Bearer dedicated-provisioning-token"},
    )
    assert conflict.status_code == 409
