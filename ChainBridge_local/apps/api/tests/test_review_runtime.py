from __future__ import annotations

import json
import time
import uuid

from fastapi.testclient import TestClient

from app.integration_auth import listener_signature


def test_review_manager_runtime_registration_requires_listener_hmac(client: TestClient) -> None:
    path = "/api/v1/protocol-runtime/contracts/REVIEW_MANAGER_V2"
    payload = {
        "chainId": 31337,
        "contractAddress": "0x" + "11" * 20,
        "codeHash": "0x" + "22" * 32,
        "manifestHash": "0x" + "33" * 32,
    }
    body = json.dumps(payload, separators=(",", ":")).encode()
    timestamp = str(int(time.time()))
    nonce = str(uuid.uuid4())
    headers = {
        "Content-Type": "application/json",
        "X-Listener-Id": "protocol-worker-local",
        "X-Listener-Timestamp": timestamp,
        "X-Listener-Nonce": nonce,
        "X-Listener-Signature": listener_signature(
            "listener-local-development-only-secret",
            "PUT",
            timestamp,
            nonce,
            path,
            body,
        ),
    }
    response = client.put(path, headers=headers, content=body)
    assert response.status_code == 200, response.text
    assert response.json()["contractRole"] == "REVIEW_MANAGER_V2"
    assert response.json()["contractAddress"] == payload["contractAddress"]

    replay = client.put(path, headers=headers, content=body)
    assert replay.status_code == 409


def test_review_manager_runtime_registration_rejects_unsigned_requests(client: TestClient) -> None:
    response = client.put(
        "/api/v1/protocol-runtime/contracts/REVIEW_MANAGER_V2",
        json={
            "chainId": 31337,
            "contractAddress": "0x" + "11" * 20,
            "codeHash": "0x" + "22" * 32,
            "manifestHash": "0x" + "33" * 32,
        },
    )
    assert response.status_code == 401
