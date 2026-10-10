from __future__ import annotations

import json
import os
import re
import base64
import binascii
import hashlib
import hmac
import ipaddress
import secrets
import socket
import time
import uuid
from datetime import datetime, timedelta, timezone
from decimal import Decimal
from pathlib import Path
from typing import Any
from urllib.parse import urlparse

import httpx
from fastapi import APIRouter, Depends, Header, HTTPException, Query, Request
from pydantic import ValidationError
from cryptography.exceptions import InvalidSignature
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import ec
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PublicKey
from sqlalchemy import func, select, update
from sqlalchemy.orm import Session

from .coordinator import (
    complete_failed_refund,
    complete_workflow_finalization,
    confirm_result_proof,
    confirm_settlement,
    confirm_workflow_funding,
    CHAIN_A,
    coordinate,
    create_workflow,
    fulfill_review_randomness,
    plan_workflow,
    report_attempt_failure,
    arbitration_vote_signing_payload,
    review_approval_signing_payload,
    resolve_review_challenge,
    submit_review_approval,
    submit_arbitration_vote,
    submit_review_challenge,
    submit_attempt_result,
    update_settlement_custody,
    workflow_ledger_summary,
)
from .database import get_session
from .integration_auth import require_listener_auth
from .models import Agent, Capability, utcnow
from .security import get_current_agent, idempotency_header, require_role
from .serializers import serialize
from .services import audit, canonical_hash, get_idempotent_response, save_idempotent_response
from .workflow_models import (
    AgentMatchCandidate,
    AgentMatchRound,
    AgentInvitation,
    AgentProvisionOutbox,
    ArbitrationVote,
    BudgetReservation,
    ChainRoute,
    CrossChainMessage,
    CoordinatorDecision,
    ExternalAgentOnboardingChallenge,
    FeeLedgerEntry,
    FailureIncident,
    GasDeposit,
    LedgerEntry,
    NodeAttempt,
    ProtocolJob,
    ProtocolRuntimeConfig,
    ReviewApproval,
    ReviewChallenge,
    RouteDeployment,
    SettlementRecoveryRecord,
    Workflow,
    WorkflowAgentProfile,
    WorkflowEvent,
    WorkflowNode,
)
from .workflow_schemas import (
    AttemptGasClosureConfirmation,
    AttemptGasDepositConfirmation,
    AttemptResultCreate,
    AgentTransactionCancelRequest,
    AgentInvitationCreate,
    ArbitrationVoteCreate,
    ArbitrationVotePayloadRequest,
    ChainRouteCreate,
    CoordinateRequest,
    ExternalAgentActivationReview,
    ExternalAgentHeartbeat,
    ExternalAgentHeartbeatChallengeRequest,
    ExternalAgentOnboardingCreate,
    AgentDescriptorOnboardingCreate,
    ExternalAgentOwnershipVerify,
    ExternalAgentSelfServiceCompletion,
    ExternalAgentTechnicalCheckRequest,
    FailedRefundConfirmation,
    ProtocolJobUpdate,
    ProtocolRuntimeContractUpdate,
    ResultProofConfirmation,
    ReviewApprovalCreate,
    ReviewApprovalPayloadRequest,
    ReviewChallengeCreate,
    ReviewChallengeResolution,
    ReviewChainStateConfirmation,
    RandomnessFulfillment,
    RouteUpdate,
    RouteDeploymentCompletion,
    SelfServiceInvitationRequest,
    SettlementConfirmation,
    SettlementCustodyUpdate,
    WorkflowAgentProfileCreate,
    WorkflowAgentProfileUpdate,
    WorkflowCreate,
    WorkflowFailureCreate,
    WorkflowFinalizationConfirmation,
    WorkflowFundingConfirmation,
    WorkflowPlanRequest,
)


router = APIRouter(prefix="/api/v1", tags=["dynamic-workflows"])


def _aware(value: datetime) -> datetime:
    return value if value.tzinfo is not None else value.replace(tzinfo=timezone.utc)


def _slugify_agent_key(value: str) -> str:
    slug = "".join(character.lower() if character.isalnum() else "-" for character in value)
    return "-".join(part for part in slug.split("-") if part)[:80]


def _managed_testnet() -> bool:
    return os.getenv("PLATFORM_PROFILE", "local-demo").lower() == "managed-testnet"


def _self_service_onboarding_enabled() -> bool:
    return _managed_testnet() and os.getenv("MANAGED_SELF_SERVICE_ONBOARDING", "false").lower() in {"1", "true", "yes"}


def _runtime_id_registered(session: Session, runtime_id: str | None) -> bool:
    if not runtime_id:
        return False
    return any(
        str(((profile.metadata_json or {}).get("onboarding") or {}).get("runtimeId") or "") == runtime_id
        for profile in session.scalars(select(WorkflowAgentProfile))
    )


def _resolved_route_rpc_url(value: str) -> str:
    if value != "secretref:DESTINATION_RPC_URL":
        return value
    if not _managed_testnet():
        raise ValueError("RPC secret references are only available in managed-testnet mode")
    resolved = os.getenv("DESTINATION_RPC_URL", "").strip()
    if not resolved:
        raise ValueError("DESTINATION_RPC_URL is not configured")
    return resolved


def _runner_control_request(method: str, path: str, payload: dict[str, object] | None = None) -> dict[str, object]:
    base_url = os.getenv("RUNNER_CONTROL_URL", "http://127.0.0.1:8010").rstrip("/")
    parsed = urlparse(base_url)
    if parsed.scheme != "http" or parsed.hostname not in {"127.0.0.1", "localhost", "::1"}:
        raise ValueError("RUNNER_CONTROL_URL must use loopback HTTP")
    secret = os.getenv("RUNNER_CONTROL_HMAC_SECRET", "")
    if not secret:
        raise ValueError("RUNNER_CONTROL_HMAC_SECRET is not configured")
    timestamp = str(int(time.time()))
    nonce = secrets.token_urlsafe(32)
    payload = payload or {}
    body = json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    signed = "\n".join((method.upper(), timestamp, nonce, path, body))
    signature = "sha256=" + hmac.new(secret.encode(), signed.encode(), hashlib.sha256).hexdigest()
    with httpx.Client(timeout=20, follow_redirects=False) as client:
        response = client.request(method, base_url + path, json=payload if method == "PUT" else None, headers={
            "X-Runner-Timestamp": timestamp,
            "X-Runner-Nonce": nonce,
            "X-Runner-Signature": signature,
        })
        response.raise_for_status()
        result = response.json()
    if not isinstance(result, dict):
        raise ValueError("Runner control response must be an object")
    return result


def _runtime_capabilities(session: Session, values: list[str], currency: str, price_floor: Decimal) -> list[dict[str, object]]:
    result: list[dict[str, object]] = []
    for capability_id in values:
        catalog = session.scalar(select(Capability).where(
            (Capability.id == capability_id) | (Capability.slug == capability_id)
        ))
        # Fail-closed: never silently degrade a missing capability to an empty
        # {"type": "object"} contract. This guards the class of bug where a
        # capability is provisioned before (or without) a matching catalog row,
        # which previously let an unconstrained schema pass acceptance silently.
        if catalog is None:
            raise HTTPException(status_code=422, detail=f"Unknown capability: {capability_id}")
        result.append({
            "capabilityId": capability_id,
            "pricingModel": "fixed",
            "basePrice": str(catalog.base_price),
            "currency": catalog.currency,
            "estimatedDurationSeconds": catalog.estimated_duration_seconds,
            "inputSchema": catalog.input_schema,
            "outputSchema": catalog.output_schema,
            "exampleInput": catalog.example_input,
            "schemaHash": canonical_hash({
                "input": catalog.input_schema,
                "output": catalog.output_schema,
            }),
        })
    return result


def _process_provision_outbox(session: Session, outbox: AgentProvisionOutbox) -> bool:
    profile = session.get(WorkflowAgentProfile, outbox.profile_id)
    invitation = session.get(AgentInvitation, outbox.invitation_id) if outbox.invitation_id else None
    if profile is None:
        outbox.status = "FAILED"
        outbox.last_error_hash = canonical_hash("profile_missing")
        session.commit()
        return False
    if outbox.status == "DEPROVISION_PENDING":
        try:
            _runner_control_request("DELETE", f"/internal/v1/runtimes/{outbox.provisioning_id}")
            outbox.status = "FAILED"
            session.commit()
        except (httpx.HTTPError, ValueError, KeyError, TypeError):
            outbox.next_attempt_at = utcnow() + timedelta(seconds=60)
            session.commit()
        return False
    try:
        result = _runner_control_request("PUT", f"/internal/v1/runtimes/{outbox.provisioning_id}", outbox.payload)
        if result.get("status") != "PROVISIONED":
            raise ValueError("Runner did not confirm PROVISIONED status")
        outbox.status = "COMPLETED"
        outbox.attempts += 1
        outbox.completed_at = utcnow()
        profile.onboarding_status = "PROVISIONED"
        metadata = dict(profile.metadata_json or {})
        onboarding = dict(metadata.get("onboarding") or {})
        onboarding["status"] = "PENDING_OWNERSHIP_PROOF"
        onboarding["provisionedAt"] = serialize(utcnow())
        metadata["onboarding"] = onboarding
        profile.metadata_json = metadata
        if invitation:
            invitation.status = "CONSUMED"
            invitation.consumed_at = utcnow()
        session.commit()
        return True
    except (httpx.HTTPError, ValueError, KeyError, TypeError) as exc:
        outbox.attempts += 1
        outbox.last_error_hash = canonical_hash(str(exc))
        outbox.next_attempt_at = utcnow() + timedelta(seconds=min(300, 2 ** min(outbox.attempts, 8)))
        max_attempts = max(1, int(os.getenv("AGENT_PROVISION_MAX_ATTEMPTS", "8")))
        outbox.status = "DEPROVISION_PENDING" if outbox.attempts >= max_attempts else "RETRY"
        profile.onboarding_status = "PROVISION_FAILED"
        metadata = dict(profile.metadata_json or {})
        onboarding = dict(metadata.get("onboarding") or {})
        onboarding["status"] = "PROVISION_FAILED"
        onboarding["lastProvisionFailureHash"] = outbox.last_error_hash
        metadata["onboarding"] = onboarding
        profile.metadata_json = metadata
        if invitation:
            invitation.status = "USED_FAILED"
            invitation.failure_reason_hash = outbox.last_error_hash
        session.commit()
        if outbox.status == "DEPROVISION_PENDING":
            try:
                _runner_control_request("DELETE", f"/internal/v1/runtimes/{outbox.provisioning_id}")
                outbox.status = "FAILED"
                session.commit()
            except (httpx.HTTPError, ValueError, KeyError, TypeError):
                pass
        return False


def _resolve_onboarding_route(session: Session, body: ExternalAgentOnboardingCreate) -> ChainRoute:
    managed_testnet = os.getenv("PLATFORM_PROFILE", "local-demo").lower() == "managed-testnet"
    query = select(ChainRoute).where(ChainRoute.status == "ACTIVE")
    query = query.where(ChainRoute.source_chain_id == (11155111 if managed_testnet else CHAIN_A))
    if body.preferred_route_key is not None:
        query = query.where(ChainRoute.route_key == body.preferred_route_key)
        if body.preferred_route_version is not None:
            query = query.where(ChainRoute.route_version == body.preferred_route_version)
    elif body.preferred_chain_id is not None:
        query = query.where(ChainRoute.destination_chain_id == body.preferred_chain_id)
    else:
        query = query.where(ChainRoute.payout_asset == body.payout_asset.upper())
    route = session.scalar(query.order_by(ChainRoute.route_version.desc()))
    if route is None:
        raise HTTPException(
            status_code=422,
            detail="No active route matches the requested payout asset or preferred chain",
        )
    if body.payout_asset.upper() != route.payout_asset:
        raise HTTPException(
            status_code=422,
            detail=f"payoutAsset {body.payout_asset} does not belong to chain {route.destination_chain_id}",
        )
    if body.settlement_asset.upper() != route.settlement_asset:
        raise HTTPException(
            status_code=422,
            detail=f"settlementAsset must be {route.settlement_asset} for route {route.route_key}",
        )
    return route


def _bytes_from_user_signature(value: str) -> bytes:
    raw = value.removeprefix("ed25519:").removeprefix("es256k:").removeprefix("base64:")
    if raw.startswith("0x"):
        try:
            return bytes.fromhex(raw[2:])
        except ValueError as exc:
            raise HTTPException(status_code=422, detail="Signature is not valid hex") from exc
    try:
        return bytes.fromhex(raw)
    except ValueError:
        try:
            return base64.urlsafe_b64decode(raw + "=" * (-len(raw) % 4))
        except (binascii.Error, ValueError) as exc:
            raise HTTPException(status_code=422, detail="Signature must be hex or base64") from exc


def _ed25519_public_key(value: str) -> Ed25519PublicKey:
    raw = value.removeprefix("ed25519:").removeprefix("0x")
    try:
        data = bytes.fromhex(raw)
    except ValueError as exc:
        raise HTTPException(status_code=422, detail="Agent publicKey must be an Ed25519 hex key") from exc
    if len(data) != 32:
        raise HTTPException(status_code=422, detail="Agent Ed25519 publicKey must be 32 bytes")
    return Ed25519PublicKey.from_public_bytes(data)


def _secp256k1_public_key(value: str) -> ec.EllipticCurvePublicKey:
    raw = value.removeprefix("es256k:").removeprefix("0x")
    try:
        data = bytes.fromhex(raw)
        return ec.EllipticCurvePublicKey.from_encoded_point(ec.SECP256K1(), data)
    except (ValueError, TypeError) as exc:
        raise HTTPException(
            status_code=422,
            detail="Agent publicKey must be a compressed or uncompressed secp256k1 key",
        ) from exc


def _identity_public_key_sha256(value: str) -> str:
    public_key = _operational_public_key(value)
    if not isinstance(public_key, ec.EllipticCurvePublicKey):
        raise HTTPException(status_code=422, detail="Managed Agent identity must use ES256K")
    encoded = public_key.public_bytes(
        serialization.Encoding.X962,
        serialization.PublicFormat.UncompressedPoint,
    )
    return hashlib.sha256(encoded).hexdigest()


def _operational_public_key(value: str) -> object:
    if value.startswith("ed25519:"):
        return _ed25519_public_key(value)
    return _secp256k1_public_key(value)


def _verify_identity_signature(agent: Agent, message: str, signature: str) -> None:
    try:
        public_key = _operational_public_key(agent.operational_public_key)
        if isinstance(public_key, Ed25519PublicKey):
            public_key.verify(_bytes_from_user_signature(signature), message.encode("utf-8"))
        else:
            public_key.verify(
                _bytes_from_user_signature(signature),
                message.encode("utf-8"),
                ec.ECDSA(hashes.SHA256()),
            )
    except InvalidSignature as exc:
        raise HTTPException(status_code=422, detail="Agent identity signature is invalid") from exc


def _verify_wallet_signature(wallet_address: str, message: str, signature: str) -> None:
    try:
        from eth_account import Account
        from eth_account.messages import encode_defunct
    except ImportError as exc:
        raise HTTPException(status_code=503, detail="Wallet signature verification requires eth-account") from exc
    try:
        signer = Account.recover_message(encode_defunct(text=message), signature=signature)
    except Exception as exc:
        raise HTTPException(status_code=422, detail="Wallet signature is invalid") from exc
    if signer.lower() != wallet_address.lower():
        raise HTTPException(status_code=422, detail="Wallet signature does not match homeWallet")


def _canonical_json_bytes(value: object) -> bytes:
    return json.dumps(
        value,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")


def _normalized_public_key(value: str) -> str:
    return value.removeprefix("es256k:").removeprefix("0x").lower()


def _verify_runtime_result(agent: Agent, result: dict[str, Any]) -> None:
    required_fields = (
        "agentId",
        "capabilityId",
        "completedAt",
        "did",
        "outputHash",
        "requestId",
    )
    if (
        result.get("did") != agent.did
        or result.get("agentId") != agent.slug
        or not all(field in result for field in required_fields)
    ):
        raise ValueError("Agent test result identity or signed fields are incomplete")
    output = result.get("output")
    expected_hash = "sha256:" + hashlib.sha256(_canonical_json_bytes(output)).hexdigest()
    if not secrets.compare_digest(str(result.get("outputHash")), expected_hash):
        raise ValueError("Agent test result outputHash does not match output")
    signature = result.get("signature")
    if not isinstance(signature, dict) or signature.get("algorithm") != "ES256K":
        raise ValueError("Agent test result must use ES256K")
    public_key = signature.get("publicKeyHex") or agent.operational_public_key
    if _normalized_public_key(str(public_key)) != _normalized_public_key(agent.operational_public_key):
        raise ValueError("Agent test result public key does not match registration")
    signed_payload = {field: result[field] for field in required_fields}
    if result.get("workflowId") is not None:
        context_fields = (
            "workflowId",
            "nodeId",
            "attemptId",
            "attemptNumber",
            "reservationId",
            "deadline",
            "nonce",
            "offerId",
        )
        if not all(field in result for field in context_fields):
            raise ValueError("Agent result workflow context is incomplete")
        signed_payload.update({field: result[field] for field in context_fields})
    _secp256k1_public_key(agent.operational_public_key).verify(
        _bytes_from_user_signature(str(signature.get("value", ""))),
        _canonical_json_bytes(signed_payload),
        ec.ECDSA(hashes.SHA256()),
    )


def _agent_endpoint_base(endpoint: str) -> str:
    return endpoint.rstrip("/").removesuffix("/invoke")


def _validate_agent_endpoint(endpoint: str) -> None:
    parsed = urlparse(endpoint)
    environment = os.getenv("APP_ENV", "development").lower()
    if parsed.scheme not in {"http", "https"} or not parsed.hostname:
        raise ValueError("Agent endpoint must be an HTTP(S) URL")
    managed_loopback = _managed_testnet() and parsed.scheme == "http" and parsed.hostname in {
        "127.0.0.1", "localhost", "::1",
    }
    if environment in {"production", "prod"} and parsed.scheme != "https" and not managed_loopback:
        raise ValueError("Production Agent endpoints must use HTTPS")
    if environment not in {"production", "prod"} or managed_loopback:
        return
    for result in socket.getaddrinfo(parsed.hostname, parsed.port or 443):
        address = ipaddress.ip_address(result[4][0])
        if address.is_private or address.is_loopback or address.is_link_local or address.is_reserved:
            raise ValueError("Production Agent endpoint resolves to a non-public address")


def _run_agent_technical_checks(
    session: Session,
    profile: WorkflowAgentProfile,
    agent: Agent,
    runtime_access_token: str,
) -> dict[str, Any]:
    # `metadata` carries the stored chain-resolution hints used below to pick
    # the active route; it lives on the profile and was previously only
    # referenced in the `complete` caller, not here (NameError).
    metadata = dict(profile.metadata_json or {})
    if not agent.endpoint or not profile.home_wallet:
        raise ValueError("Agent endpoint and homeWallet are required")
    base = _agent_endpoint_base(agent.endpoint)
    _validate_agent_endpoint(base)
    timeout = float(os.getenv("AGENT_INVOCATION_TIMEOUT_SECONDS", "120"))
    headers = {"Authorization": f"Bearer {runtime_access_token}"}
    started = time.perf_counter()
    with httpx.Client(timeout=timeout, follow_redirects=False) as client:
        health_response = client.get(f"{base}/health")
        health_response.raise_for_status()
        health = health_response.json()
        if str(health.get("status", "")).lower() not in {"ok", "online"}:
            raise ValueError("Agent health endpoint did not report online")

        manifest_response = client.get(f"{base}/manifest")
        manifest_response.raise_for_status()
        manifest = manifest_response.json()
        if manifest.get("did") != agent.did:
            raise ValueError("Agent manifest DID does not match registration")
        verification_method = manifest.get("verificationMethod") or {}
        manifest_key = verification_method.get("publicKeyHex", "")
        if _normalized_public_key(str(manifest_key)) != _normalized_public_key(agent.operational_public_key):
            raise ValueError("Agent manifest public key does not match registration")
        capabilities = manifest.get("capabilities") or []
        supported = {str(item.get("capabilityId", "")).lower() for item in capabilities}
        if not supported.intersection({item.lower() for item in profile.capabilities}):
            raise ValueError("Agent manifest does not expose a registered capability")
        selected = next(
            item for item in capabilities
            if str(item.get("capabilityId", "")).lower() in {value.lower() for value in profile.capabilities}
        )
        example_input = (selected.get("exampleRequest") or {}).get("input")
        if not isinstance(example_input, dict):
            raise ValueError("Agent manifest capability has no test input")
        request_id = f"activation:{profile.id}:{secrets.token_hex(8)}"
        invoke_response = client.post(
            f"{base}/test-invoke",
            headers=headers,
            json={"requestId": request_id, "input": example_input},
        )
        invoke_response.raise_for_status()
        result = invoke_response.json()
        _verify_runtime_result(agent, result)

    chain_resolution = dict(metadata.get("chainResolution") or {})
    route_query = select(ChainRoute).where(
        ChainRoute.destination_chain_id == profile.chain_id,
        ChainRoute.status == "ACTIVE",
    )
    if chain_resolution.get("matchedRouteKey"):
        route_query = route_query.where(ChainRoute.route_key == chain_resolution["matchedRouteKey"])
    if chain_resolution.get("matchedRouteVersion"):
        route_query = route_query.where(ChainRoute.route_version == chain_resolution["matchedRouteVersion"])
    route = session.scalar(route_query.order_by(ChainRoute.route_version.desc()))
    if route is None:
        raise ValueError("Agent homeChain has no active route")
    balance_hex = _rpc_call(_resolved_route_rpc_url(route.rpc_url), "eth_getBalance", [profile.home_wallet, "latest"])
    balance_wei = int(balance_hex, 16)
    minimum_balance = int(os.getenv("AGENT_MIN_GAS_BALANCE_WEI", "1"))
    if balance_wei < minimum_balance:
        raise ValueError("Agent homeWallet does not meet the minimum Gas balance")
    return {
        "healthCheckPassed": True,
        "manifestCheckPassed": True,
        "testTaskPassed": True,
        "gasReadyVerified": True,
        "checkedAt": serialize(utcnow()),
        "endpointLatencyMs": int((time.perf_counter() - started) * 1000),
        "manifestHash": canonical_hash(manifest),
        "testResultHash": result["outputHash"],
        "gasBalanceWei": str(balance_wei),
        "routeKey": route.route_key,
        "routeVersion": route.route_version,
    }


def _runtime_access_token(profile: WorkflowAgentProfile, agent: Agent) -> str:
    raw = os.getenv("AGENT_RUNTIME_TOKENS_JSON", "")
    if raw:
        configured = json.loads(raw)
        if not isinstance(configured, dict):
            raise ValueError("AGENT_RUNTIME_TOKENS_JSON must be an object")
        for key in (profile.agent_key, agent.slug, agent.did):
            value = configured.get(key)
            if isinstance(value, str) and value:
                return value
    token = os.getenv("AGENT_RUNNER_API_TOKEN", "")
    if not token:
        raise ValueError("No Agent Runner access token is configured")
    return token


def _workflow_agent_input(
    session: Session,
    workflow: Workflow,
    node: WorkflowNode,
) -> dict[str, Any]:
    dependencies: dict[str, Any] = {}
    for dependency_key in node.depends_on:
        dependency = session.scalar(
            select(WorkflowNode).where(
                WorkflowNode.workflow_id == workflow.id,
                WorkflowNode.node_key == dependency_key,
            )
        )
        if dependency is not None:
            dependencies[dependency_key] = dependency.output_payload or {}
    return {
        "objective": workflow.objective,
        "input": workflow.input_payload or {},
        "dependencies": dependencies,
    }


def _invoke_reserved_attempt(
    session: Session,
    workflow: Workflow,
    node: WorkflowNode,
    attempt: NodeAttempt,
) -> dict[str, Any]:
    profile = session.get(WorkflowAgentProfile, attempt.agent_profile_id)
    agent = session.get(Agent, attempt.agent_id)
    reservation = session.scalar(
        select(BudgetReservation).where(BudgetReservation.attempt_id == attempt.id)
    )
    if profile is None or agent is None or reservation is None or not agent.endpoint:
        raise ValueError("Assigned Agent runtime or reservation is incomplete")
    token = _runtime_access_token(profile, agent)
    base = _agent_endpoint_base(agent.endpoint)
    deadline = serialize(utcnow() + timedelta(minutes=15))
    offer_id = str(uuid.uuid4())
    nonce = secrets.token_urlsafe(32)
    offer = {
        "offerId": offer_id,
        "workflowId": workflow.id,
        "nodeId": node.id,
        "attemptId": attempt.id,
        "attemptNumber": attempt.attempt_number,
        "reservationId": reservation.id,
        "capabilityId": node.capability,
        "criteriaHash": canonical_hash(node.acceptance_criteria),
        "reward": str(reservation.amount),
        "settlementAsset": attempt.settlement_asset,
        "payoutAsset": attempt.payout_asset,
        "deadline": deadline,
        "nonce": nonce,
    }
    offer["coordinatorSignature"] = base64.urlsafe_b64encode(
        hmac.new(token.encode("utf-8"), _canonical_json_bytes(offer), hashlib.sha256).digest()
    ).rstrip(b"=").decode("ascii")
    headers = {"Authorization": f"Bearer {token}"}
    timeout = float(os.getenv("AGENT_INVOCATION_TIMEOUT_SECONDS", "120"))
    with httpx.Client(timeout=timeout, follow_redirects=False) as client:
        acceptance_response = client.post(f"{base}/task-offers", headers=headers, json=offer)
        acceptance_response.raise_for_status()
        acceptance = acceptance_response.json()
        if acceptance.get("accepted") is not True or acceptance.get("attemptId") != attempt.id:
            raise ValueError("Agent did not accept the current task offer")
        invocation = {
            "requestId": offer_id,
            "offerId": offer_id,
            "workflowId": workflow.id,
            "nodeId": node.id,
            "attemptId": attempt.id,
            "attemptNumber": attempt.attempt_number,
            "reservationId": reservation.id,
            "deadline": deadline,
            "nonce": nonce,
            "input": _workflow_agent_input(session, workflow, node),
        }
        attempt.status = "EXECUTING"
        invoke_response = client.post(f"{base}/invoke", headers=headers, json=invocation)
        invoke_response.raise_for_status()
        envelope = invoke_response.json()
    _verify_runtime_result(agent, envelope)
    expected_context = {
        "workflowId": workflow.id,
        "nodeId": node.id,
        "attemptId": attempt.id,
        "attemptNumber": attempt.attempt_number,
        "reservationId": reservation.id,
        "offerId": offer_id,
        "deadline": deadline,
        "nonce": nonce,
    }
    if any(envelope.get(key) != value for key, value in expected_context.items()):
        raise ValueError("Agent result context does not match its accepted offer")
    proof_hash = canonical_hash(
        {"offerId": offer_id, "runtimeSignature": envelope.get("signature")}
    )
    submission = submit_attempt_result(
        session,
        workflow,
        node,
        attempt,
        AttemptResultCreate(
            output=envelope["output"],
            proofHash=proof_hash,
            runtimeEnvelope=envelope,
        ),
    )
    return {
        "action": "agent_result_submitted",
        "agentKey": attempt.agent_key,
        "attemptId": attempt.id,
        "resultHash": submission["resultHash"],
    }


def _onboarding_profile(session: Session, profile_id: str) -> tuple[WorkflowAgentProfile, Agent]:
    profile = session.get(WorkflowAgentProfile, profile_id)
    if profile is None:
        raise HTTPException(status_code=404, detail="Workflow Agent profile not found")
    agent = session.get(Agent, profile.agent_id)
    if agent is None:
        raise HTTPException(status_code=409, detail="Workflow Agent profile has no Agent")
    return profile, agent


def _onboarding_access_token_hash(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


def _require_onboarding_access(profile: WorkflowAgentProfile, token: str | None) -> None:
    metadata = dict(profile.metadata_json or {})
    onboarding = dict(metadata.get("onboarding") or {})
    expected = str(onboarding.get("accessTokenHash") or "")
    if not token or not expected or not secrets.compare_digest(_onboarding_access_token_hash(token), expected):
        raise HTTPException(status_code=401, detail="Missing or invalid onboarding token")
    expires_at_raw = onboarding.get("accessTokenExpiresAt")
    if expires_at_raw:
        expires_at = datetime.fromisoformat(str(expires_at_raw).replace("Z", "+00:00"))
        if _aware(expires_at) <= utcnow():
            raise HTTPException(status_code=401, detail="Onboarding token has expired")


def _public_onboarding_profile(profile: WorkflowAgentProfile, agent: Agent) -> dict[str, Any]:
    result = serialize(profile)
    metadata = dict(result.get("metadata") or {})
    onboarding = dict(metadata.get("onboarding") or {})
    onboarding.pop("accessTokenHash", None)
    metadata["onboarding"] = onboarding
    result["metadata"] = metadata
    result["agent"] = serialize(agent)
    return result


def _onboarding_status_response(
    session: Session,
    profile: WorkflowAgentProfile,
    agent: Agent,
) -> dict[str, Any]:
    metadata = dict(profile.metadata_json or {})
    verification = dict(metadata.get("verification") or {})
    identity_verified = bool(verification.get("identityVerified"))
    wallet_verified = bool(verification.get("walletVerified"))
    ownership_verified = identity_verified and wallet_verified
    roles = sorted(set(agent.roles or []))
    is_provider = "provider" in roles
    is_requester = "requester" in roles
    check_names = (
        "healthCheckPassed",
        "manifestCheckPassed",
        "testTaskPassed",
        "gasReadyVerified",
    )
    checks_passed = all(bool(verification.get(name)) for name in check_names) if is_provider else ownership_verified
    matchable = (
        is_provider
        and agent.status == "active"
        and profile.active
        and profile.availability == "ONLINE"
        and profile.capacity_available
        and profile.gas_ready
        and profile.credential_status == "ACTIVE"
    )
    if profile.onboarding_status == "REVOKED":
        admission_status = "REVOKED"
    elif profile.active and not is_provider:
        admission_status = "ACTIVE"
    elif matchable:
        admission_status = "ACTIVE"
    elif profile.active:
        admission_status = "ACTIVE_AWAITING_HEARTBEAT"
    elif ownership_verified:
        admission_status = "PENDING_TECHNICAL_CHECKS"
    else:
        admission_status = "PENDING_OWNERSHIP_PROOF"
    if is_provider and _managed_testnet() and profile.onboarding_status in {"PENDING_PROVISION", "PROVISION_FAILED"}:
        admission_status = profile.onboarding_status
    technical_status = (
        "complete" if checks_passed
        else "failed" if verification.get("lastCheckFailureHash") and ownership_verified
        else "current" if ownership_verified
        else "pending"
    )
    steps = [
        {"key": "registration", "label": "Agent registration", "status": "complete"},
        {"key": "ownership", "label": "Identity and wallet proof", "status": "complete" if ownership_verified else "current"},
    ]
    if is_provider:
        steps.append({"key": "technicalChecks", "label": "Runtime and Gas checks", "status": technical_status})
    steps.append({"key": "activation", "label": "Market activation", "status": "complete" if profile.active else "current" if checks_passed else "pending"})
    if is_provider:
        steps.append({"key": "heartbeat", "label": "Signed heartbeat", "status": "complete" if matchable else "current" if profile.active else "pending"})
    route = session.scalar(
        select(ChainRoute)
        .where(
            ChainRoute.source_chain_id == CHAIN_A,
            ChainRoute.destination_chain_id == profile.chain_id,
            ChainRoute.status == "ACTIVE",
        )
        .order_by(ChainRoute.route_version.desc())
    )
    return {
        "profileId": profile.id,
        "admissionStatus": admission_status,
        "matchable": matchable,
        "roles": roles,
        "canRequest": is_requester and agent.status == "active",
        "canProvide": is_provider and agent.status == "active" and profile.active,
        "agent": {
            "id": agent.id,
            "agentKey": profile.agent_key,
            "did": agent.did,
            "name": agent.display_name,
            "endpoint": agent.endpoint,
        },
        "network": {
            "chainId": profile.chain_id,
            "homeWallet": profile.home_wallet,
            "routeKey": route.route_key if route else None,
            "payoutAsset": route.payout_asset if route else None,
        },
        "verification": {
            "identityVerified": identity_verified,
            "walletVerified": wallet_verified,
            "healthCheckPassed": bool(verification.get("healthCheckPassed")),
            "manifestCheckPassed": bool(verification.get("manifestCheckPassed")),
            "testTaskPassed": bool(verification.get("testTaskPassed")),
            "gasReadyVerified": bool(verification.get("gasReadyVerified")),
            "checkedAt": verification.get("checkedAt"),
        },
        "steps": steps,
        "updatedAt": serialize(profile.updated_at),
    }


def _challenge_message(profile: WorkflowAgentProfile, agent: Agent, nonce: str, expires_at: str) -> str:
    return "\n".join(
        [
            "Agent onboarding ownership challenge",
            f"profileId={profile.id}",
            f"agentKey={profile.agent_key}",
            f"did={agent.did}",
            f"homeWallet={profile.home_wallet or ''}",
            f"chainId={profile.chain_id}",
            f"nonce={nonce}",
            f"expiresAt={expires_at}",
        ]
    )


def _heartbeat_message(
    profile: WorkflowAgentProfile,
    agent: Agent,
    nonce: str,
    expires_at: str,
    body: ExternalAgentHeartbeatChallengeRequest,
) -> str:
    return "\n".join(
        [
            "Agent heartbeat challenge",
            f"profileId={profile.id}",
            f"agentKey={profile.agent_key}",
            f"did={agent.did}",
            f"status={body.status}",
            f"capacityAvailable={body.capacity_available}",
            f"endpointLatencyMs={body.endpoint_latency_ms}",
            f"nonce={nonce}",
            f"expiresAt={expires_at}",
        ]
    )


def _rpc_call(rpc_url: str, method: str, params: list[Any]) -> Any:
    response = httpx.post(
        rpc_url,
        json={"jsonrpc": "2.0", "id": 1, "method": method, "params": params},
        timeout=5,
    )
    response.raise_for_status()
    payload = response.json()
    if payload.get("error"):
        raise ValueError(f"RPC {method} failed: {payload['error']}")
    return payload.get("result")


def _project_root() -> Path:
    """Resolve the directory that holds `.runtime/`.

    The repository layout is `<root>/apps/api/app/workflow_api.py`, but the API
    image flattens it to `/app/app/workflow_api.py` (see `apps/api/Dockerfile`),
    where only three ancestors exist. Hardcoding `parents[3]` therefore raised
    `IndexError: 3` inside the container and turned every caller into a 500.
    """
    override = os.getenv("PROJECT_ROOT", "").strip()
    if override:
        return Path(override).expanduser()
    ancestors = list(Path(__file__).resolve().parents)
    for candidate in ancestors:
        if (candidate / ".runtime").is_dir():
            return candidate
    if len(ancestors) > 3:
        return ancestors[3]
    # Container layout: fall back to the outermost ancestor below the filesystem
    # root (`/app`) instead of walking off the end of the list.
    below_root = [item for item in ancestors if item != item.parent]
    return below_root[-1] if below_root else Path(".").resolve()


def _default_runtime_dirname() -> str:
    profile = os.getenv("PLATFORM_PROFILE", "").strip().lower()
    return "managed-testnet" if profile == "managed-testnet" else "local-v1"


def _local_protocol_manifest() -> dict[str, Any]:
    try:
        project_root = _project_root()
        runtime_dir = Path(
            os.getenv("LOCAL_PROTOCOL_RUNTIME_DIR", str(project_root / ".runtime" / _default_runtime_dirname()))
        )
        if not runtime_dir.is_absolute():
            runtime_dir = project_root / runtime_dir
        manifest_path = Path(os.getenv("LOCAL_PROTOCOL_MANIFEST", str(runtime_dir / "protocol-base.json")))
        manifest = json.loads(manifest_path.read_text(encoding="utf-8-sig"))
        chain_id = int(manifest["chainId"])
        if chain_id <= 0:
            raise ValueError("chainId must be a positive integer")
        contracts = manifest["contracts"]
        required = {"taskEscrow", "sourceToken", "gasTreasury"}
        if not required.issubset(contracts):
            raise ValueError(f"manifest is missing {sorted(required - set(contracts))}")
        return manifest
    except Exception as exc:
        raise HTTPException(status_code=503, detail=f"Local protocol manifest is unavailable: {exc}") from exc


def _local_protocol_contracts() -> dict[str, str]:
    contracts = _local_protocol_manifest()["contracts"]
    return {key: str(value).lower() for key, value in contracts.items()}


def _function_selector(rpc_url: str, signature: str) -> str:
    encoded = "0x" + signature.encode("utf-8").hex()
    result = _rpc_call(rpc_url, "web3_sha3", [encoded])
    if not isinstance(result, str) or len(result) < 10:
        raise ValueError(f"RPC returned no selector for {signature}")
    return result[:10].lower()


def _event_topic(rpc_url: str, signature: str) -> str:
    encoded = "0x" + signature.encode("utf-8").hex()
    result = _rpc_call(rpc_url, "web3_sha3", [encoded])
    if not isinstance(result, str) or len(result) != 66:
        raise ValueError(f"RPC returned no event topic for {signature}")
    return result.lower()


def _calldata_words(transaction: dict[str, Any], count: int) -> tuple[str, list[str]]:
    calldata = str(transaction.get("input") or "")
    expected_length = 10 + count * 64
    if not calldata.startswith("0x") or len(calldata) != expected_length:
        raise HTTPException(status_code=422, detail="Transaction calldata has an unexpected length")
    return calldata[:10].lower(), [calldata[10 + index * 64 : 10 + (index + 1) * 64].lower() for index in range(count)]


def _topic_address(address: str) -> str:
    return "0x" + address.lower().removeprefix("0x").rjust(64, "0")


def _verify_lock_transaction(workflow: Workflow, body: WorkflowFundingConfirmation) -> None:
    rpc_url = os.getenv("LOCAL_CHAIN_A_RPC_URL", os.getenv("CHAIN_A_RPC_URL", "http://127.0.0.1:8545"))
    contracts = _local_protocol_contracts()
    try:
        receipt = _rpc_call(rpc_url, "eth_getTransactionReceipt", [body.lock_tx_hash])
        transaction = _rpc_call(rpc_url, "eth_getTransactionByHash", [body.lock_tx_hash])
        if not receipt or receipt.get("status") != "0x1" or not transaction:
            raise HTTPException(status_code=422, detail="lockTxHash is not a successful Chain A transaction")
        if transaction.get("from", "").lower() != body.wallet_address.lower():
            raise HTTPException(status_code=422, detail="lock transaction sender does not match walletAddress")
        if transaction.get("to", "").lower() != contracts["taskEscrow"]:
            raise HTTPException(status_code=422, detail="lock transaction was not sent to the configured TaskEscrow")
        if int(transaction.get("value", "0x0"), 16) != 0:
            raise HTTPException(status_code=422, detail="lockBudget must not transfer native currency")

        selector, words = _calldata_words(transaction, 5)
        if selector != _function_selector(rpc_url, "lockBudget(bytes32,bytes32,address,uint256,uint256)"):
            raise HTTPException(status_code=422, detail="lock transaction does not call TaskEscrow.lockBudget")
        token_decimals_result = _rpc_call(
            rpc_url,
            "eth_call",
            [{"to": contracts["sourceToken"], "data": "0x313ce567"}, "latest"],
        )
        token_decimals = int(token_decimals_result, 16)
        expected_budget = int(Decimal(str(workflow.budget)) * (Decimal(10) ** token_decimals))
        expected_fee = int(Decimal(str(workflow.platform_fee)) * (Decimal(10) ** token_decimals))
        expected_workflow_id = str(workflow.onchain_workflow_id).lower().removeprefix("0x")
        expected_terms_hash = workflow.terms_hash.lower().removeprefix("0x")
        if words[0] != expected_workflow_id or words[1] != expected_terms_hash:
            raise HTTPException(status_code=422, detail="lock transaction workflowId or termsHash does not match the workflow")
        if "0x" + words[2][-40:] != contracts["sourceToken"]:
            raise HTTPException(status_code=422, detail="lock transaction token does not match the configured source token")
        if int(words[3], 16) != expected_budget or int(words[4], 16) != expected_fee:
            raise HTTPException(status_code=422, detail="lock transaction budget or platform fee does not match the workflow")

        lock_topic = _event_topic(rpc_url, "WorkflowBudgetLocked(bytes32,address,address,uint256,uint256)")
        expected_topics = [
            lock_topic,
            "0x" + expected_workflow_id,
            _topic_address(body.wallet_address),
            _topic_address(contracts["sourceToken"]),
        ]
        matching_logs = [
            log for log in receipt.get("logs", [])
            if log.get("address", "").lower() == contracts["taskEscrow"]
            and [str(topic).lower() for topic in log.get("topics", [])] == expected_topics
            and len(str(log.get("data", ""))) == 130
            and int(str(log["data"])[2:66], 16) == expected_budget
            and int(str(log["data"])[66:130], 16) == expected_fee
        ]
        if len(matching_logs) != 1:
            raise HTTPException(status_code=422, detail="lock transaction is missing the exact WorkflowBudgetLocked event")

        gas_amount = Decimal(body.source_gas_amount)
        if gas_amount > 0 and not body.source_gas_tx_hash:
            raise HTTPException(status_code=422, detail="sourceGasTxHash is required when sourceGasAmount is greater than zero")
        if gas_amount == 0 and body.source_gas_tx_hash:
            raise HTTPException(status_code=422, detail="sourceGasTxHash must be omitted when sourceGasAmount is zero")
        if body.source_gas_tx_hash:
            gas_receipt = _rpc_call(rpc_url, "eth_getTransactionReceipt", [body.source_gas_tx_hash])
            gas_transaction = _rpc_call(rpc_url, "eth_getTransactionByHash", [body.source_gas_tx_hash])
            if not gas_receipt or gas_receipt.get("status") != "0x1" or not gas_transaction:
                raise HTTPException(status_code=422, detail="sourceGasTxHash is not a successful Chain A transaction")
            if gas_transaction.get("from", "").lower() != body.wallet_address.lower():
                raise HTTPException(status_code=422, detail="source Gas transaction sender does not match walletAddress")
            if gas_transaction.get("to", "").lower() != contracts["gasTreasury"]:
                raise HTTPException(status_code=422, detail="source Gas transaction was not sent to GasTreasuryA")
            gas_selector, gas_words = _calldata_words(gas_transaction, 1)
            if gas_selector != _function_selector(rpc_url, "depositSourceGas(bytes32)") or gas_words[0] != expected_workflow_id:
                raise HTTPException(status_code=422, detail="source Gas transaction does not fund this workflow")
            expected_wei = int(gas_amount * (Decimal(10) ** 18))
            if int(gas_transaction.get("value", "0x0"), 16) != expected_wei:
                raise HTTPException(status_code=422, detail="source Gas transaction value does not match sourceGasAmount")
    except HTTPException:
        raise
    except Exception as exc:
        raise HTTPException(status_code=503, detail=f"Local Chain A funding verification failed: {exc}") from exc


def _workflow(session: Session, workflow_id: str) -> Workflow:
    item = session.get(Workflow, workflow_id)
    if item is None:
        item = session.scalar(select(Workflow).where(Workflow.reference_code == workflow_id))
    if item is None:
        raise HTTPException(status_code=404, detail="Workflow not found")
    return item


def _node(session: Session, workflow: Workflow, node_id: str) -> WorkflowNode:
    item = session.get(WorkflowNode, node_id)
    if item is None:
        item = session.scalar(
            select(WorkflowNode).where(
                WorkflowNode.workflow_id == workflow.id,
                WorkflowNode.node_key == node_id,
            )
        )
    if item is None or item.workflow_id != workflow.id:
        raise HTTPException(status_code=404, detail="Workflow node not found")
    return item


def _can_manage(workflow: Workflow, actor: Agent) -> None:
    if workflow.requester_id != actor.id and "admin" not in actor.roles:
        raise HTTPException(status_code=403, detail="Only the requester or an admin can coordinate this workflow")


def workflow_detail(session: Session, workflow: Workflow) -> dict[str, Any]:
    nodes = list(
        session.scalars(
            select(WorkflowNode)
            .where(WorkflowNode.workflow_id == workflow.id)
            .order_by(WorkflowNode.created_at, WorkflowNode.node_key)
        )
    )
    node_items: list[dict[str, Any]] = []
    for node in nodes:
        item = serialize(node)
        item["criteriaHash"] = canonical_hash(node.acceptance_criteria)
        attempts = list(
            session.scalars(
                select(NodeAttempt)
                .where(NodeAttempt.node_id == node.id)
                .order_by(NodeAttempt.attempt_number)
            )
        )
        attempt_items: list[dict[str, Any]] = []
        for attempt in attempts:
            attempt_item = serialize(attempt)
            attempt_item["reviewApprovals"] = serialize(
                list(
                    session.scalars(
                        select(ReviewApproval)
                        .where(ReviewApproval.attempt_id == attempt.id)
                        .order_by(ReviewApproval.created_at)
                    )
                )
            )
            attempt_item["reviewChallenges"] = serialize(
                list(
                    session.scalars(
                        select(ReviewChallenge)
                        .where(ReviewChallenge.attempt_id == attempt.id)
                        .order_by(ReviewChallenge.created_at)
                    )
                )
            )
            attempt_item["arbitrationVotes"] = serialize(
                list(
                    session.scalars(
                        select(ArbitrationVote)
                        .where(ArbitrationVote.attempt_id == attempt.id)
                        .order_by(ArbitrationVote.created_at)
                    )
                )
            )
            attempt_item["settlementRecovery"] = serialize(
                session.scalar(
                    select(SettlementRecoveryRecord).where(SettlementRecoveryRecord.attempt_id == attempt.id)
                )
            )
            attempt_items.append(attempt_item)
        item["attempts"] = attempt_items
        item["currentAttempt"] = next(
            (attempt_item for attempt_item in attempt_items if attempt_item["id"] == node.current_attempt_id),
            None,
        )
        item["reservations"] = serialize(
            list(
                session.scalars(
                    select(BudgetReservation)
                    .where(BudgetReservation.node_id == node.id)
                    .order_by(BudgetReservation.created_at)
                )
            )
        )
        match_rounds: list[dict[str, Any]] = []
        for match_round in session.scalars(
            select(AgentMatchRound)
            .where(AgentMatchRound.node_id == node.id)
            .order_by(AgentMatchRound.round_number)
        ):
            round_item = serialize(match_round)
            candidate_items: list[dict[str, Any]] = []
            for candidate in session.scalars(
                select(AgentMatchCandidate)
                .where(AgentMatchCandidate.match_round_id == match_round.id)
                .order_by(AgentMatchCandidate.rank)
            ):
                candidate_item = serialize(candidate)
                profile = session.get(WorkflowAgentProfile, candidate.agent_profile_id)
                agent = session.get(Agent, profile.agent_id) if profile is not None else None
                route = session.get(ChainRoute, candidate.route_id)
                candidate_item["agent"] = serialize(agent)
                candidate_item["routeKey"] = route.route_key if route is not None else None
                candidate_items.append(candidate_item)
            round_item["candidates"] = candidate_items
            match_rounds.append(round_item)
        item["matchRounds"] = match_rounds
        item["failureIncidents"] = serialize(
            list(
                session.scalars(
                    select(FailureIncident)
                    .where(FailureIncident.node_id == node.id)
                    .order_by(FailureIncident.created_at)
                )
            )
        )
        node_items.append(item)

    data = serialize(workflow)
    data["requester"] = serialize(session.get(Agent, workflow.requester_id))
    data["nodes"] = node_items
    data["events"] = serialize(
        list(
            session.scalars(
                select(WorkflowEvent)
                .where(WorkflowEvent.workflow_id == workflow.id)
                .order_by(WorkflowEvent.sequence)
            )
        )
    )
    data["messages"] = serialize(
        list(
            session.scalars(
                select(CrossChainMessage)
                .where(CrossChainMessage.workflow_id == workflow.id)
                .order_by(CrossChainMessage.created_at)
            )
        )
    )
    data["ledger"] = serialize(
        list(
            session.scalars(
                select(LedgerEntry)
                .where(LedgerEntry.workflow_id == workflow.id)
                .order_by(LedgerEntry.sequence)
            )
        )
    )
    data["gasDeposits"] = serialize(
        list(
            session.scalars(
                select(GasDeposit)
                .where(GasDeposit.workflow_id == workflow.id)
                .order_by(GasDeposit.created_at)
            )
        )
    )
    data["feeLedger"] = serialize(
        list(
            session.scalars(
                select(FeeLedgerEntry)
                .where(FeeLedgerEntry.workflow_id == workflow.id)
                .order_by(FeeLedgerEntry.created_at)
            )
        )
    )
    data["coordinatorDecisions"] = serialize(
        list(
            session.scalars(
                select(CoordinatorDecision)
                .where(CoordinatorDecision.workflow_id == workflow.id)
                .order_by(CoordinatorDecision.created_at)
            )
        )
    )
    data["protocolJobs"] = serialize(
        list(
            session.scalars(
                select(ProtocolJob)
                .where(ProtocolJob.workflow_id == workflow.id)
                .order_by(ProtocolJob.created_at)
            )
        )
    )
    data["ledgerSummary"] = workflow_ledger_summary(session, workflow)
    return data


@router.get("/workflows")
def list_workflows(
    status: str | None = Query(default=None),
    limit: int = Query(default=100, ge=1, le=500),
    session: Session = Depends(get_session),
) -> dict[str, Any]:
    query = select(Workflow).order_by(Workflow.created_at.desc()).limit(limit)
    count_query = select(func.count()).select_from(Workflow)
    if status:
        query = query.where(Workflow.status == status.upper())
        count_query = count_query.where(Workflow.status == status.upper())
    items = list(session.scalars(query))
    response_items: list[dict[str, Any]] = []
    for item in items:
        value = workflow_detail(session, item)
        value["nodeCount"] = session.scalar(
            select(func.count()).select_from(WorkflowNode).where(WorkflowNode.workflow_id == item.id)
        ) or 0
        value["settledNodeCount"] = session.scalar(
            select(func.count())
            .select_from(WorkflowNode)
            .where(WorkflowNode.workflow_id == item.id, WorkflowNode.status == "SETTLED")
        ) or 0
        response_items.append(value)
    return {"items": response_items, "total": session.scalar(count_query) or 0}


@router.get("/workflow-funding-config")
def get_workflow_funding_config() -> dict[str, Any]:
    manifest = _local_protocol_manifest()
    contracts = {key: str(value).lower() for key, value in manifest["contracts"].items()}
    chain_id = int(manifest["chainId"])
    local_testnet = os.getenv("PLATFORM_PROFILE", "local-demo").lower() == "local-demo"
    default_gas = os.getenv("WORKFLOW_DEFAULT_SOURCE_GAS_AMOUNT", "0.01" if local_testnet else "0")
    try:
        if Decimal(default_gas) < 0:
            raise ValueError("amount must not be negative")
    except Exception as exc:
        raise HTTPException(status_code=503, detail=f"Invalid WORKFLOW_DEFAULT_SOURCE_GAS_AMOUNT: {exc}") from exc
    return {
        "profile": "local-demo" if local_testnet else "managed-testnet",
        "chainId": chain_id,
        "chainName": "Chain A (local)" if local_testnet else "Chain A",
        "assetLabel": "MockUSDC" if local_testnet else "settlement token",
        "localTestnet": local_testnet,
        "defaultSourceGasAmount": default_gas,
        "contracts": {
            "sourceToken": contracts["sourceToken"],
            "taskEscrow": contracts["taskEscrow"],
            "gasTreasury": contracts["gasTreasury"],
        },
    }


@router.post("/workflow-plans")
def post_workflow_plan(
    body: WorkflowPlanRequest,
    actor: Agent = Depends(get_current_agent),
    idem: str | None = Depends(idempotency_header),
    session: Session = Depends(get_session),
) -> dict[str, Any]:
    if "requester" not in actor.roles and "admin" not in actor.roles:
        raise HTTPException(status_code=403, detail="Requester role is required")
    payload = body.model_dump(by_alias=True)
    replay = get_idempotent_response(session, actor.id, "dynamic_workflows.plan", idem, payload)
    if replay:
        return replay
    response = plan_workflow(session, body)
    save_idempotent_response(session, actor.id, "dynamic_workflows.plan", idem, payload, response)
    session.commit()
    return response


@router.post("/workflows", status_code=201)
def post_workflow(
    body: WorkflowCreate,
    actor: Agent = Depends(get_current_agent),
    idem: str | None = Depends(idempotency_header),
    session: Session = Depends(get_session),
) -> dict[str, Any]:
    if "requester" not in actor.roles and "admin" not in actor.roles:
        raise HTTPException(status_code=403, detail="Requester role is required")
    payload = body.model_dump(by_alias=True)
    replay = get_idempotent_response(session, actor.id, "dynamic_workflows.create", idem, payload)
    if replay:
        return replay
    item = create_workflow(session, actor, body)
    response = workflow_detail(session, item)
    save_idempotent_response(session, actor.id, "dynamic_workflows.create", idem, payload, response)
    session.commit()
    return response


@router.get("/workflows/{workflow_id}")
def get_workflow(workflow_id: str, session: Session = Depends(get_session)) -> dict[str, Any]:
    return workflow_detail(session, _workflow(session, workflow_id))


@router.post("/workflows/{workflow_id}/funding-confirmations")
def post_funding_confirmation(
    workflow_id: str,
    body: WorkflowFundingConfirmation,
    request: Request,
    actor: Agent = Depends(get_current_agent),
    idem: str | None = Depends(idempotency_header),
    session: Session = Depends(get_session),
) -> dict[str, Any]:
    workflow = _workflow(session, workflow_id)
    _can_manage(workflow, actor)
    payload = body.model_dump(by_alias=True)
    route = f"dynamic_workflows.{workflow.id}.funding"
    replay = get_idempotent_response(session, actor.id, route, idem, payload)
    if replay:
        return replay
    if body.verification_mode == "TEST":
        if request.app.state.settings.app_env != "test":
            raise HTTPException(status_code=403, detail="TEST funding verification is available only in automated tests")
    else:
        duplicate_lock = session.scalar(
            select(Workflow).where(Workflow.lock_tx_hash == body.lock_tx_hash, Workflow.id != workflow.id)
        )
        if duplicate_lock is not None:
            raise HTTPException(status_code=409, detail="lockTxHash has already funded another workflow")
        _verify_lock_transaction(workflow, body)
    confirmation = confirm_workflow_funding(session, workflow, body)
    session.flush()
    response = {**confirmation, "workflow": workflow_detail(session, workflow)}
    save_idempotent_response(session, actor.id, route, idem, payload, response)
    session.commit()
    return response


@router.post("/workflows/{workflow_id}/coordinate")
def post_coordinate(
    workflow_id: str,
    body: CoordinateRequest,
    actor: Agent = Depends(get_current_agent),
    idem: str | None = Depends(idempotency_header),
    session: Session = Depends(get_session),
) -> dict[str, Any]:
    item = _workflow(session, workflow_id)
    _can_manage(item, actor)
    payload = body.model_dump(by_alias=True)
    route = f"dynamic_workflows.{item.id}.coordinate"
    replay = get_idempotent_response(session, actor.id, route, idem, payload)
    if replay:
        return replay
    actions = coordinate(session, item, body.max_actions)
    auto_invoke = os.getenv("AUTO_INVOKE_AGENTS", "false").lower() in {"1", "true", "yes"}
    dispatched = next((action for action in actions if action.get("action") == "node_dispatched"), None)
    if auto_invoke and dispatched is not None and item.funding_mode == "RESERVED":
        node = _node(session, item, str(dispatched["nodeId"]))
        attempt = session.get(NodeAttempt, str(dispatched["attemptId"]))
        if attempt is None:
            raise HTTPException(status_code=409, detail="Dispatched attempt disappeared before Agent invocation")
        try:
            actions.append(_invoke_reserved_attempt(session, item, node, attempt))
        except (httpx.TimeoutException, httpx.ConnectError) as exc:
            actions.append(
                report_attempt_failure(
                    session,
                    item,
                    node,
                    attempt,
                    "AGENT_TIMEOUT",
                    {"errorHash": canonical_hash(str(exc)), "phase": "AGENT_INVOKE"},
                )
            )
        except (InvalidSignature, ValueError, KeyError, TypeError, httpx.HTTPError) as exc:
            actions.append(
                report_attempt_failure(
                    session,
                    item,
                    node,
                    attempt,
                    "AGENT_INVALID_SIGNATURE",
                    {"errorHash": canonical_hash(str(exc)), "phase": "AGENT_RESULT"},
                )
            )
    response = {"actions": actions, "workflow": workflow_detail(session, item)}
    save_idempotent_response(session, actor.id, route, idem, payload, response)
    session.commit()
    return response


@router.post("/workflows/{workflow_id}/nodes/{node_id}/attempts/{attempt_id}/results", status_code=202)
def post_attempt_result(
    workflow_id: str,
    node_id: str,
    attempt_id: str,
    body: AttemptResultCreate,
    actor: Agent = Depends(get_current_agent),
    idem: str | None = Depends(idempotency_header),
    session: Session = Depends(get_session),
) -> dict[str, Any]:
    workflow = _workflow(session, workflow_id)
    node = _node(session, workflow, node_id)
    attempt = session.get(NodeAttempt, attempt_id)
    if attempt is None or attempt.node_id != node.id:
        raise HTTPException(status_code=404, detail="Node attempt not found")
    if actor.id != attempt.agent_id and "admin" not in actor.roles:
        raise HTTPException(status_code=403, detail="Result signer does not own this attempt")
    assigned_agent = session.get(Agent, attempt.agent_id)
    reservation = session.scalar(
        select(BudgetReservation).where(BudgetReservation.attempt_id == attempt.id)
    )
    if assigned_agent is None or reservation is None:
        raise HTTPException(status_code=409, detail="Attempt identity or reservation is missing")
    if body.runtime_envelope is not None:
        try:
            _verify_runtime_result(assigned_agent, body.runtime_envelope)
        except (InvalidSignature, ValueError, KeyError, TypeError) as exc:
            raise HTTPException(status_code=422, detail=f"Agent runtime result signature is invalid: {exc}") from exc
        expected_context = {
            "workflowId": workflow.id,
            "nodeId": node.id,
            "attemptId": attempt.id,
            "attemptNumber": attempt.attempt_number,
            "reservationId": reservation.id,
        }
        if any(body.runtime_envelope.get(key) != value for key, value in expected_context.items()):
            raise HTTPException(status_code=422, detail="Agent runtime result context does not match the current attempt")
        if body.runtime_envelope.get("output") != body.output:
            raise HTTPException(status_code=422, detail="Agent runtime signed output does not match submitted output")
    else:
        environment = os.getenv("APP_ENV", "development").lower()
        require_signed = os.getenv(
            "REQUIRE_SIGNED_AGENT_RESULTS",
            "false" if environment == "test" else "true",
        ).lower() in {"1", "true", "yes"}
        if require_signed:
            raise HTTPException(status_code=422, detail="Signed Agent runtimeEnvelope is required")
    payload = body.model_dump(by_alias=True)
    route = f"dynamic_attempts.{attempt.id}.result"
    replay = get_idempotent_response(session, actor.id, route, idem, payload)
    if replay:
        return replay
    response = submit_attempt_result(session, workflow, node, attempt, body)
    save_idempotent_response(session, actor.id, route, idem, payload, response)
    session.commit()
    return response


@router.post("/workflows/{workflow_id}/nodes/{node_id}/attempts/{attempt_id}/review-approval-payload")
def post_review_approval_payload(
    workflow_id: str,
    node_id: str,
    attempt_id: str,
    body: ReviewApprovalPayloadRequest,
    actor: Agent = Depends(get_current_agent),
    session: Session = Depends(get_session),
) -> dict[str, Any]:
    workflow = _workflow(session, workflow_id)
    node = _node(session, workflow, node_id)
    attempt = session.get(NodeAttempt, attempt_id)
    if attempt is None or attempt.node_id != node.id:
        raise HTTPException(status_code=404, detail="Node attempt not found")
    return review_approval_signing_payload(
        session,
        workflow,
        node,
        attempt,
        actor,
        decision=body.decision,
        evidence_hash=body.evidence_hash,
        notes=body.notes,
        nonce=body.nonce,
        deadline=body.deadline,
    )


@router.post("/workflows/{workflow_id}/nodes/{node_id}/attempts/{attempt_id}/review-approvals")
def post_review_approval(
    workflow_id: str,
    node_id: str,
    attempt_id: str,
    body: ReviewApprovalCreate,
    actor: Agent = Depends(get_current_agent),
    session: Session = Depends(get_session),
) -> dict[str, Any]:
    workflow = _workflow(session, workflow_id)
    node = _node(session, workflow, node_id)
    attempt = session.get(NodeAttempt, attempt_id)
    if attempt is None or attempt.node_id != node.id:
        raise HTTPException(status_code=404, detail="Node attempt not found")
    result = submit_review_approval(
        session,
        workflow,
        node,
        attempt,
        actor,
        decision=body.decision,
        evidence_hash=body.evidence_hash,
        notes=body.notes,
        reviewer_wallet=body.reviewer_wallet,
        signature=body.signature,
        signature_scheme=body.signature_scheme,
        nonce=body.nonce,
        deadline=body.deadline,
    )
    session.commit()
    return {**result, "workflow": workflow_detail(session, workflow)}


@router.post("/review-randomness-fulfillments")
def post_review_randomness_fulfillment(
    body: RandomnessFulfillment,
    listener_id: str = Depends(require_listener_auth),
    session: Session = Depends(get_session),
) -> dict[str, Any]:
    attempt = session.scalar(select(NodeAttempt).where(NodeAttempt.randomness_request_id == body.request_id))
    if attempt is None:
        raise HTTPException(status_code=404, detail="Review randomness request not found")
    result = fulfill_review_randomness(
        session,
        attempt,
        request_id=body.request_id,
        random_word=body.random_word,
        provider_proof_hash=body.provider_proof_hash,
        provider_id=listener_id,
    )
    session.commit()
    return result


@router.post("/workflows/{workflow_id}/nodes/{node_id}/attempts/{attempt_id}/review-challenges")
def post_review_challenge(
    workflow_id: str,
    node_id: str,
    attempt_id: str,
    body: ReviewChallengeCreate,
    actor: Agent = Depends(get_current_agent),
    session: Session = Depends(get_session),
) -> dict[str, Any]:
    workflow = _workflow(session, workflow_id)
    node = _node(session, workflow, node_id)
    attempt = session.get(NodeAttempt, attempt_id)
    if attempt is None or attempt.node_id != node.id:
        raise HTTPException(status_code=404, detail="Node attempt not found")
    result = submit_review_challenge(
        session,
        workflow,
        node,
        attempt,
        actor,
        reason=body.reason,
        evidence_hash=body.evidence_hash,
    )
    session.commit()
    return {**result, "workflow": workflow_detail(session, workflow)}


@router.post("/workflows/{workflow_id}/nodes/{node_id}/attempts/{attempt_id}/review-challenges/{challenge_id}/resolve")
def post_review_challenge_resolution(
    workflow_id: str,
    node_id: str,
    attempt_id: str,
    challenge_id: str,
    body: ReviewChallengeResolution,
    actor: Agent = Depends(require_role("admin")),
    session: Session = Depends(get_session),
) -> dict[str, Any]:
    workflow = _workflow(session, workflow_id)
    node = _node(session, workflow, node_id)
    attempt = session.get(NodeAttempt, attempt_id)
    challenge = session.get(ReviewChallenge, challenge_id)
    if attempt is None or attempt.node_id != node.id:
        raise HTTPException(status_code=404, detail="Node attempt not found")
    if challenge is None or challenge.attempt_id != attempt.id:
        raise HTTPException(status_code=404, detail="Review challenge not found")
    result = resolve_review_challenge(
        session,
        workflow,
        node,
        attempt,
        challenge,
        actor,
        resolution=body.resolution,
        reason=body.reason,
    )
    session.commit()
    return {**result, "workflow": workflow_detail(session, workflow)}


@router.post("/workflows/{workflow_id}/nodes/{node_id}/attempts/{attempt_id}/review-challenges/{challenge_id}/arbitration-vote-payload")
def post_arbitration_vote_payload(
    workflow_id: str,
    node_id: str,
    attempt_id: str,
    challenge_id: str,
    body: ArbitrationVotePayloadRequest,
    actor: Agent = Depends(get_current_agent),
    session: Session = Depends(get_session),
) -> dict[str, Any]:
    workflow = _workflow(session, workflow_id)
    node = _node(session, workflow, node_id)
    attempt = session.get(NodeAttempt, attempt_id)
    challenge = session.get(ReviewChallenge, challenge_id)
    if attempt is None or attempt.node_id != node.id:
        raise HTTPException(status_code=404, detail="Node attempt not found")
    if challenge is None or challenge.attempt_id != attempt.id:
        raise HTTPException(status_code=404, detail="Review challenge not found")
    return arbitration_vote_signing_payload(
        session,
        workflow,
        node,
        attempt,
        challenge,
        actor,
        resolution=body.resolution,
        reason=body.reason,
        nonce=body.nonce,
        deadline=body.deadline,
    )


@router.post("/workflows/{workflow_id}/nodes/{node_id}/attempts/{attempt_id}/review-challenges/{challenge_id}/arbitration-votes")
def post_arbitration_vote(
    workflow_id: str,
    node_id: str,
    attempt_id: str,
    challenge_id: str,
    body: ArbitrationVoteCreate,
    actor: Agent = Depends(get_current_agent),
    session: Session = Depends(get_session),
) -> dict[str, Any]:
    workflow = _workflow(session, workflow_id)
    node = _node(session, workflow, node_id)
    attempt = session.get(NodeAttempt, attempt_id)
    challenge = session.get(ReviewChallenge, challenge_id)
    if attempt is None or attempt.node_id != node.id:
        raise HTTPException(status_code=404, detail="Node attempt not found")
    if challenge is None or challenge.attempt_id != attempt.id:
        raise HTTPException(status_code=404, detail="Review challenge not found")
    result = submit_arbitration_vote(
        session,
        workflow,
        node,
        attempt,
        challenge,
        actor,
        resolution=body.resolution,
        reason=body.reason,
        voter_wallet=body.voter_wallet,
        signature=body.signature,
        signature_scheme=body.signature_scheme,
        nonce=body.nonce,
        deadline=body.deadline,
    )
    session.commit()
    return {**result, "workflow": workflow_detail(session, workflow)}


@router.post("/workflows/{workflow_id}/nodes/{node_id}/attempts/{attempt_id}/gas-deposit-confirmations")
def post_attempt_gas_deposit_confirmation(
    workflow_id: str,
    node_id: str,
    attempt_id: str,
    body: AttemptGasDepositConfirmation,
    actor: Agent = Depends(require_role("admin")),
    session: Session = Depends(get_session),
) -> dict[str, Any]:
    workflow = _workflow(session, workflow_id)
    node = _node(session, workflow, node_id)
    attempt = session.get(NodeAttempt, attempt_id)
    if attempt is None or attempt.node_id != node.id:
        raise HTTPException(status_code=404, detail="Node attempt not found")
    deposit = session.scalar(select(GasDeposit).where(GasDeposit.attempt_id == attempt.id))
    if deposit is None:
        raise HTTPException(status_code=404, detail="Attempt Gas deposit record not found")
    amount = Decimal(body.amount)
    if deposit.status == "ACTIVE":
        if deposit.deposit_tx_hash != body.transaction_hash or Decimal(str(deposit.deposited_amount)) != amount:
            raise HTTPException(status_code=409, detail="Attempt Gas deposit was already confirmed with different values")
    else:
        deposit.deposited_amount = amount
        deposit.reserved_amount = amount
        deposit.deposit_tx_hash = body.transaction_hash
        deposit.status = "ACTIVE"
    session.commit()
    return serialize(deposit)


@router.post("/workflows/{workflow_id}/nodes/{node_id}/attempts/{attempt_id}/gas-closure-confirmations")
def post_attempt_gas_closure_confirmation(
    workflow_id: str,
    node_id: str,
    attempt_id: str,
    body: AttemptGasClosureConfirmation,
    actor: Agent = Depends(require_role("admin")),
    session: Session = Depends(get_session),
) -> dict[str, Any]:
    workflow = _workflow(session, workflow_id)
    node = _node(session, workflow, node_id)
    attempt = session.get(NodeAttempt, attempt_id)
    if attempt is None or attempt.node_id != node.id:
        raise HTTPException(status_code=404, detail="Node attempt not found")
    deposit = session.scalar(select(GasDeposit).where(GasDeposit.attempt_id == attempt.id))
    if deposit is None or deposit.status not in {"ACTIVE", "REFUNDED"}:
        raise HTTPException(status_code=409, detail="Attempt Gas deposit is not active")
    charged = Decimal(body.charged_amount)
    refunded = Decimal(body.refunded_amount)
    if charged + refunded != Decimal(str(deposit.deposited_amount)):
        raise HTTPException(status_code=422, detail="Attempt Gas closure does not balance the deposited amount")
    deposit.charged_amount = charged
    deposit.refunded_amount = refunded
    deposit.refund_tx_hash = body.refund_transaction_hash
    deposit.status = "REFUNDED"
    session.commit()
    return serialize(deposit)


@router.post("/workflows/{workflow_id}/nodes/{node_id}/attempts/{attempt_id}/result-proof-confirmations")
def post_result_proof_confirmation(
    workflow_id: str,
    node_id: str,
    attempt_id: str,
    body: ResultProofConfirmation,
    actor: Agent = Depends(require_role("admin")),
    idem: str | None = Depends(idempotency_header),
    session: Session = Depends(get_session),
) -> dict[str, Any]:
    workflow = _workflow(session, workflow_id)
    node = _node(session, workflow, node_id)
    attempt = session.get(NodeAttempt, attempt_id)
    if attempt is None or attempt.node_id != node.id:
        raise HTTPException(status_code=404, detail="Node attempt not found")
    payload = body.model_dump(by_alias=True)
    route = f"dynamic_attempts.{attempt.id}.result_proof"
    replay = get_idempotent_response(session, actor.id, route, idem, payload)
    if replay:
        return replay
    result = confirm_result_proof(session, workflow, node, attempt, body)
    response = {**result, "workflow": workflow_detail(session, workflow)}
    save_idempotent_response(session, actor.id, route, idem, payload, response)
    session.commit()
    return response


@router.post("/workflows/{workflow_id}/nodes/{node_id}/attempts/{attempt_id}/settlement-custody")
def post_settlement_custody_update(
    workflow_id: str,
    node_id: str,
    attempt_id: str,
    body: SettlementCustodyUpdate,
    listener_id: str = Depends(require_listener_auth),
    session: Session = Depends(get_session),
) -> dict[str, Any]:
    workflow = _workflow(session, workflow_id)
    node = _node(session, workflow, node_id)
    attempt = session.get(NodeAttempt, attempt_id)
    if attempt is None or attempt.node_id != node.id:
        raise HTTPException(status_code=404, detail="Node attempt not found")
    result = update_settlement_custody(
        session,
        workflow,
        node,
        attempt,
        phase=body.phase,
        custody_chain_id=body.custody_chain_id,
        custody_contract=body.custody_contract,
        asset=body.asset,
        token_address=body.token_address,
        amount=body.amount,
        transport_message_id=body.transport_message_id,
        transaction_hash=body.transaction_hash,
        proof_hash=body.proof_hash,
        failure_code=body.failure_code,
        recovery_action=body.recovery_action,
        expected_state_version=body.expected_state_version,
        worker_id=listener_id,
    )
    session.commit()
    return {**result, "workflow": workflow_detail(session, workflow)}


@router.post("/workflows/{workflow_id}/nodes/{node_id}/attempts/{attempt_id}/settlement-confirmations")
def post_settlement_confirmation(
    workflow_id: str,
    node_id: str,
    attempt_id: str,
    body: SettlementConfirmation,
    actor: Agent = Depends(require_role("admin")),
    idem: str | None = Depends(idempotency_header),
    session: Session = Depends(get_session),
) -> dict[str, Any]:
    workflow = _workflow(session, workflow_id)
    node = _node(session, workflow, node_id)
    attempt = session.get(NodeAttempt, attempt_id)
    if attempt is None or attempt.node_id != node.id:
        raise HTTPException(status_code=404, detail="Node attempt not found")
    payload = body.model_dump(by_alias=True)
    route = f"dynamic_attempts.{attempt.id}.settlement"
    replay = get_idempotent_response(session, actor.id, route, idem, payload)
    if replay:
        return replay
    result = confirm_settlement(session, workflow, node, attempt, body)
    response = {**result, "workflow": workflow_detail(session, workflow)}
    save_idempotent_response(session, actor.id, route, idem, payload, response)
    session.commit()
    return response


@router.post("/workflows/{workflow_id}/nodes/{node_id}/attempts/{attempt_id}/failures")
def post_attempt_failure(
    workflow_id: str,
    node_id: str,
    attempt_id: str,
    body: WorkflowFailureCreate,
    actor: Agent = Depends(get_current_agent),
    idem: str | None = Depends(idempotency_header),
    session: Session = Depends(get_session),
) -> dict[str, Any]:
    workflow = _workflow(session, workflow_id)
    node = _node(session, workflow, node_id)
    attempt = session.get(NodeAttempt, attempt_id)
    if attempt is None or attempt.node_id != node.id:
        raise HTTPException(status_code=404, detail="Node attempt not found")
    if actor.id != attempt.agent_id and "admin" not in actor.roles:
        raise HTTPException(status_code=403, detail="Only the attempt Agent or platform admin can report this failure")
    payload = body.model_dump(by_alias=True)
    route = f"dynamic_attempts.{attempt.id}.failure.{body.reason_code}"
    replay = get_idempotent_response(session, actor.id, route, idem, payload)
    if replay:
        return replay
    result = report_attempt_failure(
        session,
        workflow,
        node,
        attempt,
        body.reason_code,
        {"evidenceHash": body.evidence_hash, "detail": body.detail, "reporterDid": actor.did},
    )
    response = {**result, "workflow": workflow_detail(session, workflow)}
    save_idempotent_response(session, actor.id, route, idem, payload, response)
    session.commit()
    return response


@router.post("/workflows/{workflow_id}/failed-refund-confirmations")
def post_failed_refund_confirmation(
    workflow_id: str,
    body: FailedRefundConfirmation,
    actor: Agent = Depends(require_role("admin")),
    idem: str | None = Depends(idempotency_header),
    session: Session = Depends(get_session),
) -> dict[str, Any]:
    workflow = _workflow(session, workflow_id)
    payload = body.model_dump(by_alias=True)
    route = f"dynamic_workflows.{workflow.id}.failed_refund"
    replay = get_idempotent_response(session, actor.id, route, idem, payload)
    if replay:
        return replay
    result = complete_failed_refund(
        session,
        workflow,
        token_refund_tx_hash=body.token_refund_tx_hash,
        gas_refund_tx_hash=body.gas_refund_tx_hash,
        source_gas_refund_amount=Decimal(body.source_gas_refund_amount),
    )
    response = {**result, "workflow": workflow_detail(session, workflow)}
    save_idempotent_response(session, actor.id, route, idem, payload, response)
    session.commit()
    return response


@router.post("/workflows/{workflow_id}/finalization-confirmations")
def post_workflow_finalization_confirmation(
    workflow_id: str,
    body: WorkflowFinalizationConfirmation,
    actor: Agent = Depends(require_role("admin")),
    idem: str | None = Depends(idempotency_header),
    session: Session = Depends(get_session),
) -> dict[str, Any]:
    workflow = _workflow(session, workflow_id)
    payload = body.model_dump(by_alias=True)
    route = f"dynamic_workflows.{workflow.id}.finalization"
    replay = get_idempotent_response(session, actor.id, route, idem, payload)
    if replay:
        return replay
    result = complete_workflow_finalization(session, workflow, body)
    response = {**result, "workflow": workflow_detail(session, workflow)}
    save_idempotent_response(session, actor.id, route, idem, payload, response)
    session.commit()
    return response


@router.get("/chain-routes")
def list_chain_routes(session: Session = Depends(get_session)) -> dict[str, Any]:
    items = list(session.scalars(select(ChainRoute).order_by(ChainRoute.destination_chain_id, ChainRoute.route_version)))
    return {"items": serialize(items), "total": len(items)}


@router.post("/chain-routes", status_code=201)
def post_chain_route(
    body: ChainRouteCreate,
    actor: Agent = Depends(require_role("admin")),
    idem: str | None = Depends(idempotency_header),
    session: Session = Depends(get_session),
) -> dict[str, Any]:
    payload = body.model_dump(by_alias=True)
    replay = get_idempotent_response(session, actor.id, "chain_routes.create", idem, payload)
    if replay:
        return replay
    expected_source_chain = 11155111 if os.getenv("PLATFORM_PROFILE", "local-demo").lower() == "managed-testnet" else CHAIN_A
    if body.source_chain_id != expected_source_chain:
        raise HTTPException(status_code=422, detail=f"Current profile requires source chain {expected_source_chain}")
    try:
        observed = _rpc_call(_resolved_route_rpc_url(body.rpc_url), "eth_chainId", [])
        if int(observed, 16) != body.destination_chain_id:
            raise HTTPException(status_code=422, detail="RPC chainId does not match destinationChainId")
    except HTTPException:
        raise
    except Exception as exc:
        raise HTTPException(status_code=422, detail=f"Destination RPC validation failed: {exc}") from exc
    latest_version = session.scalar(
        select(func.max(ChainRoute.route_version)).where(ChainRoute.route_key == body.route_key)
    ) or 0
    duplicate_chain = session.scalar(
        select(ChainRoute).where(
            ChainRoute.source_chain_id == body.source_chain_id,
            ChainRoute.destination_chain_id == body.destination_chain_id,
            ChainRoute.status == "ACTIVE",
        )
    )
    if duplicate_chain is not None:
        raise HTTPException(status_code=409, detail="An active route to destinationChainId already exists")
    item = ChainRoute(
        route_key=body.route_key,
        route_version=latest_version + 1,
        source_chain_id=body.source_chain_id,
        destination_chain_id=body.destination_chain_id,
        status="ACTIVE",
        transport=body.transport,
        rpc_url=body.rpc_url,
        source_chain_selector=body.source_chain_selector,
        destination_chain_selector=body.destination_chain_selector,
        source_router_address=body.source_router_address,
        destination_router_address=body.destination_router_address,
        token_address=body.token_address,
        source_token_address=body.source_token_address,
        destination_token_address=body.destination_token_address or body.token_address,
        token_decimals=body.token_decimals,
        settlement_mode=body.settlement_mode,
        settlement_asset=body.settlement_asset,
        payout_asset=body.payout_asset,
        payout_decimals=body.payout_decimals,
        payout_rate=Decimal(body.payout_rate),
        payout_slippage_bps=body.payout_slippage_bps,
        confirmation_blocks=body.confirmation_blocks,
        deployment_status="PLACEHOLDER",
    )
    session.add(item)
    session.flush()
    response = serialize(item)
    save_idempotent_response(session, actor.id, "chain_routes.create", idem, payload, response)
    session.commit()
    return response


@router.patch("/chain-routes/{route_id}")
def patch_chain_route(
    route_id: str,
    body: RouteUpdate,
    actor: Agent = Depends(require_role("admin")),
    idem: str | None = Depends(idempotency_header),
    session: Session = Depends(get_session),
) -> dict[str, Any]:
    item = session.get(ChainRoute, route_id)
    if item is None:
        raise HTTPException(status_code=404, detail="Chain route not found")
    payload = body.model_dump(by_alias=True, exclude_none=True)
    route = f"chain_routes.{route_id}.patch"
    replay = get_idempotent_response(session, actor.id, route, idem, payload)
    if replay:
        return replay
    for field, value in body.model_dump(exclude_none=True).items():
        setattr(item, field, value)
    response = serialize(item)
    save_idempotent_response(session, actor.id, route, idem, payload, response)
    session.commit()
    return response


@router.post("/chain-routes/{route_id}/deploy", status_code=202)
def queue_chain_route_deployment(
    route_id: str,
    actor: Agent = Depends(require_role("admin")),
    session: Session = Depends(get_session),
) -> dict[str, Any]:
    route = session.get(ChainRoute, route_id)
    if route is None:
        raise HTTPException(status_code=404, detail="Chain route not found")
    if route.status != "ACTIVE":
        raise HTTPException(status_code=409, detail="Only an ACTIVE route can be deployed")
    deployment = session.scalar(select(RouteDeployment).where(
        RouteDeployment.route_id == route.id,
        RouteDeployment.route_version == route.route_version,
    ))
    if deployment is None:
        deployment = RouteDeployment(
            route_id=route.id,
            route_version=route.route_version,
            home_chain_id=route.destination_chain_id,
            trigger_agent_profile_id=None,
            status="PLANNED",
        )
        session.add(deployment)
        session.flush()
    if deployment.status == "READY":
        return {"deployment": serialize(deployment), "protocolJob": None}
    job = session.scalar(select(ProtocolJob).where(
        ProtocolJob.route_id == route.id,
        ProtocolJob.route_version == route.route_version,
        ProtocolJob.job_type == "DEPLOY_ROUTE_STACK",
        ProtocolJob.status.in_(["QUEUED", "RUNNING"]),
    ))
    if job is None:
        job = ProtocolJob(
            route_id=route.id,
            route_version=route.route_version,
            job_type="DEPLOY_ROUTE_STACK",
            payload={
                "deploymentId": deployment.id,
                "homeChainId": route.destination_chain_id,
                "routeKey": route.route_key,
                "routeVersion": route.route_version,
            },
        )
        session.add(job)
    route.deployment_status = "DEPLOYING"
    deployment.status = "QUEUED"
    audit(session, actor.id, "chain_route.deployment_queued", "chain_route", route.id, {
        "deploymentId": deployment.id,
        "routeVersion": route.route_version,
    })
    session.commit()
    return {"deployment": serialize(deployment), "protocolJob": serialize(job)}


@router.get("/route-deployments")
def list_route_deployments(
    status: str | None = Query(default=None),
    session: Session = Depends(get_session),
) -> dict[str, Any]:
    query = select(RouteDeployment).order_by(RouteDeployment.created_at.desc())
    if status:
        query = query.where(RouteDeployment.status == status.upper())
    items = list(session.scalars(query))
    return {"items": serialize(items), "total": len(items)}


@router.post("/route-deployments/{deployment_id}/complete")
def complete_route_deployment(
    deployment_id: str,
    body: RouteDeploymentCompletion,
    actor: Agent = Depends(require_role("admin")),
    idem: str | None = Depends(idempotency_header),
    session: Session = Depends(get_session),
) -> dict[str, Any]:
    deployment = session.get(RouteDeployment, deployment_id)
    if deployment is None:
        raise HTTPException(status_code=404, detail="Route deployment not found")
    payload = body.model_dump(by_alias=True)
    route_key = f"route_deployments.{deployment.id}.complete"
    replay = get_idempotent_response(session, actor.id, route_key, idem, payload)
    if replay:
        return replay
    route = session.get(ChainRoute, deployment.route_id)
    if route is None:
        raise HTTPException(status_code=409, detail="Deployment route no longer exists")
    deployment.status = body.status
    deployment.contracts = body.contracts
    deployment.transaction_hashes = body.transaction_hashes
    deployment.code_hashes = body.code_hashes
    deployment.manifest_hash = body.manifest_hash
    deployment.failure_reason = body.failure_reason
    deployment.deployed_at = utcnow() if body.status == "READY" else None
    route.deployment_status = body.status
    route.deployment_manifest_hash = body.manifest_hash
    if body.status == "READY":
        route.source_gateway_address = body.contracts.get("resultProofGatewayA", route.source_gateway_address)
        route.remote_work_board_address = body.contracts.get("remoteWorkBoard", route.remote_work_board_address)
        route.payout_receiver_address = body.contracts.get(
            "dynamicCCIPWorkflowReceiver",
            body.contracts.get("dynamicSettlementReceiver", route.payout_receiver_address),
        )
        route.token_address = body.contracts.get("destinationToken", route.token_address)
        route.payout_token_address = body.contracts.get("payoutToken", route.payout_token_address)
        route.payout_swap_address = body.contracts.get("payoutSwap", route.payout_swap_address)
        waiting_nodes = list(
            session.scalars(
                select(WorkflowNode).where(WorkflowNode.status == "WAITING_FOR_ROUTE_DEPLOYMENT")
            )
        )
        for waiting_node in waiting_nodes:
            waiting_node.status = "RETRYING"
    response = serialize(deployment)
    save_idempotent_response(session, actor.id, route_key, idem, payload, response)
    session.commit()
    return response


@router.get("/protocol-jobs")
def list_protocol_jobs(
    status: str | None = Query(default="QUEUED"),
    limit: int = Query(default=100, ge=1, le=500),
    actor: Agent = Depends(require_role("admin")),
    session: Session = Depends(get_session),
) -> dict[str, Any]:
    del actor
    query = select(ProtocolJob).order_by(ProtocolJob.created_at).limit(limit)
    if status:
        query = query.where(ProtocolJob.status == status.upper())
        if status.upper() == "QUEUED":
            query = query.where(ProtocolJob.available_at <= utcnow())
    items = list(session.scalars(query))
    return {"items": serialize(items), "total": len(items)}


@router.post("/workflows/{workflow_id}/nodes/{node_id}/attempts/{attempt_id}/agent-transaction-cancel", status_code=202)
def request_agent_transaction_cancel(
    workflow_id: str,
    node_id: str,
    attempt_id: str,
    body: AgentTransactionCancelRequest,
    actor: Agent = Depends(require_role("admin")),
    session: Session = Depends(get_session),
) -> dict[str, Any]:
    attempt = session.get(NodeAttempt, attempt_id)
    if attempt is None or attempt.node_id != node_id:
        raise HTTPException(status_code=404, detail="Attempt not found")
    node = session.get(WorkflowNode, node_id)
    if node is None or node.workflow_id != workflow_id:
        raise HTTPException(status_code=404, detail="Workflow node not found")
    if attempt.status not in {"DISPATCHED", "EXECUTING"} or node.current_attempt_id != attempt.id:
        raise HTTPException(status_code=409, detail="Only the current unsubmitted Agent attempt can be cancelled")
    existing = session.scalar(select(ProtocolJob).where(
        ProtocolJob.attempt_id == attempt.id,
        ProtocolJob.job_type == "CANCEL_AGENT_TRANSACTION",
        ProtocolJob.status.in_(["QUEUED", "RUNNING", "COMPLETED"]),
    ))
    if existing is not None:
        return serialize(existing)
    dispatch = session.scalar(select(ProtocolJob).where(
        ProtocolJob.attempt_id == attempt.id,
        ProtocolJob.job_type == "DISPATCH_ASSIGNMENT",
    ).order_by(ProtocolJob.created_at.desc()))
    if dispatch is None:
        raise HTTPException(status_code=409, detail="The original Agent dispatch job was not found")
    payload = dict(dispatch.payload or {})
    payload["cancelReasonHash"] = canonical_hash(body.reason)
    job = ProtocolJob(
        workflow_id=workflow_id,
        node_id=node_id,
        attempt_id=attempt_id,
        route_id=attempt.route_id,
        route_version=attempt.route_version,
        job_type="CANCEL_AGENT_TRANSACTION",
        status="QUEUED",
        payload=payload,
        available_at=utcnow(),
    )
    session.add(job)
    session.flush()
    audit(session, actor.id, "agent_transaction.cancel_requested", "node_attempt", attempt.id, {
        "jobId": job.id,
        "reasonHash": payload["cancelReasonHash"],
    })
    session.commit()
    return serialize(job)


@router.patch("/protocol-jobs/{job_id}")
def patch_protocol_job(
    job_id: str,
    body: ProtocolJobUpdate,
    actor: Agent = Depends(require_role("admin")),
    idem: str | None = Depends(idempotency_header),
    session: Session = Depends(get_session),
) -> dict[str, Any]:
    job = session.get(ProtocolJob, job_id)
    if job is None:
        raise HTTPException(status_code=404, detail="Protocol job not found")
    payload = body.model_dump(by_alias=True)
    route = f"protocol_jobs.{job.id}.patch"
    replay = get_idempotent_response(session, actor.id, route, idem, payload)
    if replay:
        return replay
    if body.status == "RUNNING" and job.status != "RUNNING":
        job.attempts += 1
    job.transaction_hash = body.transaction_hash or job.transaction_hash
    job.last_error = body.error
    job.status = "QUEUED" if body.status == "RETRY" else body.status
    if body.status == "RETRY":
        job.available_at = utcnow() + timedelta(seconds=body.retry_after_seconds or 0)
        if body.retry_after_seconds is not None:
            job.attempts = max(0, job.attempts - 1)
    if body.status == "COMPLETED":
        job.completed_at = utcnow()
        if job.job_type == "REGISTER_WORKFLOW" and job.workflow_id:
            workflow = session.get(Workflow, job.workflow_id)
            if workflow is not None:
                workflow.contract_sync_status = "SYNCED"
        if job.job_type == "DISPATCH_ASSIGNMENT" and job.attempt_id:
            message = session.scalar(
                select(CrossChainMessage).where(
                    CrossChainMessage.attempt_id == job.attempt_id,
                    CrossChainMessage.message_type == "DISPATCH",
                )
            )
            if message is not None:
                message.status = "DELIVERED"
                message.transaction_hash = body.transaction_hash
                message.relay_attempts += 1
                message.delivered_at = utcnow()
        if job.job_type == "SYSTEM_RETRY" and job.node_id and job.attempt_id:
            node = session.get(WorkflowNode, job.node_id)
            attempt = session.get(NodeAttempt, job.attempt_id)
            if node is not None and attempt is not None:
                node.status = "ASSIGNED"
                attempt.status = "DISPATCHED"
    response = serialize(job)
    save_idempotent_response(session, actor.id, route, idem, payload, response)
    session.commit()
    return response


@router.put("/protocol-runtime/contracts/{contract_role}")
def put_protocol_runtime_contract(
    contract_role: str,
    body: ProtocolRuntimeContractUpdate,
    listener_id: str = Depends(require_listener_auth),
    session: Session = Depends(get_session),
) -> dict[str, Any]:
    role = contract_role.upper()
    if role != "REVIEW_MANAGER_V2":
        raise HTTPException(status_code=422, detail="Unsupported protocol runtime contract role")
    runtime = session.scalar(
        select(ProtocolRuntimeConfig).where(
            ProtocolRuntimeConfig.chain_id == body.chain_id,
            ProtocolRuntimeConfig.contract_role == role,
        )
    )
    if runtime is None:
        runtime = ProtocolRuntimeConfig(
            chain_id=body.chain_id,
            contract_role=role,
            contract_address=body.contract_address,
            code_hash=body.code_hash,
            manifest_hash=body.manifest_hash,
            active=True,
        )
        session.add(runtime)
    else:
        runtime.contract_address = body.contract_address
        runtime.code_hash = body.code_hash
        runtime.manifest_hash = body.manifest_hash
        runtime.active = True
    audit(
        session,
        None,
        "protocol.runtime_contract_registered",
        "protocol_runtime_config",
        runtime.id,
        {
            "listenerId": listener_id,
            "chainId": body.chain_id,
            "contractRole": role,
            "contractAddress": body.contract_address,
        },
    )
    session.commit()
    return serialize(runtime)


@router.post("/workflows/{workflow_id}/nodes/{node_id}/attempts/{attempt_id}/review-chain-confirmations")
def post_review_chain_confirmation(
    workflow_id: str,
    node_id: str,
    attempt_id: str,
    body: ReviewChainStateConfirmation,
    listener_id: str = Depends(require_listener_auth),
    session: Session = Depends(get_session),
) -> dict[str, Any]:
    workflow = _workflow(session, workflow_id)
    node = _node(session, workflow, node_id)
    attempt = session.get(NodeAttempt, attempt_id)
    if attempt is None or attempt.node_id != node.id:
        raise HTTPException(status_code=404, detail="Node attempt not found")
    runtime = session.scalar(
        select(ProtocolRuntimeConfig).where(
            ProtocolRuntimeConfig.chain_id == body.chain_id,
            ProtocolRuntimeConfig.contract_role == "REVIEW_MANAGER_V2",
            ProtocolRuntimeConfig.active.is_(True),
        )
    )
    if (
        body.chain_id != workflow.source_chain_id
        or runtime is None
        or runtime.contract_address.lower() != body.contract_address.lower()
    ):
        raise HTTPException(status_code=422, detail="Review chain confirmation does not match the registered runtime")
    if body.status == "CHALLENGE_OPEN":
        if body.challenge_deadline is None:
            raise HTTPException(status_code=422, detail="CHALLENGE_OPEN requires the on-chain deadline")
        attempt.challenge_expires_at = body.challenge_deadline
        challenge = session.scalar(select(ReviewChallenge).where(ReviewChallenge.attempt_id == attempt.id))
        if challenge is not None and body.transaction_hash:
            challenge.challenge_open_tx_hash = body.transaction_hash
    _event_payload = {
        "listenerId": listener_id,
        "status": body.status,
        "contractAddress": body.contract_address,
        "transactionHash": body.transaction_hash,
        "challengeDeadline": body.challenge_deadline.isoformat() if body.challenge_deadline else None,
    }
    audit(session, None, "review.chain_state_confirmed", "node_attempt", attempt.id, _event_payload)
    session.commit()
    return {"accepted": True, "attemptId": attempt.id, **_event_payload}


@router.post("/agent-onboarding/invitations/request", status_code=201)
def request_self_service_invitation(
    body: SelfServiceInvitationRequest,
    session: Session = Depends(get_session),
) -> dict[str, object]:
    if not _self_service_onboarding_enabled():
        raise HTTPException(status_code=404, detail="Self-service onboarding is not enabled")
    route_key = os.getenv(
        "MANAGED_SELF_SERVICE_ROUTE_KEY",
        "SEPOLIA_TO_ARBITRUM_SEPOLIA_CCIP_BNM_V1",
    ).strip()
    try:
        route_version = int(os.getenv("MANAGED_SELF_SERVICE_ROUTE_VERSION", "1"))
        ttl_minutes = max(5, min(int(os.getenv("MANAGED_SELF_SERVICE_INVITATION_TTL_MINUTES", "15")), 60))
    except ValueError as exc:
        raise HTTPException(status_code=503, detail="Self-service onboarding policy is invalid") from exc
    is_provider = "provider" in body.roles
    capabilities = sorted({
        value.strip().lower()
        for value in os.getenv("MANAGED_SELF_SERVICE_CAPABILITIES", "").split(",")
        if value.strip()
    }) if is_provider else []
    if is_provider and not capabilities:
        raise HTTPException(status_code=503, detail="Self-service onboarding capabilities are not configured")
    credential_ref = None
    if is_provider:
        credential_ref = os.getenv("MANAGED_SELF_SERVICE_EXECUTOR_CREDENTIAL_REF", "env:DEEPSEEK_API_KEY").strip()
        if not credential_ref.startswith(("env:", "file:")) or any(character.isspace() for character in credential_ref):
            raise HTTPException(status_code=503, detail="Self-service executor credential policy is invalid")
    route = session.scalar(select(ChainRoute).where(
        ChainRoute.route_key == route_key,
        ChainRoute.route_version == route_version,
        ChainRoute.status == "ACTIVE",
        ChainRoute.deployment_status == "READY",
    ))
    if route is None or not route.remote_work_board_address:
        raise HTTPException(status_code=409, detail="The managed route is not deployed and ready for self-service onboarding")
    if route.source_chain_id != 11155111 or route.destination_chain_id != 421614 or route.transport != "CHAINLINK_CCIP":
        raise HTTPException(status_code=503, detail="The configured self-service route is not the supported CCIP testnet route")
    public_key_sha256 = _identity_public_key_sha256(body.public_key)
    if session.scalar(select(Agent).where(Agent.did == body.did)) is not None:
        raise HTTPException(status_code=409, detail="Agent DID already exists")
    if session.scalar(select(WorkflowAgentProfile).where(WorkflowAgentProfile.agent_key == body.agent_key)) is not None:
        raise HTTPException(status_code=409, detail="Workflow Agent key already exists")
    if _runtime_id_registered(session, body.runtime_id):
        raise HTTPException(status_code=409, detail="Runtime ID already exists")
    for previous in session.scalars(select(AgentInvitation).where(
        AgentInvitation.issuance_type == "SELF_SERVICE",
        AgentInvitation.bound_runtime_id == body.runtime_id,
        AgentInvitation.status == "AVAILABLE",
    )):
        previous.status = "REPLACED"
    code = secrets.token_urlsafe(32)
    invitation = AgentInvitation(
        code_hash=hashlib.sha256(code.encode()).hexdigest(),
        route_key=route.route_key,
        min_route_version=route.route_version,
        max_route_version=route.route_version,
        capabilities=capabilities,
        roles=body.roles,
        executor_type="llm",
        executor_credential_ref=credential_ref,
        issuance_type="SELF_SERVICE",
        bound_agent_key=body.agent_key,
        bound_did=body.did,
        bound_runtime_id=body.runtime_id,
        bound_home_wallet=body.home_wallet.lower(),
        bound_public_key_sha256=public_key_sha256,
        status="AVAILABLE",
        expires_at=utcnow() + timedelta(minutes=ttl_minutes),
    )
    session.add(invitation)
    session.flush()
    audit(session, None, "agent_invitation.self_service_created", "agent_invitation", invitation.id, {
        "routeKey": route.route_key,
        "routeVersion": route.route_version,
        "runtimeIdHash": canonical_hash(body.runtime_id),
        "didHash": canonical_hash(body.did),
        "roles": body.roles,
    })
    session.commit()
    return {
        "id": invitation.id,
        "inviteCode": code,
        "expiresAt": serialize(invitation.expires_at),
        "routeKey": route.route_key,
        "routeVersion": route.route_version,
        "homeChainId": route.destination_chain_id,
        "remoteWorkBoard": route.remote_work_board_address,
        "capabilities": capabilities,
        "roles": body.roles,
        "executorType": invitation.executor_type,
    }


@router.post("/agent-invitations", status_code=201)
def create_agent_invitation(
    body: AgentInvitationCreate,
    actor: Agent = Depends(require_role("admin")),
    session: Session = Depends(get_session),
) -> dict[str, object]:
    route = session.scalar(select(ChainRoute).where(
        ChainRoute.route_key == body.route_key,
        ChainRoute.route_version >= body.min_route_version,
        ChainRoute.route_version <= body.max_route_version,
        ChainRoute.status == "ACTIVE",
    ))
    if route is None:
        raise HTTPException(status_code=422, detail="Invitation route/version is not active")
    code = secrets.token_urlsafe(32)
    invitation = AgentInvitation(
        code_hash=hashlib.sha256(code.encode()).hexdigest(),
        route_key=body.route_key,
        min_route_version=body.min_route_version,
        max_route_version=body.max_route_version,
        capabilities=sorted({item.strip().lower() for item in body.capabilities if item.strip()}),
        roles=body.roles,
        executor_type=body.executor_type,
        executor_credential_ref=body.executor_credential_ref if "provider" in body.roles and body.executor_type == "llm" else None,
        issuance_type="ADMIN",
        status="AVAILABLE",
        expires_at=utcnow() + timedelta(hours=body.expires_in_hours),
    )
    session.add(invitation)
    session.flush()
    audit(session, actor.id, "agent_invitation.created", "agent_invitation", invitation.id, {
        "routeKey": invitation.route_key, "capabilities": invitation.capabilities, "roles": invitation.roles,
    })
    session.commit()
    return {"id": invitation.id, "inviteCode": code, "status": invitation.status, "routeKey": invitation.route_key,
            "minRouteVersion": invitation.min_route_version, "maxRouteVersion": invitation.max_route_version,
            "capabilities": invitation.capabilities, "roles": invitation.roles, "executorType": invitation.executor_type,
            "executorCredentialRef": invitation.executor_credential_ref, "expiresAt": serialize(invitation.expires_at)}


@router.get("/agent-invitations")
def list_agent_invitations(
    actor: Agent = Depends(require_role("admin")),
    session: Session = Depends(get_session),
) -> dict[str, object]:
    items = session.scalars(select(AgentInvitation).order_by(AgentInvitation.created_at.desc())).all()
    return {"items": serialize(items), "total": len(items)}


@router.post("/agent-onboarding/registrations/{profile_id}/provision-retry")
def retry_agent_provision(
    profile_id: str,
    onboarding_token: str | None = Header(default=None, alias="X-Onboarding-Token"),
    session: Session = Depends(get_session),
) -> dict[str, object]:
    profile, _ = _onboarding_profile(session, profile_id)
    _require_onboarding_access(profile, onboarding_token)
    outbox = session.scalar(select(AgentProvisionOutbox).where(AgentProvisionOutbox.profile_id == profile.id))
    if not outbox:
        raise HTTPException(status_code=404, detail="Provisioning outbox was not found")
    if outbox.status == "COMPLETED":
        return {"profileId": profile.id, "provisioningId": outbox.provisioning_id, "status": "PROVISIONED"}
    if outbox.status == "DEPROVISION_PENDING":
        _process_provision_outbox(session, outbox)
    succeeded = _process_provision_outbox(session, outbox)
    return {"profileId": profile.id, "provisioningId": outbox.provisioning_id,
            "status": "PROVISIONED" if succeeded else "PROVISION_FAILED", "attempts": outbox.attempts}


@router.post("/agent-onboarding/provisioning/reconcile")
def reconcile_agent_provisioning(
    actor: Agent = Depends(require_role("admin")),
    session: Session = Depends(get_session),
) -> dict[str, object]:
    items = session.scalars(select(AgentProvisionOutbox).where(
        AgentProvisionOutbox.status.in_(["PENDING", "RETRY", "DEPROVISION_PENDING"]),
        AgentProvisionOutbox.next_attempt_at <= utcnow(),
    ).limit(20)).all()
    completed = sum(1 for item in items if _process_provision_outbox(session, item))
    return {"processed": len(items), "completed": completed, "failed": len(items) - completed}


@router.get("/agent-onboarding/provisioning")
def list_agent_provisioning(
    actor: Agent = Depends(require_role("admin")),
    session: Session = Depends(get_session),
) -> dict[str, object]:
    rows = session.scalars(select(AgentProvisionOutbox).order_by(AgentProvisionOutbox.created_at.desc()).limit(200)).all()
    items: list[dict[str, object]] = []
    for row in rows:
        profile = session.get(WorkflowAgentProfile, row.profile_id)
        agent = session.get(Agent, profile.agent_id) if profile else None
        items.append({
            "id": row.id,
            "provisioningId": row.provisioning_id,
            "profileId": row.profile_id,
            "invitationId": row.invitation_id,
            "status": row.status,
            "attempts": row.attempts,
            "lastErrorHash": row.last_error_hash,
            "nextAttemptAt": serialize(row.next_attempt_at),
            "completedAt": serialize(row.completed_at),
            "createdAt": serialize(row.created_at),
            "agent": {
                "name": agent.display_name if agent else None,
                "did": agent.did if agent else None,
                "agentKey": profile.agent_key if profile else None,
                "onboardingStatus": profile.onboarding_status if profile else None,
            },
        })
    return {"items": items, "total": len(items)}


@router.post("/agent-onboarding/registrations/{profile_id}/provision-admin-retry")
def admin_retry_agent_provision(
    profile_id: str,
    actor: Agent = Depends(require_role("admin")),
    session: Session = Depends(get_session),
) -> dict[str, object]:
    profile, _ = _onboarding_profile(session, profile_id)
    outbox = session.scalar(select(AgentProvisionOutbox).where(AgentProvisionOutbox.profile_id == profile.id))
    if not outbox:
        raise HTTPException(status_code=404, detail="Provisioning outbox was not found")
    if outbox.status == "COMPLETED":
        return {"profileId": profile.id, "provisioningId": outbox.provisioning_id, "status": "PROVISIONED"}
    succeeded = _process_provision_outbox(session, outbox)
    audit(session, actor.id, "agent_onboarding.provision_admin_retry", "workflow_agent_profile", profile.id, {
        "provisioningId": outbox.provisioning_id,
        "succeeded": succeeded,
    })
    session.commit()
    return {"profileId": profile.id, "provisioningId": outbox.provisioning_id,
            "status": "PROVISIONED" if succeeded else "PROVISION_FAILED", "attempts": outbox.attempts}


@router.post("/agent-onboarding/registrations", status_code=201)
def post_external_agent_onboarding(
    body: ExternalAgentOnboardingCreate,
    session: Session = Depends(get_session),
) -> dict[str, Any]:
    return _register_external_agent(body, session)


@router.post("/agent-onboarding/from-descriptor", status_code=201)
def post_descriptor_agent_onboarding(
    body: AgentDescriptorOnboardingCreate,
    session: Session = Depends(get_session),
) -> dict[str, Any]:
    """声明式接入：提交一份描述文件（JSON / Markdown 内嵌 ```json 块 / YAML frontmatter），
    由平台解析为 ExternalAgentOnboardingCreate 后完成注册。支持不同电脑上的 agent 通过
    一份 md/json 文件一键接入，roles 可自由选择 provider / requester 或两者兼得。"""
    try:
        data = _parse_agent_descriptor(body.descriptor, body.format)
    except HTTPException:
        raise
    except Exception as exc:  # json/yaml 解析错误都属于调用方的输入问题，不该报 500
        raise HTTPException(status_code=422, detail=f"Descriptor is not parseable: {exc}") from exc
    try:
        create = ExternalAgentOnboardingCreate(**data)
    except ValidationError as exc:
        raise HTTPException(status_code=422, detail=json.loads(exc.json())) from exc
    except TypeError as exc:
        raise HTTPException(status_code=422, detail=f"Descriptor has unexpected fields: {exc}") from exc
    return _register_external_agent(create, session)


def _yaml_or_json(content: str) -> Any:
    try:
        import yaml  # type: ignore
    except ImportError:
        return json.loads(content)
    return yaml.safe_load(content)


# 只认「行首、带语言标签」的围栏，避免误抓文档里的 ```bash 示例或跨块误配。
_FENCED_JSON = re.compile(r"^```json[ \t]*$(.*?)^```[ \t]*$", re.DOTALL | re.MULTILINE)
_FENCED_YAML = re.compile(r"^```ya?ml[ \t]*$(.*?)^```[ \t]*$", re.DOTALL | re.MULTILINE)


def _descriptor_frontmatter(raw: str) -> str | None:
    if not raw.startswith("---"):
        return None
    end = raw.find("\n---", 3)
    return raw[3:end].strip() if end != -1 else None


def _parse_agent_descriptor(text: str, fmt: str) -> dict[str, Any]:
    """把接入描述文件解析成 ExternalAgentOnboardingCreate 的入参字典。

    支持三种载体：纯 JSON、Markdown（行首 ```json / ```yaml 围栏，或 YAML frontmatter）、纯 YAML。

    auto 模式按「结构特征」判断而不是只看首字符：最常见的 md 描述文件是以 `# 标题` 开头的，
    早期按首字符判断会把整篇 Markdown 当成 YAML 喂给解析器，导致一键接入直接失败。
    """
    raw = text.strip()
    if not raw:
        raise ValueError("Descriptor is empty")

    fenced = _FENCED_JSON.search(raw) or _FENCED_YAML.search(raw)
    frontmatter = _descriptor_frontmatter(raw)

    if fmt == "auto":
        if raw.startswith("{"):
            fmt = "json"
        elif fenced is not None or frontmatter is not None:
            fmt = "markdown"
        else:
            fmt = "yaml"

    if fmt == "json":
        data = json.loads(raw)
    elif fmt == "markdown":
        if fenced is not None:
            data = _yaml_or_json(fenced.group(1).strip())
        elif frontmatter is not None:
            data = _yaml_or_json(frontmatter)
        else:
            raise ValueError(
                "Markdown descriptor must contain a fenced ```json / ```yaml block or YAML frontmatter"
            )
    else:
        data = _yaml_or_json(raw)

    if not isinstance(data, dict):
        raise ValueError("Descriptor must describe a single agent object")
    return data


def _register_external_agent(
    body: ExternalAgentOnboardingCreate,
    session: Session,
) -> dict[str, Any]:
    route = _resolve_onboarding_route(session, body)
    roles = sorted(set(body.roles))
    is_provider = "provider" in roles
    capabilities = sorted({item.strip().lower() for item in body.capabilities if item.strip()})
    if is_provider and not capabilities:
        raise HTTPException(status_code=422, detail="At least one capability is required")
    slug = _slugify_agent_key(body.agent_key)
    did = body.did or f"did:local:{slug}"
    operational_key = _operational_public_key(body.public_key)

    invitation: AgentInvitation | None = None
    if _managed_testnet():
        missing = [name for name, value in {
            "inviteCode": body.invite_code,
            "runtimeId": body.runtime_id,
            "signerUrl": body.signer_url,
            "signerCredentialRef": body.signer_credential_ref,
            "homeWallet": body.home_wallet,
        }.items() if not value]
        if missing:
            raise HTTPException(status_code=422, detail=f"Managed onboarding requires {', '.join(missing)}")
        invitation = session.scalar(select(AgentInvitation).where(
            AgentInvitation.code_hash == hashlib.sha256(str(body.invite_code).encode()).hexdigest()
        ))
        if invitation is None or invitation.status != "AVAILABLE" or _aware(invitation.expires_at) <= utcnow():
            raise HTTPException(status_code=409, detail="Invitation is invalid, expired, or already reserved")
        if invitation.route_key != route.route_key or not (invitation.min_route_version <= route.route_version <= invitation.max_route_version):
            raise HTTPException(status_code=422, detail="Invitation does not allow the selected route version")
        invitation_roles = sorted(set(invitation.roles or ["provider"]))
        if roles != invitation_roles:
            raise HTTPException(status_code=403, detail="Requested roles must exactly match the invitation scope")
        if not set(capabilities).issubset(set(invitation.capabilities)):
            raise HTTPException(status_code=422, detail="Requested capabilities exceed the invitation scope")
        if invitation.issuance_type == "SELF_SERVICE":
            if not _self_service_onboarding_enabled():
                raise HTTPException(status_code=403, detail="Self-service onboarding has been disabled")
            bindings = {
                "agentKey": (invitation.bound_agent_key, body.agent_key),
                "did": (invitation.bound_did, did),
                "runtimeId": (invitation.bound_runtime_id, body.runtime_id),
                "homeWallet": (invitation.bound_home_wallet, body.home_wallet.lower() if body.home_wallet else None),
                "publicKey": (invitation.bound_public_key_sha256, _identity_public_key_sha256(body.public_key)),
            }
            mismatched = [name for name, (expected, actual) in bindings.items() if expected != actual]
            if mismatched:
                raise HTTPException(status_code=403, detail=f"Invitation identity binding mismatch: {', '.join(mismatched)}")
        if body.executor_type == "llm" and "apiKey" in body.executor_config:
            raise HTTPException(status_code=422, detail="BYOK plaintext must not be submitted to the API")

    if session.scalar(select(Agent).where(Agent.did == did)) is not None:
        raise HTTPException(status_code=409, detail="Agent DID already exists")
    if session.scalar(select(Agent).where(Agent.slug == slug)) is not None:
        raise HTTPException(status_code=409, detail="Agent key already maps to an existing slug")
    if session.scalar(select(WorkflowAgentProfile).where(WorkflowAgentProfile.agent_key == body.agent_key)) is not None:
        raise HTTPException(status_code=409, detail="Workflow Agent key already exists")
    if _managed_testnet() and _runtime_id_registered(session, body.runtime_id):
        raise HTTPException(status_code=409, detail="Runtime ID already exists")
    if _managed_testnet() and not isinstance(operational_key, ec.EllipticCurvePublicKey):
        raise HTTPException(status_code=422, detail="Managed Agent identity must use ES256K")
    runtime_base = os.getenv("RUNNER_PUBLIC_BASE_URL", "http://127.0.0.1:8010").rstrip("/")
    # External independent Runner (executor_type == "http") keeps the caller-supplied
    # endpoint; managed LLM providers are dispatched to the platform-owned runner.
    use_platform_runner = _managed_testnet() and is_provider and body.executor_type == "llm"
    runtime_endpoint = f"{runtime_base}/agents/{slug}/invoke" if use_platform_runner else body.endpoint
    if is_provider and not runtime_endpoint:
        raise HTTPException(status_code=422, detail="endpoint is required for provider registrations")

    llm_descriptor = {
        "provider": body.llm_provider,
        "model": body.llm_model,
        "systemPromptHash": canonical_hash(body.system_prompt),
    }
    agent = Agent(
        did=did,
        slug=slug,
        display_name=body.agent_name,
        description=f"External Agent operated by {body.owner_name}.",
        roles=roles,
        status="pending",
        endpoint=runtime_endpoint,
        operational_public_key=body.public_key,
        erc8004_registration={
            "type": "external-llm-agent",
            "ownerContactHash": canonical_hash(body.owner_email.lower()),
            "organization": body.organization,
            "llm": llm_descriptor,
            "serviceEndpoint": runtime_endpoint,
            "admissionStatus": "PENDING",
        },
    )
    session.add(agent)
    session.flush()

    credential_fingerprint = canonical_hash(
        {
            "did": did,
            "agentKey": body.agent_key,
            "endpoint": runtime_endpoint,
            "capabilities": capabilities,
            "chainId": route.destination_chain_id,
            "payoutAsset": route.payout_asset,
            "llm": llm_descriptor,
        }
    )
    onboarding_access_token = secrets.token_urlsafe(32)
    onboarding_access_expires_at = utcnow() + timedelta(hours=24)
    profile = WorkflowAgentProfile(
        agent_id=agent.id,
        agent_key=body.agent_key,
        chain_id=route.destination_chain_id,
        home_wallet=body.home_wallet,
        capabilities=capabilities,
        priority=100,
        price_floor=Decimal(body.price_floor),
        currency=body.currency.upper(),
        active=False,
        success_rate_bps=0,
        trust_score=0,
        endpoint_latency_ms=500,
        availability="OFFLINE",
        capacity_available=False,
        gas_ready=False,
        last_heartbeat_at=None,
        metadata_json={
            "onboarding": {
                "ownerName": body.owner_name,
                "ownerEmailHash": canonical_hash(body.owner_email.lower()),
                "organization": body.organization,
                "submittedAt": serialize(utcnow()),
                "status": "PENDING_PROVISION" if use_platform_runner else "PENDING_OWNERSHIP_PROOF",
                "runtimeId": body.runtime_id if _managed_testnet() else None,
                "accessTokenHash": _onboarding_access_token_hash(onboarding_access_token),
                "accessTokenExpiresAt": serialize(onboarding_access_expires_at),
            },
            "llm": {
                **llm_descriptor,
            },
            "chainResolution": {
                "requestedPayoutAsset": body.payout_asset.upper(),
                "matchedRouteKey": route.route_key,
                "matchedRouteVersion": route.route_version,
                "destinationChainId": route.destination_chain_id,
            },
            "verification": {
                "identityVerified": False,
                "walletVerified": False,
                "manifestCheckPassed": False,
                "healthCheckPassed": False,
                "testTaskPassed": False,
            },
        },
        credential_status="UNCONFIGURED",
        credential_ref=body.signer_credential_ref if _managed_testnet() else f"external-agent:{did}",
        credential_fingerprint=credential_fingerprint,
        provisioning_id=str(uuid.uuid4()) if use_platform_runner else None,
        onboarding_status="PENDING_PROVISION" if use_platform_runner else "PENDING_VERIFICATION",
        invitation_id=invitation.id if invitation else None,
    )
    session.add(profile)
    session.flush()

    provision_outbox: AgentProvisionOutbox | None = None
    if _managed_testnet() and invitation is not None:
        reserved = session.execute(update(AgentInvitation).where(
            AgentInvitation.id == invitation.id,
            AgentInvitation.status == "AVAILABLE",
        ).values(status="RESERVED", reserved_registration_id=profile.id))
        if reserved.rowcount != 1:
            session.rollback()
            raise HTTPException(status_code=409, detail="Invitation was reserved by another registration")
        if not is_provider:
            invitation.status = "CONSUMED"
            invitation.consumed_at = utcnow()

    if _managed_testnet() and invitation is not None and use_platform_runner:
        public_key_hex = "0x" + operational_key.public_bytes(
            serialization.Encoding.X962,
            serialization.PublicFormat.UncompressedPoint,
        ).hex()
        executor_config = dict(body.executor_config)
        if body.executor_type == "llm":
            credential_ref = invitation.executor_credential_ref or str(executor_config.get("credentialRef", "env:DEEPSEEK_API_KEY"))
            if not credential_ref.startswith(("env:", "file:")) or any(character.isspace() for character in credential_ref):
                raise HTTPException(status_code=422, detail="Managed LLM credentialRef must use env: or file:")
            executor_config = {
                "credentialRef": credential_ref,
                "model": body.llm_model,
                "baseUrl": "https://api.deepseek.com",
                "systemPrompt": body.system_prompt,
                "timeoutSeconds": min(int(executor_config.get("timeoutSeconds", 60)), 120),
                "maxTokens": min(int(executor_config.get("maxTokens", 4096)), 8192),
            }
        provision_payload = {
            "runtimeId": body.runtime_id,
            "slug": slug,
            "did": did,
            "publicKeyHex": public_key_hex,
            "homeWallet": body.home_wallet,
            "homeChainId": route.destination_chain_id,
            "routeKey": route.route_key,
            "routeVersion": route.route_version,
            "capabilities": _runtime_capabilities(session, capabilities, body.currency.upper(), Decimal(body.price_floor)),
            "name": body.agent_name,
            "description": f"Managed Agent operated by {body.owner_name}.",
            "version": "1.0.0",
            "executorType": body.executor_type,
            "executorConfig": executor_config,
            "signerUrl": body.signer_url,
            "signerCredentialRef": body.signer_credential_ref,
            "platformRegistration": {
                "platformUrl": os.getenv(
                    "PLATFORM_API_BASE_URL",
                    os.getenv("API_URL", "http://127.0.0.1:8000"),
                ).rstrip("/"),
                "profileId": profile.id,
                "status": "ONLINE",
                "capacityAvailable": True,
                "endpointLatencyMs": 500,
                "heartbeatIntervalSeconds": 30,
            },
        }
        provision_outbox = AgentProvisionOutbox(
            provisioning_id=str(profile.provisioning_id),
            profile_id=profile.id,
            invitation_id=invitation.id,
            status="PENDING",
            payload=provision_payload,
            attempts=0,
            next_attempt_at=utcnow(),
        )
        session.add(provision_outbox)

    profile_data = _public_onboarding_profile(profile, agent)
    response = {
        "admissionStatus": "PENDING",
        "roles": roles,
        "canRequest": False,
        "canProvide": False,
        "onboardingToken": onboarding_access_token,
        "statusCode": f"{profile.id}.{onboarding_access_token}",
        "agent": serialize(agent),
        "profile": profile_data,
        "route": serialize(route),
        "chainResolution": {
            "sourceChainId": route.source_chain_id,
            "destinationChainId": route.destination_chain_id,
            "routeKey": route.route_key,
            "routeVersion": route.route_version,
            "settlementAsset": route.settlement_asset,
            "payoutAsset": route.payout_asset,
            "payoutRate": serialize(route.payout_rate),
        },
        "next": {
            "challengeEndpoint": f"/api/v1/agent-onboarding/registrations/{profile.id}/challenge",
            "verifyEndpoint": f"/api/v1/agent-onboarding/registrations/{profile.id}/verify",
            "statusEndpoint": f"/api/v1/agent-onboarding/registrations/{profile.id}/status",
            "completeEndpoint": f"/api/v1/agent-onboarding/registrations/{profile.id}/complete",
            "heartbeatEndpoint": f"/api/v1/agent-onboarding/registrations/{profile.id}/heartbeat",
            "resultSubmission": "/api/v1/workflows/{workflowId}/nodes/{nodeId}/attempts/{attemptId}/results",
            "requestSubmission": "/api/v1/workflows",
        },
    }
    audit(session, None, "agent_onboarding.registered", "workflow_agent_profile", profile.id, response["chainResolution"])
    session.commit()
    if provision_outbox is not None:
        provisioned = _process_provision_outbox(session, provision_outbox)
        response["admissionStatus"] = "PENDING_OWNERSHIP_PROOF" if provisioned else "PROVISION_FAILED"
        response["provisioning"] = {
            "provisioningId": provision_outbox.provisioning_id,
            "status": "PROVISIONED" if provisioned else "PROVISION_FAILED",
            "retryEndpoint": f"/api/v1/agent-onboarding/registrations/{profile.id}/provision-retry",
        }
        response["profile"] = _public_onboarding_profile(profile, agent)
    return response


@router.post("/agent-onboarding/registrations/{profile_id}/challenge", status_code=201)
def create_external_agent_challenge(
    profile_id: str,
    session: Session = Depends(get_session),
) -> dict[str, Any]:
    profile, agent = _onboarding_profile(session, profile_id)
    if _managed_testnet() and profile.onboarding_status not in {"PROVISIONED", "PENDING_VERIFICATION"}:
        raise HTTPException(status_code=409, detail="Runner provisioning must complete before ownership verification")
    if profile.home_wallet is None:
        raise HTTPException(status_code=422, detail="homeWallet is required before ownership verification")
    nonce = secrets.token_urlsafe(32)
    expires_at = utcnow() + timedelta(minutes=10)
    message = _challenge_message(profile, agent, nonce, serialize(expires_at))
    challenge = ExternalAgentOnboardingChallenge(
        profile_id=profile.id,
        nonce=nonce,
        message=message,
        expires_at=expires_at,
    )
    session.add(challenge)
    if _managed_testnet():
        profile.onboarding_status = "PENDING_VERIFICATION"
    session.flush()
    response = {
        "challengeId": challenge.id,
        "profileId": profile.id,
        "nonce": nonce,
        "message": message,
        "expiresAt": serialize(expires_at),
        "signatureRequirements": {
            "identity": "Sign the UTF-8 message with the registered ES256K Agent identity key.",
            "wallet": "Sign the same message with the EVM homeWallet using EIP-191 personal_sign.",
        },
    }
    audit(session, None, "agent_onboarding.challenge_created", "workflow_agent_profile", profile.id)
    session.commit()
    return response


@router.post("/agent-onboarding/registrations/{profile_id}/verify")
def verify_external_agent_ownership(
    profile_id: str,
    body: ExternalAgentOwnershipVerify,
    session: Session = Depends(get_session),
) -> dict[str, Any]:
    profile, agent = _onboarding_profile(session, profile_id)
    challenge = session.get(ExternalAgentOnboardingChallenge, body.challenge_id)
    if challenge is None or challenge.profile_id != profile.id:
        raise HTTPException(status_code=404, detail="Onboarding challenge not found")
    if challenge.used_at is not None:
        raise HTTPException(status_code=409, detail="Onboarding challenge has already been used")
    if _aware(challenge.expires_at) <= utcnow():
        raise HTTPException(status_code=409, detail="Onboarding challenge has expired")
    if profile.home_wallet is None:
        raise HTTPException(status_code=422, detail="homeWallet is required for wallet verification")

    _verify_identity_signature(agent, challenge.message, body.identity_signature)
    _verify_wallet_signature(profile.home_wallet, challenge.message, body.wallet_signature)

    challenge.used_at = utcnow()
    metadata = dict(profile.metadata_json or {})
    verification = dict(metadata.get("verification") or {})
    verification.update({
        "identityVerified": True,
        "walletVerified": True,
        "ownershipVerifiedAt": serialize(utcnow()),
        "challengeId": challenge.id,
    })
    metadata["verification"] = verification
    onboarding = dict(metadata.get("onboarding") or {})
    is_provider = "provider" in (agent.roles or [])
    onboarding["status"] = "PENDING_PLATFORM_REVIEW" if is_provider else "ACTIVE"
    metadata["onboarding"] = onboarding
    profile.metadata_json = metadata
    if is_provider:
        profile.credential_status = "REFERENCE_ONLY"
        profile.onboarding_status = "PENDING_VERIFICATION"
    else:
        agent.status = "active"
        profile.active = True
        profile.availability = "OFFLINE"
        profile.capacity_available = False
        profile.gas_ready = False
        profile.credential_status = "ACTIVE"
        profile.onboarding_status = "ACTIVE"
        verification["activatedAt"] = serialize(utcnow())
        verification["activatedBy"] = "ownership-verification"
        metadata["verification"] = verification
        profile.metadata_json = metadata
    audit(session, None, "agent_onboarding.ownership_verified", "workflow_agent_profile", profile.id)
    session.commit()
    response = _public_onboarding_profile(profile, agent)
    return {
        "admissionStatus": "PENDING_PLATFORM_REVIEW" if is_provider else "ACTIVE",
        "roles": sorted(set(agent.roles or [])),
        "canRequest": "requester" in (agent.roles or []) and agent.status == "active",
        "canProvide": is_provider and profile.active,
        "profile": response,
    }


@router.get("/agent-onboarding/registrations/{profile_id}/status")
def get_external_agent_onboarding_status(
    profile_id: str,
    onboarding_token: str | None = Header(default=None, alias="X-Onboarding-Token"),
    session: Session = Depends(get_session),
) -> dict[str, Any]:
    profile, agent = _onboarding_profile(session, profile_id)
    _require_onboarding_access(profile, onboarding_token)
    return _onboarding_status_response(session, profile, agent)


@router.post("/agent-onboarding/registrations/{profile_id}/complete")
def complete_external_agent_onboarding(
    profile_id: str,
    body: ExternalAgentSelfServiceCompletion,
    onboarding_token: str | None = Header(default=None, alias="X-Onboarding-Token"),
    session: Session = Depends(get_session),
) -> dict[str, Any]:
    profile, agent = _onboarding_profile(session, profile_id)
    _require_onboarding_access(profile, onboarding_token)
    if "provider" not in (agent.roles or []):
        if not profile.active:
            raise HTTPException(status_code=409, detail="Ownership verification is required before completion")
        return {"checksPassed": True, "checksSkipped": True, **_onboarding_status_response(session, profile, agent)}
    if profile.active and profile.credential_status == "ACTIVE":
        return {"checksPassed": True, **_onboarding_status_response(session, profile, agent)}

    metadata = dict(profile.metadata_json or {})
    verification = dict(metadata.get("verification") or {})
    if not verification.get("identityVerified") or not verification.get("walletVerified"):
        raise HTTPException(status_code=409, detail="Ownership verification is required before completion")
    if _managed_testnet():
        runtime_access_token = os.getenv("RUNNER_CHECK_API_TOKEN", "")
        if not runtime_access_token:
            raise HTTPException(status_code=503, detail="Managed Runner check credential is not configured")
    else:
        runtime_access_token = body.runtime_access_token or ""
        if not runtime_access_token:
            raise HTTPException(status_code=422, detail="runtimeAccessToken is required outside managed-testnet mode")
    try:
        evidence = _run_agent_technical_checks(session, profile, agent, runtime_access_token)
    except (httpx.HTTPError, InvalidSignature, ValueError, KeyError, TypeError) as exc:
        verification.update({
            "manifestCheckPassed": False,
            "healthCheckPassed": False,
            "testTaskPassed": False,
            "gasReadyVerified": False,
            "lastCheckFailureHash": canonical_hash(str(exc)),
            "checkedAt": serialize(utcnow()),
        })
        metadata["verification"] = verification
        profile.metadata_json = metadata
        audit(session, None, "agent_onboarding.self_service_check_failed", "workflow_agent_profile", profile.id)
        session.commit()
        raise HTTPException(status_code=422, detail=f"Agent technical checks failed: {exc}") from exc

    now = utcnow()
    verification.update(evidence)
    verification.update({"activatedAt": serialize(now), "activatedBy": "self-service"})
    metadata["verification"] = verification
    onboarding = dict(metadata.get("onboarding") or {})
    onboarding.update({"status": "ACTIVE", "accessTokenUsedAt": serialize(now)})
    metadata["onboarding"] = onboarding
    agent.status = "active"
    profile.active = True
    profile.availability = "OFFLINE"
    profile.capacity_available = False
    profile.gas_ready = True
    profile.endpoint_latency_ms = int(evidence["endpointLatencyMs"])
    profile.trust_score = 75
    profile.success_rate_bps = 9000
    profile.credential_status = "ACTIVE"
    profile.onboarding_status = "ACTIVE"
    profile.metadata_json = metadata
    audit(session, None, "agent_onboarding.self_service_activated", "workflow_agent_profile", profile.id)
    session.commit()
    return {
        "checksPassed": True,
        "evidence": evidence,
        **_onboarding_status_response(session, profile, agent),
    }


@router.post(
    "/agent-onboarding/registrations/{profile_id}/heartbeat-challenge",
    status_code=201,
)
def create_external_agent_heartbeat_challenge(
    profile_id: str,
    body: ExternalAgentHeartbeatChallengeRequest,
    session: Session = Depends(get_session),
) -> dict[str, Any]:
    profile, agent = _onboarding_profile(session, profile_id)
    if "provider" not in (agent.roles or []):
        raise HTTPException(status_code=409, detail="Only provider Agents use runtime heartbeats")
    if profile.credential_status not in {"REFERENCE_ONLY", "ACTIVE"}:
        raise HTTPException(status_code=409, detail="Agent ownership must be verified before heartbeat")
    nonce = secrets.token_urlsafe(32)
    expires_at = utcnow() + timedelta(minutes=2)
    message = _heartbeat_message(profile, agent, nonce, serialize(expires_at), body)
    challenge = ExternalAgentOnboardingChallenge(
        profile_id=profile.id,
        purpose="HEARTBEAT",
        nonce=nonce,
        message=message,
        expires_at=expires_at,
    )
    session.add(challenge)
    session.flush()
    session.commit()
    return {
        "challengeId": challenge.id,
        "message": message,
        "expiresAt": serialize(expires_at),
    }


@router.post("/agent-onboarding/registrations/{profile_id}/heartbeat")
def post_external_agent_heartbeat(
    profile_id: str,
    body: ExternalAgentHeartbeat,
    session: Session = Depends(get_session),
) -> dict[str, Any]:
    profile, agent = _onboarding_profile(session, profile_id)
    challenge = session.get(ExternalAgentOnboardingChallenge, body.challenge_id)
    if challenge is None or challenge.profile_id != profile.id or challenge.purpose != "HEARTBEAT":
        raise HTTPException(status_code=404, detail="Heartbeat challenge not found")
    if challenge.used_at is not None:
        raise HTTPException(status_code=409, detail="Heartbeat challenge has already been used")
    if _aware(challenge.expires_at) <= utcnow():
        raise HTTPException(status_code=409, detail="Heartbeat challenge has expired")
    expected = _heartbeat_message(
        profile,
        agent,
        challenge.nonce,
        serialize(challenge.expires_at),
        ExternalAgentHeartbeatChallengeRequest(
            status=body.status,
            capacityAvailable=body.capacity_available,
            endpointLatencyMs=body.endpoint_latency_ms,
        ),
    )
    if not secrets.compare_digest(expected, challenge.message):
        raise HTTPException(status_code=422, detail="Heartbeat state does not match the issued challenge")
    _verify_identity_signature(agent, challenge.message, body.signature)
    challenge.used_at = utcnow()
    metadata = dict(profile.metadata_json or {})
    metadata["lastSignedHeartbeat"] = {
        "challengeId": challenge.id,
        "nonce": challenge.nonce,
        "status": body.status,
        "capacityAvailable": body.capacity_available,
        "receivedAt": serialize(utcnow()),
    }
    profile.metadata_json = metadata
    profile.last_heartbeat_at = utcnow()
    profile.endpoint_latency_ms = body.endpoint_latency_ms
    if profile.active and profile.credential_status == "ACTIVE":
        profile.availability = body.status
        profile.capacity_available = body.capacity_available
    session.commit()
    return {
        "accepted": True,
        "profileId": profile.id,
        "matchable": (
            "provider" in (agent.roles or [])
            and agent.status == "active"
            and profile.active
            and profile.availability == "ONLINE"
            and profile.gas_ready
            and profile.credential_status == "ACTIVE"
        ),
    }


@router.post("/agent-onboarding/registrations/{profile_id}/activate")
def activate_external_agent_registration(
    profile_id: str,
    body: ExternalAgentActivationReview,
    actor: Agent = Depends(require_role("admin")),
    session: Session = Depends(get_session),
) -> dict[str, Any]:
    profile, agent = _onboarding_profile(session, profile_id)
    if "provider" not in (agent.roles or []):
        raise HTTPException(status_code=409, detail="Requester-only Agents activate after ownership verification")
    metadata = dict(profile.metadata_json or {})
    verification = dict(metadata.get("verification") or {})
    if not verification.get("identityVerified") or not verification.get("walletVerified"):
        raise HTTPException(status_code=409, detail="Ownership verification is required before activation")
    missing_checks = [name for name in (
        "manifestCheckPassed",
        "healthCheckPassed",
        "testTaskPassed",
        "gasReadyVerified",
    ) if not verification.get(name)]
    if missing_checks:
        raise HTTPException(
            status_code=409,
            detail=f"Platform technical checks are required: {', '.join(missing_checks)}",
        )
    checked_at_raw = verification.get("checkedAt")
    if not checked_at_raw:
        raise HTTPException(status_code=409, detail="Platform technical check timestamp is missing")
    checked_at = datetime.fromisoformat(str(checked_at_raw).replace("Z", "+00:00"))
    if _aware(checked_at) <= utcnow() - timedelta(minutes=10):
        raise HTTPException(status_code=409, detail="Platform technical checks have expired")

    verification.update({
        "activatedAt": serialize(utcnow()),
        "activatedBy": actor.did,
    })
    metadata["verification"] = verification
    onboarding = dict(metadata.get("onboarding") or {})
    onboarding["status"] = "ACTIVE"
    onboarding["reviewNotesHash"] = canonical_hash(body.notes) if body.notes else None
    metadata["onboarding"] = onboarding
    agent.status = "active"
    profile.active = True
    profile.availability = "OFFLINE"
    profile.capacity_available = False
    profile.gas_ready = True
    profile.trust_score = 75
    profile.success_rate_bps = 9000
    profile.credential_status = "ACTIVE"
    profile.onboarding_status = "ACTIVE"
    profile.metadata_json = metadata
    audit(session, actor.id, "agent_onboarding.activated", "workflow_agent_profile", profile.id)
    session.commit()
    response = _public_onboarding_profile(profile, agent)
    return {
        "admissionStatus": "ACTIVE_AWAITING_HEARTBEAT",
        "profile": response,
    }


@router.post("/agent-onboarding/registrations/{profile_id}/checks")
def run_external_agent_technical_checks(
    profile_id: str,
    body: ExternalAgentTechnicalCheckRequest,
    actor: Agent = Depends(require_role("admin")),
    session: Session = Depends(get_session),
) -> dict[str, Any]:
    profile, agent = _onboarding_profile(session, profile_id)
    if "provider" not in (agent.roles or []):
        raise HTTPException(status_code=409, detail="Requester-only Agents do not have a Runner to check")
    metadata = dict(profile.metadata_json or {})
    verification = dict(metadata.get("verification") or {})
    if not verification.get("identityVerified") or not verification.get("walletVerified"):
        raise HTTPException(status_code=409, detail="Ownership verification is required before technical checks")
    try:
        evidence = _run_agent_technical_checks(
            session,
            profile,
            agent,
            body.runtime_access_token,
        )
    except (httpx.HTTPError, InvalidSignature, ValueError, KeyError, TypeError) as exc:
        verification.update({
            "manifestCheckPassed": False,
            "healthCheckPassed": False,
            "testTaskPassed": False,
            "gasReadyVerified": False,
            "lastCheckFailureHash": canonical_hash(str(exc)),
            "checkedAt": serialize(utcnow()),
        })
        metadata["verification"] = verification
        profile.metadata_json = metadata
        audit(session, actor.id, "agent_onboarding.technical_check_failed", "workflow_agent_profile", profile.id)
        session.commit()
        raise HTTPException(status_code=422, detail=f"Agent technical checks failed: {exc}") from exc
    verification.update(evidence)
    metadata["verification"] = verification
    profile.metadata_json = metadata
    profile.endpoint_latency_ms = int(evidence["endpointLatencyMs"])
    profile.gas_ready = True
    audit(session, actor.id, "agent_onboarding.technical_checks_passed", "workflow_agent_profile", profile.id)
    session.commit()
    return {
        "checksPassed": True,
        "profileId": profile.id,
        "evidence": evidence,
    }


@router.get("/workflow-agents")
def list_workflow_agents(session: Session = Depends(get_session)) -> dict[str, Any]:
    profiles = list(
        session.scalars(select(WorkflowAgentProfile).order_by(WorkflowAgentProfile.chain_id, WorkflowAgentProfile.priority))
    )
    items: list[dict[str, Any]] = []
    for profile in profiles:
        agent = session.get(Agent, profile.agent_id)
        if agent is None or "provider" not in (agent.roles or []):
            continue
        item = _public_onboarding_profile(profile, agent)
        item.pop("credentialRef", None)
        items.append(item)
    return {"items": items, "total": len(items)}


@router.post("/workflow-agents", status_code=201)
def post_workflow_agent(
    body: WorkflowAgentProfileCreate,
    actor: Agent = Depends(require_role("admin")),
    idem: str | None = Depends(idempotency_header),
    session: Session = Depends(get_session),
) -> dict[str, Any]:
    payload = body.model_dump(by_alias=True)
    replay = get_idempotent_response(session, actor.id, "workflow_agents.create", idem, payload)
    if replay:
        return replay
    agent = session.get(Agent, body.agent_id)
    if agent is None:
        raise HTTPException(status_code=404, detail="Agent not found")
    if "provider" not in agent.roles:
        raise HTTPException(status_code=422, detail="Workflow Agent must have the provider role")
    duplicate = session.scalar(
        select(WorkflowAgentProfile).where(
            (WorkflowAgentProfile.agent_key == body.agent_key) | (WorkflowAgentProfile.agent_id == body.agent_id)
        )
    )
    if duplicate is not None:
        raise HTTPException(status_code=409, detail="Workflow Agent profile already exists")
    route = session.scalar(
        select(ChainRoute).where(
            ChainRoute.source_chain_id == CHAIN_A,
            ChainRoute.destination_chain_id == body.chain_id,
            ChainRoute.status == "ACTIVE",
        )
    )
    if route is None:
        raise HTTPException(status_code=422, detail="Register and validate an active chain route before adding this Agent")
    item = WorkflowAgentProfile(
        agent_id=body.agent_id,
        agent_key=body.agent_key,
        chain_id=body.chain_id,
        home_wallet=body.home_wallet,
        capabilities=body.capabilities,
        priority=body.priority,
        price_floor=body.price_floor,
        currency=body.currency,
        success_rate_bps=body.success_rate_bps,
        trust_score=body.trust_score,
        endpoint_latency_ms=body.endpoint_latency_ms,
        availability=body.availability,
        capacity_available=body.capacity_available,
        gas_ready=body.gas_ready,
        last_heartbeat_at=body.last_heartbeat_at or utcnow(),
        quote_expires_at=body.quote_expires_at,
        metadata_json=body.metadata,
        credential_status=body.credential_status,
        credential_ref=body.credential_ref,
        credential_fingerprint=body.credential_fingerprint,
    )
    session.add(item)
    session.flush()
    response = serialize(item)
    response["agent"] = serialize(agent)
    save_idempotent_response(session, actor.id, "workflow_agents.create", idem, payload, response)
    session.commit()
    return response


@router.patch("/workflow-agents/{profile_id}")
def patch_workflow_agent(
    profile_id: str,
    body: WorkflowAgentProfileUpdate,
    actor: Agent = Depends(require_role("admin")),
    idem: str | None = Depends(idempotency_header),
    session: Session = Depends(get_session),
) -> dict[str, Any]:
    item = session.get(WorkflowAgentProfile, profile_id)
    if item is None:
        raise HTTPException(status_code=404, detail="Workflow Agent profile not found")
    payload = body.model_dump(by_alias=True, exclude_none=True)
    route = f"workflow_agents.{profile_id}.patch"
    replay = get_idempotent_response(session, actor.id, route, idem, payload)
    if replay:
        return replay
    for field, value in body.model_dump(exclude_none=True).items():
        setattr(item, "metadata_json" if field == "metadata" else field, value)
    response = serialize(item)
    response["agent"] = serialize(session.get(Agent, item.agent_id))
    save_idempotent_response(session, actor.id, route, idem, payload, response)
    session.commit()
    return response


@router.get("/workflow-agents/{profile_id}")
def get_workflow_agent(profile_id: str, session: Session = Depends(get_session)) -> dict[str, Any]:
    # Resolve by workflow agent profile id first, then fall back to the linked
    # Agent id so legacy deep links (/agents/<agentId>) keep working.
    profile = session.get(WorkflowAgentProfile, profile_id)
    if profile is None:
        profile = session.scalar(
            select(WorkflowAgentProfile).where(WorkflowAgentProfile.agent_id == profile_id)
        )
    if profile is None:
        raise HTTPException(status_code=404, detail="Workflow Agent profile not found")
    agent = session.get(Agent, profile.agent_id)
    if agent is None:
        raise HTTPException(status_code=404, detail="Agent not found")
    item = _public_onboarding_profile(profile, agent)
    item.pop("credentialRef", None)
    return item


@router.get("/chain-worker-health")
def chain_worker_health() -> dict[str, Any]:
    # Lightweight proxy so the web console can show chain-worker scanner
    # diagnostics without exposing the worker container to the public network.
    url = os.getenv("CHAIN_WORKER_HEALTH_URL", "http://chain-worker:9090/health")
    try:
        with httpx.Client(timeout=5.0) as client:
            resp = client.get(url)
    except Exception as exc:
        raise HTTPException(status_code=502, detail=f"chain-worker health unreachable: {exc}")
    if resp.status_code != 200:
        raise HTTPException(status_code=502, detail=f"chain-worker health returned {resp.status_code}")
    try:
        return resp.json()
    except Exception as exc:
        raise HTTPException(status_code=502, detail=f"chain-worker health invalid JSON: {exc}")


@router.get("/cross-chain-messages")
def list_cross_chain_messages(
    status: str | None = Query(default=None),
    session: Session = Depends(get_session),
) -> dict[str, Any]:
    query = select(CrossChainMessage).order_by(CrossChainMessage.created_at.desc()).limit(500)
    if status:
        query = query.where(CrossChainMessage.status == status.upper())
    items = list(session.scalars(query))
    return {"items": serialize(items), "total": len(items)}
