from __future__ import annotations

import hashlib
import hmac
import base64
import asyncio
import json
import os
import secrets
import threading
import uuid
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Annotated, Literal
from urllib.parse import urlparse

import httpx
from fastapi import Depends, FastAPI, Header, HTTPException, Request, status
from fastapi.middleware.cors import CORSMiddleware
from jsonschema import Draft202012Validator
from jsonschema.exceptions import ValidationError
from pydantic import BaseModel, ConfigDict, Field, model_validator
from eth_account import Account
from eth_utils import keccak, to_checksum_address
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError

from app.agents import AgentDefinition, build_agents
from app.database import Base as RunnerBase, build_engine as build_runner_engine, session_factory as runner_session_factory
from app.identity import RuntimeIdentity, canonical_json, load_runtime_identities
from app.managed import ManagedIdentity, SidecarClient, definition_from_runtime, execute_runtime, recover_and_validate_transaction, runtime_capability
from app.providers import DeepSeekCodeReviewProvider, DeepSeekStructuredTaskProvider, MockCodeReviewProvider
from app.runtime_models import ControlNonce, ManagedRuntime


class InvokeRequest(BaseModel):
    model_config = ConfigDict(populate_by_name=True)

    request_id: str | None = Field(default=None, alias="requestId")
    input: dict[str, object]
    offer_id: str | None = Field(default=None, alias="offerId")
    workflow_id: str | None = Field(default=None, alias="workflowId")
    node_id: str | None = Field(default=None, alias="nodeId")
    attempt_id: str | None = Field(default=None, alias="attemptId")
    attempt_number: int | None = Field(default=None, alias="attemptNumber", ge=1)
    reservation_id: str | None = Field(default=None, alias="reservationId")
    deadline: str | None = None
    nonce: str | None = Field(default=None, min_length=16, max_length=128)

    @model_validator(mode="after")
    def validate_task_context(self) -> "InvokeRequest":
        values = (
            self.offer_id,
            self.workflow_id,
            self.node_id,
            self.attempt_id,
            self.attempt_number,
            self.reservation_id,
            self.deadline,
            self.nonce,
        )
        if any(value is not None for value in values) and not all(value is not None for value in values):
            raise ValueError("Formal invocation requires the complete workflow task context")
        return self


class TaskOfferRequest(BaseModel):
    model_config = ConfigDict(populate_by_name=True)

    offer_id: str = Field(alias="offerId", min_length=8, max_length=128)
    workflow_id: str = Field(alias="workflowId", min_length=3, max_length=128)
    node_id: str = Field(alias="nodeId", min_length=3, max_length=128)
    attempt_id: str = Field(alias="attemptId", min_length=3, max_length=128)
    attempt_number: int = Field(alias="attemptNumber", ge=1)
    reservation_id: str = Field(alias="reservationId", min_length=3, max_length=128)
    capability_id: str = Field(alias="capabilityId", min_length=2, max_length=100)
    criteria_hash: str = Field(alias="criteriaHash", min_length=8, max_length=128)
    reward: str = Field(min_length=1, max_length=80)
    settlement_asset: str = Field(alias="settlementAsset", min_length=2, max_length=32)
    payout_asset: str = Field(alias="payoutAsset", min_length=2, max_length=32)
    deadline: str
    nonce: str = Field(min_length=16, max_length=128)
    coordinator_signature: str = Field(alias="coordinatorSignature", min_length=32, max_length=256)


class IdentitySignRequest(BaseModel):
    message: str = Field(min_length=16, max_length=10000)


class PlatformRegistrationRequest(BaseModel):
    model_config = ConfigDict(populate_by_name=True)

    platform_url: str = Field(alias="platformUrl", pattern=r"^https?://[^\s]+$", max_length=500)
    profile_id: str = Field(alias="profileId", min_length=36, max_length=36)
    status: str = Field(default="ONLINE", pattern=r"^(ONLINE|BUSY|OFFLINE)$")
    capacity_available: bool = Field(default=True, alias="capacityAvailable")
    endpoint_latency_ms: int = Field(default=500, alias="endpointLatencyMs", ge=0, le=60000)
    heartbeat_interval_seconds: int = Field(default=30, alias="heartbeatIntervalSeconds", ge=10, le=50)


class LlmRuntimeConfigRequest(BaseModel):
    model_config = ConfigDict(populate_by_name=True)

    provider: Literal["deepseek", "mock"] = "deepseek"
    api_key: str | None = Field(default=None, alias="apiKey", min_length=8, max_length=512)
    model: str = Field(default="deepseek-chat", min_length=2, max_length=120)
    base_url: str = Field(
        default="https://api.deepseek.com",
        alias="baseUrl",
        pattern=r"^https?://[^\s]+$",
        max_length=500,
    )


class WalletTransactionSignRequest(BaseModel):
    model_config = ConfigDict(populate_by_name=True)

    chain_id: int = Field(alias="chainId", ge=1)
    to: str = Field(pattern=r"^0x[a-fA-F0-9]{40}$")
    data: str = Field(pattern=r"^0x[a-fA-F0-9]{8,}$")
    value_wei: str = Field(default="0", alias="valueWei", pattern=r"^[0-9]+$")
    nonce: int = Field(ge=0)
    gas: int = Field(ge=21000)
    gas_price_wei: str = Field(alias="gasPriceWei", pattern=r"^[0-9]+$")
    attempt_id: str = Field(alias="attemptId", min_length=3, max_length=128)
    attempt_id_bytes32: str = Field(alias="attemptIdBytes32", pattern=r"^0x[a-fA-F0-9]{64}$")
    request_nonce: str = Field(alias="requestNonce", pattern=r"^0x[a-fA-F0-9]{64}$")


class ManagedRuntimeProvision(BaseModel):
    model_config = ConfigDict(populate_by_name=True)

    runtime_id: str = Field(alias="runtimeId", min_length=3, max_length=128)
    slug: str = Field(pattern=r"^[A-Za-z][A-Za-z0-9_-]{1,79}$")
    did: str = Field(min_length=8, max_length=255)
    public_key_hex: str = Field(alias="publicKeyHex", pattern=r"^0x04[a-fA-F0-9]{128}$")
    home_wallet: str = Field(alias="homeWallet", pattern=r"^0x[a-fA-F0-9]{40}$")
    home_chain_id: int = Field(alias="homeChainId", ge=1)
    route_key: str = Field(alias="routeKey", pattern=r"^[A-Z][A-Z0-9_-]{2,79}$")
    route_version: int = Field(alias="routeVersion", ge=1)
    capabilities: list[dict[str, object]] = Field(min_length=1, max_length=50)
    name: str = Field(min_length=2, max_length=160)
    description: str = Field(default="", max_length=2000)
    version: str = Field(default="1.0.0", min_length=1, max_length=32)
    executor_type: Literal["llm", "http"] = Field(alias="executorType")
    executor_config: dict[str, object] = Field(alias="executorConfig")
    signer_url: str = Field(alias="signerUrl", pattern=r"^http://(?:127\.0\.0\.1|localhost|\[::1\])(?::[0-9]+)?(?:/.*)?$")
    signer_credential_ref: str = Field(alias="signerCredentialRef", pattern=r"^(?:env|file):.+$", max_length=500)
    platform_registration: PlatformRegistrationRequest | None = Field(default=None, alias="platformRegistration")


class ManagedTransactionIntent(BaseModel):
    model_config = ConfigDict(populate_by_name=True, extra="forbid")
    intent_id: str = Field(alias="intentId", min_length=8, max_length=128)
    request_id: str = Field(alias="requestId", min_length=8, max_length=128)
    runtime_id: str = Field(alias="runtimeId", min_length=3, max_length=128)
    did: str = Field(min_length=8, max_length=255)
    purpose: Literal["submit_result", "replace", "cancel"]
    chain_id: int = Field(alias="chainId", ge=1)
    to: str = Field(pattern=r"^0x[a-fA-F0-9]{40}$")
    data: str = Field(pattern=r"^0x[a-fA-F0-9]*$")
    value: str = Field(pattern=r"^[0-9]+$")
    gas_limit: int = Field(alias="gasLimit", ge=21000)
    max_fee_per_gas: str = Field(alias="maxFeePerGas", pattern=r"^[0-9]+$")
    max_priority_fee_per_gas: str = Field(alias="maxPriorityFeePerGas", pattern=r"^[0-9]+$")
    observed_pending_nonce: int = Field(alias="observedPendingNonce", ge=0)
    observed_block_number: int = Field(alias="observedBlockNumber", ge=0)
    observed_at: datetime = Field(alias="observedAt")
    route_key: str = Field(alias="routeKey", min_length=3, max_length=80)
    route_version: int = Field(alias="routeVersion", ge=1)
    policy_version: str = Field(alias="policyVersion", min_length=1, max_length=64)
    expires_at: datetime = Field(alias="expiresAt")
    replaces_request_id: str | None = Field(default=None, alias="replacesRequestId")
    original_status: Literal["PENDING", "DROPPED"] | None = Field(default=None, alias="originalStatus")


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


def _b64url_hmac(secret: str, payload: object) -> str:
    digest = hmac.new(secret.encode("utf-8"), canonical_json(payload), hashlib.sha256).digest()
    return base64.urlsafe_b64encode(digest).rstrip(b"=").decode("ascii")


def _platform_url(value: str) -> str:
    normalized = value.rstrip("/")
    parsed = urlparse(normalized)
    if parsed.scheme not in {"http", "https"} or not parsed.hostname:
        raise ValueError("platformUrl must be an HTTP(S) URL")
    managed_loopback = (
        os.getenv("RUNNER_PROFILE", "local-demo").lower() == "managed"
        and parsed.scheme == "http"
        and parsed.hostname in {"127.0.0.1", "localhost", "::1"}
    )
    if os.getenv("APP_ENV", "development").lower() in {"production", "prod"} and parsed.scheme != "https" and not managed_loopback:
        raise ValueError("Production platformUrl must use HTTPS")
    return normalized


def _artifact_policy(project_root: Path, artifact_name: str, function_names: list[str]) -> tuple[dict[str, object], set[str]]:
    path = project_root / "hardhat-artifacts" / "contracts" / "src" / f"{artifact_name}.sol" / f"{artifact_name}.json"
    try:
        artifact = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise ValueError(f"Wallet policy artifact {artifact_name} is unavailable") from exc
    selectors: set[str] = set()
    for item in artifact.get("abi", []):
        if item.get("type") != "function" or item.get("name") not in function_names:
            continue
        signature = f"{item['name']}({','.join(value['type'] for value in item.get('inputs', []))})"
        selectors.add("0x" + keccak(text=signature)[:4].hex())
    if not selectors:
        raise ValueError(f"Wallet policy artifact {artifact_name} has no allowed function")
    return artifact, selectors


def _masked_runtime_code_hash(code_hex: str, immutable_references: dict[str, object]) -> str:
    code = bytearray.fromhex(code_hex.removeprefix("0x"))
    for references in immutable_references.values():
        if not isinstance(references, list):
            continue
        for reference in references:
            if not isinstance(reference, dict):
                continue
            start = int(reference.get("start", -1))
            length = int(reference.get("length", -1))
            if start < 0 or length < 0 or start + length > len(code):
                raise ValueError("Wallet policy artifact contains invalid immutable references")
            code[start:start + length] = b"\x00" * length
    return "0x" + keccak(bytes(code)).hex()


def _opaque_id_to_bytes32(value: str) -> str:
    if value.startswith("0x") and len(value) == 66:
        return value.lower()
    compact = value.replace("-", "")
    if compact and len(compact) <= 64 and all(character in "0123456789abcdefABCDEF" for character in compact):
        return "0x" + compact.lower().ljust(64, "0")
    return "0x" + keccak(text=value).hex()


async def _artifact_allowed_selectors(policy: dict[str, object], contract: str) -> set[str]:
    configured = policy.get("allowedArtifacts", {})
    rpc_url = str(policy.get("rpcUrl", ""))
    if not isinstance(configured, dict) or not configured or not rpc_url:
        return set()
    parsed = urlparse(rpc_url)
    if parsed.scheme not in {"http", "https"} or not parsed.hostname:
        raise ValueError("Agent wallet policy rpcUrl is invalid")
    async with httpx.AsyncClient(timeout=10, follow_redirects=False) as client:
        response = await client.post(
            rpc_url,
            json={"jsonrpc": "2.0", "id": 1, "method": "eth_getCode", "params": [contract, "latest"]},
        )
        response.raise_for_status()
        result = response.json().get("result")
    if not isinstance(result, str) or result == "0x":
        raise ValueError("Allowed Agent transaction target has no deployed bytecode")
    project_root = Path(__file__).resolve().parents[3]
    for artifact_name, functions in configured.items():
        if not isinstance(artifact_name, str) or not isinstance(functions, list):
            continue
        artifact, selectors = _artifact_policy(project_root, artifact_name, [str(item) for item in functions])
        expected = str(artifact.get("deployedBytecode", ""))
        references = artifact.get("immutableReferences", {})
        if (
            expected
            and len(expected) == len(result)
            and _masked_runtime_code_hash(expected, references) == _masked_runtime_code_hash(result, references)
        ):
            return selectors
    return set()


def build_app() -> FastAPI:
    app = FastAPI(
        title="Agent Market Runtime",
        version="1.0.0",
        description="Signed Agent runtime with configurable model providers.",
    )
    cors_origins = [
        item.strip()
        for item in os.getenv(
            "AGENT_RUNNER_CORS_ORIGINS",
            "http://127.0.0.1:3000,http://localhost:3000",
        ).split(",")
        if item.strip()
    ]
    app.add_middleware(
        CORSMiddleware,
        allow_origins=cors_origins,
        allow_credentials=False,
        allow_methods=["GET", "POST"],
        allow_headers=["Authorization", "Content-Type"],
    )
    runner_profile = os.getenv("RUNNER_PROFILE", "local-demo").lower()
    if runner_profile not in {"local-demo", "managed"}:
        raise ValueError("RUNNER_PROFILE must be local-demo or managed")
    llm_provider = os.getenv("LLM_PROVIDER", "mock").lower()
    review_provider = DeepSeekCodeReviewProvider.from_env() if llm_provider == "deepseek" else MockCodeReviewProvider()
    task_provider = DeepSeekStructuredTaskProvider.from_env() if llm_provider == "deepseek" else None
    definitions = build_agents(review_provider=review_provider, task_provider=task_provider) if runner_profile == "local-demo" else {}
    identities: dict[str, RuntimeIdentity | ManagedIdentity] = load_runtime_identities(list(definitions)) if runner_profile == "local-demo" else {}
    api_token = os.getenv("AGENT_RUNNER_API_TOKEN")
    allow_insecure_local = os.getenv("AGENT_RUNNER_ALLOW_INSECURE_LOCAL", "").lower() in {"1", "true", "yes"}
    raw_wallet_keys = json.loads(os.getenv("AGENT_HOME_WALLET_PRIVATE_KEYS_JSON", "{}"))
    raw_wallet_policies = json.loads(os.getenv("AGENT_WALLET_POLICIES_JSON", "{}"))
    if not isinstance(raw_wallet_keys, dict) or not isinstance(raw_wallet_policies, dict):
        raise ValueError("Agent wallet keys and policies must be JSON objects")
    if runner_profile == "managed" and (raw_wallet_keys or raw_wallet_policies):
        raise ValueError("Managed Runner must not load Agent wallet keys or local wallet policies")
    if runner_profile == "managed" and (not api_token or allow_insecure_local):
        raise ValueError("Managed Runner requires AGENT_RUNNER_API_TOKEN and forbids insecure invoke access")
    wallet_accounts = {}
    for slug, private_key in raw_wallet_keys.items():
        if slug not in definitions or not isinstance(private_key, str) or not private_key.startswith("0x"):
            raise ValueError(f"Invalid Agent home-wallet key configuration for {slug}")
        wallet_accounts[slug] = Account.from_key(private_key)
    wallet_nonce_store_value = os.getenv("AGENT_WALLET_NONCE_STORE_PATH", "").strip()
    if (
        runner_profile == "local-demo"
        and os.getenv("APP_ENV", "development").lower() in {"production", "prod"}
        and not wallet_nonce_store_value
    ):
        raise ValueError("Production Agent Runner requires AGENT_WALLET_NONCE_STORE_PATH")
    wallet_nonce_store = Path(wallet_nonce_store_value).resolve() if wallet_nonce_store_value else None
    wallet_request_nonces: set[tuple[str, str]] = set()
    if wallet_nonce_store is not None and wallet_nonce_store.exists():
        stored = json.loads(wallet_nonce_store.read_text(encoding="utf-8"))
        if not isinstance(stored, list):
            raise ValueError("Agent wallet nonce store must contain a JSON array")
        wallet_request_nonces = {
            (str(item[0]), str(item[1]))
            for item in stored
            if isinstance(item, list) and len(item) == 2
        }

    app.state.runner_profile = runner_profile
    app.state.runner_engine = build_runner_engine() if runner_profile == "managed" else None
    app.state.runner_sessions = runner_session_factory(app.state.runner_engine) if app.state.runner_engine is not None else None
    if app.state.runner_engine is not None and (
        os.getenv("APP_ENV", "development").lower() == "test"
        or os.getenv("RUNNER_AUTO_CREATE_SCHEMA", "").lower() in {"1", "true", "yes"}
    ):
        RunnerBase.metadata.create_all(app.state.runner_engine)
    app.state.definitions = definitions
    app.state.identities = identities
    app.state.managed_runtimes = {}
    app.state.accepted_offers = {}
    app.state.completed_offers = {}
    app.state.platform_registrations = {}
    app.state.heartbeat_tasks = {}
    app.state.wallet_accounts = wallet_accounts
    app.state.wallet_policies = raw_wallet_policies
    app.state.wallet_request_nonces = wallet_request_nonces
    app.state.wallet_nonce_store = wallet_nonce_store
    app.state.wallet_nonce_lock = threading.Lock()
    app.state.llm_config = {
        "provider": llm_provider,
        "model": getattr(review_provider, "model", "deterministic-local"),
        "baseUrl": getattr(review_provider, "base_url", None),
        "configured": llm_provider == "deepseek",
        "source": "environment" if llm_provider == "deepseek" else "default",
        "updatedAt": utc_now(),
    }

    def refresh_managed_runtimes() -> None:
        if runner_profile != "managed":
            return
        with app.state.runner_sessions() as session:
            runtimes = session.scalars(select(ManagedRuntime).where(ManagedRuntime.active.is_(True))).all()
        app.state.managed_runtimes = {runtime.slug: runtime for runtime in runtimes}
        app.state.definitions = {runtime.slug: definition_from_runtime(runtime) for runtime in runtimes}
        app.state.identities = {
            runtime.slug: ManagedIdentity(did=runtime.did, public_key_hex=runtime.public_key_hex)
            for runtime in runtimes
        }

    refresh_managed_runtimes()

    def get_agent(slug: str) -> tuple[AgentDefinition, RuntimeIdentity | ManagedIdentity]:
        definition = app.state.definitions.get(slug)
        if definition is None:
            raise HTTPException(status_code=404, detail="Agent not found")
        return definition, app.state.identities[slug]

    def require_invoke_access(
        authorization: Annotated[str | None, Header()] = None,
    ) -> None:
        if not api_token:
            if allow_insecure_local:
                return
            raise HTTPException(
                status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
                detail="AGENT_RUNNER_API_TOKEN is required for invoke access",
            )
        expected = f"Bearer {api_token}"
        if authorization is None or not secrets.compare_digest(authorization, expected):
            raise HTTPException(
                status_code=status.HTTP_401_UNAUTHORIZED,
                detail="Missing or invalid runtime token",
            )

    def verify_control_request(
        method: str,
        path: str,
        payload: dict[str, object],
        timestamp_value: str | None,
        nonce: str | None,
        signature: str | None,
    ) -> None:
        if runner_profile != "managed":
            raise HTTPException(status_code=404, detail="Managed control plane is disabled")
        secret = os.getenv("RUNNER_CONTROL_HMAC_SECRET", "")
        if not secret:
            raise HTTPException(status_code=503, detail="RUNNER_CONTROL_HMAC_SECRET is not configured")
        try:
            timestamp = int(timestamp_value or "")
        except ValueError as exc:
            raise HTTPException(status_code=401, detail="Runner control timestamp is invalid") from exc
        if abs(int(datetime.now(timezone.utc).timestamp()) - timestamp) > 60 or not nonce:
            raise HTTPException(status_code=401, detail="Runner control request is stale")
        body = canonical_json(payload).decode("utf-8")
        signed = "\n".join((method.upper(), str(timestamp), nonce, path, body))
        expected = "sha256=" + hmac.new(secret.encode(), signed.encode(), hashlib.sha256).hexdigest()
        if not signature or not secrets.compare_digest(expected, signature):
            raise HTTPException(status_code=401, detail="Runner control signature is invalid")
        try:
            with app.state.runner_sessions() as session:
                session.add(ControlNonce(nonce=nonce))
                session.commit()
        except IntegrityError as exc:
            raise HTTPException(status_code=409, detail="Runner control nonce was already used") from exc

    def runtime_response(runtime: ManagedRuntime) -> dict[str, object]:
        return {
            "provisioningId": runtime.provisioning_id,
            "runtimeId": runtime.runtime_id,
            "slug": runtime.slug,
            "did": runtime.did,
            "homeWallet": runtime.home_wallet,
            "homeChainId": runtime.home_chain_id,
            "routeKey": runtime.route_key,
            "routeVersion": runtime.route_version,
            "capabilities": runtime.capabilities,
            "executorType": runtime.executor_type,
            "heartbeatConfigured": runtime.platform_registration is not None,
            "active": runtime.active,
            "status": "PROVISIONED" if runtime.active else "DEPROVISIONED",
        }

    @app.put("/internal/v1/runtimes/{provisioning_id}")
    async def provision_runtime(
        provisioning_id: str,
        body: ManagedRuntimeProvision,
        control_timestamp: str | None = Header(default=None, alias="X-Runner-Timestamp"),
        control_nonce: str | None = Header(default=None, alias="X-Runner-Nonce"),
        control_signature: str | None = Header(default=None, alias="X-Runner-Signature"),
    ) -> dict[str, object]:
        payload = body.model_dump(by_alias=True, mode="json", exclude_none=True, exclude_unset=True)
        path = f"/internal/v1/runtimes/{provisioning_id}"
        verify_control_request("PUT", path, payload, control_timestamp, control_nonce, control_signature)
        capability_ids = [str(item.get("capabilityId", "")) for item in body.capabilities]
        if any(not value for value in capability_ids) or len(set(capability_ids)) != len(capability_ids):
            raise HTTPException(status_code=422, detail="Runtime capability IDs must be present and unique")
        if body.executor_type == "llm":
            config = body.executor_config
            if "apiKey" in config or not str(config.get("credentialRef", "")).startswith(("env:", "file:")):
                raise HTTPException(status_code=422, detail="Managed LLM executor requires credentialRef and forbids plaintext apiKey")
            if str(config.get("baseUrl", "https://api.deepseek.com")).rstrip("/") != "https://api.deepseek.com":
                raise HTTPException(status_code=422, detail="Managed DeepSeek baseUrl must be https://api.deepseek.com")
        elif "url" not in body.executor_config:
            raise HTTPException(status_code=422, detail="Managed HTTP executor requires url")
        platform_registration = None
        if body.platform_registration is not None:
            platform_registration = {
                **body.platform_registration.model_dump(by_alias=True),
                "platformUrl": _platform_url(body.platform_registration.platform_url),
            }
        with app.state.runner_sessions() as session:
            existing = session.scalar(select(ManagedRuntime).where(ManagedRuntime.provisioning_id == provisioning_id))
            if existing:
                comparable = runtime_response(existing)
                requested = {
                    "runtimeId": body.runtime_id, "slug": body.slug, "did": body.did,
                    "homeWallet": body.home_wallet.lower(), "homeChainId": body.home_chain_id,
                    "routeKey": body.route_key, "routeVersion": body.route_version,
                    "capabilities": body.capabilities, "executorType": body.executor_type,
                    "publicKeyHex": body.public_key_hex, "executorConfig": body.executor_config,
                    "signerUrl": body.signer_url.rstrip("/"),
                    "signerCredentialRef": body.signer_credential_ref,
                    "platformRegistration": platform_registration,
                }
                comparable.update({
                    "publicKeyHex": existing.public_key_hex,
                    "executorConfig": existing.executor_config,
                    "signerUrl": existing.signer_url,
                    "signerCredentialRef": existing.signer_credential_ref,
                    "platformRegistration": existing.platform_registration,
                })
                if any(comparable.get(key) != value for key, value in requested.items()):
                    raise HTTPException(status_code=409, detail="provisioningId is already bound to another Runtime")
                existing.active = True
                existing.platform_registration = platform_registration
                session.commit()
                result = runtime_response(existing)
            else:
                runtime = ManagedRuntime(
                    provisioning_id=provisioning_id,
                    runtime_id=body.runtime_id,
                    slug=body.slug,
                    did=body.did,
                    public_key_hex=body.public_key_hex,
                    home_wallet=body.home_wallet.lower(),
                    home_chain_id=body.home_chain_id,
                    route_key=body.route_key,
                    route_version=body.route_version,
                    capabilities=body.capabilities,
                    name=body.name,
                    description=body.description,
                    version=body.version,
                    executor_type=body.executor_type,
                    executor_config=body.executor_config,
                    signer_url=body.signer_url.rstrip("/"),
                    signer_credential_ref=body.signer_credential_ref,
                    platform_registration=platform_registration,
                    active=True,
                )
                session.add(runtime)
                try:
                    session.commit()
                except IntegrityError as exc:
                    session.rollback()
                    raise HTTPException(status_code=409, detail="Runtime slug, DID, or runtimeId already exists") from exc
                result = runtime_response(runtime)
        refresh_managed_runtimes()
        if platform_registration is not None:
            start_heartbeat(body.slug, dict(platform_registration))
        return result

    @app.get("/internal/v1/runtimes/{provisioning_id}")
    def get_provisioned_runtime(
        provisioning_id: str,
        control_timestamp: str | None = Header(default=None, alias="X-Runner-Timestamp"),
        control_nonce: str | None = Header(default=None, alias="X-Runner-Nonce"),
        control_signature: str | None = Header(default=None, alias="X-Runner-Signature"),
    ) -> dict[str, object]:
        path = f"/internal/v1/runtimes/{provisioning_id}"
        verify_control_request("GET", path, {}, control_timestamp, control_nonce, control_signature)
        with app.state.runner_sessions() as session:
            runtime = session.scalar(select(ManagedRuntime).where(ManagedRuntime.provisioning_id == provisioning_id))
            if not runtime:
                raise HTTPException(status_code=404, detail="Runtime was not found")
            return runtime_response(runtime)

    @app.delete("/internal/v1/runtimes/{provisioning_id}")
    async def deprovision_runtime(
        provisioning_id: str,
        control_timestamp: str | None = Header(default=None, alias="X-Runner-Timestamp"),
        control_nonce: str | None = Header(default=None, alias="X-Runner-Nonce"),
        control_signature: str | None = Header(default=None, alias="X-Runner-Signature"),
    ) -> dict[str, object]:
        path = f"/internal/v1/runtimes/{provisioning_id}"
        verify_control_request("DELETE", path, {}, control_timestamp, control_nonce, control_signature)
        with app.state.runner_sessions() as session:
            runtime = session.scalar(select(ManagedRuntime).where(ManagedRuntime.provisioning_id == provisioning_id))
            if not runtime:
                return {"provisioningId": provisioning_id, "status": "DEPROVISIONED"}
            runtime.active = False
            session.commit()
            result = runtime_response(runtime)
        refresh_managed_runtimes()
        app.state.platform_registrations.pop(runtime.slug, None)
        heartbeat_task = app.state.heartbeat_tasks.pop(runtime.slug, None)
        if heartbeat_task is not None:
            heartbeat_task.cancel()
        return result

    async def send_platform_heartbeat(slug: str, registration: dict[str, object]) -> dict[str, object]:
        _, identity = get_agent(slug)
        base = str(registration["platformUrl"])
        profile_id = str(registration["profileId"])
        heartbeat_state = {
            "status": registration["status"],
            "capacityAvailable": registration["capacityAvailable"],
            "endpointLatencyMs": registration["endpointLatencyMs"],
        }
        timeout = float(os.getenv("AGENT_PLATFORM_TIMEOUT_SECONDS", "10"))
        async with httpx.AsyncClient(timeout=timeout, follow_redirects=False) as client:
            challenge_response = await client.post(
                f"{base}/api/v1/agent-onboarding/registrations/{profile_id}/heartbeat-challenge",
                json=heartbeat_state,
            )
            challenge_response.raise_for_status()
            challenge = challenge_response.json()
            message = str(challenge["message"])
            if runner_profile == "managed":
                runtime = app.state.managed_runtimes[slug]
                signed = SidecarClient(runtime).sign_identity_message(message)
                signature = str(signed["signature"])
                identity.verify_message(message, signature)
            else:
                signature = identity.sign_message(message)
            heartbeat_response = await client.post(
                f"{base}/api/v1/agent-onboarding/registrations/{profile_id}/heartbeat",
                json={
                    "challengeId": challenge["challengeId"],
                    **heartbeat_state,
                    "signature": signature,
                },
            )
            heartbeat_response.raise_for_status()
            return heartbeat_response.json()

    async def heartbeat_loop(slug: str) -> None:
        while True:
            registration = app.state.platform_registrations.get(slug)
            if registration is None:
                return
            await asyncio.sleep(int(registration["heartbeatIntervalSeconds"]))
            registration = app.state.platform_registrations.get(slug)
            if registration is None:
                return
            try:
                result = await send_platform_heartbeat(slug, registration)
                registration["lastHeartbeatAt"] = utc_now()
                registration["lastHeartbeatResult"] = result
                registration["lastError"] = None
            except (httpx.HTTPError, KeyError, TypeError, ValueError) as exc:
                registration["lastError"] = str(exc)

    def start_heartbeat(slug: str, registration: dict[str, object]) -> None:
        previous = app.state.heartbeat_tasks.pop(slug, None)
        if previous is not None:
            previous.cancel()
        app.state.platform_registrations[slug] = registration
        app.state.heartbeat_tasks[slug] = asyncio.create_task(heartbeat_loop(slug))

    @app.on_event("startup")
    async def start_configured_heartbeats() -> None:
        if runner_profile == "managed":
            for runtime in app.state.managed_runtimes.values():
                if runtime.platform_registration is not None:
                    start_heartbeat(runtime.slug, dict(runtime.platform_registration))
        raw = os.getenv("AGENT_PLATFORM_REGISTRATIONS_JSON", "")
        if not raw:
            return
        configured = json.loads(raw)
        if not isinstance(configured, dict):
            raise ValueError("AGENT_PLATFORM_REGISTRATIONS_JSON must be an object")
        for slug, value in configured.items():
            if slug not in app.state.definitions or not isinstance(value, dict):
                raise ValueError(f"Invalid platform heartbeat registration for {slug}")
            body = PlatformRegistrationRequest.model_validate(value)
            registration = {
                **body.model_dump(by_alias=True),
                "platformUrl": _platform_url(body.platform_url),
            }
            result = await send_platform_heartbeat(slug, registration)
            registration["lastHeartbeatAt"] = utc_now()
            registration["lastHeartbeatResult"] = result
            registration["lastError"] = None
            start_heartbeat(slug, registration)

    @app.on_event("shutdown")
    async def stop_heartbeats() -> None:
        tasks = list(app.state.heartbeat_tasks.values())
        for task in tasks:
            task.cancel()
        if tasks:
            await asyncio.gather(*tasks, return_exceptions=True)

    def manifest(slug: str, request: Request) -> dict[str, object]:
        definition, identity = get_agent(slug)
        base_url = str(request.base_url).rstrip("/")
        endpoint = f"{base_url}/agents/{slug}/invoke"
        runtime = app.state.managed_runtimes.get(slug) if runner_profile == "managed" else None
        if runtime is not None:
            capabilities = []
            for item in runtime.capabilities:
                entry = dict(item)
                if "exampleRequest" not in entry and isinstance(entry.get("exampleInput"), dict):
                    entry["exampleRequest"] = {"input": entry["exampleInput"]}
                capabilities.append(entry)
        else:
            capabilities = [{
                "capabilityId": definition.capability_id,
                "pricingModel": "fixed",
                "basePrice": definition.base_price,
                "currency": definition.currency,
                "estimatedDurationSeconds": definition.estimated_duration_seconds,
                "inputSchema": definition.input_schema,
                "outputSchema": definition.output_schema,
                "exampleRequest": {"input": definition.example_input},
            }]
        return {
            "agentId": slug,
            "did": identity.did,
            "name": definition.name,
            "description": definition.description,
            "version": definition.version,
            "status": "online",
            "verificationMethod": {
                "id": f"{identity.did}#operational-1",
                "type": "EcdsaSecp256k1VerificationKey2019",
                "controller": identity.did,
                "publicKeyHex": identity.public_key_hex,
            },
            "capabilities": [{**item, "endpoint": endpoint, "method": "POST"} for item in capabilities],
        }

    @app.get("/health")
    async def health() -> dict[str, object]:
        return {
            "status": "ok",
            "service": "agent-crosschain-platform-agent-runner",
            "runtimeId": os.getenv("LOCAL_PLATFORM_RUNTIME_ID"),
            "profile": runner_profile,
            "agents": len(app.state.definitions),
            "llm": dict(app.state.llm_config),
            "timestamp": utc_now(),
        }

    @app.get("/runtime/llm-config", dependencies=[Depends(require_invoke_access)])
    async def get_llm_runtime_config() -> dict[str, object]:
        return dict(app.state.llm_config)

    @app.post("/runtime/llm-config", dependencies=[Depends(require_invoke_access)])
    async def set_llm_runtime_config(body: LlmRuntimeConfigRequest) -> dict[str, object]:
        if runner_profile == "managed":
            raise HTTPException(status_code=403, detail="Managed Runner credentials must be provisioned by secretRef")
        if body.provider == "deepseek":
            if not body.api_key:
                raise HTTPException(status_code=422, detail="apiKey is required for DeepSeek")
            base_url = body.base_url.rstrip("/")
            parsed = urlparse(base_url)
            if parsed.scheme not in {"http", "https"} or not parsed.hostname:
                raise HTTPException(status_code=422, detail="baseUrl must be an HTTP(S) URL")
            review = DeepSeekCodeReviewProvider(
                api_key=body.api_key,
                model=body.model,
                base_url=base_url,
                timeout_seconds=float(os.getenv("DEEPSEEK_TIMEOUT_SECONDS", "30")),
                max_retries=int(os.getenv("DEEPSEEK_MAX_RETRIES", "2")),
            )
            task = DeepSeekStructuredTaskProvider(
                api_key=body.api_key,
                model=body.model,
                base_url=base_url,
                timeout_seconds=float(os.getenv("DEEPSEEK_TIMEOUT_SECONDS", "60")),
                max_retries=int(os.getenv("DEEPSEEK_MAX_RETRIES", "2")),
                temperature=float(os.getenv("DEEPSEEK_TEMPERATURE", "0")),
                max_tokens=int(os.getenv("DEEPSEEK_MAX_TOKENS", "4096")),
            )
            source = "runtime-memory"
        else:
            review = MockCodeReviewProvider()
            task = None
            base_url = None
            source = "deterministic-local"
        app.state.definitions = build_agents(review_provider=review, task_provider=task)
        app.state.llm_config = {
            "provider": body.provider,
            "model": body.model if body.provider == "deepseek" else "deterministic-local",
            "baseUrl": base_url,
            "configured": body.provider == "deepseek",
            "source": source,
            "updatedAt": utc_now(),
        }
        return dict(app.state.llm_config)

    @app.post("/runtime/llm-config/test", dependencies=[Depends(require_invoke_access)])
    async def test_llm_runtime_config() -> dict[str, object]:
        definition, _ = get_agent("workflow-research-b")
        try:
            output = await definition.handler(definition.example_input)
            Draft202012Validator(definition.output_schema).validate(output)
        except (httpx.HTTPError, ValidationError, RuntimeError, ValueError, KeyError, TypeError) as exc:
            raise HTTPException(status_code=422, detail=f"Model connection test failed: {exc}") from exc
        return {
            "ok": True,
            "provider": app.state.llm_config["provider"],
            "model": app.state.llm_config["model"],
            "testedAt": utc_now(),
        }

    @app.get("/agents")
    async def list_agents(request: Request) -> dict[str, object]:
        return {
            "items": [manifest(slug, request) for slug in app.state.definitions],
            "total": len(app.state.definitions),
        }

    @app.get("/agents/{slug}/health")
    async def agent_health(slug: str) -> dict[str, object]:
        definition, identity = get_agent(slug)
        return {
            "status": "online",
            "agentId": slug,
            "did": identity.did,
            "capabilityId": definition.capability_id,
            "timestamp": utc_now(),
        }

    @app.get("/agents/{slug}/manifest")
    async def get_manifest(slug: str, request: Request) -> dict[str, object]:
        return manifest(slug, request)

    @app.get("/agents/{slug}/schemas/input")
    async def get_input_schema(slug: str) -> dict[str, object]:
        definition, _ = get_agent(slug)
        return definition.input_schema

    @app.get("/agents/{slug}/schemas/output")
    async def get_output_schema(slug: str) -> dict[str, object]:
        definition, _ = get_agent(slug)
        return definition.output_schema

    @app.post("/agents/{slug}/identity-signatures", dependencies=[Depends(require_invoke_access)])
    async def sign_identity_message(slug: str, body: IdentitySignRequest) -> dict[str, object]:
        _, identity = get_agent(slug)
        allowed_prefixes = (
            "Agent onboarding ownership challenge\n",
            "Agent heartbeat challenge\n",
        )
        if not body.message.startswith(allowed_prefixes):
            raise HTTPException(status_code=422, detail="Unsupported identity signing purpose")
        if runner_profile == "managed":
            runtime = app.state.managed_runtimes[slug]
            try:
                signed = SidecarClient(runtime).sign_identity_message(body.message)
                identity.verify_message(body.message, str(signed["signature"]))
            except (httpx.HTTPError, ValueError, KeyError) as exc:
                raise HTTPException(status_code=502, detail=f"Agent Sidecar identity signing failed: {exc}") from exc
            return {
                "did": identity.did,
                "algorithm": "ES256K",
                "publicKeyHex": identity.public_key_hex,
                "signature": signed["signature"],
            }
        return {
            "did": identity.did,
            "algorithm": "ES256K",
            "publicKeyHex": identity.public_key_hex,
            "signature": identity.sign_message(body.message),
        }

    @app.post("/agents/{slug}/platform-registration", dependencies=[Depends(require_invoke_access)])
    async def register_with_platform(slug: str, body: PlatformRegistrationRequest) -> dict[str, object]:
        get_agent(slug)
        try:
            registration = {
                **body.model_dump(by_alias=True),
                "platformUrl": _platform_url(body.platform_url),
            }
            if runner_profile == "managed":
                with app.state.runner_sessions() as session:
                    runtime = session.scalar(select(ManagedRuntime).where(ManagedRuntime.slug == slug))
                    if runtime is None or not runtime.active:
                        raise ValueError("Managed Runtime is not active")
                    runtime.platform_registration = registration
                    session.commit()
                refresh_managed_runtimes()
            first_heartbeat = await send_platform_heartbeat(slug, registration)
        except (httpx.HTTPError, KeyError, TypeError, ValueError) as exc:
            raise HTTPException(status_code=422, detail=f"Platform heartbeat registration failed: {exc}") from exc
        registration["lastHeartbeatAt"] = utc_now()
        registration["lastHeartbeatResult"] = first_heartbeat
        registration["lastError"] = None
        start_heartbeat(slug, registration)
        return {
            "registered": True,
            "agentId": slug,
            "profileId": body.profile_id,
            "heartbeat": first_heartbeat,
        }

    @app.get("/agents/{slug}/platform-registration", dependencies=[Depends(require_invoke_access)])
    async def platform_registration_status(slug: str) -> dict[str, object]:
        get_agent(slug)
        registration = app.state.platform_registrations.get(slug)
        if registration is None:
            raise HTTPException(status_code=404, detail="Agent has no platform registration")
        return dict(registration)

    @app.post("/agents/{slug}/wallet-transactions/sign", dependencies=[Depends(require_invoke_access)])
    async def sign_wallet_transaction(
        slug: str,
        raw_body: dict[str, object],
        worker_signature: str | None = Header(default=None, alias="X-Worker-Intent-Signature"),
    ) -> dict[str, object]:
        get_agent(slug)
        if runner_profile == "managed":
            try:
                intent = ManagedTransactionIntent.model_validate(raw_body)
            except ValueError as exc:
                raise HTTPException(status_code=422, detail=f"Invalid managed transaction intent: {exc}") from exc
            runtime = app.state.managed_runtimes[slug]
            if intent.runtime_id != runtime.runtime_id or intent.did != runtime.did:
                raise HTTPException(status_code=422, detail="Transaction intent is bound to another Runtime")
            payload = dict(raw_body)
            worker_secret = os.getenv("WORKER_INTENT_HMAC_SECRET", "")
            expected = "sha256=" + hmac.new(worker_secret.encode(), canonical_json(payload), hashlib.sha256).hexdigest() if worker_secret else ""
            if not worker_secret or not worker_signature or not secrets.compare_digest(expected, worker_signature):
                raise HTTPException(status_code=401, detail="Worker intent signature is invalid")
            try:
                signed = SidecarClient(runtime).sign_transaction(payload, worker_signature)
                raw_transaction = str(signed["rawTransaction"])
                assigned_nonce = int(signed["assignedNonce"])
                recover_and_validate_transaction(raw_transaction, payload, runtime.home_wallet, assigned_nonce)
            except (httpx.HTTPError, ValueError, KeyError, TypeError) as exc:
                raise HTTPException(status_code=502, detail=f"Agent Sidecar transaction signing failed: {exc}") from exc
            return {
                "agentId": slug,
                "runtimeId": runtime.runtime_id,
                "requestId": intent.request_id,
                "chainId": intent.chain_id,
                "assignedNonce": assigned_nonce,
                "signer": runtime.home_wallet,
                "transactionHash": signed["transactionHash"],
                "rawTransaction": raw_transaction,
                "status": signed.get("status", "SIGNED"),
            }
        try:
            body = WalletTransactionSignRequest.model_validate(raw_body)
        except ValueError as exc:
            raise HTTPException(status_code=422, detail=f"Invalid local-demo transaction: {exc}") from exc
        account = app.state.wallet_accounts.get(slug)
        policy = app.state.wallet_policies.get(slug)
        if account is None or not isinstance(policy, dict):
            raise HTTPException(status_code=409, detail="Agent home wallet or signing policy is not configured")
        expected_wallet = str(policy.get("homeWallet", ""))
        if account.address.lower() != expected_wallet.lower():
            raise HTTPException(status_code=409, detail="Agent private key does not match policy homeWallet")
        if body.chain_id != int(policy.get("chainId", 0)):
            raise HTTPException(status_code=422, detail="Transaction chainId does not match Agent homeChain")
        allowed_contracts = policy.get("allowedContracts", {})
        allowed_selectors = allowed_contracts.get(body.to.lower()) if isinstance(allowed_contracts, dict) else None
        selector = body.data[:10].lower()
        selectors = {str(item).lower() for item in allowed_selectors} if isinstance(allowed_selectors, list) else set()
        if selector not in selectors:
            try:
                selectors = await _artifact_allowed_selectors(policy, body.to)
            except (httpx.HTTPError, ValueError, TypeError) as exc:
                raise HTTPException(status_code=422, detail=f"Agent contract policy verification failed: {exc}") from exc
        if selector not in selectors:
            raise HTTPException(status_code=422, detail="Transaction contract or method is not allowed")
        if body.attempt_id_bytes32.lower() != _opaque_id_to_bytes32(body.attempt_id):
            raise HTTPException(status_code=422, detail="Transaction attemptId binding is invalid")
        if body.attempt_id_bytes32[2:].lower() not in body.data[10:].lower():
            raise HTTPException(status_code=422, detail="Transaction calldata is not bound to attemptId")
        if int(body.value_wei) > int(policy.get("maxValueWei", "0")):
            raise HTTPException(status_code=422, detail="Transaction value exceeds Agent policy")
        if body.gas > int(policy.get("maxGas", 500_000)):
            raise HTTPException(status_code=422, detail="Transaction gas exceeds Agent policy")
        if int(body.gas_price_wei) > int(policy.get("maxGasPriceWei", "100000000000")):
            raise HTTPException(status_code=422, detail="Transaction gas price exceeds Agent policy")
        replay_key = (slug, body.request_nonce)
        with app.state.wallet_nonce_lock:
            if replay_key in app.state.wallet_request_nonces:
                raise HTTPException(status_code=409, detail="Wallet signing request nonce was already used")
            transaction = {
                "chainId": body.chain_id,
                "to": to_checksum_address(body.to),
                "data": body.data,
                "value": int(body.value_wei),
                "nonce": body.nonce,
                "gas": body.gas,
                "gasPrice": int(body.gas_price_wei),
            }
            signed = account.sign_transaction(transaction)
            app.state.wallet_request_nonces.add(replay_key)
            nonce_store = app.state.wallet_nonce_store
            if nonce_store is not None:
                nonce_store.parent.mkdir(parents=True, exist_ok=True)
                temporary = nonce_store.with_suffix(nonce_store.suffix + ".tmp")
                temporary.write_text(
                    json.dumps(sorted([list(item) for item in app.state.wallet_request_nonces]), separators=(",", ":")),
                    encoding="utf-8",
                )
                temporary.replace(nonce_store)
        raw_transaction = signed.raw_transaction.hex()
        if not raw_transaction.startswith("0x"):
            raw_transaction = "0x" + raw_transaction
        return {
            "agentId": slug,
            "attemptId": body.attempt_id,
            "chainId": body.chain_id,
            "signer": account.address,
            "transactionHash": signed.hash.hex(),
            "rawTransaction": raw_transaction,
            "requestNonce": body.request_nonce,
        }

    def verify_worker_payload(payload: dict[str, object], signature: str | None) -> None:
        secret = os.getenv("WORKER_INTENT_HMAC_SECRET", "")
        expected = "sha256=" + hmac.new(secret.encode(), canonical_json(payload), hashlib.sha256).hexdigest() if secret else ""
        if not secret or not signature or not secrets.compare_digest(expected, signature):
            raise HTTPException(status_code=401, detail="Worker payload signature is invalid")

    @app.post("/agents/{slug}/canonical-results/sign", dependencies=[Depends(require_invoke_access)])
    async def bind_canonical_result(
        slug: str,
        body: dict[str, object],
        worker_signature: str | None = Header(default=None, alias="X-Worker-Intent-Signature"),
    ) -> dict[str, object]:
        if runner_profile != "managed":
            raise HTTPException(status_code=404, detail="Canonical binding proxy is only available in managed mode")
        runtime = app.state.managed_runtimes.get(slug)
        if not runtime:
            raise HTTPException(status_code=404, detail="Agent not found")
        payload = {**body, "runtimeId": runtime.runtime_id, "did": runtime.did}
        verify_worker_payload(payload, worker_signature)
        if payload.get("routeKey") != runtime.route_key or payload.get("routeVersion") != runtime.route_version:
            raise HTTPException(status_code=422, detail="Canonical result route does not match Runtime")
        try:
            signed = SidecarClient(runtime).sign_canonical_result(payload)
            ManagedIdentity(runtime.did, runtime.public_key_hex).verify(payload, str(signed["signature"]))
            return signed
        except (httpx.HTTPError, ValueError, KeyError) as exc:
            raise HTTPException(status_code=502, detail=f"Agent Sidecar canonical signing failed: {exc}") from exc

    @app.post("/agents/{slug}/wallet-transactions/{request_id}/status", dependencies=[Depends(require_invoke_access)])
    async def report_wallet_transaction_status(
        slug: str,
        request_id: str,
        body: dict[str, object],
        worker_signature: str | None = Header(default=None, alias="X-Worker-Intent-Signature"),
    ) -> dict[str, object]:
        if runner_profile != "managed":
            raise HTTPException(status_code=404, detail="Transaction status proxy is only available in managed mode")
        runtime = app.state.managed_runtimes.get(slug)
        if not runtime:
            raise HTTPException(status_code=404, detail="Agent not found")
        payload = {**body, "runtimeId": runtime.runtime_id, "did": runtime.did}
        verify_worker_payload(payload, worker_signature)
        try:
            return SidecarClient(runtime).update_transaction_status(
                request_id,
                payload,
                worker_signature or "",
                broadcast=payload.get("status") == "BROADCAST",
            )
        except (httpx.HTTPError, ValueError, KeyError) as exc:
            raise HTTPException(status_code=502, detail=f"Agent Sidecar status update failed: {exc}") from exc

    @app.post("/agents/{slug}/task-offers", dependencies=[Depends(require_invoke_access)])
    async def accept_task_offer(slug: str, body: TaskOfferRequest) -> dict[str, object]:
        definition, identity = get_agent(slug)
        if not api_token:
            raise HTTPException(status_code=503, detail="Signed task offers require AGENT_RUNNER_API_TOKEN")
        unsigned_offer = body.model_dump(by_alias=True, exclude={"coordinator_signature"})
        expected_signature = _b64url_hmac(api_token, unsigned_offer)
        if not secrets.compare_digest(body.coordinator_signature, expected_signature):
            raise HTTPException(status_code=401, detail="Task offer signature is invalid")
        supported_capabilities = (
            {str(item.get("capabilityId")) for item in app.state.managed_runtimes[slug].capabilities}
            if runner_profile == "managed" else {definition.capability_id}
        )
        if body.capability_id not in supported_capabilities:
            raise HTTPException(status_code=422, detail="Task capability does not match this Agent")
        try:
            deadline = datetime.fromisoformat(body.deadline.replace("Z", "+00:00"))
        except ValueError as exc:
            raise HTTPException(status_code=422, detail="Task deadline is invalid") from exc
        if deadline.tzinfo is None:
            deadline = deadline.replace(tzinfo=timezone.utc)
        if deadline <= datetime.now(timezone.utc):
            raise HTTPException(status_code=409, detail="Task offer has expired")
        offer = unsigned_offer
        existing = app.state.accepted_offers.get(body.offer_id)
        if existing is not None and existing != offer:
            raise HTTPException(status_code=409, detail="offerId is already bound to another task")
        if body.offer_id in app.state.completed_offers:
            raise HTTPException(status_code=409, detail="Task offer has already completed")
        app.state.accepted_offers[body.offer_id] = offer
        accepted_at = utc_now()
        signed_payload = {
            "accepted": True,
            "acceptedAt": accepted_at,
            "agentId": slug,
            "attemptId": body.attempt_id,
            "did": identity.did,
            "offerId": body.offer_id,
        }
        response = {
            **signed_payload,
        }
        if runner_profile == "local-demo":
            response["signature"] = {
                "algorithm": "ES256K",
                "publicKeyHex": identity.public_key_hex,
                "value": identity.sign(signed_payload),  # type: ignore[union-attr]
            }
        return response

    @app.post("/agents/{slug}/test-invoke", dependencies=[Depends(require_invoke_access)])
    @app.post("/agents/{slug}/invoke", dependencies=[Depends(require_invoke_access)])
    async def invoke(slug: str, body: InvokeRequest) -> dict[str, object]:
        definition, identity = get_agent(slug)
        selected_capability = definition.capability_id
        if body.offer_id is not None:
            expected_context = {
                "workflowId": body.workflow_id,
                "nodeId": body.node_id,
                "attemptId": body.attempt_id,
                "attemptNumber": body.attempt_number,
                "reservationId": body.reservation_id,
                "deadline": body.deadline,
                "nonce": body.nonce,
            }
            completed = app.state.completed_offers.get(body.offer_id)
            if completed is not None:
                if any(completed.get(key) != value for key, value in expected_context.items()):
                    raise HTTPException(status_code=409, detail="Invocation context does not match completed offer")
                return completed
            accepted_offer = app.state.accepted_offers.get(body.offer_id)
            if accepted_offer is None:
                raise HTTPException(status_code=409, detail="Task offer was not accepted by this Runner")
            if any(accepted_offer.get(key) != value for key, value in expected_context.items()):
                raise HTTPException(status_code=409, detail="Invocation context does not match accepted offer")
            selected_capability = str(accepted_offer["capabilityId"])
            deadline = datetime.fromisoformat(str(body.deadline).replace("Z", "+00:00"))
            if deadline <= datetime.now(timezone.utc):
                raise HTTPException(status_code=409, detail="Accepted task offer has expired")
        managed_capability = runtime_capability(app.state.managed_runtimes[slug], selected_capability) if runner_profile == "managed" else None
        input_schema = managed_capability.get("inputSchema", {"type": "object"}) if managed_capability else definition.input_schema
        output_schema = managed_capability.get("outputSchema", {"type": "object"}) if managed_capability else definition.output_schema
        try:
            Draft202012Validator(input_schema).validate(body.input)
        except ValidationError as exc:
            location = ".".join(str(item) for item in exc.absolute_path) or "input"
            raise HTTPException(
                status_code=422,
                detail={
                    "code": "invalid_agent_input",
                    "field": location,
                    "message": exc.message,
                },
            ) from exc

        request_id = body.request_id or str(uuid.uuid4())
        try:
            output = (
                await execute_runtime(app.state.managed_runtimes[slug], body.input, selected_capability)
                if runner_profile == "managed" else await definition.handler(body.input)
            )
            Draft202012Validator(output_schema).validate(output)
        except ValidationError as exc:
            raise HTTPException(
                status_code=500,
                detail={"code": "invalid_agent_output", "message": exc.message},
            ) from exc
        except (TypeError, ValueError, ArithmeticError, RuntimeError) as exc:
            raise HTTPException(
                status_code=422,
                detail={"code": "agent_execution_error", "message": str(exc)},
            ) from exc

        completed_at = utc_now()
        output_hash = "sha256:" + hashlib.sha256(canonical_json(output)).hexdigest()
        signed_payload = {
            "agentId": slug,
            "capabilityId": selected_capability,
            "completedAt": completed_at,
            "did": identity.did,
            "outputHash": output_hash,
            "requestId": request_id,
        }
        if body.workflow_id is not None:
            signed_payload.update(
                {
                    "workflowId": body.workflow_id,
                    "nodeId": body.node_id,
                    "attemptId": body.attempt_id,
                    "attemptNumber": body.attempt_number,
                    "reservationId": body.reservation_id,
                    "deadline": body.deadline,
                    "nonce": body.nonce,
                    "offerId": body.offer_id,
                }
            )
        if runner_profile == "managed":
            try:
                signed = SidecarClient(app.state.managed_runtimes[slug]).sign_runtime_result(signed_payload)
                identity.verify(signed_payload, str(signed["signature"]))
                signature_value = str(signed["signature"])
            except (httpx.HTTPError, ValueError, KeyError) as exc:
                raise HTTPException(status_code=502, detail={"code": "sidecar_signing_error", "message": str(exc)}) from exc
        else:
            signature_value = identity.sign(signed_payload)  # type: ignore[union-attr]
        response = {
            **signed_payload,
            "output": output,
            "signature": {
                "algorithm": "ES256K",
                "verificationMethod": f"{identity.did}#operational-1",
                "publicKeyHex": identity.public_key_hex,
                "publicKeyMultibase": identity.public_key_multibase,
                "value": signature_value,
            },
        }
        if body.offer_id is not None:
            app.state.completed_offers[body.offer_id] = response
            app.state.accepted_offers.pop(body.offer_id, None)
        return response

    return app


app = build_app()
