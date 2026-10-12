from __future__ import annotations

from datetime import timedelta

from app.models import AuthChallenge, utcnow
from app.security import sign_demo_message


def test_did_challenge_authentication_and_replay_rejection(client):
    challenge_response = client.post(
        "/api/v1/auth/challenges",
        json={"did": "did:local:campus-requester", "audience": "agent-market"},
    )
    assert challenge_response.status_code == 201
    challenge = challenge_response.json()
    signature = sign_demo_message("campus-requester", challenge["message"])
    payload = {
        "challengeId": challenge["id"],
        "did": "did:local:campus-requester",
        "audience": "agent-market",
        "signature": signature,
    }

    verified = client.post("/api/v1/auth/verify", json=payload)
    assert verified.status_code == 200
    assert verified.json()["agent"]["did"] == "did:local:campus-requester"
    token = verified.json()["accessToken"]
    assert client.get("/api/v1/me", headers={"Authorization": f"Bearer {token}"}).status_code == 200

    replay = client.post("/api/v1/auth/verify", json=payload)
    assert replay.status_code == 401
    assert "already been used" in replay.json()["error"]["message"]


def test_tampered_signature_and_audience_are_rejected(client):
    challenge = client.post(
        "/api/v1/auth/challenges",
        json={"did": "did:local:text-summary", "audience": "agent-market"},
    ).json()
    signature = sign_demo_message("hash-audit", challenge["message"])
    response = client.post(
        "/api/v1/auth/verify",
        json={
            "challengeId": challenge["id"],
            "did": "did:local:text-summary",
            "audience": "agent-market",
            "signature": signature,
        },
    )
    assert response.status_code == 401

    correct_signature = sign_demo_message("text-summary", challenge["message"])
    wrong_audience = client.post(
        "/api/v1/auth/verify",
        json={
            "challengeId": challenge["id"],
            "did": "did:local:text-summary",
            "audience": "other-service",
            "signature": correct_signature,
        },
    )
    assert wrong_audience.status_code == 401

    unsupported = client.post(
        "/api/v1/auth/challenges",
        json={"did": "did:local:text-summary", "audience": "other-service"},
    )
    assert unsupported.status_code == 422


def test_expired_challenge_is_rejected(client):
    challenge = client.post(
        "/api/v1/auth/challenges",
        json={"did": "did:local:data-statistics", "audience": "agent-market"},
    ).json()
    with client.app.state.session_factory() as session:
        record = session.get(AuthChallenge, challenge["id"])
        record.expires_at = utcnow() - timedelta(seconds=1)
        session.commit()
    signature = sign_demo_message("data-statistics", challenge["message"])
    response = client.post(
        "/api/v1/auth/verify",
        json={
            "challengeId": challenge["id"],
            "did": "did:local:data-statistics",
            "audience": "agent-market",
            "signature": signature,
        },
    )
    assert response.status_code == 401
    assert "expired" in response.json()["error"]["message"]


def test_private_keys_are_not_exposed(client):
    identities = client.get("/api/v1/dev/identities").json()
    serialized = str(identities).lower()
    assert "private" not in serialized
    agent_id = identities["items"][0]["id"]
    details = client.get(f"/api/v1/agents/{agent_id}").json()
    assert "operationalPublicKey" in details
    assert "privateKey" not in details
