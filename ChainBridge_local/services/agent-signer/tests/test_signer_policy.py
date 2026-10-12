from __future__ import annotations

import json
from datetime import datetime, timedelta, timezone

from eth_account import Account
from fastapi.testclient import TestClient

from app.main import Settings, build_app


def configured_client(tmp_path):
    slug = "test-agent"
    wallet_key = "0x" + "2" * 64
    wallet = Account.from_key(wallet_key)
    secrets_path = tmp_path / "secrets.json"
    secrets_path.write_text(json.dumps({
        "agentSlug": slug,
        "identityPrivateKey": "1" * 64,
        "walletPrivateKey": wallet_key,
    }), encoding="utf-8")
    policy_path = tmp_path / "policy.json"
    policy_path.write_text(json.dumps({slug: {
        "did": f"did:local:{slug}",
        "chainId": 31339,
        "homeWallet": wallet.address,
        "allowedContracts": {
            "0x1111111111111111111111111111111111111111": {
                "selectors": ["0x12345678"]
            }
        },
        "maxValueWei": "0",
        "maxGas": 500000,
        "maxGasPriceWei": "100000000000",
        "maxTtlSeconds": 300,
    }}), encoding="utf-8")
    app = build_app(Settings(
        agent_slug=slug,
        chain_id=31339,
        rpc_url="http://127.0.0.1:1",
        secrets_path=secrets_path,
        policy_path=policy_path,
        database_path=tmp_path / "audit.db",
        api_token="test-sidecar-token",
    ))
    return TestClient(app), {"Authorization": "Bearer test-sidecar-token"}


def result_payload() -> dict[str, object]:
    return {
        "protocolVersion": "2.0",
        "agentId": "test-agent",
        "capabilityId": "research",
        "completedAt": datetime.now(timezone.utc).isoformat(),
        "did": "did:local:test-agent",
        "outputHash": "sha256:" + "a" * 64,
        "requestId": "request-1",
    }


def test_signer_accepts_only_the_v2_result_field_contract(tmp_path):
    client, headers = configured_client(tmp_path)
    response = client.post(
        "/v1/sign/payload",
        headers=headers,
        json={"kind": "result", "payload": result_payload()},
    )
    assert response.status_code == 200
    assert response.json()["algorithm"] == "ES256K"

    changed = {**result_payload(), "transferTo": "0x" + "f" * 40}
    rejected = client.post(
        "/v1/sign/payload",
        headers=headers,
        json={"kind": "result", "payload": changed},
    )
    assert rejected.status_code == 422
    assert rejected.json()["error"]["code"] == "signer_payload_fields_rejected"


def test_signer_rejects_unknown_contract_before_contacting_rpc(tmp_path):
    client, headers = configured_client(tmp_path)
    attempt_bytes = "ab" * 32
    response = client.post(
        "/v1/sign/transaction",
        headers=headers,
        json={
            "agentId": "test-agent",
            "chainId": 31339,
            "to": "0x2222222222222222222222222222222222222222",
            "data": "0x12345678" + attempt_bytes,
            "valueWei": "0",
            "nonce": 0,
            "gas": 100000,
            "gasPriceWei": "1000000000",
            "attemptId": "0x" + attempt_bytes,
            "attemptIdBytes32": "0x" + attempt_bytes,
            "requestNonce": "0x" + "c" * 64,
            "expiresAt": (datetime.now(timezone.utc) + timedelta(minutes=1)).isoformat(),
        },
    )
    assert response.status_code == 422
    assert response.json()["error"]["code"] == "signer_contract_rejected"


def test_signer_never_exposes_private_key_material(tmp_path):
    client, headers = configured_client(tmp_path)
    response = client.get("/v1/public", headers=headers)
    serialized = json.dumps(response.json()).lower()
    assert response.status_code == 200
    assert "private" not in serialized
    assert "walletprivatekey" not in serialized
