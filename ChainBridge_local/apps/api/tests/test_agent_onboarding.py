from __future__ import annotations

from datetime import datetime, timedelta, timezone

from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import ec
from eth_account import Account
from eth_account.messages import encode_defunct
from sqlalchemy import select

from .conftest import auth_headers
from app.models import Agent
from app.workflow_api import _agent_endpoint_base
from app.workflow_models import ChainRoute, ExternalAgentOnboardingChallenge, WorkflowAgentProfile


def _identity() -> tuple[ec.EllipticCurvePrivateKey, str]:
    private_key = ec.generate_private_key(ec.SECP256K1())
    public_hex = private_key.public_key().public_bytes(
        encoding=serialization.Encoding.X962,
        format=serialization.PublicFormat.UncompressedPoint,
    ).hex()
    return private_key, f"es256k:{public_hex}"


def _register(
    client,
    *,
    agent_key: str = "Partner-BTK-Research",
    payout_asset: str = "BTK",
    preferred_chain_id: int | None = None,
    capabilities: list[str] | None = None,
):
    identity_key, public_key = _identity()
    wallet = Account.create()
    payload = {
        "ownerName": "External Operator",
        "ownerEmail": "operator@example.com",
        "organization": "Partner Lab",
        "agentName": "Partner Research Agent",
        "agentKey": agent_key,
        "endpoint": "https://partner.example.com/agent/run",
        "publicKey": public_key,
        "llmProvider": "deepseek",
        "llmModel": "deepseek-chat",
        "systemPrompt": "Answer with auditable evidence.",
        "capabilities": capabilities or ["research", "extraction"],
        "payoutAsset": payout_asset,
        "homeWallet": wallet.address,
        "priceFloor": "4.20",
    }
    if preferred_chain_id is not None:
        payload["preferredChainId"] = preferred_chain_id
    response = client.post("/api/v1/agent-onboarding/registrations", json=payload)
    return response, identity_key, wallet


def _verify_ownership(client, response, identity_key, wallet):
    profile_id = response.json()["profile"]["id"]
    challenge = client.post(f"/api/v1/agent-onboarding/registrations/{profile_id}/challenge")
    assert challenge.status_code == 201, challenge.text
    body = challenge.json()
    verified = client.post(
        f"/api/v1/agent-onboarding/registrations/{profile_id}/verify",
        json={
            "challengeId": body["challengeId"],
            "identitySignature": identity_key.sign(body["message"].encode(), ec.ECDSA(hashes.SHA256())).hex(),
            "walletSignature": Account.sign_message(encode_defunct(text=body["message"]), wallet.key).signature.hex(),
        },
    )
    assert verified.status_code == 200, verified.text
    assert verified.json()["profile"]["agent"]["erc8004Registration"]["admissionStatus"] == "PENDING_PLATFORM_REVIEW"
    return profile_id


def test_agent_endpoint_base_accepts_runtime_paths():
    assert _agent_endpoint_base("https://agent.example.com/invoke") == "https://agent.example.com"
    assert _agent_endpoint_base("https://agent.example.com/chat/") == "https://agent.example.com"
    assert _agent_endpoint_base("https://agent.example.com") == "https://agent.example.com"


def test_external_llm_agent_onboarding_starts_pending_and_resolves_route(client):
    response, _, _ = _register(client)
    assert response.status_code == 201, response.text
    body = response.json()
    assert body["admissionStatus"] == "PENDING_OWNERSHIP"
    assert body["chainStatus"] == "READY"
    assert body["agent"]["status"] == "pending"
    assert body["agent"]["roles"] == ["provider"]
    assert body["profile"]["chainId"] == 31338
    assert body["profile"]["agentKey"] == "Partner-BTK-Research"
    assert body["profile"]["active"] is False
    assert body["profile"]["availability"] == "OFFLINE"
    assert body["profile"]["gasReady"] is False
    assert body["profile"]["trustScore"] == 0
    assert body["profile"]["successRateBps"] == 0
    assert body["profile"]["credentialStatus"] == "UNCONFIGURED"
    assert body["chainResolution"]["routeKey"] == "A_TO_B"
    assert body["chainResolution"]["payoutAsset"] == "BTK"

    directory = client.get("/api/v1/workflow-agents").json()["items"]
    onboarded = next(item for item in directory if item["agentKey"] == "Partner-BTK-Research")
    assert onboarded["agent"]["displayName"] == "Partner Research Agent"
    assert onboarded["metadata"]["llm"]["model"] == "deepseek-chat"
    assert "systemPrompt" not in onboarded["metadata"]["llm"]
    assert "ownerEmail" not in onboarded["metadata"]["onboarding"]


def test_external_agent_requires_ownership_proof_before_admin_activation(client, monkeypatch):
    response, identity_key, wallet = _register(client, agent_key="Partner-Verified-B")
    assert response.status_code == 201, response.text
    profile_id = response.json()["profile"]["id"]

    challenge = client.post(f"/api/v1/agent-onboarding/registrations/{profile_id}/challenge")
    assert challenge.status_code == 201, challenge.text
    challenge_body = challenge.json()
    message = challenge_body["message"].encode("utf-8")
    identity_signature = identity_key.sign(message, ec.ECDSA(hashes.SHA256())).hex()
    wallet_signature = Account.sign_message(
        encode_defunct(text=challenge_body["message"]),
        wallet.key,
    ).signature.hex()

    verified = client.post(
        f"/api/v1/agent-onboarding/registrations/{profile_id}/verify",
        json={
            "challengeId": challenge_body["challengeId"],
            "identitySignature": identity_signature,
            "walletSignature": wallet_signature,
        },
    )
    assert verified.status_code == 200, verified.text
    assert verified.json()["admissionStatus"] == "PENDING_PLATFORM_REVIEW"
    assert verified.json()["profile"]["credentialStatus"] == "REFERENCE_ONLY"
    assert verified.json()["profile"]["active"] is False

    blocked_activation = client.post(
        f"/api/v1/agent-onboarding/registrations/{profile_id}/activate",
        headers=auth_headers(client, "platform-admin", "activation-blocked"),
        json={"notes": "Checks have not run."},
    )
    assert blocked_activation.status_code == 409

    monkeypatch.setattr(
        "app.workflow_api._run_agent_technical_checks",
        lambda session, profile, agent, token: {
            "healthCheckPassed": True,
            "manifestCheckPassed": True,
            "testTaskPassed": True,
            "gasReadyVerified": True,
            "checkedAt": __import__("datetime").datetime.now(__import__("datetime").timezone.utc).isoformat().replace("+00:00", "Z"),
            "endpointLatencyMs": 12,
            "manifestHash": "0x" + "11" * 32,
            "testResultHash": "sha256:" + "22" * 32,
            "gasBalanceWei": "1000000000000000000",
            "routeKey": "A_TO_B",
            "routeVersion": 1,
        },
    )
    checked = client.post(
        f"/api/v1/agent-onboarding/registrations/{profile_id}/checks",
        headers=auth_headers(client, "platform-admin", "technical-checks"),
        json={"runtimeAccessToken": "test-runtime-token-value"},
    )
    assert checked.status_code == 200, checked.text

    rechecked = client.post(
        f"/api/v1/agent-onboarding/registrations/{profile_id}/checks",
        headers=auth_headers(client, "platform-admin", "technical-recheck"),
        json={},
    )
    assert rechecked.status_code == 200, rechecked.text
    assert rechecked.json()["checksPassed"] is True

    activated = client.post(
        f"/api/v1/agent-onboarding/registrations/{profile_id}/activate",
        headers=auth_headers(client, "platform-admin", "activation-ok"),
        json={"notes": "Platform checks passed."},
    )
    assert activated.status_code == 200, activated.text
    profile = activated.json()["profile"]
    assert activated.json()["admissionStatus"] == "APPROVED"
    assert activated.json()["operationalStatus"] == "ACTIVE"
    assert profile["active"] is True
    assert profile["availability"] == "OFFLINE"
    assert profile["gasReady"] is True
    assert profile["credentialStatus"] == "ACTIVE"
    assert profile["trustScore"] == 75
    assert profile["successRateBps"] == 0
    assert profile["metadata"]["reputation"]["sampleCount"] == 0


def test_external_agent_can_be_activated_before_gas_funding(client, monkeypatch):
    response, identity_key, wallet = _register(client, agent_key="Partner-Awaiting-Gas-B")
    profile_id = _verify_ownership(client, response, identity_key, wallet)
    monkeypatch.setattr(
        "app.workflow_api._run_agent_technical_checks",
        lambda session, profile, agent, token: {
            "healthCheckPassed": True,
            "manifestCheckPassed": True,
            "testTaskPassed": True,
            "gasReadyVerified": False,
            "checkedAt": datetime.now(timezone.utc).isoformat().replace("+00:00", "Z"),
            "endpointLatencyMs": 12,
            "manifestHash": "0x" + "11" * 32,
            "testResultHash": "sha256:" + "22" * 32,
            "gasBalanceWei": "0",
            "routeKey": "A_TO_B",
            "routeVersion": 1,
        },
    )
    admin = auth_headers(client, "platform-admin", "awaiting-gas-check")
    checked = client.post(
        f"/api/v1/agent-onboarding/registrations/{profile_id}/checks",
        headers=admin,
        json={"runtimeAccessToken": "test-runtime-token-value"},
    )
    assert checked.status_code == 200, checked.text
    assert checked.json()["gasReady"] is False
    activated = client.post(
        f"/api/v1/agent-onboarding/registrations/{profile_id}/activate",
        headers={**admin, "Idempotency-Key": "awaiting-gas-activate"},
        json={"notes": "Platform admission approved before Gas funding."},
    )
    assert activated.status_code == 200, activated.text
    assert activated.json()["admissionStatus"] == "APPROVED"
    assert activated.json()["operationalStatus"] == "ACTIVE_AWAITING_GAS"
    profile = activated.json()["profile"]
    assert profile["active"] is True
    assert profile["gasReady"] is False
    assert profile["credentialStatus"] == "ACTIVE"


def _activate_awaiting_gas(client, monkeypatch, *, agent_key: str):
    response, identity_key, wallet = _register(client, agent_key=agent_key)
    profile_id = _verify_ownership(client, response, identity_key, wallet)
    monkeypatch.setattr(
        "app.workflow_api._run_agent_technical_checks",
        lambda session, profile, agent, token: {
            "healthCheckPassed": True,
            "manifestCheckPassed": True,
            "testTaskPassed": True,
            "gasReadyVerified": False,
            "checkedAt": datetime.now(timezone.utc).isoformat().replace("+00:00", "Z"),
            "endpointLatencyMs": 12,
            "manifestHash": "0x" + "11" * 32,
            "testResultHash": "sha256:" + "22" * 32,
            "gasBalanceWei": "0",
            "routeKey": "A_TO_B",
            "routeVersion": 1,
        },
    )
    admin = auth_headers(client, "platform-admin", f"{agent_key}-check")
    checked = client.post(
        f"/api/v1/agent-onboarding/registrations/{profile_id}/checks",
        headers=admin,
        json={"runtimeAccessToken": "test-runtime-token-value"},
    )
    assert checked.status_code == 200, checked.text
    activated = client.post(
        f"/api/v1/agent-onboarding/registrations/{profile_id}/activate",
        headers={**admin, "Idempotency-Key": f"{agent_key}-activate"},
        json={"notes": "Approved pending local Gas funding."},
    )
    assert activated.status_code == 200, activated.text
    return profile_id, identity_key


def test_gas_check_updates_active_agent_after_real_balance_observation(client, monkeypatch):
    profile_id, identity_key = _activate_awaiting_gas(
        client,
        monkeypatch,
        agent_key="Partner-Gas-Ready-B",
    )
    heartbeat = client.post(
        f"/api/v1/agent-onboarding/registrations/{profile_id}/heartbeat-challenge",
        json={"status": "ONLINE", "capacityAvailable": True, "endpointLatencyMs": 15},
    ).json()
    accepted = client.post(
        f"/api/v1/agent-onboarding/registrations/{profile_id}/heartbeat",
        json={
            "challengeId": heartbeat["challengeId"],
            "status": "ONLINE",
            "capacityAvailable": True,
            "endpointLatencyMs": 15,
            "signature": identity_key.sign(heartbeat["message"].encode(), ec.ECDSA(hashes.SHA256())).hex(),
        },
    )
    assert accepted.status_code == 200

    def rpc_call(_url, method, _params):
        return "0x7a6a" if method == "eth_chainId" else "0xde0b6b3a7640000"

    monkeypatch.setattr("app.workflow_api._rpc_call", rpc_call)
    response = client.post(
        f"/api/v1/agent-onboarding/registrations/{profile_id}/gas-check",
        headers=auth_headers(client, "platform-admin", "gas-ready-recheck"),
    )
    assert response.status_code == 200, response.text
    body = response.json()
    assert body["chainId"] == 31338
    assert body["gasBalanceWei"] == "1000000000000000000"
    assert body["gasReady"] is True
    assert body["admissionStatus"] == "APPROVED"
    assert body["operationalStatus"] == "ACTIVE"
    assert body["matchable"] is True

    directory = client.get("/api/v1/workflow-agents").json()["items"]
    profile = next(item for item in directory if item["id"] == profile_id)
    assert profile["gasReady"] is True
    assert profile["metadata"]["verification"]["gasReadyVerified"] is True
    assert profile["metadata"]["onboarding"]["status"] == "ACTIVE"


def test_agent_account_snapshot_reads_gas_and_payout_token_balances(client, monkeypatch):
    directory = client.get("/api/v1/workflow-agents?agentType=EXECUTOR").json()["items"]
    profile = next(item for item in directory if item["chainId"] == 31340)
    with client.app.state.session_factory() as session:
        route = session.scalar(
            select(ChainRoute).where(
                ChainRoute.destination_chain_id == 31340,
                ChainRoute.status == "ACTIVE",
            )
        )
        assert route is not None
        route.payout_asset = "DTK"
        route.payout_token_address = "0x" + "e" * 40
        route.payout_decimals = 6
        session.commit()

    def rpc_call(_url, method, params):
        if method == "eth_getBalance":
            assert params == [profile["homeWallet"], "latest"]
            return hex(10**18)
        if method == "eth_call":
            assert params[0]["data"].startswith("0x70a08231")
            assert params[0]["data"].endswith(profile["homeWallet"][2:].lower().rjust(64, "0"))
            return hex(35_600_000)
        raise AssertionError(f"Unexpected RPC method: {method}")

    monkeypatch.setattr("app.workflow_api._rpc_call", rpc_call)
    response = client.get(f"/api/v1/workflow-agents/{profile['id']}/account")
    assert response.status_code == 200, response.text
    body = response.json()
    assert body["profileId"] == profile["id"]
    assert body["chainId"] == 31340
    assert body["wallet"] == profile["homeWallet"]
    assert body["gasBalanceWei"] == "1000000000000000000"
    assert body["gasBalanceError"] is None
    assert body["gasSymbol"] == "ETH"
    assert body["payoutAsset"] == "DTK"
    assert body["payoutBalanceRaw"] == "35600000"
    assert body["payoutBalance"] == "35.6"
    assert body["payoutBalanceError"] is None
    assert body["latestPayout"] is None


def test_agent_account_snapshot_keeps_history_available_when_token_rpc_is_stale(client, monkeypatch):
    directory = client.get("/api/v1/workflow-agents?agentType=EXECUTOR").json()["items"]
    profile = next(item for item in directory if item["chainId"] == 31340)
    with client.app.state.session_factory() as session:
        route = session.scalar(
            select(ChainRoute).where(
                ChainRoute.destination_chain_id == 31340,
                ChainRoute.status == "ACTIVE",
            )
        )
        assert route is not None
        route.payout_token_address = "0x" + "e" * 40
        session.commit()

    def rpc_call(_url, method, _params):
        return hex(10**18) if method == "eth_getBalance" else "0x"

    monkeypatch.setattr("app.workflow_api._rpc_call", rpc_call)
    response = client.get(f"/api/v1/workflow-agents/{profile['id']}/account")
    assert response.status_code == 200, response.text
    body = response.json()
    assert body["gasBalanceWei"] == "1000000000000000000"
    assert body["payoutBalance"] is None
    assert body["payoutBalanceError"] == "PAYOUT_BALANCE_UNAVAILABLE"
    assert body["latestPayout"] is None


def test_gas_check_can_fund_an_agent_on_a_local_hardhat_chain(client, monkeypatch):
    profile_id, _ = _activate_awaiting_gas(
        client,
        monkeypatch,
        agent_key="Partner-Local-Gas-Top-Up-B",
    )
    monkeypatch.setenv("APP_ENV", "development")
    monkeypatch.setenv("LOCAL_AGENT_GAS_AUTO_TOP_UP", "true")
    monkeypatch.setenv("LOCAL_AGENT_GAS_TOP_UP_WEI", "1000000000000000000")
    monkeypatch.setattr(
        "app.workflow_api.urlparse",
        lambda _url: type("ParsedRpc", (), {"hostname": "127.0.0.1"})(),
    )
    calls = []
    balances = iter(["0x0", "0xde0b6b3a7640000"])

    def rpc_call(_url, method, params):
        calls.append((method, params))
        if method == "eth_chainId":
            return "0x7a6a"
        if method == "eth_getBalance":
            return next(balances)
        if method == "hardhat_setBalance":
            return True
        raise AssertionError(f"Unexpected RPC method: {method}")

    monkeypatch.setattr("app.workflow_api._rpc_call", rpc_call)
    response = client.post(
        f"/api/v1/agent-onboarding/registrations/{profile_id}/gas-check",
        headers=auth_headers(client, "platform-admin", "local-gas-top-up"),
    )
    assert response.status_code == 200, response.text
    assert response.json()["gasReady"] is True
    assert any(method == "hardhat_setBalance" for method, _ in calls)


def test_gas_check_keeps_zero_balance_agent_out_of_matching(client, monkeypatch):
    profile_id, _ = _activate_awaiting_gas(
        client,
        monkeypatch,
        agent_key="Partner-Zero-Gas-B",
    )

    def rpc_call(_url, method, _params):
        return "0x7a6a" if method == "eth_chainId" else "0x0"

    monkeypatch.setattr("app.workflow_api._rpc_call", rpc_call)
    response = client.post(
        f"/api/v1/agent-onboarding/registrations/{profile_id}/gas-check",
        headers=auth_headers(client, "platform-admin", "zero-gas-recheck"),
    )
    assert response.status_code == 200, response.text
    assert response.json()["gasReady"] is False
    assert response.json()["admissionStatus"] == "APPROVED"
    assert response.json()["operationalStatus"] == "ACTIVE_AWAITING_GAS"
    assert response.json()["matchable"] is False


def test_gas_check_rejects_wrong_rpc_chain_and_requires_admin(client, monkeypatch):
    profile_id, _ = _activate_awaiting_gas(
        client,
        monkeypatch,
        agent_key="Partner-Wrong-Rpc-B",
    )
    unauthorized = client.post(
        f"/api/v1/agent-onboarding/registrations/{profile_id}/gas-check",
        headers=auth_headers(client, "campus-requester", "gas-check-not-admin"),
    )
    assert unauthorized.status_code == 403

    monkeypatch.setattr(
        "app.workflow_api._rpc_call",
        lambda _url, method, _params: "0x7a6b" if method == "eth_chainId" else "0xde0b6b3a7640000",
    )
    response = client.post(
        f"/api/v1/agent-onboarding/registrations/{profile_id}/gas-check",
        headers=auth_headers(client, "platform-admin", "wrong-chain-gas-check"),
    )
    assert response.status_code == 422
    assert "reports chainId 31339, expected 31338" in response.json()["error"]["message"]

    directory = client.get("/api/v1/workflow-agents").json()["items"]
    profile = next(item for item in directory if item["id"] == profile_id)
    assert profile["gasReady"] is False


def test_external_llm_agent_onboarding_rejects_asset_chain_mismatch(client):
    response, _, _ = _register(
        client,
        agent_key="Wrong-Chain-Agent",
        payout_asset="BTK",
        preferred_chain_id=31339,
    )
    assert response.status_code == 422
    assert "does not belong" in response.json()["error"]["message"]


def test_signed_heartbeat_rejects_replay_and_expired_challenge(client):
    response, identity_key, wallet = _register(client, agent_key="Partner-Heartbeat-B")
    profile_id = _verify_ownership(client, response, identity_key, wallet)
    heartbeat = client.post(
        f"/api/v1/agent-onboarding/registrations/{profile_id}/heartbeat-challenge",
        json={"status": "ONLINE", "capacityAvailable": True, "endpointLatencyMs": 25},
    )
    assert heartbeat.status_code == 201, heartbeat.text
    challenge = heartbeat.json()
    payload = {
        "challengeId": challenge["challengeId"],
        "status": "ONLINE",
        "capacityAvailable": True,
        "endpointLatencyMs": 25,
        "signature": identity_key.sign(challenge["message"].encode(), ec.ECDSA(hashes.SHA256())).hex(),
    }
    accepted = client.post(f"/api/v1/agent-onboarding/registrations/{profile_id}/heartbeat", json=payload)
    assert accepted.status_code == 200, accepted.text
    replay = client.post(f"/api/v1/agent-onboarding/registrations/{profile_id}/heartbeat", json=payload)
    assert replay.status_code == 409

    expired = client.post(
        f"/api/v1/agent-onboarding/registrations/{profile_id}/heartbeat-challenge",
        json={"status": "ONLINE", "capacityAvailable": True, "endpointLatencyMs": 25},
    ).json()
    with client.app.state.session_factory() as session:
        challenge_record = session.get(ExternalAgentOnboardingChallenge, expired["challengeId"])
        challenge_record.expires_at = datetime.now(timezone.utc) - timedelta(seconds=1)
        session.commit()
    expired_response = client.post(
        f"/api/v1/agent-onboarding/registrations/{profile_id}/heartbeat",
        json={
            "challengeId": expired["challengeId"],
            "status": "ONLINE",
            "capacityAvailable": True,
            "endpointLatencyMs": 25,
            "signature": "0" * 128,
        },
    )
    assert expired_response.status_code == 409


def test_matcher_rejects_external_agent_with_stale_heartbeat(client, monkeypatch):
    response, identity_key, wallet = _register(
        client,
        agent_key="Partner-Stale-B",
        capabilities=["external-only-research"],
    )
    profile_id = _verify_ownership(client, response, identity_key, wallet)
    monkeypatch.setattr(
        "app.workflow_api._run_agent_technical_checks",
        lambda session, profile, agent, token: {
            "healthCheckPassed": True,
            "manifestCheckPassed": True,
            "testTaskPassed": True,
            "gasReadyVerified": True,
            "checkedAt": datetime.now(timezone.utc).isoformat().replace("+00:00", "Z"),
            "endpointLatencyMs": 12,
            "manifestHash": "0x" + "11" * 32,
            "testResultHash": "sha256:" + "22" * 32,
            "gasBalanceWei": "1000000000000000000",
            "routeKey": "A_TO_B",
            "routeVersion": 1,
        },
    )
    admin = auth_headers(client, "platform-admin", "stale-agent-check")
    checked = client.post(
        f"/api/v1/agent-onboarding/registrations/{profile_id}/checks",
        headers=admin,
        json={"runtimeAccessToken": "test-runtime-token-value"},
    )
    assert checked.status_code == 200, checked.text
    activated = client.post(
        f"/api/v1/agent-onboarding/registrations/{profile_id}/activate",
        headers={**admin, "Idempotency-Key": "stale-agent-activate"},
        json={"notes": "Ready for heartbeat test."},
    )
    assert activated.status_code == 200, activated.text
    heartbeat = client.post(
        f"/api/v1/agent-onboarding/registrations/{profile_id}/heartbeat-challenge",
        json={"status": "ONLINE", "capacityAvailable": True, "endpointLatencyMs": 10},
    ).json()
    accepted = client.post(
        f"/api/v1/agent-onboarding/registrations/{profile_id}/heartbeat",
        json={
            "challengeId": heartbeat["challengeId"],
            "status": "ONLINE",
            "capacityAvailable": True,
            "endpointLatencyMs": 10,
            "signature": identity_key.sign(heartbeat["message"].encode(), ec.ECDSA(hashes.SHA256())).hex(),
        },
    )
    assert accepted.status_code == 200 and accepted.json()["matchable"] is True

    with client.app.state.session_factory() as session:
        profile = session.get(WorkflowAgentProfile, profile_id)
        profile.last_heartbeat_at = datetime.now(timezone.utc) - timedelta(minutes=5)
        session.commit()

    requester = auth_headers(client, "campus-requester", "stale-workflow-create")
    created = client.post(
        "/api/v1/workflows",
        headers=requester,
        json={
            "title": "Stale heartbeat matching test",
            "objective": "Do not select an Agent whose signed heartbeat has expired.",
            "budget": "3",
            "platformFee": "0.2",
            "nodes": [{
                "nodeKey": "N1",
                "title": "External research",
                "capability": "external-only-research",
                "reward": "2",
            }],
        },
    )
    assert created.status_code == 201, created.text
    coordinated = client.post(
        f"/api/v1/workflows/{created.json()['id']}/coordinate",
        headers={**requester, "Idempotency-Key": "stale-workflow-coordinate"},
        json={"maxActions": 1},
    )
    assert coordinated.status_code == 200, coordinated.text
    assert coordinated.json()["actions"][0]["action"] == "waiting_for_agent"
    assert coordinated.json()["workflow"]["nodes"][0]["attempts"] == []


def _mark_agent_active(client, profile_id: str) -> None:
    with client.app.state.session_factory() as session:
        profile = session.get(WorkflowAgentProfile, profile_id)
        agent = session.get(Agent, profile.agent_id)
        profile.active = True
        profile.admission_status = "APPROVED"
        profile.chain_status = "READY"
        profile.credential_status = "ACTIVE"
        agent.status = "active"
        session.commit()


def test_active_agent_manifest_sync_publishes_capability_to_market(client, monkeypatch):
    response, identity_key, wallet = _register(
        client,
        agent_key="Partner-Capability-Sync-B",
        capabilities=["research"],
    )
    profile_id = _verify_ownership(client, response, identity_key, wallet)
    _mark_agent_active(client, profile_id)

    manifest = {
        "agentId": "partner-capability-sync-b",
        "did": "did:placeholder:replaced-by-registration",
        "name": "Partner Capability Sync",
        "description": "Auditable research provider.",
        "version": "2.1.0",
        "capabilities": [{
            "capabilityId": "research",
            "name": "Evidence research",
            "description": "Research with structured evidence.",
            "pricingModel": "fixed",
            "basePrice": "4.20",
            "currency": "USDC",
            "estimatedDurationSeconds": 90,
            "inputSchema": {"type": "object", "required": ["question"]},
            "outputSchema": {"type": "object", "required": ["summary"]},
            "exampleRequest": {"input": {"question": "Check settlement evidence"}},
        }],
    }

    def fetch_manifest(_profile, agent):
        return {**manifest, "agentId": agent.slug, "did": agent.did}

    monkeypatch.setattr("app.workflow_api._fetch_agent_manifest", fetch_manifest)
    synced = client.post(
        f"/api/v1/workflow-agents/{profile_id}/capabilities/sync",
        headers=auth_headers(client, "platform-admin", "capability-sync"),
    )
    assert synced.status_code == 200, synced.text
    body = synced.json()
    assert body["total"] == 1
    capability = body["items"][0]
    assert capability["name"] == "Evidence research"
    assert float(capability["basePrice"]) == 4.2
    assert capability["version"] == "2.1.0"
    assert capability["provider"]["slug"] == "partner-capability-sync-b"

    market = client.get("/api/v1/capabilities").json()["items"]
    published = next(item for item in market if item["id"] == capability["id"])
    assert published["inputSchema"]["required"] == ["question"]
    assert published["outputSchema"]["required"] == ["summary"]


def test_no_route_registration_is_approved_pending_route_then_promoted(client, monkeypatch):
    pending_chain_id = 41414
    response, identity_key, wallet = _register(
        client,
        agent_key="Partner-Pending-Route",
        payout_asset="XTK",
        preferred_chain_id=pending_chain_id,
        capabilities=["pending-route-research"],
    )
    assert response.status_code == 201, response.text
    registration = response.json()
    assert registration["route"] is None
    assert registration["chainStatus"] == "PENDING_ROUTE"
    assert registration["profile"]["chainStatus"] == "PENDING_ROUTE"
    assert registration["matchable"] is False

    profile_id = _verify_ownership(client, response, identity_key, wallet)
    monkeypatch.setattr(
        "app.workflow_api._run_agent_technical_checks",
        lambda session, profile, agent, token: {
            "healthCheckPassed": True,
            "manifestCheckPassed": True,
            "testTaskPassed": True,
            "gasReadyVerified": False,
            "checkedAt": datetime.now(timezone.utc).isoformat().replace("+00:00", "Z"),
            "endpointLatencyMs": 20,
            "manifestHash": "0x" + "33" * 32,
            "testResultHash": "sha256:" + "44" * 32,
            "gasBalanceWei": None,
            "routeKey": None,
            "routeVersion": None,
        },
    )
    admin = auth_headers(client, "platform-admin", "pending-route-admin")
    checked = client.post(
        f"/api/v1/agent-onboarding/registrations/{profile_id}/checks",
        headers=admin,
        json={"runtimeAccessToken": "pending-route-runtime-token"},
    )
    assert checked.status_code == 200, checked.text
    assert checked.json()["chainStatus"] == "PENDING_ROUTE"

    activated = client.post(
        f"/api/v1/agent-onboarding/registrations/{profile_id}/activate",
        headers=admin,
        json={"notes": "Identity and runtime approved while route deployment is pending."},
    )
    assert activated.status_code == 200, activated.text
    assert activated.json()["admissionStatus"] == "APPROVED"
    assert activated.json()["chainStatus"] == "PENDING_ROUTE"
    assert activated.json()["operationalStatus"] == "APPROVED_PENDING_ROUTE"
    assert activated.json()["matchable"] is False

    monkeypatch.setattr(
        "app.workflow_api._rpc_call",
        lambda _url, method, _params: hex(pending_chain_id) if method == "eth_chainId" else "0x0",
    )
    created_route = client.post(
        "/api/v1/chain-routes",
        headers=admin,
        json={
            "routeKey": "A_TO_PENDING_ROUTE_TEST",
            "sourceChainId": 31337,
            "destinationChainId": pending_chain_id,
            "rpcUrl": "http://127.0.0.1:19545",
            "transport": "LOCAL_MOCK",
            "settlementAsset": "USDC",
            "payoutAsset": "XTK",
            "payoutRate": "1",
        },
    )
    assert created_route.status_code == 201, created_route.text
    assert created_route.json()["refreshedAgentProfiles"] == 1

    refreshed = client.get(f"/api/v1/agent-onboarding/registrations/{profile_id}")
    assert refreshed.status_code == 200, refreshed.text
    assert refreshed.json()["chainStatus"] == "READY"
    assert refreshed.json()["gasReady"] is False
    assert refreshed.json()["matchable"] is False


def test_admin_registration_queue_and_rejection_are_database_backed(client):
    response, identity_key, wallet = _register(
        client,
        agent_key="Partner-Admin-Reject",
    )
    profile_id = _verify_ownership(client, response, identity_key, wallet)

    unauthorized = client.get(
        "/api/v1/agent-onboarding/registrations?admissionStatus=PENDING_PLATFORM_REVIEW",
        headers=auth_headers(client, "campus-requester", "registration-list-requester"),
    )
    assert unauthorized.status_code == 403

    admin = auth_headers(client, "platform-admin", "registration-list-admin")
    queue = client.get(
        "/api/v1/agent-onboarding/registrations?admissionStatus=PENDING_PLATFORM_REVIEW",
        headers=admin,
    )
    assert queue.status_code == 200, queue.text
    assert any(item["id"] == profile_id for item in queue.json()["items"])

    applicant_activation = client.post(
        f"/api/v1/agent-onboarding/registrations/{profile_id}/activate",
        headers=auth_headers(client, "campus-requester", "registration-activate-requester"),
        json={"notes": "Applicant must not approve itself."},
    )
    assert applicant_activation.status_code == 403

    rejected = client.post(
        f"/api/v1/agent-onboarding/registrations/{profile_id}/reject",
        headers=admin,
        json={"reason": "Manifest ownership is valid, but the operator application is incomplete."},
    )
    assert rejected.status_code == 200, rejected.text
    body = rejected.json()
    assert body["admissionStatus"] == "REJECTED"
    assert body["matchable"] is False
    assert body["profile"]["metadata"]["onboarding"]["reviewedBy"] == "did:local:platform-admin"
    assert body["profile"]["metadata"]["onboarding"]["rejectionReason"]


def test_platform_chat_routes_proxy_session_and_message(client, monkeypatch):
    response, identity_key, wallet = _register(
        client,
        agent_key="Partner-Chat-B",
        capabilities=["research"],
    )
    profile_id = _verify_ownership(client, response, identity_key, wallet)
    _mark_agent_active(client, profile_id)
    calls: list[dict] = []
    session_id = "11111111-1111-4111-8111-111111111111"

    def proxy(_profile, _agent, path, *, method="POST", body=None):
        calls.append({"path": path, "method": method, "body": body})
        if path == "/chat/sessions":
            return {"sessionId": session_id, "messages": []}
        return {
            "sessionId": session_id,
            "agentId": _agent.slug,
            "message": "Settlement evidence is consistent.",
            "sources": [],
            "toolCalls": [],
        }

    monkeypatch.setattr("app.workflow_api._proxy_agent_request", proxy)
    requester_headers = auth_headers(client, "campus-requester", "chat-session")
    created = client.post(
        f"/api/v1/workflow-agents/{profile_id}/chat/sessions",
        headers=requester_headers,
        json={"title": "Settlement review"},
    )
    assert created.status_code == 201, created.text
    assert created.json()["sessionId"] == session_id
    assert calls[0]["path"] == "/chat/sessions"
    assert calls[0]["body"]["title"] == "Settlement review"
    assert calls[0]["body"]["ownerId"]

    chatted = client.post(
        f"/api/v1/workflow-agents/{profile_id}/chat",
        headers={**requester_headers, "Idempotency-Key": "chat-message"},
        json={"sessionId": session_id, "message": "Check the settlement evidence."},
    )
    assert chatted.status_code == 200, chatted.text
    assert chatted.json()["message"] == "Settlement evidence is consistent."
    assert calls[1] == {
        "path": "/chat",
        "method": "POST",
        "body": {"sessionId": session_id, "message": "Check the settlement evidence."},
    }


def test_coordinator_agent_uses_identity_only_and_never_enters_executor_matching(client, monkeypatch):
    identity_key, public_key = _identity()
    registered = client.post(
        "/api/v1/agent-onboarding/registrations",
        json={
            "agentType": "COORDINATOR",
            "ownerName": "Coordinator Operator",
            "ownerEmail": "coordinator@example.com",
            "agentName": "Independent Coordinator",
            "agentKey": "Independent-Coordinator",
            "endpoint": "https://coordinator.example.com",
            "publicKey": public_key,
            "llmProvider": "openai_compatible",
            "llmModel": "coordinator-model",
            "systemPrompt": "Propose a DAG and leave execution decisions to Core.",
            "capabilities": ["workflow_coordination", "research"],
            "priceFloor": "0",
        },
    )
    assert registered.status_code == 201, registered.text
    registration = registered.json()
    profile_id = registration["profile"]["id"]
    assert registration["agent"]["roles"] == ["coordinator"]
    assert registration["profile"]["agentType"] == "COORDINATOR"
    assert registration["profile"]["homeWallet"] is None
    assert registration["chainStatus"] == "READY"

    challenge = client.post(f"/api/v1/agent-onboarding/registrations/{profile_id}/challenge")
    assert challenge.status_code == 201, challenge.text
    challenge_body = challenge.json()
    assert challenge_body["signatureRequirements"]["wallet"] is None
    verified = client.post(
        f"/api/v1/agent-onboarding/registrations/{profile_id}/verify",
        json={
            "challengeId": challenge_body["challengeId"],
            "identitySignature": identity_key.sign(
                challenge_body["message"].encode(), ec.ECDSA(hashes.SHA256())
            ).hex(),
        },
    )
    assert verified.status_code == 200, verified.text

    monkeypatch.setattr(
        "app.workflow_api._run_agent_technical_checks",
        lambda session, profile, agent, token: {
            "healthCheckPassed": True,
            "manifestCheckPassed": True,
            "testTaskPassed": True,
            "gasReadyVerified": True,
            "checkedAt": datetime.now(timezone.utc).isoformat().replace("+00:00", "Z"),
            "endpointLatencyMs": 15,
            "manifestHash": "0x" + "44" * 32,
            "testResultHash": "sha256:" + "55" * 32,
            "manifest": {
                "agentId": "independent-coordinator",
                "agentType": "COORDINATOR",
                "capabilities": [{"capabilityId": "workflow_coordination"}],
            },
        },
    )
    checked = client.post(
        f"/api/v1/agent-onboarding/registrations/{profile_id}/checks",
        headers=auth_headers(client, "platform-admin", "coordinator-check"),
        json={"runtimeAccessToken": "coordinator-runtime-token"},
    )
    assert checked.status_code == 200, checked.text
    activated = client.post(
        f"/api/v1/agent-onboarding/registrations/{profile_id}/activate",
        headers=auth_headers(client, "platform-admin", "coordinator-activate"),
        json={"notes": "Coordinator identity and planning contract passed."},
    )
    assert activated.status_code == 200, activated.text
    assert activated.json()["operationalStatus"] == "ACTIVE"
    assert activated.json()["profile"]["agent"]["erc8004Registration"]["admissionStatus"] == "APPROVED"

    heartbeat = client.post(
        f"/api/v1/agent-onboarding/registrations/{profile_id}/heartbeat-challenge",
        json={"status": "ONLINE", "capacityAvailable": True, "endpointLatencyMs": 15},
    ).json()
    accepted = client.post(
        f"/api/v1/agent-onboarding/registrations/{profile_id}/heartbeat",
        json={
            "challengeId": heartbeat["challengeId"],
            "status": "ONLINE",
            "capacityAvailable": True,
            "endpointLatencyMs": 15,
            "signature": identity_key.sign(
                heartbeat["message"].encode(), ec.ECDSA(hashes.SHA256())
            ).hex(),
        },
    )
    assert accepted.status_code == 200, accepted.text
    assert accepted.json()["matchable"] is True

    plan = client.post(
        "/api/v1/workflow-plans",
        headers=auth_headers(client, "campus-requester", "coordinator-role-isolation-plan"),
        json={
            "objective": "Research one source without assigning coordination identities as workers.",
            "budget": "20.00",
            "platformFee": "1.10",
            "proposedNodes": [{
                "nodeKey": "N1",
                "title": "Research",
                "description": "Collect one source.",
                "capability": "research",
                "dependsOn": [],
                "acceptanceCriteria": {},
                "inputPayload": {},
                "suggestedReward": "10.00",
                "maxAttempts": 2,
            }],
            "proposalVersion": "LLM_DAG_PROPOSAL_V1",
        },
    )
    assert plan.status_code == 200, plan.text
    candidate_ids = [item["agentProfileId"] for item in plan.json()["nodes"][0]["candidates"]]
    assert profile_id not in candidate_ids


def test_admin_profile_patch_canonicalizes_datetime_fields(client):
    profile = client.get("/api/v1/workflow-agents").json()["items"][0]
    heartbeat_at = "2026-08-12T07:30:45.123Z"
    headers = auth_headers(client, "platform-admin", "profile-datetime-patch")

    response = client.patch(
        f"/api/v1/workflow-agents/{profile['id']}",
        headers=headers,
        json={"lastHeartbeatAt": heartbeat_at},
    )

    assert response.status_code == 200, response.text
    parsed_heartbeat = datetime.fromisoformat(response.json()["lastHeartbeatAt"].replace("Z", "+00:00"))
    assert parsed_heartbeat == datetime.fromisoformat(heartbeat_at.replace("Z", "+00:00"))
