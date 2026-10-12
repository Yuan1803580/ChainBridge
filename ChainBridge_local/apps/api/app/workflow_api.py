from __future__ import annotations

import json
import os
import base64
import binascii
import hashlib
import hmac
import ipaddress
import re
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
from fastapi import APIRouter, Depends, HTTPException, Query, Request, Response
from cryptography.exceptions import InvalidSignature
from cryptography.hazmat.primitives import hashes
from cryptography.hazmat.primitives.asymmetric import ec
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PublicKey
from jsonschema import Draft202012Validator, SchemaError, ValidationError
from sqlalchemy import delete, func, or_, select
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
    matching_diagnostics,
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
from .reputation import initialize_agent_reputation
from .database import get_session
from .integration_auth import require_listener_auth
from .jcs import canonical_json_bytes, canonical_sha256
from .models import Agent, Capability, utcnow
from .security import get_current_agent, idempotency_header, require_role
from .serializers import decimal_string, serialize
from .services import audit, canonical_hash, get_idempotent_response, save_idempotent_response
from .workflow_models import (
    AgentMatchCandidate,
    AgentMatchRound,
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
    LocalFaucetIssuance,
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
    ArbitrationVoteCreate,
    ArbitrationVotePayloadRequest,
    ChainRouteCreate,
    CoordinateRequest,
    ExternalAgentActivationReview,
    ExternalAgentHeartbeat,
    ExternalAgentHeartbeatChallengeRequest,
    ExternalAgentOnboardingCreate,
    ExternalAgentOwnershipVerify,
    ExternalAgentRejectionReview,
    ExternalAgentTechnicalCheckRequest,
    FailedRefundConfirmation,
    LocalProtocolFaucetRequest,
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
    SettlementConfirmation,
    SettlementCustodyUpdate,
    WorkflowAgentProfileCreate,
    WorkflowAgentChatRequest,
    WorkflowAgentChatSessionCreate,
    WorkflowAgentProfileUpdate,
    WorkflowCreate,
    WorkflowFailureCreate,
    WorkflowFinalizationConfirmation,
    WorkflowFundingConfirmation,
    WorkflowPlanRequest,
)
from .workflow_recovery import workflow_coordination_guard


router = APIRouter(prefix="/api/v1", tags=["dynamic-workflows"])
_checked_runtime_tokens: dict[str, str] = {}
EXECUTOR_AGENT_TYPE = "EXECUTOR"
COORDINATOR_AGENT_TYPE = "COORDINATOR"
COORDINATOR_CAPABILITY = "workflow_coordination"
_COORDINATOR_RUNTIME_PATH = re.compile(
    r"^(chat|conversations/[0-9a-f-]+(?:/refine|/workflows|/coordinate|/failure-advice|/summary)?|workflows/[0-9a-f-]+/summary)$",
    re.IGNORECASE,
)


def _aware(value: datetime) -> datetime:
    return value if value.tzinfo is not None else value.replace(tzinfo=timezone.utc)


def _slugify_agent_key(value: str) -> str:
    slug = "".join(character.lower() if character.isalnum() else "-" for character in value)
    return "-".join(part for part in slug.split("-") if part)[:80]


def _agent_type(profile: WorkflowAgentProfile, agent: Agent | None = None) -> str:
    configured = str((profile.metadata_json or {}).get("agentType") or "").upper()
    if configured in {EXECUTOR_AGENT_TYPE, COORDINATOR_AGENT_TYPE}:
        return configured
    if agent is not None and "coordinator" in (agent.roles or []):
        return COORDINATOR_AGENT_TYPE
    return EXECUTOR_AGENT_TYPE


def _resolve_onboarding_route(
    session: Session,
    body: ExternalAgentOnboardingCreate,
) -> tuple[ChainRoute | None, int]:
    if body.agent_type == COORDINATOR_AGENT_TYPE:
        return None, CHAIN_A
    query = select(ChainRoute).where(
        ChainRoute.source_chain_id == CHAIN_A,
        ChainRoute.status == "ACTIVE",
    )
    if body.preferred_chain_id is not None:
        query = query.where(ChainRoute.destination_chain_id == body.preferred_chain_id)
    else:
        query = query.where(ChainRoute.payout_asset == body.payout_asset.upper())
    route = session.scalar(query.order_by(ChainRoute.route_version.desc()))
    if route is None and body.preferred_chain_id is None:
        raise HTTPException(
            status_code=422,
            detail="preferredChainId is required when no active route matches payoutAsset",
        )
    if route is None:
        return None, int(body.preferred_chain_id)
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
    return route, route.destination_chain_id


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
    return canonical_json_bytes(value)


def _normalized_public_key(value: str) -> str:
    return value.removeprefix("es256k:").removeprefix("0x").lower()


EXTERNAL_AGENT_PROTOCOL_VERSION = "2.0"
SUPPORTED_EXTERNAL_AGENT_PROTOCOL_VERSIONS = {"1.0", "2.0"}


def _agent_protocol_version(manifest: dict[str, Any]) -> str:
    version = str(manifest.get("protocolVersion") or "1.0")
    if version not in SUPPORTED_EXTERNAL_AGENT_PROTOCOL_VERSIONS:
        raise ValueError(f"Unsupported external Agent protocol version: {version}")
    if version == EXTERNAL_AGENT_PROTOCOL_VERSION:
        protocol = manifest.get("protocol")
        if not isinstance(protocol, dict):
            raise ValueError("External Agent V2 manifest is missing protocol metadata")
        if protocol.get("canonicalization") != "RFC8785-JCS":
            raise ValueError("External Agent V2 must use RFC8785-JCS canonicalization")
        if protocol.get("resultSignature") != "ES256K":
            raise ValueError("External Agent V2 must use ES256K result signatures")
    return version


def _agent_protocol_headers(manifest: dict[str, Any]) -> dict[str, str]:
    return {
        "X-Agent-Protocol-Version": _agent_protocol_version(manifest),
        "Accept-Agent-Protocol": EXTERNAL_AGENT_PROTOCOL_VERSION,
    }


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
    expected_hash = canonical_sha256(output, prefix="sha256:")
    if not secrets.compare_digest(str(result.get("outputHash")), expected_hash):
        raise ValueError("Agent test result outputHash does not match output")
    signature = result.get("signature")
    if not isinstance(signature, dict) or signature.get("algorithm") != "ES256K":
        raise ValueError("Agent test result must use ES256K")
    public_key = signature.get("publicKeyHex") or agent.operational_public_key
    if _normalized_public_key(str(public_key)) != _normalized_public_key(agent.operational_public_key):
        raise ValueError("Agent test result public key does not match registration")
    signed_payload = {field: result[field] for field in required_fields}
    protocol_version = result.get("protocolVersion")
    if protocol_version is not None:
        if str(protocol_version) != EXTERNAL_AGENT_PROTOCOL_VERSION:
            raise ValueError("Agent result uses an unsupported protocol version")
        signed_payload["protocolVersion"] = str(protocol_version)
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
    base = endpoint.rstrip("/")
    for runtime_path in ("/invoke", "/chat"):
        if base.endswith(runtime_path):
            return base.removesuffix(runtime_path)
    return base


def _set_agent_admission_snapshot(agent: Agent, admission_status: str) -> None:
    registration = dict(agent.erc8004_registration or {})
    registration["admissionStatus"] = admission_status
    agent.erc8004_registration = registration


def _market_capability_slug(agent: Agent, capability_id: str) -> str:
    suffix = _slugify_agent_key(capability_id) or "capability"
    prefix = _slugify_agent_key(agent.slug) or "agent"
    return f"{prefix[: max(1, 99 - len(suffix))]}-{suffix}"[:100].rstrip("-")


def _manifest_snapshot(manifest: dict[str, Any]) -> dict[str, Any]:
    capabilities: list[dict[str, Any]] = []
    for raw in manifest.get("capabilities") or []:
        if not isinstance(raw, dict):
            continue
        capability_id = str(raw.get("capabilityId", "")).strip().lower()
        if not capability_id:
            continue
        capabilities.append({
            "capabilityId": capability_id,
            "name": str(raw.get("name") or capability_id.replace("-", " ").title()),
            "description": str(raw.get("description") or ""),
            "endpoint": str(raw.get("endpoint") or ""),
            "pricingModel": str(raw.get("pricingModel") or "fixed"),
            "basePrice": str(raw.get("basePrice") or "0"),
            "currency": str(raw.get("currency") or "USDC").upper(),
            "estimatedDurationSeconds": int(raw.get("estimatedDurationSeconds") or 60),
            "inputSchema": raw.get("inputSchema") if isinstance(raw.get("inputSchema"), dict) else {"type": "object"},
            "outputSchema": raw.get("outputSchema") if isinstance(raw.get("outputSchema"), dict) else {"type": "object"},
            "exampleRequest": raw.get("exampleRequest") if isinstance(raw.get("exampleRequest"), dict) else {},
        })
    return {
        "protocolVersion": _agent_protocol_version(manifest),
        "protocol": manifest.get("protocol") if isinstance(manifest.get("protocol"), dict) else {},
        "agentId": str(manifest.get("agentId") or ""),
        "agentType": str(manifest.get("agentType") or EXECUTOR_AGENT_TYPE).upper(),
        "did": str(manifest.get("did") or ""),
        "name": str(manifest.get("name") or ""),
        "description": str(manifest.get("description") or ""),
        "version": str(manifest.get("version") or "1.0.0"),
        "conversation": manifest.get("conversation") if isinstance(manifest.get("conversation"), dict) else {},
        "tools": manifest.get("tools") if isinstance(manifest.get("tools"), list) else [],
        "capabilities": capabilities,
    }


def _agent_invocation_timeout_seconds() -> float:
    return max(1.0, float(os.getenv("AGENT_INVOCATION_TIMEOUT_SECONDS", "2100")))


def _agent_task_deadline_seconds() -> float:
    configured = float(os.getenv("AGENT_TASK_DEADLINE_SECONDS", "2400"))
    return max(_agent_invocation_timeout_seconds() + 60.0, configured)


def _fetch_agent_manifest(profile: WorkflowAgentProfile, agent: Agent) -> dict[str, Any]:
    if not agent.endpoint:
        raise ValueError("Agent endpoint is required")
    base = _agent_endpoint_base(agent.endpoint)
    _validate_agent_endpoint(base)
    token = _runtime_access_token(profile, agent)
    timeout = _agent_invocation_timeout_seconds()
    with httpx.Client(timeout=timeout, follow_redirects=False) as client:
        response = client.get(f"{base}/manifest", headers={"Authorization": f"Bearer {token}"})
        response.raise_for_status()
        manifest = response.json()
    _agent_protocol_version(manifest)
    if manifest.get("did") != agent.did or manifest.get("agentId") != agent.slug:
        raise ValueError("Agent manifest identity does not match registration")
    return _manifest_snapshot(manifest)


def _sync_market_capabilities(
    session: Session,
    profile: WorkflowAgentProfile,
    agent: Agent,
    manifest: dict[str, Any] | None = None,
    *,
    reconcile_profile: bool = False,
) -> list[Capability]:
    snapshot = manifest or ((profile.metadata_json or {}).get("verification") or {}).get("manifest")
    if not isinstance(snapshot, dict):
        return []
    manifest_capability_ids = sorted({
        str(item.get("capabilityId") or "").strip().lower()
        for item in snapshot.get("capabilities") or []
        if isinstance(item, dict) and str(item.get("capabilityId") or "").strip()
    })
    if reconcile_profile:
        if not manifest_capability_ids:
            raise ValueError("Agent manifest must expose at least one capability")
        if len(manifest_capability_ids) > 20:
            raise ValueError("Agent manifest exposes too many capabilities")
        invalid = [item for item in manifest_capability_ids if not re.fullmatch(r"[a-z0-9][a-z0-9._-]{1,99}", item)]
        if invalid:
            raise ValueError(f"Agent manifest has invalid capability IDs: {', '.join(invalid)}")
        profile.capabilities = manifest_capability_ids
    registered = {item.lower() for item in profile.capabilities}
    active_slugs: set[str] = set()
    synced: list[Capability] = []
    market_availability = "available" if _external_profile_matchable(profile, agent) else "offline"
    for raw in snapshot.get("capabilities") or []:
        if not isinstance(raw, dict):
            continue
        capability_id = str(raw.get("capabilityId") or "").lower()
        if capability_id not in registered:
            continue
        slug = _market_capability_slug(agent, capability_id)
        active_slugs.add(slug)
        capability = session.scalar(select(Capability).where(Capability.slug == slug))
        if capability is None:
            capability = Capability(
                provider_id=agent.id,
                name="",
                slug=slug,
                description="",
                input_schema={},
                output_schema={},
                endpoint="",
                base_price=Decimal("0"),
            )
            session.add(capability)
        elif capability.provider_id != agent.id:
            raise ValueError(f"Capability slug {slug} belongs to another Agent")
        capability.name = str(raw.get("name") or f"{agent.display_name} · {capability_id}")
        capability.description = str(raw.get("description") or agent.description or f"{agent.display_name} provides {capability_id}.")
        capability.input_schema = raw.get("inputSchema") if isinstance(raw.get("inputSchema"), dict) else {"type": "object"}
        capability.output_schema = raw.get("outputSchema") if isinstance(raw.get("outputSchema"), dict) else {"type": "object"}
        example = raw.get("exampleRequest") if isinstance(raw.get("exampleRequest"), dict) else {}
        capability.example_input = example.get("input") if isinstance(example.get("input"), dict) else {}
        capability.endpoint = str(raw.get("endpoint") or agent.endpoint or "")
        pricing_model = str(raw.get("pricingModel") or "fixed")
        capability.pricing_model = pricing_model if pricing_model in {"fixed", "per_request", "per_token"} else "fixed"
        capability.base_price = Decimal(str(raw.get("basePrice") or profile.price_floor or "0"))
        capability.currency = str(raw.get("currency") or profile.currency).upper()
        capability.estimated_duration_seconds = max(1, min(int(raw.get("estimatedDurationSeconds") or 60), 604800))
        capability.availability = market_availability
        capability.version = str(snapshot.get("version") or "1.0.0")
        synced.append(capability)
    for capability in session.scalars(select(Capability).where(Capability.provider_id == agent.id)):
        if capability.slug not in active_slugs:
            capability.availability = "offline"
    metadata = dict(profile.metadata_json or {})
    verification = dict(metadata.get("verification") or {})
    verification["manifest"] = snapshot
    verification["marketCapabilitySlugs"] = sorted(active_slugs)
    verification["marketSyncedAt"] = serialize(utcnow())
    metadata["verification"] = verification
    profile.metadata_json = metadata
    return synced


def _validate_agent_endpoint(endpoint: str) -> None:
    parsed = urlparse(endpoint)
    environment = os.getenv("APP_ENV", "development").lower()
    if parsed.scheme not in {"http", "https"} or not parsed.hostname:
        raise ValueError("Agent endpoint must be an HTTP(S) URL")
    if environment in {"production", "prod"} and parsed.scheme != "https":
        raise ValueError("Production Agent endpoints must use HTTPS")
    if environment not in {"production", "prod"}:
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
    agent_type = _agent_type(profile, agent)
    if not agent.endpoint:
        raise ValueError("Agent endpoint is required")
    if agent_type == EXECUTOR_AGENT_TYPE and not profile.home_wallet:
        raise ValueError("Executor Agent endpoint and homeWallet are required")
    base = _agent_endpoint_base(agent.endpoint)
    _validate_agent_endpoint(base)
    timeout = _agent_invocation_timeout_seconds()
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
        _agent_protocol_version(manifest)
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
        input_schema = selected.get("inputSchema")
        output_schema = selected.get("outputSchema")
        if not isinstance(input_schema, dict) or not isinstance(output_schema, dict):
            raise ValueError("Agent manifest capability must declare inputSchema and outputSchema")
        try:
            Draft202012Validator.check_schema(input_schema)
            Draft202012Validator.check_schema(output_schema)
        except SchemaError as exc:
            raise ValueError(f"Agent manifest capability has an invalid JSON Schema: {exc.message}") from exc
        example_input = (selected.get("exampleRequest") or {}).get("input")
        if not isinstance(example_input, dict):
            raise ValueError("Agent manifest capability has no test input")
        try:
            Draft202012Validator(input_schema).validate(example_input)
        except ValidationError as exc:
            raise ValueError(f"Agent manifest example input violates inputSchema: {exc.message}") from exc
        if agent_type == COORDINATOR_AGENT_TYPE:
            if str(manifest.get("agentType") or "").upper() != COORDINATOR_AGENT_TYPE:
                raise ValueError("Coordinator manifest must declare agentType=COORDINATOR")
            if COORDINATOR_CAPABILITY not in supported:
                raise ValueError(f"Coordinator manifest must expose {COORDINATOR_CAPABILITY}")
            test_response = client.post(
                f"{base}/test-plan",
                headers=headers,
                json=example_input,
            )
            test_response.raise_for_status()
            result = test_response.json()
            if (
                result.get("agentId") != agent.slug
                or result.get("did") != agent.did
                or str(result.get("agentType") or "").upper() != COORDINATOR_AGENT_TYPE
                or not result.get("proposalValid")
                or not isinstance(result.get("plan"), dict)
            ):
                raise ValueError("Coordinator test plan response failed the runtime contract")
            return {
                "healthCheckPassed": True,
                "manifestCheckPassed": True,
                "testTaskPassed": True,
                "gasReadyVerified": True,
                "checkedAt": serialize(utcnow()),
                "endpointLatencyMs": int((time.perf_counter() - started) * 1000),
                "manifestHash": canonical_hash(manifest),
                "manifest": _manifest_snapshot(manifest),
                "testResultHash": str(result.get("outputHash") or canonical_hash(result)),
                "gasStatus": "NOT_REQUIRED",
                "chainId": CHAIN_A,
                "routeKey": None,
                "routeVersion": None,
            }
        request_id = f"activation:{profile.id}:{secrets.token_hex(8)}"
        invoke_response = client.post(
            f"{base}/test-invoke",
            headers=headers,
            json={"requestId": request_id, "input": example_input},
        )
        invoke_response.raise_for_status()
        result = invoke_response.json()
        _verify_runtime_result(agent, result)
        result_output = result.get("output")
        if not isinstance(result_output, dict):
            raise ValueError("Agent test invocation output must be a JSON object")
        try:
            Draft202012Validator(output_schema).validate(result_output)
        except ValidationError as exc:
            raise ValueError(f"Agent test invocation violates outputSchema: {exc.message}") from exc

    gas_evidence = _agent_gas_evidence(session, profile, allow_pending_route=True)
    return {
        "healthCheckPassed": True,
        "manifestCheckPassed": True,
        "testTaskPassed": True,
        "checkedAt": serialize(utcnow()),
        "endpointLatencyMs": int((time.perf_counter() - started) * 1000),
        "manifestHash": canonical_hash(manifest),
        "manifest": _manifest_snapshot(manifest),
        "testResultHash": result["outputHash"],
        **gas_evidence,
    }


def _agent_gas_evidence(
    session: Session,
    profile: WorkflowAgentProfile,
    *,
    allow_pending_route: bool = False,
) -> dict[str, Any]:
    if not profile.home_wallet:
        raise ValueError("Agent homeWallet is required for Gas verification")
    route = session.scalar(
        select(ChainRoute)
        .where(
            ChainRoute.source_chain_id == CHAIN_A,
            ChainRoute.destination_chain_id == profile.chain_id,
            ChainRoute.status == "ACTIVE",
        )
        .order_by(ChainRoute.route_version.desc())
    )
    if route is None:
        profile.chain_status = "PENDING_ROUTE"
        if allow_pending_route:
            return {
                "gasReadyVerified": False,
                "gasBalanceWei": None,
                "gasMinimumBalanceWei": str(int(os.getenv("AGENT_MIN_GAS_BALANCE_WEI", "1"))),
                "gasCheckedAt": None,
                "gasStatus": "PENDING_ROUTE",
                "chainId": profile.chain_id,
                "routeKey": None,
                "routeVersion": None,
            }
        raise ValueError("Agent homeChain has no active route")
    profile.chain_status = "READY"
    chain_id_hex = _rpc_call(route.rpc_url, "eth_chainId", [])
    if not isinstance(chain_id_hex, str):
        raise ValueError("Agent homeChain RPC returned an invalid chainId")
    observed_chain_id = int(chain_id_hex, 16)
    if observed_chain_id != profile.chain_id:
        raise ValueError(
            f"Agent homeChain RPC reports chainId {observed_chain_id}, expected {profile.chain_id}"
        )
    balance_hex = _rpc_call(route.rpc_url, "eth_getBalance", [profile.home_wallet, "latest"])
    if not isinstance(balance_hex, str):
        raise ValueError("Agent homeChain RPC returned an invalid wallet balance")
    balance_wei = int(balance_hex, 16)
    minimum_balance = int(os.getenv("AGENT_MIN_GAS_BALANCE_WEI", "1"))
    top_up_applied = False
    parsed_rpc = urlparse(route.rpc_url)
    local_rpc = parsed_rpc.hostname in {"127.0.0.1", "localhost", "::1"}
    auto_top_up = (
        os.getenv("APP_ENV", "development").lower() == "development"
        and os.getenv("LOCAL_AGENT_GAS_AUTO_TOP_UP", "false").lower() in {"1", "true", "yes"}
        and local_rpc
    )
    if balance_wei < minimum_balance and auto_top_up:
        top_up_balance = max(
            minimum_balance,
            int(os.getenv("LOCAL_AGENT_GAS_TOP_UP_WEI", "1000000000000000000")),
        )
        _rpc_call(route.rpc_url, "hardhat_setBalance", [profile.home_wallet, hex(top_up_balance)])
        balance_hex = _rpc_call(route.rpc_url, "eth_getBalance", [profile.home_wallet, "latest"])
        if not isinstance(balance_hex, str):
            raise ValueError("Agent homeChain RPC returned an invalid wallet balance after local Gas funding")
        balance_wei = int(balance_hex, 16)
        top_up_applied = True
    gas_ready = balance_wei >= minimum_balance
    return {
        "gasReadyVerified": gas_ready,
        "gasBalanceWei": str(balance_wei),
        "gasMinimumBalanceWei": str(minimum_balance),
        "gasTopUpApplied": top_up_applied,
        "gasCheckedAt": serialize(utcnow()),
        "chainId": observed_chain_id,
        "routeKey": route.route_key,
        "routeVersion": route.route_version,
    }


def _format_asset_units(raw_amount: int, decimals: int) -> str:
    if raw_amount < 0 or decimals < 0:
        raise ValueError("Asset balance and decimals must be non-negative")
    if decimals == 0:
        return str(raw_amount)
    scale = 10 ** decimals
    whole, fraction = divmod(raw_amount, scale)
    if fraction == 0:
        return str(whole)
    return f"{whole}.{fraction:0{decimals}d}".rstrip("0")


def _decimal_text(value: Any) -> str:
    text = format(Decimal(str(value)), "f")
    return text.rstrip("0").rstrip(".") if "." in text else text


def _agent_account_snapshot(
    session: Session,
    profile: WorkflowAgentProfile,
) -> dict[str, Any]:
    if not profile.home_wallet:
        raise ValueError("Agent homeWallet is not configured")
    route = session.scalar(
        select(ChainRoute)
        .where(
            ChainRoute.source_chain_id == CHAIN_A,
            ChainRoute.destination_chain_id == profile.chain_id,
            ChainRoute.status == "ACTIVE",
        )
        .order_by(ChainRoute.route_version.desc())
    )
    if route is None:
        raise ValueError("Agent homeChain has no active route")

    gas_balance_wei: int | None = None
    gas_balance_error: str | None = None
    try:
        gas_balance_hex = _rpc_call(route.rpc_url, "eth_getBalance", [profile.home_wallet, "latest"])
        if not isinstance(gas_balance_hex, str):
            raise ValueError("Agent homeChain RPC returned an invalid Gas balance")
        gas_balance_wei = int(gas_balance_hex, 16)
    except (httpx.HTTPError, ValueError, TypeError):
        gas_balance_error = "GAS_BALANCE_UNAVAILABLE"

    payout_balance_raw: int | None = None
    payout_balance: str | None = None
    payout_balance_error: str | None = None
    if route.payout_token_address:
        try:
            wallet_word = profile.home_wallet.removeprefix("0x").lower().rjust(64, "0")
            balance_hex = _rpc_call(
                route.rpc_url,
                "eth_call",
                [
                    {
                        "to": route.payout_token_address,
                        "data": f"0x70a08231{wallet_word}",
                    },
                    "latest",
                ],
            )
            if not isinstance(balance_hex, str):
                raise ValueError("Agent homeChain RPC returned an invalid payout balance")
            payout_balance_raw = int(balance_hex, 16)
            payout_balance = _format_asset_units(payout_balance_raw, route.payout_decimals)
        except (httpx.HTTPError, ValueError, TypeError):
            payout_balance_error = "PAYOUT_BALANCE_UNAVAILABLE"

    latest_attempt = session.scalar(
        select(NodeAttempt)
        .where(
            NodeAttempt.agent_profile_id == profile.id,
            NodeAttempt.status == "SETTLED",
            NodeAttempt.settled_at.is_not(None),
        )
        .order_by(NodeAttempt.settled_at.desc())
        .limit(1)
    )
    latest_payout: dict[str, Any] | None = None
    if latest_attempt is not None:
        node = session.get(WorkflowNode, latest_attempt.node_id)
        workflow = session.get(Workflow, node.workflow_id) if node is not None else None
        reservation = session.scalar(
            select(BudgetReservation).where(BudgetReservation.attempt_id == latest_attempt.id)
        )
        payout_amount = latest_attempt.payout_quote_amount
        if payout_amount is None and reservation is not None:
            payout_amount = _decimal_text(reservation.amount)
        latest_payout = {
            "workflowId": workflow.id if workflow is not None else None,
            "workflowReference": workflow.reference_code if workflow is not None else None,
            "nodeId": node.id if node is not None else None,
            "nodeKey": node.node_key if node is not None else None,
            "attemptId": latest_attempt.id,
            "amount": payout_amount,
            "asset": latest_attempt.payout_asset,
            "settlementAmount": _decimal_text(reservation.amount) if reservation is not None else None,
            "settlementAsset": reservation.currency if reservation is not None else latest_attempt.settlement_asset,
            "transactionHash": latest_attempt.payout_tx_hash or latest_attempt.destination_tx_hash,
            "settledAt": serialize(latest_attempt.settled_at),
        }

    return {
        "profileId": profile.id,
        "chainId": profile.chain_id,
        "wallet": profile.home_wallet,
        "checkedAt": serialize(utcnow()),
        "gasBalanceWei": str(gas_balance_wei) if gas_balance_wei is not None else None,
        "gasBalanceError": gas_balance_error,
        "gasSymbol": "ETH",
        "payoutAsset": route.payout_asset,
        "payoutTokenAddress": route.payout_token_address,
        "payoutDecimals": route.payout_decimals,
        "payoutBalanceRaw": str(payout_balance_raw) if payout_balance_raw is not None else None,
        "payoutBalance": payout_balance,
        "payoutBalanceError": payout_balance_error,
        "latestPayout": latest_payout,
    }


def _external_profile_matchable(
    profile: WorkflowAgentProfile,
    agent: Agent,
) -> bool:
    heartbeat = profile.last_heartbeat_at
    if heartbeat is not None and heartbeat.tzinfo is None:
        heartbeat = heartbeat.replace(tzinfo=timezone.utc)
    heartbeat_ttl = int(os.getenv("AGENT_HEARTBEAT_TTL_SECONDS", "60"))
    common_ready = bool(
        profile.admission_status == "APPROVED"
        and profile.active
        and agent.status == "active"
        and profile.credential_status == "ACTIVE"
        and profile.availability == "ONLINE"
        and profile.capacity_available
        and heartbeat is not None
        and heartbeat > utcnow() - timedelta(seconds=heartbeat_ttl)
    )
    if not common_ready:
        return False
    if _agent_type(profile, agent) == COORDINATOR_AGENT_TYPE:
        return COORDINATOR_CAPABILITY in {item.lower() for item in profile.capabilities}
    return bool(profile.chain_status == "READY" and profile.gas_ready)


def _registration_detail(
    profile: WorkflowAgentProfile,
    agent: Agent,
) -> dict[str, Any]:
    data = serialize(profile)
    data.pop("credentialRef", None)
    agent_data = serialize(agent)
    registration = dict(agent_data.get("erc8004Registration") or {})
    if registration:
        registration["admissionStatus"] = profile.admission_status
        agent_data["erc8004Registration"] = registration
    data["agent"] = agent_data
    data["agentType"] = _agent_type(profile, agent)
    data["matchable"] = _external_profile_matchable(profile, agent)
    onboarding = (profile.metadata_json or {}).get("onboarding") or {}
    data["operationalStatus"] = onboarding.get("status")
    return data


def _refresh_profile_chain_status(
    session: Session,
    profile: WorkflowAgentProfile,
) -> ChainRoute | None:
    metadata = dict(profile.metadata_json or {})
    resolution = dict(metadata.get("chainResolution") or {})
    query = (
        select(ChainRoute)
        .where(
            ChainRoute.source_chain_id == CHAIN_A,
            ChainRoute.destination_chain_id == profile.chain_id,
            ChainRoute.status == "ACTIVE",
        )
        .order_by(ChainRoute.route_version.desc())
    )
    route = session.scalar(query)
    requested_payout = str(resolution.get("requestedPayoutAsset") or "").upper()
    requested_settlement = str(resolution.get("requestedSettlementAsset") or "").upper()
    if route is not None and (
        (not requested_payout or route.payout_asset == requested_payout)
        and (not requested_settlement or route.settlement_asset == requested_settlement)
    ):
        profile.chain_status = "READY"
        resolution.update({
            "matchedRouteKey": route.route_key,
            "matchedRouteVersion": route.route_version,
            "destinationChainId": route.destination_chain_id,
            "resolvedAt": serialize(utcnow()),
        })
        metadata["chainResolution"] = resolution
        profile.metadata_json = metadata
        return route
    if profile.chain_status != "REJECTED":
        profile.chain_status = "PENDING_ROUTE"
    return None


def _refresh_profiles_for_route(session: Session, route: ChainRoute) -> int:
    if route.status != "ACTIVE" or route.source_chain_id != CHAIN_A:
        return 0
    refreshed = 0
    profiles = session.scalars(
        select(WorkflowAgentProfile).where(
            WorkflowAgentProfile.chain_id == route.destination_chain_id,
            WorkflowAgentProfile.chain_status == "PENDING_ROUTE",
        )
    )
    for profile in profiles:
        if _refresh_profile_chain_status(session, profile) is not None:
            refreshed += 1
    return refreshed


def _runtime_access_token(profile: WorkflowAgentProfile, agent: Agent) -> str:
    for key in (profile.agent_key, agent.slug, agent.did):
        token = _checked_runtime_tokens.get(key)
        if token:
            return token
    stored = _load_runtime_token_store()
    for key in (profile.agent_key, agent.slug, agent.did):
        token = stored.get(key)
        if isinstance(token, str) and token:
            _checked_runtime_tokens[key] = token
            return token
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


def _runtime_token_store_path() -> Path:
    project_root = Path(__file__).resolve().parents[3]
    runtime_dir = Path(os.getenv("LOCAL_PROTOCOL_RUNTIME_DIR", str(project_root / ".runtime" / "local-v1")))
    if not runtime_dir.is_absolute():
        runtime_dir = project_root / runtime_dir
    configured = Path(os.getenv("AGENT_RUNTIME_TOKEN_STORE_PATH", str(runtime_dir / "secrets" / "agent-runtime-tokens.json")))
    return configured if configured.is_absolute() else project_root / configured


def _load_runtime_token_store() -> dict[str, str]:
    path = _runtime_token_store_path()
    if not path.exists():
        return {}
    try:
        value = json.loads(path.read_text(encoding="utf-8-sig"))
    except (OSError, json.JSONDecodeError) as exc:
        raise ValueError(f"Agent runtime token store is unreadable: {exc}") from exc
    if not isinstance(value, dict) or any(not isinstance(key, str) or not isinstance(token, str) for key, token in value.items()):
        raise ValueError("Agent runtime token store must be a string-to-string object")
    return value


def _persist_runtime_token_store(values: dict[str, str]) -> None:
    path = _runtime_token_store_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_text(
        json.dumps(values, ensure_ascii=False, sort_keys=True, separators=(",", ":")),
        encoding="utf-8",
    )
    try:
        os.chmod(temporary, 0o600)
    except OSError:
        pass
    temporary.replace(path)


def _cache_checked_runtime_token(
    profile: WorkflowAgentProfile,
    agent: Agent,
    token: str,
) -> None:
    stored = _load_runtime_token_store()
    for key in (profile.agent_key, agent.slug, agent.did):
        _checked_runtime_tokens[key] = token
        stored[key] = token
    _persist_runtime_token_store(stored)


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
    task_input = dict(workflow.input_payload or {})
    task_input["_node"] = {
        "nodeKey": node.node_key,
        "title": node.title,
        "description": node.description,
        "capability": node.capability,
        "input": node.input_payload or {},
        "acceptanceCriteria": node.acceptance_criteria or {},
        "reward": str(node.reward),
        "riskLevel": node.risk_level,
    }
    return {
        "objective": node.description or node.title or workflow.objective,
        "input": task_input,
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
    deadline = serialize(utcnow() + timedelta(seconds=_agent_task_deadline_seconds()))
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
    manifest = ((profile.metadata_json or {}).get("verification") or {}).get("manifest")
    if isinstance(manifest, dict):
        headers.update(_agent_protocol_headers(manifest))
    timeout = _agent_invocation_timeout_seconds()
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


def _pending_reserved_invocations(
    session: Session,
    workflow: Workflow,
) -> list[tuple[WorkflowNode, NodeAttempt]]:
    pending: list[tuple[WorkflowNode, NodeAttempt]] = []
    nodes = session.scalars(
        select(WorkflowNode)
        .where(WorkflowNode.workflow_id == workflow.id)
        .order_by(WorkflowNode.created_at, WorkflowNode.node_key)
    )
    for node in nodes:
        if not node.current_attempt_id:
            continue
        attempt = session.get(NodeAttempt, node.current_attempt_id)
        if attempt is not None and attempt.status == "DISPATCHED":
            pending.append((node, attempt))
    return pending


def _onboarding_profile(session: Session, profile_id: str) -> tuple[WorkflowAgentProfile, Agent]:
    profile = session.get(WorkflowAgentProfile, profile_id)
    if profile is None:
        raise HTTPException(status_code=404, detail="Workflow Agent profile not found")
    agent = session.get(Agent, profile.agent_id)
    if agent is None:
        raise HTTPException(status_code=409, detail="Workflow Agent profile has no Agent")
    return profile, agent


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


def _local_protocol_manifest() -> dict[str, Any]:
    project_root = Path(__file__).resolve().parents[3]
    runtime_dir = Path(os.getenv("LOCAL_PROTOCOL_RUNTIME_DIR", str(project_root / ".runtime" / "local-v1")))
    if not runtime_dir.is_absolute():
        runtime_dir = project_root / runtime_dir
    default_path = runtime_dir / "protocol-base.json"
    manifest_path = Path(os.getenv("LOCAL_PROTOCOL_MANIFEST", str(default_path)))
    try:
        manifest = json.loads(manifest_path.read_text(encoding="utf-8-sig"))
        contracts = manifest["contracts"]
        required = {"taskEscrow", "sourceToken", "gasTreasury"}
        if not required.issubset(contracts):
            raise ValueError(f"manifest is missing {sorted(required - set(contracts))}")
        manifest["contracts"] = {key: str(value).lower() for key, value in contracts.items()}
        return manifest
    except Exception as exc:
        raise HTTPException(status_code=503, detail=f"Local protocol manifest is unavailable: {exc}") from exc


def _local_protocol_contracts() -> dict[str, str]:
    return _local_protocol_manifest()["contracts"]


def _local_protocol_deployment_id(manifest: dict[str, Any] | None = None) -> str:
    value = manifest or _local_protocol_manifest()
    deployment_id = value.get("deploymentId") or value.get("manifestHash")
    if not isinstance(deployment_id, str) or len(deployment_id) < 8:
        raise HTTPException(status_code=503, detail="Local protocol manifest has no deployment identifier")
    return deployment_id


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


def _verify_receipt_confirmations(rpc_url: str, receipt: dict[str, Any]) -> None:
    block_number = receipt.get("blockNumber")
    if not isinstance(block_number, str):
        raise HTTPException(status_code=422, detail="Transaction receipt has no blockNumber")
    latest = _rpc_call(rpc_url, "eth_blockNumber", [])
    required = max(1, int(os.getenv("LOCAL_FUNDING_CONFIRMATION_BLOCKS", "1")))
    confirmations = int(latest, 16) - int(block_number, 16) + 1
    if confirmations < required:
        raise HTTPException(
            status_code=409,
            detail=f"Funding transaction has {confirmations} confirmation(s); {required} required",
        )


def _verify_approval_transaction(
    workflow: Workflow,
    body: WorkflowFundingConfirmation,
    rpc_url: str,
    contracts: dict[str, str],
    token_decimals: int,
) -> None:
    receipt = _rpc_call(rpc_url, "eth_getTransactionReceipt", [body.approve_tx_hash])
    transaction = _rpc_call(rpc_url, "eth_getTransactionByHash", [body.approve_tx_hash])
    if not receipt or receipt.get("status") != "0x1" or not transaction:
        raise HTTPException(status_code=422, detail="approveTxHash is not a successful Chain A transaction")
    _verify_receipt_confirmations(rpc_url, receipt)
    if transaction.get("from", "").lower() != body.wallet_address.lower():
        raise HTTPException(status_code=422, detail="approval transaction sender does not match walletAddress")
    if transaction.get("to", "").lower() != contracts["sourceToken"]:
        raise HTTPException(status_code=422, detail="approval transaction was not sent to the configured source token")
    if int(transaction.get("value", "0x0"), 16) != 0:
        raise HTTPException(status_code=422, detail="ERC20 approval must not transfer native currency")
    selector, words = _calldata_words(transaction, 2)
    if selector != _function_selector(rpc_url, "approve(address,uint256)"):
        raise HTTPException(status_code=422, detail="approval transaction does not call approve")
    if "0x" + words[0][-40:] != contracts["taskEscrow"]:
        raise HTTPException(status_code=422, detail="approval spender is not the configured TaskEscrow")
    expected_budget = int(Decimal(str(workflow.budget)) * (Decimal(10) ** token_decimals))
    if int(words[1], 16) != expected_budget:
        raise HTTPException(status_code=422, detail="approval amount does not equal the workflow budget")

    approval_topic = _event_topic(rpc_url, "Approval(address,address,uint256)")
    expected_topics = [approval_topic, _topic_address(body.wallet_address), _topic_address(contracts["taskEscrow"])]
    matching_logs = [
        log for log in receipt.get("logs", [])
        if log.get("address", "").lower() == contracts["sourceToken"]
        and [str(topic).lower() for topic in log.get("topics", [])] == expected_topics
        and int(str(log.get("data", "0x0")), 16) == expected_budget
    ]
    if len(matching_logs) != 1:
        raise HTTPException(status_code=422, detail="approval transaction is missing the exact Approval event")


def _verify_lock_transaction(workflow: Workflow, body: WorkflowFundingConfirmation) -> None:
    rpc_url = os.getenv("LOCAL_CHAIN_A_RPC_URL", os.getenv("CHAIN_A_RPC_URL", "http://127.0.0.1:8545"))
    manifest = _local_protocol_manifest()
    contracts = manifest["contracts"]
    try:
        if body.terms_hash.lower() != workflow.terms_hash.lower():
            raise HTTPException(status_code=422, detail="termsHash does not match the immutable workflow terms")
        if body.deployment_id != _local_protocol_deployment_id(manifest):
            raise HTTPException(status_code=409, detail="deploymentId does not match the active local deployment")
        receipt = _rpc_call(rpc_url, "eth_getTransactionReceipt", [body.lock_tx_hash])
        transaction = _rpc_call(rpc_url, "eth_getTransactionByHash", [body.lock_tx_hash])
        if not receipt or receipt.get("status") != "0x1" or not transaction:
            raise HTTPException(status_code=422, detail="lockTxHash is not a successful Chain A transaction")
        _verify_receipt_confirmations(rpc_url, receipt)
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
        _verify_approval_transaction(workflow, body, rpc_url, contracts, token_decimals)
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
            _verify_receipt_confirmations(rpc_url, gas_receipt)
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


def _wait_for_local_receipt(rpc_url: str, transaction_hash: str) -> dict[str, Any]:
    for _ in range(50):
        receipt = _rpc_call(rpc_url, "eth_getTransactionReceipt", [transaction_hash])
        if receipt:
            if receipt.get("status") != "0x1":
                raise HTTPException(status_code=422, detail="Local faucet transaction reverted")
            return receipt
        time.sleep(0.1)
    raise HTTPException(status_code=504, detail="Timed out waiting for the local faucet transaction")


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
        item["waitingDiagnostics"] = matching_diagnostics(session, node)
        node_items.append(item)

    data = serialize(workflow)
    data["requester"] = serialize(session.get(Agent, workflow.requester_id))
    data["nodes"] = node_items
    final_attempts = [
        attempt
        for node_item in node_items
        for attempt in node_item["attempts"]
        if attempt.get("qualityStatus") == "FINAL" and attempt.get("qualityScore") is not None
    ]
    terminal_nodes = [node_item for node_item in node_items if node_item["status"] in {"SETTLED", "FAILED"}]
    settled_nodes = [node_item for node_item in node_items if node_item["status"] == "SETTLED"]
    reviewed_attempts = [
        attempt
        for node_item in node_items
        for attempt in node_item["attempts"]
        if attempt.get("reviewStatus") in {"PASSED", "FAILED"}
    ]
    passed_attempts = [attempt for attempt in reviewed_attempts if attempt.get("reviewStatus") == "PASSED"]
    completion_rate = Decimal(len(settled_nodes) * 100) / Decimal(len(nodes)) if nodes else Decimal("0")
    review_pass_rate = (
        Decimal(len(passed_attempts) * 100) / Decimal(len(reviewed_attempts))
        if reviewed_attempts else Decimal("0")
    )
    trust_values = [
        Decimal(str(attempt["matchingTrustScore"]))
        for attempt in final_attempts
        if attempt.get("matchingTrustScore") is not None
    ]
    average_trust = sum(trust_values, Decimal("0")) / Decimal(len(trust_values)) if trust_values else Decimal("0")
    aggregate_quality = (
        completion_rate * Decimal("0.50")
        + review_pass_rate * Decimal("0.30")
        + average_trust * Decimal("0.20")
    ).quantize(Decimal("0.01"))
    node_quality_values = [Decimal(str(attempt["qualityScore"])) for attempt in final_attempts]
    average_node_quality = (
        sum(node_quality_values, Decimal("0")) / Decimal(len(node_quality_values))
        if node_quality_values else None
    )
    data["qualitySummary"] = {
        "policyVersion": "WORKFLOW_QUALITY_V1",
        "qualityStatus": "FINAL" if len(terminal_nodes) == len(nodes) else "PROVISIONAL",
        "formula": "0.50 * completionRate + 0.30 * reviewPassRate + 0.20 * averageMatchingTrust",
        "qualityScore": decimal_string(aggregate_quality),
        "completionRate": decimal_string(completion_rate.quantize(Decimal("0.01"))),
        "reviewPassRate": decimal_string(review_pass_rate.quantize(Decimal("0.01"))),
        "averageMatchingTrust": decimal_string(average_trust.quantize(Decimal("0.01"))),
        "averageFinalAttemptQuality": (
            decimal_string(average_node_quality.quantize(Decimal("0.01")))
            if average_node_quality is not None else None
        ),
        "totalNodes": len(nodes),
        "terminalNodes": len(terminal_nodes),
        "settledNodes": len(settled_nodes),
        "reviewedAttempts": len(reviewed_attempts),
        "finalScoredAttempts": len(final_attempts),
    }
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


def workflow_list_summary(session: Session, workflow: Workflow) -> dict[str, Any]:
    nodes = list(
        session.scalars(
            select(WorkflowNode)
            .where(WorkflowNode.workflow_id == workflow.id)
            .order_by(WorkflowNode.created_at, WorkflowNode.node_key)
        )
    )
    node_items: list[dict[str, Any]] = []
    attempt_fields = {
        "id", "attemptNumber", "agentId", "agentKey", "chainId", "homeWallet",
        "routeId", "routeVersion", "status", "createdAt", "updatedAt", "submittedAt",
        "reviewedAt", "settledAt", "payoutStatus",
    }
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
        attempt_items = [
            {key: value for key, value in serialize(attempt).items() if key in attempt_fields}
            for attempt in attempts
        ]
        item["attempts"] = attempt_items
        item["currentAttempt"] = next(
            (attempt for attempt in attempt_items if attempt["id"] == node.current_attempt_id),
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
        item["matchRounds"] = []
        item["failureIncidents"] = []
        item["waitingDiagnostics"] = None
        node_items.append(item)

    data = serialize(workflow)
    data["requester"] = None
    data["nodes"] = node_items
    data["events"] = []
    data["messages"] = []
    data["ledger"] = []
    data["gasDeposits"] = []
    data["feeLedger"] = []
    data["coordinatorDecisions"] = []
    data["protocolJobs"] = []
    data["nodeCount"] = len(nodes)
    data["settledNodeCount"] = sum(node.status == "SETTLED" for node in nodes)
    return data


@router.get("/workflows")
def list_workflows(
    status: str | None = Query(default=None),
    limit: int = Query(default=100, ge=1, le=500),
    view: str = Query(default="detail", pattern="^(detail|summary)$"),
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
        value = workflow_list_summary(session, item) if view == "summary" else workflow_detail(session, item)
        if view == "detail":
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


@router.get("/local-protocol/public-config")
def get_local_protocol_public_config(request: Request) -> dict[str, Any]:
    if request.app.state.settings.app_env.lower() in {"production", "prod"}:
        raise HTTPException(status_code=404, detail="Local protocol configuration is not available")
    rpc_url = os.getenv("LOCAL_CHAIN_A_RPC_URL", os.getenv("CHAIN_A_RPC_URL", "http://127.0.0.1:8545"))
    observed_chain_id = int(_rpc_call(rpc_url, "eth_chainId", []), 16)
    if observed_chain_id != CHAIN_A:
        raise HTTPException(status_code=503, detail="Configured Chain A RPC returned an unexpected chainId")
    manifest = _local_protocol_manifest()
    contracts = manifest["contracts"]
    return {
        "mode": "LOCAL_EXPERIMENT",
        "chainId": observed_chain_id,
        "chainName": "Local Workflow Chain A",
        "rpcUrl": rpc_url,
        "nativeCurrency": {"name": "Local ETH", "symbol": "ETH", "decimals": 18},
        "sourceToken": contracts["sourceToken"],
        "taskEscrow": contracts["taskEscrow"],
        "gasTreasury": contracts["gasTreasury"],
        "sourceGasAmount": "0.01",
        "deploymentId": _local_protocol_deployment_id(manifest),
        "confirmationBlocks": max(1, int(os.getenv("LOCAL_FUNDING_CONFIRMATION_BLOCKS", "1"))),
    }


@router.post("/local-protocol/faucet")
def fund_local_protocol_wallet(
    body: LocalProtocolFaucetRequest,
    request: Request,
    actor: Agent = Depends(get_current_agent),
    session: Session = Depends(get_session),
) -> dict[str, Any]:
    if request.app.state.settings.app_env.lower() in {"production", "prod"}:
        raise HTTPException(status_code=404, detail="Local faucet is not available")
    token_amount = Decimal(body.token_amount)
    native_amount = Decimal(body.native_amount)
    if token_amount <= 0 or token_amount > Decimal("1000") or native_amount < 0 or native_amount > Decimal("5"):
        raise HTTPException(status_code=422, detail="Local faucet request exceeds the per-call limit")
    rpc_url = os.getenv("LOCAL_CHAIN_A_RPC_URL", os.getenv("CHAIN_A_RPC_URL", "http://127.0.0.1:8545"))
    observed_chain_id = int(_rpc_call(rpc_url, "eth_chainId", []), 16)
    if observed_chain_id != CHAIN_A:
        raise HTTPException(status_code=503, detail="Local faucet is connected to the wrong chain")
    manifest = _local_protocol_manifest()
    contracts = manifest["contracts"]
    deployment_id = _local_protocol_deployment_id(manifest)
    allow_repeat = os.getenv("LOCAL_FAUCET_ALLOW_REPEAT", "false").lower() in {"1", "true", "yes"}

    def issuance_exists_on_current_chain(value: LocalFaucetIssuance) -> bool:
        if not value.token_tx_hash:
            return False
        return _rpc_call(rpc_url, "eth_getTransactionReceipt", [value.token_tx_hash]) is not None

    existing_request = session.scalar(
        select(LocalFaucetIssuance).where(LocalFaucetIssuance.request_id == body.request_id)
    )
    issuance: LocalFaucetIssuance
    if existing_request is not None:
        same_request = (
            existing_request.actor_id == actor.id
            and existing_request.wallet_address.lower() == body.wallet_address.lower()
            and Decimal(existing_request.token_amount) == token_amount
            and Decimal(existing_request.native_amount) == native_amount
            and existing_request.deployment_id == deployment_id
        )
        if not same_request:
            raise HTTPException(status_code=409, detail="requestId was already used with different Faucet parameters")
        if existing_request.status == "COMPLETED" and issuance_exists_on_current_chain(existing_request):
            return {
                "requestId": existing_request.request_id,
                "walletAddress": existing_request.wallet_address,
                "chainId": observed_chain_id,
                "deploymentId": existing_request.deployment_id,
                "tokenAmount": str(existing_request.token_amount),
                "nativeAmount": str(existing_request.native_amount),
                "tokenTransactionHash": existing_request.token_tx_hash,
                "nativeTransactionHash": existing_request.native_tx_hash,
                "issuedBy": actor.did,
                "localOnly": True,
                "replayed": True,
            }
        issuance = existing_request
        issuance.status = "PENDING"
        issuance.token_tx_hash = None
        issuance.native_tx_hash = None
        session.commit()
    else:
        previous_issuance = session.scalar(
            select(LocalFaucetIssuance).where(
                LocalFaucetIssuance.deployment_id == deployment_id,
                func.lower(LocalFaucetIssuance.wallet_address) == body.wallet_address.lower(),
            )
        )
        if (
            previous_issuance is not None
            and issuance_exists_on_current_chain(previous_issuance)
            and not allow_repeat
        ):
            raise HTTPException(status_code=409, detail="This wallet has already used the Faucet for the active deployment")
        if previous_issuance is None:
            issuance = LocalFaucetIssuance(
                request_id=body.request_id,
                deployment_id=deployment_id,
                actor_id=actor.id,
                wallet_address=body.wallet_address.lower(),
                token_amount=token_amount,
                native_amount=native_amount,
                status="PENDING",
            )
            session.add(issuance)
        else:
            issuance = previous_issuance
            issuance.request_id = body.request_id
            issuance.actor_id = actor.id
            issuance.token_amount = token_amount
            issuance.native_amount = native_amount
            issuance.token_tx_hash = None
            issuance.native_tx_hash = None
            issuance.status = "PENDING"
        session.commit()
    accounts = _rpc_call(rpc_url, "eth_accounts", [])
    if not isinstance(accounts, list) or not accounts:
        raise HTTPException(status_code=503, detail="Local Chain A exposes no unlocked faucet account")
    faucet = str(accounts[0]).lower()
    decimals_hex = _rpc_call(rpc_url, "eth_call", [{"to": contracts["sourceToken"], "data": "0x313ce567"}, "latest"])
    decimals = int(decimals_hex, 16)
    token_units = int(token_amount * (Decimal(10) ** decimals))
    mint_data = (
        _function_selector(rpc_url, "mint(address,uint256)")
        + body.wallet_address.lower().removeprefix("0x").rjust(64, "0")
        + hex(token_units)[2:].rjust(64, "0")
    )
    token_tx = issuance.token_tx_hash
    if not token_tx:
        token_tx = _rpc_call(rpc_url, "eth_sendTransaction", [{
            "from": faucet,
            "to": contracts["sourceToken"],
            "data": mint_data,
        }])
        issuance.token_tx_hash = token_tx
        session.commit()
    _wait_for_local_receipt(rpc_url, token_tx)
    native_tx = issuance.native_tx_hash
    if native_amount > 0:
        if not native_tx:
            native_tx = _rpc_call(rpc_url, "eth_sendTransaction", [{
                "from": faucet,
                "to": body.wallet_address,
                "value": hex(int(native_amount * (Decimal(10) ** 18))),
            }])
            issuance.native_tx_hash = native_tx
            session.commit()
        _wait_for_local_receipt(rpc_url, native_tx)
    issuance.native_tx_hash = native_tx
    issuance.status = "COMPLETED"
    session.commit()
    return {
        "requestId": body.request_id,
        "walletAddress": body.wallet_address,
        "chainId": observed_chain_id,
        "deploymentId": deployment_id,
        "tokenAmount": str(token_amount),
        "nativeAmount": str(native_amount),
        "tokenTransactionHash": token_tx,
        "nativeTransactionHash": native_tx,
        "issuedBy": actor.did,
        "localOnly": True,
        "replayed": False,
    }


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
        supplied_hashes = [body.approve_tx_hash, body.lock_tx_hash]
        if body.source_gas_tx_hash:
            supplied_hashes.append(body.source_gas_tx_hash)
        if len({value.lower() for value in supplied_hashes}) != len(supplied_hashes):
            raise HTTPException(status_code=409, detail="Each funding transaction hash must be distinct")
        duplicate_evidence = session.scalar(
            select(Workflow).where(
                Workflow.id != workflow.id,
                or_(
                    Workflow.approve_tx_hash.in_(supplied_hashes),
                    Workflow.lock_tx_hash.in_(supplied_hashes),
                    Workflow.source_gas_tx_hash.in_(supplied_hashes),
                ),
            )
        )
        if duplicate_evidence is not None:
            raise HTTPException(status_code=409, detail="A funding transaction hash has already been used by another workflow")
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
    _coordination_guard: None = Depends(workflow_coordination_guard),
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
    if auto_invoke and item.funding_mode == "RESERVED":
        for node, attempt in _pending_reserved_invocations(session, item)[:body.max_actions]:
            try:
                actions.append(_invoke_reserved_attempt(session, item, node, attempt))
            except httpx.TransportError as exc:
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


@router.post("/workflows/{workflow_id}/resume")
def post_workflow_resume(
    workflow_id: str,
    body: CoordinateRequest,
    request: Request,
    actor: Agent = Depends(get_current_agent),
    idem: str | None = Depends(idempotency_header),
    session: Session = Depends(get_session),
    _coordination_guard: None = Depends(workflow_coordination_guard),
) -> dict[str, Any]:
    workflow = _workflow(session, workflow_id)
    _can_manage(workflow, actor)
    payload = body.model_dump(by_alias=True)
    route = f"dynamic_workflows.{workflow.id}.resume"
    replay = get_idempotent_response(session, actor.id, route, idem, payload)
    if replay:
        return replay
    actions = coordinate(session, workflow, body.max_actions)
    response = {
        "resumed": True,
        "actions": actions,
        "workflow": workflow_detail(session, workflow),
    }
    save_idempotent_response(session, actor.id, route, idem, payload, response)
    session.commit()
    request.app.state.workflow_recovery.wake(workflow.id)
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
    request: Request,
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
    request.app.state.workflow_recovery.wake(workflow.id)
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
    if body.source_chain_id != CHAIN_A:
        raise HTTPException(status_code=422, detail=f"Dynamic workflows currently require source chain {CHAIN_A}")
    try:
        observed = _rpc_call(body.rpc_url, "eth_chainId", [])
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
    refreshed_profiles = _refresh_profiles_for_route(session, item)
    response = serialize(item)
    response["refreshedAgentProfiles"] = refreshed_profiles
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
    refreshed_profiles = _refresh_profiles_for_route(session, item)
    response = serialize(item)
    response["refreshedAgentProfiles"] = refreshed_profiles
    save_idempotent_response(session, actor.id, route, idem, payload, response)
    session.commit()
    return response


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


@router.post("/protocol-jobs/recover-stale")
def recover_stale_protocol_jobs(
    stale_after_seconds: int = Query(default=3600, alias="staleAfterSeconds", ge=0, le=86400),
    actor: Agent = Depends(require_role("admin")),
    session: Session = Depends(get_session),
) -> dict[str, Any]:
    recovered_at = utcnow()
    cutoff = recovered_at - timedelta(seconds=stale_after_seconds)
    jobs = list(
        session.scalars(
            select(ProtocolJob)
            .where(
                ProtocolJob.status == "RUNNING",
                ProtocolJob.updated_at <= cutoff,
            )
            .order_by(ProtocolJob.updated_at, ProtocolJob.id)
        )
    )
    for job in jobs:
        job.status = "QUEUED"
        job.available_at = recovered_at
        job.last_error = "Recovered stale RUNNING job after Protocol Worker restart."
        audit(
            session,
            actor.id,
            "protocol_job.stale_recovered",
            "protocol_job",
            job.id,
            {"staleAfterSeconds": stale_after_seconds, "attempts": job.attempts},
        )
    session.commit()
    return {
        "recoveredCount": len(jobs),
        "recoveredJobIds": [job.id for job in jobs],
        "staleAfterSeconds": stale_after_seconds,
        "recoveredAt": serialize(recovered_at),
    }


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


@router.get("/agent-onboarding/registrations")
def list_external_agent_registrations(
    admission_status: str | None = Query(default=None, alias="admissionStatus"),
    chain_status: str | None = Query(default=None, alias="chainStatus"),
    agent_type: str | None = Query(default=None, alias="agentType"),
    actor: Agent = Depends(require_role("admin")),
    session: Session = Depends(get_session),
) -> dict[str, Any]:
    del actor
    query = select(WorkflowAgentProfile).order_by(WorkflowAgentProfile.created_at.desc())
    if admission_status:
        query = query.where(WorkflowAgentProfile.admission_status == admission_status.upper())
    if chain_status:
        query = query.where(WorkflowAgentProfile.chain_status == chain_status.upper())
    requested_type = agent_type.upper() if agent_type else None
    if requested_type not in {None, EXECUTOR_AGENT_TYPE, COORDINATOR_AGENT_TYPE}:
        raise HTTPException(status_code=422, detail="agentType must be EXECUTOR or COORDINATOR")
    profiles = [
        profile for profile in session.scalars(query)
        if "onboarding" in (profile.metadata_json or {})
        and (
            requested_type is None
            or _agent_type(profile, session.get(Agent, profile.agent_id)) == requested_type
        )
    ]
    items = [
        _registration_detail(profile, session.get(Agent, profile.agent_id))
        for profile in profiles
        if session.get(Agent, profile.agent_id) is not None
    ]
    return {"items": items, "total": len(items)}


@router.get("/agent-onboarding/registrations/{profile_id}")
def get_external_agent_registration(
    profile_id: str,
    session: Session = Depends(get_session),
) -> dict[str, Any]:
    profile, agent = _onboarding_profile(session, profile_id)
    if "onboarding" not in (profile.metadata_json or {}):
        raise HTTPException(status_code=404, detail="External Agent registration not found")
    return _registration_detail(profile, agent)


@router.post("/agent-onboarding/registrations", status_code=201)
def post_external_agent_onboarding(
    body: ExternalAgentOnboardingCreate,
    session: Session = Depends(get_session),
) -> dict[str, Any]:
    route, chain_id = _resolve_onboarding_route(session, body)
    agent_type = body.agent_type
    is_coordinator = agent_type == COORDINATOR_AGENT_TYPE
    chain_status = "READY" if route is not None or is_coordinator else "PENDING_ROUTE"
    capabilities = sorted({item.strip().lower() for item in body.capabilities if item.strip()})
    if is_coordinator and COORDINATOR_CAPABILITY not in capabilities:
        capabilities.append(COORDINATOR_CAPABILITY)
        capabilities.sort()
    if not capabilities:
        raise HTTPException(status_code=422, detail="At least one capability is required")

    slug = _slugify_agent_key(body.agent_key)
    did = body.did or f"did:local:{slug}"
    if session.scalar(select(Agent).where(Agent.did == did)) is not None:
        raise HTTPException(status_code=409, detail="Agent DID already exists")
    if session.scalar(select(Agent).where(Agent.slug == slug)) is not None:
        raise HTTPException(status_code=409, detail="Agent key already maps to an existing slug")
    if session.scalar(select(WorkflowAgentProfile).where(WorkflowAgentProfile.agent_key == body.agent_key)) is not None:
        raise HTTPException(status_code=409, detail="Workflow Agent key already exists")
    _operational_public_key(body.public_key)

    llm_descriptor = {
        "provider": body.llm_provider,
        "model": body.llm_model,
        "systemPromptHash": canonical_hash(body.system_prompt),
    }
    agent = Agent(
        did=did,
        slug=slug,
        display_name=body.agent_name,
        description=(
            f"External Coordinator Agent operated by {body.owner_name}."
            if is_coordinator
            else f"External LLM Agent operated by {body.owner_name}."
        ),
        roles=["coordinator"] if is_coordinator else ["provider"],
        status="pending",
        endpoint=body.endpoint,
        operational_public_key=body.public_key,
        erc8004_registration={
            "type": "external-coordinator-agent" if is_coordinator else "external-llm-agent",
            "agentType": agent_type,
            "ownerContactHash": canonical_hash(body.owner_email.lower()),
            "organization": body.organization,
            "llm": llm_descriptor,
            "serviceEndpoint": body.endpoint,
            "admissionStatus": "PENDING_OWNERSHIP",
        },
    )
    session.add(agent)
    session.flush()

    credential_fingerprint = canonical_hash(
        {
            "did": did,
            "agentKey": body.agent_key,
            "endpoint": body.endpoint,
            "capabilities": capabilities,
            "chainId": chain_id,
            "payoutAsset": None if is_coordinator else body.payout_asset.upper(),
            "llm": llm_descriptor,
        }
    )
    profile = WorkflowAgentProfile(
        agent_id=agent.id,
        agent_key=body.agent_key,
        chain_id=chain_id,
        home_wallet=None if is_coordinator else body.home_wallet,
        capabilities=capabilities,
        priority=100,
        price_floor=Decimal(body.price_floor),
        currency=body.currency.upper(),
        active=False,
        admission_status="PENDING_OWNERSHIP",
        chain_status=chain_status,
        success_rate_bps=0,
        trust_score=0,
        endpoint_latency_ms=500,
        availability="OFFLINE",
        capacity_available=False,
        gas_ready=is_coordinator,
        last_heartbeat_at=None,
        metadata_json={
            "agentType": agent_type,
            "onboarding": {
                "ownerName": body.owner_name,
                "ownerEmailHash": canonical_hash(body.owner_email.lower()),
                "organization": body.organization,
                "submittedAt": serialize(utcnow()),
                "status": "PENDING_OWNERSHIP_PROOF",
            },
            "llm": {
                **llm_descriptor,
            },
            "chainResolution": {
                "required": not is_coordinator,
                "requestedPayoutAsset": None if is_coordinator else body.payout_asset.upper(),
                "requestedSettlementAsset": None if is_coordinator else body.settlement_asset.upper(),
                "matchedRouteKey": route.route_key if route else None,
                "matchedRouteVersion": route.route_version if route else None,
                "destinationChainId": chain_id,
            },
            "verification": {
                "identityVerified": False,
                "walletVerified": is_coordinator,
                "walletVerificationRequired": not is_coordinator,
                "manifestCheckPassed": False,
                "healthCheckPassed": False,
                "testTaskPassed": False,
            },
        },
        credential_status="UNCONFIGURED",
        credential_ref=f"external-agent:{did}",
        credential_fingerprint=credential_fingerprint,
    )
    session.add(profile)
    session.flush()

    profile_data = _registration_detail(profile, agent)
    chain_resolution = {
        "sourceChainId": route.source_chain_id if route else CHAIN_A,
        "destinationChainId": chain_id,
        "routeKey": route.route_key if route else None,
        "routeVersion": route.route_version if route else None,
        "settlementAsset": route.settlement_asset if route else ("NOT_APPLICABLE" if is_coordinator else body.settlement_asset.upper()),
        "payoutAsset": route.payout_asset if route else ("NOT_APPLICABLE" if is_coordinator else body.payout_asset.upper()),
        "payoutRate": serialize(route.payout_rate) if route else None,
    }
    response = {
        "admissionStatus": profile.admission_status,
        "chainStatus": profile.chain_status,
        "matchable": False,
        "agent": serialize(agent),
        "profile": profile_data,
        "route": serialize(route) if route else None,
        "chainResolution": chain_resolution,
        "next": {
            "challengeEndpoint": f"/api/v1/agent-onboarding/registrations/{profile.id}/challenge",
            "verifyEndpoint": f"/api/v1/agent-onboarding/registrations/{profile.id}/verify",
            "heartbeatEndpoint": f"/api/v1/agent-onboarding/registrations/{profile.id}/heartbeat",
            "resultSubmission": (
                "/api/v1/coordinator-runtime/{profileId}/chat"
                if is_coordinator
                else "/api/v1/workflows/{workflowId}/nodes/{nodeId}/attempts/{attemptId}/results"
            ),
        },
    }
    audit(session, None, "agent_onboarding.registered", "workflow_agent_profile", profile.id, chain_resolution)
    session.commit()
    return response


@router.post("/agent-onboarding/registrations/{profile_id}/challenge", status_code=201)
def create_external_agent_challenge(
    profile_id: str,
    session: Session = Depends(get_session),
) -> dict[str, Any]:
    profile, agent = _onboarding_profile(session, profile_id)
    is_coordinator = _agent_type(profile, agent) == COORDINATOR_AGENT_TYPE
    if not is_coordinator and profile.home_wallet is None:
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
    session.flush()
    response = {
        "challengeId": challenge.id,
        "profileId": profile.id,
        "nonce": nonce,
        "message": message,
        "expiresAt": serialize(expires_at),
        "signatureRequirements": {
            "identity": "Sign the UTF-8 message with the registered ES256K Agent identity key.",
            "wallet": (
                None
                if is_coordinator
                else "Sign the same message with the EVM homeWallet using EIP-191 personal_sign."
            ),
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
    is_coordinator = _agent_type(profile, agent) == COORDINATOR_AGENT_TYPE
    if not is_coordinator and profile.home_wallet is None:
        raise HTTPException(status_code=422, detail="homeWallet is required for wallet verification")

    _verify_identity_signature(agent, challenge.message, body.identity_signature)
    if not is_coordinator:
        if body.wallet_signature is None:
            raise HTTPException(status_code=422, detail="walletSignature is required for Executor Agent verification")
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
    onboarding["status"] = "PENDING_PLATFORM_REVIEW"
    metadata["onboarding"] = onboarding
    profile.metadata_json = metadata
    profile.admission_status = "PENDING_PLATFORM_REVIEW"
    _set_agent_admission_snapshot(agent, profile.admission_status)
    profile.credential_status = "REFERENCE_ONLY"
    audit(session, None, "agent_onboarding.ownership_verified", "workflow_agent_profile", profile.id)
    session.commit()
    return {
        "admissionStatus": profile.admission_status,
        "chainStatus": profile.chain_status,
        "matchable": False,
        "profile": _registration_detail(profile, agent),
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
    if profile.admission_status == "REJECTED":
        raise HTTPException(status_code=409, detail="Rejected Agent cannot send heartbeat")
    if profile.credential_status not in {"REFERENCE_ONLY", "ACTIVE"}:
        raise HTTPException(status_code=409, detail="Agent ownership must be verified before heartbeat")
    nonce = secrets.token_urlsafe(32)
    expires_at = utcnow() + timedelta(minutes=2)
    message = _heartbeat_message(profile, agent, nonce, serialize(expires_at), body)
    try:
        retention_seconds = max(
            120,
            min(int(os.getenv("HEARTBEAT_CHALLENGE_RETENTION_SECONDS", "3600")), 86400),
        )
    except ValueError:
        retention_seconds = 3600
    session.execute(
        delete(ExternalAgentOnboardingChallenge).where(
            ExternalAgentOnboardingChallenge.profile_id == profile.id,
            ExternalAgentOnboardingChallenge.purpose == "HEARTBEAT",
            ExternalAgentOnboardingChallenge.expires_at < utcnow() - timedelta(seconds=retention_seconds),
        )
    )
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
    request: Request,
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
        is_coordinator = _agent_type(profile, agent) == COORDINATOR_AGENT_TYPE
        refresh_gas = not is_coordinator and os.getenv("LOCAL_AGENT_GAS_REFRESH_ON_HEARTBEAT", "false").lower() in {
            "1", "true", "yes",
        }
        if refresh_gas:
            verification = dict(metadata.get("verification") or {})
            onboarding = dict(metadata.get("onboarding") or {})
            try:
                gas_evidence = _agent_gas_evidence(session, profile, allow_pending_route=True)
                verification.update(gas_evidence)
                profile.gas_ready = bool(gas_evidence["gasReadyVerified"])
                onboarding["status"] = (
                    "APPROVED_PENDING_ROUTE"
                    if profile.chain_status != "READY"
                    else "ACTIVE" if profile.gas_ready else "ACTIVE_AWAITING_GAS"
                )
            except (httpx.HTTPError, ValueError, TypeError) as exc:
                profile.gas_ready = False
                verification.update({
                    "gasReadyVerified": False,
                    "gasCheckFailureHash": canonical_hash(str(exc)),
                    "gasCheckedAt": serialize(utcnow()),
                })
                onboarding["status"] = "ACTIVE_AWAITING_GAS"
            metadata["verification"] = verification
            metadata["onboarding"] = onboarding
            profile.metadata_json = metadata
        if not is_coordinator:
            try:
                _sync_market_capabilities(session, profile, agent)
            except (httpx.HTTPError, ValueError, KeyError, TypeError):
                pass
    session.commit()
    if body.status == "ONLINE" and body.capacity_available:
        request.app.state.workflow_recovery.wake()
    return {
        "accepted": True,
        "profileId": profile.id,
        "admissionStatus": profile.admission_status,
        "chainStatus": profile.chain_status,
        "matchable": _external_profile_matchable(profile, agent),
    }


@router.post("/agent-onboarding/registrations/{profile_id}/activate")
def activate_external_agent_registration(
    profile_id: str,
    body: ExternalAgentActivationReview,
    actor: Agent = Depends(require_role("admin")),
    session: Session = Depends(get_session),
) -> dict[str, Any]:
    profile, agent = _onboarding_profile(session, profile_id)
    if profile.admission_status == "REJECTED":
        raise HTTPException(status_code=409, detail="Rejected Agent must submit a new registration")
    metadata = dict(profile.metadata_json or {})
    verification = dict(metadata.get("verification") or {})
    is_coordinator = _agent_type(profile, agent) == COORDINATOR_AGENT_TYPE
    if not verification.get("identityVerified") or (
        not is_coordinator and not verification.get("walletVerified")
    ):
        raise HTTPException(status_code=409, detail="Ownership verification is required before activation")
    missing_checks = [name for name in (
        "manifestCheckPassed",
        "healthCheckPassed",
        "testTaskPassed",
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
        "evidenceHash": canonical_hash(verification),
    })
    metadata["verification"] = verification
    onboarding = dict(metadata.get("onboarding") or {})
    gas_ready = True if is_coordinator else bool(verification.get("gasReadyVerified"))
    if not is_coordinator and profile.chain_status != "READY":
        gas_ready = False
        onboarding["status"] = "APPROVED_PENDING_ROUTE"
    else:
        onboarding["status"] = "ACTIVE" if gas_ready else "ACTIVE_AWAITING_GAS"
    onboarding["reviewNotesHash"] = canonical_hash(body.notes) if body.notes else None
    onboarding["reviewNotes"] = body.notes
    onboarding["reviewedAt"] = serialize(utcnow())
    onboarding["reviewedBy"] = actor.did
    metadata["onboarding"] = onboarding
    agent.status = "active"
    profile.active = True
    profile.admission_status = "APPROVED"
    _set_agent_admission_snapshot(agent, profile.admission_status)
    profile.availability = "OFFLINE"
    profile.capacity_available = False
    profile.gas_ready = gas_ready
    profile.credential_status = "ACTIVE"
    profile.metadata_json = metadata
    initialize_agent_reputation(profile, baseline_trust_score=75)
    synced = [] if is_coordinator else _sync_market_capabilities(
        session, profile, agent, verification.get("manifest")
    )
    audit(session, actor.id, "agent_onboarding.activated", "workflow_agent_profile", profile.id)
    session.commit()
    return {
        "admissionStatus": profile.admission_status,
        "chainStatus": profile.chain_status,
        "operationalStatus": onboarding["status"],
        "matchable": _external_profile_matchable(profile, agent),
        "profile": _registration_detail(profile, agent),
        "marketCapabilities": serialize(synced),
    }


@router.post("/agent-onboarding/registrations/{profile_id}/reject")
def reject_external_agent_registration(
    profile_id: str,
    body: ExternalAgentRejectionReview,
    actor: Agent = Depends(require_role("admin")),
    session: Session = Depends(get_session),
) -> dict[str, Any]:
    profile, agent = _onboarding_profile(session, profile_id)
    metadata = dict(profile.metadata_json or {})
    onboarding = dict(metadata.get("onboarding") or {})
    onboarding.update({
        "status": "REJECTED",
        "rejectionReason": body.reason,
        "rejectionReasonHash": canonical_hash(body.reason),
        "reviewedAt": serialize(utcnow()),
        "reviewedBy": actor.did,
    })
    metadata["onboarding"] = onboarding
    profile.metadata_json = metadata
    profile.admission_status = "REJECTED"
    profile.active = False
    profile.availability = "OFFLINE"
    profile.capacity_available = False
    profile.gas_ready = False
    profile.credential_status = "REVOKED" if profile.credential_status == "ACTIVE" else "REFERENCE_ONLY"
    agent.status = "rejected"
    _set_agent_admission_snapshot(agent, profile.admission_status)
    for capability in session.scalars(select(Capability).where(Capability.provider_id == agent.id)):
        capability.availability = "offline"
    audit(
        session,
        actor.id,
        "agent_onboarding.rejected",
        "workflow_agent_profile",
        profile.id,
        {"reasonHash": onboarding["rejectionReasonHash"]},
    )
    session.commit()
    return {
        "admissionStatus": profile.admission_status,
        "chainStatus": profile.chain_status,
        "matchable": False,
        "profile": _registration_detail(profile, agent),
    }


@router.post("/agent-onboarding/registrations/{profile_id}/checks")
def run_external_agent_technical_checks(
    profile_id: str,
    body: ExternalAgentTechnicalCheckRequest,
    actor: Agent = Depends(require_role("admin")),
    session: Session = Depends(get_session),
) -> dict[str, Any]:
    profile, agent = _onboarding_profile(session, profile_id)
    if profile.admission_status == "REJECTED":
        raise HTTPException(status_code=409, detail="Rejected Agent cannot be checked")
    metadata = dict(profile.metadata_json or {})
    verification = dict(metadata.get("verification") or {})
    is_coordinator = _agent_type(profile, agent) == COORDINATOR_AGENT_TYPE
    if not verification.get("identityVerified") or (
        not is_coordinator and not verification.get("walletVerified")
    ):
        raise HTTPException(status_code=409, detail="Ownership verification is required before technical checks")
    try:
        runtime_access_token = body.runtime_access_token or _runtime_access_token(profile, agent)
        evidence = _run_agent_technical_checks(
            session,
            profile,
            agent,
            runtime_access_token,
        )
    except (httpx.HTTPError, InvalidSignature, ValueError, KeyError, TypeError) as exc:
        verification.update({
            "manifestCheckPassed": False,
            "healthCheckPassed": False,
            "testTaskPassed": False,
            "gasReadyVerified": is_coordinator,
            "lastCheckFailureHash": canonical_hash(str(exc)),
            "checkedAt": serialize(utcnow()),
        })
        metadata["verification"] = verification
        profile.metadata_json = metadata
        audit(session, actor.id, "agent_onboarding.technical_check_failed", "workflow_agent_profile", profile.id)
        session.commit()
        raise HTTPException(status_code=422, detail=f"Agent technical checks failed: {exc}") from exc
    verification.update(evidence)
    if body.runtime_access_token:
        _cache_checked_runtime_token(profile, agent, body.runtime_access_token)
    metadata["verification"] = verification
    profile.metadata_json = metadata
    profile.endpoint_latency_ms = int(evidence["endpointLatencyMs"])
    profile.gas_ready = bool(evidence["gasReadyVerified"])
    if not is_coordinator and profile.active and profile.credential_status == "ACTIVE":
        _sync_market_capabilities(session, profile, agent, evidence.get("manifest"))
    audit(session, actor.id, "agent_onboarding.technical_checks_passed", "workflow_agent_profile", profile.id)
    session.commit()
    return {
        "checksPassed": True,
        "gasReady": profile.gas_ready,
        "admissionStatus": profile.admission_status,
        "chainStatus": profile.chain_status,
        "matchable": _external_profile_matchable(profile, agent),
        "profileId": profile.id,
        "evidence": evidence,
    }


@router.post("/agent-onboarding/registrations/{profile_id}/gas-check")
def run_external_agent_gas_check(
    profile_id: str,
    actor: Agent = Depends(require_role("admin")),
    session: Session = Depends(get_session),
) -> dict[str, Any]:
    profile, agent = _onboarding_profile(session, profile_id)
    if _agent_type(profile, agent) == COORDINATOR_AGENT_TYPE:
        raise HTTPException(status_code=409, detail="Coordinator Agent does not use an execution-chain Gas wallet")
    if profile.chain_status != "READY":
        _refresh_profile_chain_status(session, profile)
    if profile.chain_status != "READY":
        session.commit()
        raise HTTPException(status_code=409, detail="Agent homeChain route is not ready")
    metadata = dict(profile.metadata_json or {})
    verification = dict(metadata.get("verification") or {})
    if not verification.get("identityVerified") or not verification.get("walletVerified"):
        raise HTTPException(status_code=409, detail="Ownership verification is required before Gas checks")
    try:
        evidence = _agent_gas_evidence(session, profile)
    except (httpx.HTTPError, ValueError, TypeError) as exc:
        verification.update({
            "gasReadyVerified": False,
            "gasCheckFailureHash": canonical_hash(str(exc)),
            "gasCheckedAt": serialize(utcnow()),
        })
        metadata["verification"] = verification
        profile.metadata_json = metadata
        profile.gas_ready = False
        audit(session, actor.id, "agent_onboarding.gas_check_failed", "workflow_agent_profile", profile.id)
        session.commit()
        raise HTTPException(status_code=422, detail=f"Agent Gas check failed: {exc}") from exc

    verification.update(evidence)
    verification.pop("gasCheckFailureHash", None)
    metadata["verification"] = verification
    onboarding = dict(metadata.get("onboarding") or {})
    profile.gas_ready = bool(evidence["gasReadyVerified"])
    onboarding["status"] = "ACTIVE" if profile.gas_ready else "ACTIVE_AWAITING_GAS"
    metadata["onboarding"] = onboarding
    profile.metadata_json = metadata
    if profile.active and profile.credential_status == "ACTIVE":
        _sync_market_capabilities(session, profile, agent)
    matchable = _external_profile_matchable(profile, agent)
    audit(
        session,
        actor.id,
        "agent_onboarding.gas_checked",
        "workflow_agent_profile",
        profile.id,
        {
            "chainId": evidence["chainId"],
            "routeKey": evidence["routeKey"],
            "routeVersion": evidence["routeVersion"],
            "gasBalanceWei": evidence["gasBalanceWei"],
            "gasReady": profile.gas_ready,
            "matchable": matchable,
        },
    )
    session.commit()
    return {
        "profileId": profile.id,
        "chainId": evidence["chainId"],
        "routeKey": evidence["routeKey"],
        "routeVersion": evidence["routeVersion"],
        "gasBalanceWei": evidence["gasBalanceWei"],
        "minimumBalanceWei": evidence["gasMinimumBalanceWei"],
        "gasReady": profile.gas_ready,
        "admissionStatus": profile.admission_status,
        "chainStatus": profile.chain_status,
        "operationalStatus": onboarding.get("status"),
        "matchable": matchable,
        "checkedAt": evidence["gasCheckedAt"],
    }


@router.get("/workflow-agents")
def list_workflow_agents(
    agent_type: str | None = Query(default=None, alias="agentType"),
    session: Session = Depends(get_session),
) -> dict[str, Any]:
    profiles = list(
        session.scalars(select(WorkflowAgentProfile).order_by(WorkflowAgentProfile.chain_id, WorkflowAgentProfile.priority))
    )
    items: list[dict[str, Any]] = []
    requested_type = agent_type.upper() if agent_type else None
    if requested_type not in {None, EXECUTOR_AGENT_TYPE, COORDINATOR_AGENT_TYPE}:
        raise HTTPException(status_code=422, detail="agentType must be EXECUTOR or COORDINATOR")
    for profile in profiles:
        agent = session.get(Agent, profile.agent_id)
        if agent is not None and (
            requested_type is None or _agent_type(profile, agent) == requested_type
        ):
            items.append(_registration_detail(profile, agent))
    return {"items": items, "total": len(items)}


@router.get("/workflow-agents/{profile_id}/account")
def get_workflow_agent_account(
    profile_id: str,
    session: Session = Depends(get_session),
) -> dict[str, Any]:
    profile = session.get(WorkflowAgentProfile, profile_id)
    if profile is None:
        raise HTTPException(status_code=404, detail="Workflow Agent profile not found")
    agent = session.get(Agent, profile.agent_id)
    if agent is None:
        raise HTTPException(status_code=404, detail="Agent identity not found")
    if _agent_type(profile, agent) == COORDINATOR_AGENT_TYPE:
        raise HTTPException(status_code=409, detail="Coordinator Agent does not have an execution account")
    try:
        return _agent_account_snapshot(session, profile)
    except (httpx.HTTPError, ValueError, TypeError) as exc:
        raise HTTPException(status_code=502, detail=f"Unable to read Agent account balances: {exc}") from exc


@router.api_route(
    "/coordinator-runtime/{profile_id}/{runtime_path:path}",
    methods=["GET", "POST"],
)
async def proxy_coordinator_runtime(
    profile_id: str,
    runtime_path: str,
    request: Request,
    session: Session = Depends(get_session),
) -> Response:
    if not _COORDINATOR_RUNTIME_PATH.fullmatch(runtime_path):
        raise HTTPException(status_code=404, detail="Coordinator runtime path is not allowed")
    profile, agent = _onboarding_profile(session, profile_id)
    if _agent_type(profile, agent) != COORDINATOR_AGENT_TYPE:
        raise HTTPException(status_code=409, detail="Selected Agent is not a Coordinator Agent")
    if not _external_profile_matchable(profile, agent):
        raise HTTPException(status_code=409, detail="Selected Coordinator Agent is not currently available")
    if not agent.endpoint:
        raise HTTPException(status_code=409, detail="Coordinator Agent endpoint is missing")
    base = _agent_endpoint_base(agent.endpoint)
    try:
        _validate_agent_endpoint(base)
        token = _runtime_access_token(profile, agent)
    except ValueError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc

    body = await request.body() if request.method == "POST" else None
    session.commit()
    try:
        async with httpx.AsyncClient(
            timeout=float(os.getenv("COORDINATOR_RUNTIME_TIMEOUT_SECONDS", "240")),
            follow_redirects=False,
        ) as client:
            upstream = await client.request(
                request.method,
                f"{base}/{runtime_path}",
                headers={
                    "Accept": "application/json",
                    "Authorization": f"Bearer {token}",
                    **({"Content-Type": "application/json"} if body else {}),
                },
                content=body,
            )
    except httpx.HTTPError as exc:
        raise HTTPException(status_code=502, detail=f"Coordinator Agent request failed: {exc}") from exc

    if request.method == "POST":
        audit(
            session,
            None,
            "coordinator_agent.runtime_called",
            "workflow_agent_profile",
            profile.id,
            {"path": runtime_path, "requestHash": canonical_hash(body.decode("utf-8") if body else "")},
        )
        session.commit()
    return Response(
        content=upstream.content,
        status_code=upstream.status_code,
        media_type=upstream.headers.get("content-type", "application/json").split(";", 1)[0],
    )


@router.post("/workflow-agents/{profile_id}/capabilities/sync")
def sync_workflow_agent_capabilities(
    profile_id: str,
    actor: Agent = Depends(require_role("admin")),
    session: Session = Depends(get_session),
) -> dict[str, Any]:
    profile, agent = _onboarding_profile(session, profile_id)
    if not profile.active or profile.credential_status != "ACTIVE":
        raise HTTPException(status_code=409, detail="Agent must be active before publishing market capabilities")
    try:
        manifest = _fetch_agent_manifest(profile, agent)
        capabilities = _sync_market_capabilities(
            session,
            profile,
            agent,
            manifest,
            reconcile_profile=True,
        )
    except (httpx.HTTPError, ValueError, KeyError, TypeError) as exc:
        raise HTTPException(status_code=422, detail=f"Agent capability sync failed: {exc}") from exc
    audit(
        session,
        actor.id,
        "workflow_agent.market_capabilities_synced",
        "workflow_agent_profile",
        profile.id,
        {"capabilitySlugs": [item.slug for item in capabilities]},
    )
    session.commit()
    return {
        "profileId": profile.id,
        "profileCapabilities": profile.capabilities,
        "items": [_capability_market_detail(session, item) for item in capabilities],
        "total": len(capabilities),
    }


def _capability_market_detail(session: Session, capability: Capability) -> dict[str, Any]:
    data = serialize(capability)
    data["provider"] = serialize(session.get(Agent, capability.provider_id))
    return data


def _proxy_agent_request(
    profile: WorkflowAgentProfile,
    agent: Agent,
    path: str,
    *,
    method: str = "POST",
    body: dict[str, Any] | None = None,
) -> dict[str, Any]:
    if not agent.endpoint:
        raise HTTPException(status_code=409, detail="Agent Runner endpoint is not configured")
    try:
        token = _runtime_access_token(profile, agent)
        base = _agent_endpoint_base(agent.endpoint)
        _validate_agent_endpoint(base)
        timeout = float(os.getenv("AGENT_CHAT_TIMEOUT_SECONDS", "320"))
        with httpx.Client(timeout=timeout, follow_redirects=False) as client:
            response = client.request(
                method,
                f"{base}{path}",
                headers={"Authorization": f"Bearer {token}"},
                json=body,
            )
            response.raise_for_status()
            payload = response.json()
        if not isinstance(payload, dict):
            raise ValueError("Agent Runner returned a non-object response")
        return payload
    except httpx.HTTPStatusError as exc:
        detail = exc.response.json() if exc.response.headers.get("content-type", "").startswith("application/json") else exc.response.text
        raise HTTPException(status_code=502, detail={"code": "agent_chat_failed", "runnerStatus": exc.response.status_code, "runnerDetail": detail}) from exc
    except (httpx.HTTPError, ValueError, TypeError) as exc:
        raise HTTPException(status_code=502, detail=f"Agent Runner chat request failed: {exc}") from exc


@router.post("/workflow-agents/{profile_id}/chat/sessions", status_code=201)
def create_workflow_agent_chat_session(
    profile_id: str,
    body: WorkflowAgentChatSessionCreate,
    actor: Agent = Depends(get_current_agent),
    session: Session = Depends(get_session),
) -> dict[str, Any]:
    profile, agent = _onboarding_profile(session, profile_id)
    if not profile.active or agent.status != "active":
        raise HTTPException(status_code=409, detail="Agent is not active")
    return _proxy_agent_request(
        profile,
        agent,
        f"/chat/sessions",
        body={"ownerId": actor.id, "title": body.title or f"Conversation with {agent.display_name}"},
    )


@router.post("/workflow-agents/{profile_id}/chat")
def chat_with_workflow_agent(
    profile_id: str,
    body: WorkflowAgentChatRequest,
    actor: Agent = Depends(get_current_agent),
    session: Session = Depends(get_session),
) -> dict[str, Any]:
    profile, agent = _onboarding_profile(session, profile_id)
    if not profile.active or agent.status != "active":
        raise HTTPException(status_code=409, detail="Agent is not active")
    response = _proxy_agent_request(
        profile,
        agent,
        "/chat",
        body={"sessionId": body.session_id, "message": body.message},
    )
    audit(
        session,
        actor.id,
        "workflow_agent.chat_completed",
        "workflow_agent_profile",
        profile.id,
        {"sessionId": body.session_id, "messageHash": canonical_hash(body.message)},
    )
    session.commit()
    return response


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
    chain_status = "READY" if route is not None else "PENDING_ROUTE"
    item = WorkflowAgentProfile(
        agent_id=body.agent_id,
        agent_key=body.agent_key,
        chain_id=body.chain_id,
        home_wallet=body.home_wallet,
        capabilities=body.capabilities,
        priority=body.priority,
        admission_status=body.admission_status,
        chain_status=chain_status,
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
    payload = body.model_dump(by_alias=True, exclude_none=True, mode="json")
    route = f"workflow_agents.{profile_id}.patch"
    replay = get_idempotent_response(session, actor.id, route, idem, payload)
    if replay:
        return replay
    for field, value in body.model_dump(exclude_none=True).items():
        setattr(item, "metadata_json" if field == "metadata" else field, value)
    linked_agent = session.get(Agent, item.agent_id)
    if linked_agent is not None and item.credential_status == "ACTIVE":
        try:
            _sync_market_capabilities(session, item, linked_agent)
        except (httpx.HTTPError, ValueError, KeyError, TypeError):
            pass
    response = serialize(item)
    response["agent"] = serialize(linked_agent)
    save_idempotent_response(session, actor.id, route, idem, payload, response)
    session.commit()
    return response


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
