from __future__ import annotations

from datetime import datetime, timedelta, timezone

from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import ec
from eth_account import Account
from eth_account.messages import encode_defunct
import pytest
from sqlalchemy import select

from .conftest import auth_headers
from app.workflow_models import AgentInvitation, AgentProvisionOutbox, ChainRoute, ExternalAgentOnboardingChallenge, ProtocolJob, RouteDeployment, WorkflowAgentProfile


TESTNET_ROUTE = "SEPOLIA_TO_ARBITRUM_SEPOLIA_CCIP_BNM_V1"


def _managed_route(client):
    with client.app.state.session_factory() as session:
        route = ChainRoute(
            route_key=TESTNET_ROUTE,
            route_version=1,
            source_chain_id=11155111,
            destination_chain_id=421614,
            status="ACTIVE",
            transport="CHAINLINK_CCIP",
            rpc_url="https://arbitrum-sepolia.example.invalid",
            source_chain_selector="16015286601757825753",
            destination_chain_selector="3478487238524512106",
            source_router_address="0x" + "10" * 20,
            destination_router_address="0x" + "20" * 20,
            source_token_address="0x" + "30" * 20,
            destination_token_address="0x" + "40" * 20,
            token_decimals=18,
            settlement_mode="ccip_direct",
            settlement_asset="CCIP-BNM",
            payout_asset="CCIP-BNM",
            payout_decimals=18,
            confirmation_blocks=5,
            deployment_status="READY",
            remote_work_board_address="0x" + "50" * 20,
        )
        session.add(route)
        session.commit()
        return route.id


def test_managed_route_can_be_predeployed_before_agent_matching(client, monkeypatch):
    monkeypatch.setenv("PLATFORM_PROFILE", "managed-testnet")
    route_id = _managed_route(client)
    admin = auth_headers(client, "platform-admin", "managed-route-predeploy")
    queued = client.post(f"/api/v1/chain-routes/{route_id}/deploy", headers=admin)
    assert queued.status_code == 202, queued.text
    body = queued.json()
    assert body["deployment"]["status"] == "QUEUED"
    assert body["protocolJob"]["jobType"] == "DEPLOY_ROUTE_STACK"
    repeated = client.post(f"/api/v1/chain-routes/{route_id}/deploy", headers=admin)
    assert repeated.status_code == 202, repeated.text
    assert repeated.json()["protocolJob"]["id"] == body["protocolJob"]["id"]
    with client.app.state.session_factory() as session:
        assert session.scalar(select(RouteDeployment)).id == body["deployment"]["id"]
        assert session.scalar(select(ProtocolJob)).id == body["protocolJob"]["id"]


def _managed_registration(
    client,
    invite_code: str,
    agent_key: str,
    *,
    roles: list[str] | None = None,
    capabilities: list[str] | None = None,
):
    identity_key, public_key = _identity()
    wallet = Account.create()
    response = client.post("/api/v1/agent-onboarding/registrations", json={
        "inviteCode": invite_code,
        "ownerName": "Managed Operator",
        "ownerEmail": "managed@example.com",
        "agentName": "Managed OpenClaw",
        "agentKey": agent_key,
        "did": f"did:agent:{agent_key.lower()}:testnet",
        "publicKey": public_key,
        "llmProvider": "deepseek",
        "llmModel": "deepseek-chat",
        "systemPrompt": "Return auditable JSON.",
        "capabilities": ["research"] if capabilities is None else capabilities,
        "preferredRouteKey": TESTNET_ROUTE,
        "preferredRouteVersion": 1,
        "payoutAsset": "CCIP-BNM",
        "settlementAsset": "CCIP-BNM",
        "homeWallet": wallet.address,
        "priceFloor": "1",
        "runtimeId": f"runtime-{agent_key.lower()}",
        "signerUrl": "http://127.0.0.1:8020",
        "signerCredentialRef": f"env:SIGNER_TOKEN_{agent_key.upper()}",
        "executorType": "llm",
        "executorConfig": {"credentialRef": "env:DEEPSEEK_API_KEY"},
        "roles": roles or ["provider"],
    })
    return response, identity_key, wallet


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
    roles: list[str] | None = None,
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
        "capabilities": ["research", "extraction"] if capabilities is None else capabilities,
        "payoutAsset": payout_asset,
        "homeWallet": wallet.address,
        "priceFloor": "4.20",
        "roles": roles or ["provider"],
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
    return profile_id


def _authenticate_external_agent(client, did: str, identity_key: ec.EllipticCurvePrivateKey) -> dict[str, str]:
    challenge = client.post("/api/v1/auth/challenges", json={"did": did, "audience": "agent-market"})
    assert challenge.status_code == 201, challenge.text
    challenge_body = challenge.json()
    signature = identity_key.sign(
        challenge_body["message"].encode(), ec.ECDSA(hashes.SHA256())
    ).hex()
    verified = client.post("/api/v1/auth/verify", json={
        "challengeId": challenge_body["id"],
        "did": did,
        "audience": "agent-market",
        "signature": signature,
    })
    assert verified.status_code == 200, verified.text
    return {"Authorization": f"Bearer {verified.json()['accessToken']}"}


def test_external_llm_agent_onboarding_starts_pending_and_resolves_route(client):
    response, _, _ = _register(client)
    assert response.status_code == 201, response.text
    body = response.json()
    assert body["admissionStatus"] == "PENDING"
    assert len(body["onboardingToken"]) >= 32
    assert body["statusCode"] == f"{body['profile']['id']}.{body['onboardingToken']}"
    assert "accessTokenHash" not in body["profile"]["metadata"]["onboarding"]
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
    assert "accessTokenHash" not in onboarded["metadata"]["onboarding"]


def test_managed_onboarding_consumes_invitation_only_after_runner_provision(client, monkeypatch):
    monkeypatch.setenv("PLATFORM_PROFILE", "managed-testnet")
    _managed_route(client)
    invite = client.post(
        "/api/v1/agent-invitations",
        headers=auth_headers(client, "platform-admin", "managed-invite-success"),
        json={"routeKey": TESTNET_ROUTE, "capabilities": ["research"], "executorCredentialRef": "file:C:/secrets/deepseek.key"},
    )
    assert invite.status_code == 201, invite.text
    calls = []

    def provision(method, path, payload=None):
        calls.append((method, path, payload))
        return {"status": "PROVISIONED"}

    monkeypatch.setattr("app.workflow_api._runner_control_request", provision)
    registered, identity_key, wallet = _managed_registration(client, invite.json()["inviteCode"], "Managed-Success")
    assert registered.status_code == 201, registered.text
    body = registered.json()
    assert body["provisioning"]["status"] == "PROVISIONED"
    assert body["profile"]["onboardingStatus"] == "PROVISIONED"
    assert calls[0][0] == "PUT"
    assert calls[0][2]["executorConfig"]["credentialRef"] == "file:C:/secrets/deepseek.key"
    assert calls[0][2]["platformRegistration"]["profileId"] == body["profile"]["id"]
    with client.app.state.session_factory() as session:
        stored = session.get(AgentInvitation, invite.json()["id"])
        assert stored.status == "CONSUMED"
    challenge = client.post(f"/api/v1/agent-onboarding/registrations/{body['profile']['id']}/challenge")
    assert challenge.status_code == 201, challenge.text
    challenge_body = challenge.json()
    verified = client.post(
        f"/api/v1/agent-onboarding/registrations/{body['profile']['id']}/verify",
        json={
            "challengeId": challenge_body["challengeId"],
            "identitySignature": identity_key.sign(
                challenge_body["message"].encode(), ec.ECDSA(hashes.SHA256())
            ).hex(),
            "walletSignature": Account.sign_message(
                encode_defunct(text=challenge_body["message"]), wallet.key
            ).signature.hex(),
        },
    )
    assert verified.status_code == 200, verified.text
    checked_tokens = []
    monkeypatch.setenv("RUNNER_CHECK_API_TOKEN", "server-managed-runner-check-token")
    monkeypatch.setattr(
        "app.workflow_api._run_agent_technical_checks",
        lambda session, profile, agent, token: checked_tokens.append(token) or {
            "healthCheckPassed": True,
            "manifestCheckPassed": True,
            "testTaskPassed": True,
            "gasReadyVerified": True,
            "checkedAt": datetime.now(timezone.utc).isoformat().replace("+00:00", "Z"),
            "endpointLatencyMs": 18,
            "manifestHash": "0x" + "33" * 32,
            "testResultHash": "sha256:" + "44" * 32,
            "gasBalanceWei": "1000000000000000000",
            "routeKey": TESTNET_ROUTE,
            "routeVersion": 1,
        },
    )
    completed = client.post(
        f"/api/v1/agent-onboarding/registrations/{body['profile']['id']}/complete",
        headers={"X-Onboarding-Token": body["onboardingToken"]},
        json={"runtimeAccessToken": "must-not-be-used-by-managed-mode"},
    )
    assert completed.status_code == 200, completed.text
    assert checked_tokens == ["server-managed-runner-check-token"]
    with client.app.state.session_factory() as session:
        profile = session.get(WorkflowAgentProfile, body["profile"]["id"])
        assert profile.onboarding_status == "ACTIVE"


def test_self_service_invitation_is_disabled_by_default(client, monkeypatch):
    monkeypatch.setenv("PLATFORM_PROFILE", "managed-testnet")
    _, public_key = _identity()
    response = client.post("/api/v1/agent-onboarding/invitations/request", json={
        "agentKey": "Self-Service-Disabled",
        "did": "did:agent:self-service-disabled:testnet",
        "runtimeId": "runtime-self-service-disabled",
        "homeWallet": Account.create().address,
        "publicKey": public_key,
    })
    assert response.status_code == 404


def test_requester_only_agent_activates_without_runner_and_can_publish_demands(client, monkeypatch):
    monkeypatch.setenv("PLATFORM_PROFILE", "managed-testnet")
    _managed_route(client)
    invitation = client.post(
        "/api/v1/agent-invitations",
        headers=auth_headers(client, "platform-admin", "requester-only-invitation"),
        json={
            "routeKey": TESTNET_ROUTE,
            "capabilities": [],
            "roles": ["requester"],
        },
    )
    assert invitation.status_code == 201, invitation.text
    assert invitation.json()["roles"] == ["requester"]
    assert invitation.json()["executorCredentialRef"] is None

    def unexpected_runner_call(*_args, **_kwargs):
        raise AssertionError("requester-only onboarding must not call the Runner")

    monkeypatch.setattr("app.workflow_api._runner_control_request", unexpected_runner_call)
    registered, identity_key, wallet = _managed_registration(
        client,
        invitation.json()["inviteCode"],
        "Managed-Requester-Only",
        roles=["requester"],
        capabilities=[],
    )
    assert registered.status_code == 201, registered.text
    registration_body = registered.json()
    assert registration_body["roles"] == ["requester"]
    assert "provisioning" not in registration_body
    profile_id = registration_body["profile"]["id"]

    with client.app.state.session_factory() as session:
        stored_invitation = session.get(AgentInvitation, invitation.json()["id"])
        stored_profile = session.get(WorkflowAgentProfile, profile_id)
        assert stored_invitation.status == "CONSUMED"
        assert stored_profile.provisioning_id is None
        assert session.query(AgentProvisionOutbox).count() == 0

    profile_id = _verify_ownership(client, registered, identity_key, wallet)
    token = registration_body["onboardingToken"]
    status = client.get(
        f"/api/v1/agent-onboarding/registrations/{profile_id}/status",
        headers={"X-Onboarding-Token": token},
    )
    assert status.status_code == 200, status.text
    assert status.json()["admissionStatus"] == "ACTIVE"
    assert status.json()["roles"] == ["requester"]
    assert status.json()["canRequest"] is True
    assert status.json()["canProvide"] is False
    assert status.json()["matchable"] is False
    assert [step["key"] for step in status.json()["steps"]] == ["registration", "ownership", "activation"]

    completed = client.post(
        f"/api/v1/agent-onboarding/registrations/{profile_id}/complete",
        headers={"X-Onboarding-Token": token},
        json={},
    )
    assert completed.status_code == 200, completed.text
    assert completed.json()["checksSkipped"] is True
    heartbeat = client.post(
        f"/api/v1/agent-onboarding/registrations/{profile_id}/heartbeat-challenge",
        json={},
    )
    assert heartbeat.status_code == 409
    assert all(item["id"] != profile_id for item in client.get("/api/v1/workflow-agents").json()["items"])

    did = registration_body["agent"]["did"]
    requester_headers = _authenticate_external_agent(client, did, identity_key)
    demand = client.post(
        "/api/v1/demands",
        headers={**requester_headers, "Idempotency-Key": "external-requester-demand"},
        json={
            "title": "Research current testnet liquidity",
            "description": "Collect evidence about current testnet liquidity conditions.",
            "acceptanceCriteria": "Return cited observations.",
            "budget": "2",
            "currency": "CCIP-BNM",
        },
    )
    assert demand.status_code == 201, demand.text
    provider_only = client.post(
        "/api/v1/demands/nonexistent/bids",
        headers=requester_headers,
        json={"amount": "1", "currency": "CCIP-BNM", "proposal": "Not allowed", "estimatedDurationSeconds": 60},
    )
    assert provider_only.status_code == 403


def test_invitation_roles_prevent_privilege_escalation_and_provider_requires_capability(client, monkeypatch):
    missing_capability = client.post(
        "/api/v1/agent-invitations",
        headers=auth_headers(client, "platform-admin", "provider-without-capability"),
        json={"routeKey": "A_TO_B", "capabilities": [], "roles": ["provider"]},
    )
    assert missing_capability.status_code == 422

    monkeypatch.setenv("PLATFORM_PROFILE", "managed-testnet")
    _managed_route(client)
    invitation = client.post(
        "/api/v1/agent-invitations",
        headers=auth_headers(client, "platform-admin", "requester-scope-invitation"),
        json={"routeKey": TESTNET_ROUTE, "capabilities": [], "roles": ["requester"]},
    )
    assert invitation.status_code == 201, invitation.text
    escalated, _, _ = _managed_registration(
        client,
        invitation.json()["inviteCode"],
        "Managed-Role-Escalation",
        roles=["requester", "provider"],
        capabilities=["research"],
    )
    assert escalated.status_code == 403
    assert "exactly match" in escalated.json()["error"]["message"]


def test_dual_role_agent_can_request_and_provide(client, monkeypatch):
    registered, identity_key, wallet = _register(
        client,
        agent_key="Partner-Dual-Role",
        roles=["requester", "provider"],
        capabilities=["dual-role-research"],
    )
    assert registered.status_code == 201, registered.text
    assert registered.json()["agent"]["roles"] == ["provider", "requester"]
    profile_id = _verify_ownership(client, registered, identity_key, wallet)
    monkeypatch.setattr(
        "app.workflow_api._run_agent_technical_checks",
        lambda session, profile, agent, token: {
            "healthCheckPassed": True,
            "manifestCheckPassed": True,
            "testTaskPassed": True,
            "gasReadyVerified": True,
            "checkedAt": datetime.now(timezone.utc).isoformat().replace("+00:00", "Z"),
            "endpointLatencyMs": 15,
            "manifestHash": "0x" + "55" * 32,
            "testResultHash": "sha256:" + "66" * 32,
            "gasBalanceWei": "1000000000000000000",
            "routeKey": "A_TO_B",
            "routeVersion": 1,
        },
    )
    completed = client.post(
        f"/api/v1/agent-onboarding/registrations/{profile_id}/complete",
        headers={"X-Onboarding-Token": registered.json()["onboardingToken"]},
        json={"runtimeAccessToken": "dual-role-runtime-token"},
    )
    assert completed.status_code == 200, completed.text
    assert completed.json()["canRequest"] is True
    assert completed.json()["canProvide"] is True

    did = registered.json()["agent"]["did"]
    headers = _authenticate_external_agent(client, did, identity_key)
    demand = client.post(
        "/api/v1/demands",
        headers={**headers, "Idempotency-Key": "dual-role-demand"},
        json={
            "title": "Dual role request",
            "description": "Verify that a dual role Agent can act as requester.",
            "acceptanceCriteria": "Return valid evidence.",
            "budget": "2",
        },
    )
    assert demand.status_code == 201, demand.text
    provider_access = client.post(
        "/api/v1/demands/nonexistent/bids",
        headers=headers,
        json={"amount": "1", "proposal": "Provider access check", "estimatedDurationSeconds": 60},
    )
    assert provider_access.status_code == 404


def test_self_service_invitation_is_identity_bound_and_cannot_escalate_capabilities(client, monkeypatch):
    monkeypatch.setenv("PLATFORM_PROFILE", "managed-testnet")
    monkeypatch.setenv("MANAGED_SELF_SERVICE_ONBOARDING", "true")
    monkeypatch.setenv("MANAGED_SELF_SERVICE_CAPABILITIES", "research")
    monkeypatch.setenv("MANAGED_SELF_SERVICE_EXECUTOR_CREDENTIAL_REF", "file:C:/secrets/deepseek.key")
    _managed_route(client)
    identity_key, public_key = _identity()
    wallet = Account.create()
    agent_key = "Self-Service-Bound"
    did = "did:agent:self-service-bound:testnet"
    runtime_id = "runtime-self-service-bound"
    requested = client.post("/api/v1/agent-onboarding/invitations/request", json={
        "agentKey": agent_key,
        "did": did,
        "runtimeId": runtime_id,
        "homeWallet": wallet.address,
        "publicKey": public_key,
    })
    assert requested.status_code == 201, requested.text
    invitation = requested.json()
    assert invitation["capabilities"] == ["research"]
    assert invitation["homeChainId"] == 421614
    with client.app.state.session_factory() as session:
        stored = session.get(AgentInvitation, invitation["id"])
        assert stored.issuance_type == "SELF_SERVICE"
        assert stored.bound_did == did
        assert stored.bound_home_wallet == wallet.address.lower()

    base = {
        "inviteCode": invitation["inviteCode"],
        "ownerName": "Self Service Operator",
        "ownerEmail": "self-service@example.com",
        "agentName": "Self Service Agent",
        "agentKey": agent_key,
        "did": did,
        "publicKey": public_key,
        "llmProvider": "deepseek",
        "llmModel": "deepseek-chat",
        "systemPrompt": "Return auditable JSON.",
        "capabilities": ["research"],
        "preferredRouteKey": TESTNET_ROUTE,
        "preferredRouteVersion": 1,
        "preferredChainId": 421614,
        "payoutAsset": "CCIP-BNM",
        "settlementAsset": "CCIP-BNM",
        "currency": "CCIP-BNM",
        "homeWallet": wallet.address,
        "priceFloor": "1",
        "runtimeId": runtime_id,
        "signerUrl": "http://127.0.0.1:8020",
        "signerCredentialRef": "file:C:/secure/self-service/signer.credential",
        "executorType": "llm",
        "executorConfig": {},
    }
    wrong_identity = client.post(
        "/api/v1/agent-onboarding/registrations",
        json={**base, "did": "did:agent:stolen-invitation:testnet"},
    )
    assert wrong_identity.status_code == 403
    escalated = client.post(
        "/api/v1/agent-onboarding/registrations",
        json={**base, "capabilities": ["research", "admin"]},
    )
    assert escalated.status_code == 422

    monkeypatch.setattr("app.workflow_api._runner_control_request", lambda method, path, payload=None: {"status": "PROVISIONED"})
    registered = client.post("/api/v1/agent-onboarding/registrations", json=base)
    assert registered.status_code == 201, registered.text
    assert registered.json()["profile"]["onboardingStatus"] == "PROVISIONED"
    assert registered.json()["profile"]["homeWallet"].lower() == wallet.address.lower()
    duplicate_runtime = client.post("/api/v1/agent-onboarding/invitations/request", json={
        "agentKey": "Another-Self-Service",
        "did": "did:agent:another-self-service:testnet",
        "runtimeId": runtime_id,
        "homeWallet": Account.create().address,
        "publicKey": _identity()[1],
    })
    assert duplicate_runtime.status_code == 409
    assert identity_key is not None


def test_managed_production_allows_only_loopback_http_runner(monkeypatch):
    from app.workflow_api import _validate_agent_endpoint

    monkeypatch.setenv("PLATFORM_PROFILE", "managed-testnet")
    monkeypatch.setenv("APP_ENV", "production")
    _validate_agent_endpoint("http://127.0.0.1:8010")
    with pytest.raises(ValueError, match="must use HTTPS"):
        _validate_agent_endpoint("http://runner.example.com")


def test_managed_onboarding_retries_only_the_same_failed_registration(client, monkeypatch):
    monkeypatch.setenv("PLATFORM_PROFILE", "managed-testnet")
    _managed_route(client)
    invite = client.post(
        "/api/v1/agent-invitations",
        headers=auth_headers(client, "platform-admin", "managed-invite-retry"),
        json={"routeKey": TESTNET_ROUTE, "capabilities": ["research"]},
    ).json()
    outcomes = iter([ValueError("Runner unavailable"), {"status": "PROVISIONED"}])

    def provision(method, path, payload=None):
        outcome = next(outcomes)
        if isinstance(outcome, Exception):
            raise outcome
        return outcome

    monkeypatch.setattr("app.workflow_api._runner_control_request", provision)
    registered, _, _ = _managed_registration(client, invite["inviteCode"], "Managed-Retry")
    assert registered.status_code == 201, registered.text
    body = registered.json()
    assert body["provisioning"]["status"] == "PROVISION_FAILED"
    with client.app.state.session_factory() as session:
        assert session.get(AgentInvitation, invite["id"]).status == "USED_FAILED"

    replay = client.post("/api/v1/agent-onboarding/registrations", json={})
    assert replay.status_code == 422
    admin = auth_headers(client, "platform-admin", "managed-provision-admin")
    listing = client.get("/api/v1/agent-onboarding/provisioning", headers=admin)
    assert listing.status_code == 200, listing.text
    assert listing.json()["items"][0]["profileId"] == body["profile"]["id"]
    assert "payload" not in listing.json()["items"][0]
    retried = client.post(
        f"/api/v1/agent-onboarding/registrations/{body['profile']['id']}/provision-admin-retry",
        headers=admin,
    )
    assert retried.status_code == 200, retried.text
    assert retried.json()["status"] == "PROVISIONED"
    with client.app.state.session_factory() as session:
        assert session.get(AgentInvitation, invite["id"]).status == "CONSUMED"


def test_external_agent_can_complete_self_service_onboarding_with_scoped_token(client, monkeypatch):
    response, identity_key, wallet = _register(client, agent_key="Partner-Self-Service-B")
    assert response.status_code == 201, response.text
    body = response.json()
    profile_id = _verify_ownership(client, response, identity_key, wallet)
    token = body["onboardingToken"]

    assert client.get(f"/api/v1/agent-onboarding/registrations/{profile_id}/status").status_code == 401
    status = client.get(
        f"/api/v1/agent-onboarding/registrations/{profile_id}/status",
        headers={"X-Onboarding-Token": token},
    )
    assert status.status_code == 200, status.text
    assert status.json()["admissionStatus"] == "PENDING_TECHNICAL_CHECKS"

    missing_runtime_token = client.post(
        f"/api/v1/agent-onboarding/registrations/{profile_id}/complete",
        headers={"X-Onboarding-Token": token},
        json={},
    )
    assert missing_runtime_token.status_code == 422
    assert "outside managed-testnet" in missing_runtime_token.json()["error"]["message"]

    monkeypatch.setattr(
        "app.workflow_api._run_agent_technical_checks",
        lambda session, profile, agent, runtime_token: {
            "healthCheckPassed": True,
            "manifestCheckPassed": True,
            "testTaskPassed": True,
            "gasReadyVerified": True,
            "checkedAt": datetime.now(timezone.utc).isoformat().replace("+00:00", "Z"),
            "endpointLatencyMs": 18,
            "manifestHash": "0x" + "33" * 32,
            "testResultHash": "sha256:" + "44" * 32,
            "gasBalanceWei": "1000000000000000000",
            "routeKey": "A_TO_B",
            "routeVersion": 1,
        },
    )
    runtime_token = "temporary-runner-token"
    completed = client.post(
        f"/api/v1/agent-onboarding/registrations/{profile_id}/complete",
        headers={"X-Onboarding-Token": token},
        json={"runtimeAccessToken": runtime_token},
    )
    assert completed.status_code == 200, completed.text
    completed_body = completed.json()
    assert completed_body["checksPassed"] is True
    assert completed_body["admissionStatus"] == "ACTIVE_AWAITING_HEARTBEAT"
    assert completed_body["matchable"] is False

    heartbeat = client.post(
        f"/api/v1/agent-onboarding/registrations/{profile_id}/heartbeat-challenge",
        json={"status": "ONLINE", "capacityAvailable": True, "endpointLatencyMs": 18},
    )
    assert heartbeat.status_code == 201, heartbeat.text
    challenge = heartbeat.json()
    accepted = client.post(
        f"/api/v1/agent-onboarding/registrations/{profile_id}/heartbeat",
        json={
            "challengeId": challenge["challengeId"],
            "status": "ONLINE",
            "capacityAvailable": True,
            "endpointLatencyMs": 18,
            "signature": identity_key.sign(
                challenge["message"].encode(),
                ec.ECDSA(hashes.SHA256()),
            ).hex(),
        },
    )
    assert accepted.status_code == 200, accepted.text
    assert accepted.json()["matchable"] is True

    active_status = client.get(
        f"/api/v1/agent-onboarding/registrations/{profile_id}/status",
        headers={"X-Onboarding-Token": token},
    )
    assert active_status.status_code == 200, active_status.text
    assert active_status.json()["admissionStatus"] == "ACTIVE"
    assert active_status.json()["matchable"] is True
    assert active_status.json()["steps"][-1]["status"] == "complete"

    directory_item = next(
        item for item in client.get("/api/v1/workflow-agents").json()["items"]
        if item["id"] == profile_id
    )
    assert runtime_token not in str(directory_item)
    assert token not in str(directory_item)
    assert "accessTokenHash" not in directory_item["metadata"]["onboarding"]


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

    activated = client.post(
        f"/api/v1/agent-onboarding/registrations/{profile_id}/activate",
        headers=auth_headers(client, "platform-admin", "activation-ok"),
        json={"notes": "Platform checks passed."},
    )
    assert activated.status_code == 200, activated.text
    profile = activated.json()["profile"]
    assert activated.json()["admissionStatus"] == "ACTIVE_AWAITING_HEARTBEAT"
    assert profile["active"] is True
    assert profile["availability"] == "OFFLINE"
    assert profile["gasReady"] is True
    assert profile["credentialStatus"] == "ACTIVE"
    assert profile["trustScore"] == 75


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
