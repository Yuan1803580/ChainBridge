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
from datetime import datetime, timezone
from pathlib import Path
from typing import Annotated
from urllib.parse import urlparse

import httpx
from fastapi import Depends, FastAPI, Header, HTTPException, Request, status
from fastapi.middleware.cors import CORSMiddleware
from jsonschema import Draft202012Validator
from jsonschema.exceptions import ValidationError
from pydantic import BaseModel, ConfigDict, Field, model_validator
from eth_account import Account
from eth_account.messages import encode_defunct
from eth_utils import keccak, to_checksum_address

from app.agents import AgentDefinition, build_agents
from app.chat_store import ChatSessionNotFound, ChatSessionStore
from app.identity import (
    RuntimeIdentity,
    assert_canonical_sha256,
    canonical_json,
    canonical_sha256,
    load_runtime_identities,
)
from app.knowledge import ProjectKnowledgeIndex
from app.providers import (
    DeepSeekCodeReviewProvider,
    DeepSeekStructuredTaskProvider,
    KimiProvider,
    MockCodeReviewProvider,
    MockConversationProvider,
)
from app.tools import ControlledToolRegistry


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


class WalletSignRequest(BaseModel):
    message: str = Field(min_length=16, max_length=10000)


class PlatformRegistrationRequest(BaseModel):
    model_config = ConfigDict(populate_by_name=True)

    platform_url: str = Field(alias="platformUrl", pattern=r"^https?://[^\s]+$", max_length=500)
    profile_id: str = Field(alias="profileId", min_length=36, max_length=36)
    status: str = Field(default="ONLINE", pattern=r"^(ONLINE|BUSY|OFFLINE)$")
    capacity_available: bool = Field(default=True, alias="capacityAvailable")
    endpoint_latency_ms: int = Field(default=500, alias="endpointLatencyMs", ge=0, le=60000)
    heartbeat_interval_seconds: int = Field(default=30, alias="heartbeatIntervalSeconds", ge=10, le=50)


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


class ChatSessionCreateRequest(BaseModel):
    model_config = ConfigDict(populate_by_name=True)

    owner_id: str = Field(alias="ownerId", min_length=3, max_length=128)
    title: str | None = Field(default=None, max_length=200)


class ChatRequest(BaseModel):
    model_config = ConfigDict(populate_by_name=True)

    session_id: str = Field(alias="sessionId", min_length=36, max_length=36)
    message: str = Field(min_length=1, max_length=20_000)


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
    if os.getenv("APP_ENV", "development").lower() in {"production", "prod"} and parsed.scheme != "https":
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
        title="Agent Market Demo Runtime",
        version="1.0.0",
        description="Deterministic demo agents with signed invocation results.",
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
        allow_methods=["GET", "POST", "DELETE"],
        allow_headers=["Authorization", "Content-Type"],
    )

    @app.middleware("http")
    async def add_json_utf8_charset(request: Request, call_next):
        response = await call_next(request)
        content_type = response.headers.get("content-type", "")
        if content_type.lower().startswith("application/json") and "charset=" not in content_type.lower():
            response.headers["content-type"] = f"{content_type}; charset=utf-8"
        return response

    llm_provider = os.getenv("LLM_PROVIDER", "mock").lower()
    if llm_provider == "deepseek":
        review_provider = DeepSeekCodeReviewProvider.from_env()
        task_provider = DeepSeekStructuredTaskProvider.from_env()
        conversation_provider = None
    elif llm_provider == "kimi":
        kimi_provider = KimiProvider.from_env()
        review_provider = MockCodeReviewProvider()
        task_provider = kimi_provider
        conversation_provider = kimi_provider
    elif llm_provider == "mock":
        review_provider = MockCodeReviewProvider()
        task_provider = None
        conversation_provider = MockConversationProvider()
    else:
        raise ValueError("LLM_PROVIDER must be one of: mock, deepseek, kimi")
    definitions = build_agents(review_provider=review_provider, task_provider=task_provider)
    configured_slugs = {
        item.strip()
        for item in os.getenv("AGENT_RUNNER_SLUGS", "").split(",")
        if item.strip()
    }
    if configured_slugs:
        unknown_slugs = configured_slugs - set(definitions)
        if unknown_slugs:
            raise ValueError(
                "AGENT_RUNNER_SLUGS contains unknown Agent definitions: "
                + ", ".join(sorted(unknown_slugs))
            )
        definitions = {
            slug: definition
            for slug, definition in definitions.items()
            if slug in configured_slugs
        }
    if not definitions:
        raise ValueError("Agent Runner must host at least one Agent definition")
    identities = load_runtime_identities(list(definitions))
    api_token = os.getenv("AGENT_RUNNER_API_TOKEN")
    allow_insecure_local = os.getenv("AGENT_RUNNER_ALLOW_INSECURE_LOCAL", "").lower() in {"1", "true", "yes"}
    raw_wallet_keys = json.loads(os.getenv("AGENT_HOME_WALLET_PRIVATE_KEYS_JSON", "{}"))
    raw_wallet_policies = json.loads(os.getenv("AGENT_WALLET_POLICIES_JSON", "{}"))
    if not isinstance(raw_wallet_keys, dict) or not isinstance(raw_wallet_policies, dict):
        raise ValueError("Agent wallet keys and policies must be JSON objects")
    wallet_accounts = {}
    for slug, private_key in raw_wallet_keys.items():
        if slug not in definitions or not isinstance(private_key, str) or not private_key.startswith("0x"):
            raise ValueError(f"Invalid Agent home-wallet key configuration for {slug}")
        wallet_accounts[slug] = Account.from_key(private_key)
    wallet_nonce_store_value = os.getenv("AGENT_WALLET_NONCE_STORE_PATH", "").strip()
    if os.getenv("APP_ENV", "development").lower() in {"production", "prod"} and not wallet_nonce_store_value:
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
    project_root = Path(__file__).resolve().parents[3]
    platform_registration_store_value = os.getenv(
        "AGENT_PLATFORM_REGISTRATION_STORE_PATH", ""
    ).strip()
    platform_registration_store = (
        Path(platform_registration_store_value).resolve()
        if platform_registration_store_value
        else None
    )
    runner_id = os.getenv("AGENT_RUNNER_ID", "shared-runner")
    chat_database_value = os.getenv("AGENT_CHAT_DB_PATH", "").strip()
    if not chat_database_value:
        if os.getenv("APP_ENV", "development").lower() == "test":
            chat_database_value = ":memory:"
        else:
            chat_database_value = str(project_root / ".runtime" / f"{runner_id}-chat.db")
    chat_store = ChatSessionStore(chat_database_value)
    knowledge_index = ProjectKnowledgeIndex.from_env(project_root)
    tool_registry = ControlledToolRegistry(project_root, knowledge_index)
    chat_max_turns = max(1, min(int(os.getenv("AGENT_CHAT_MAX_TURNS", "12")), 50))
    chat_tool_max_rounds = max(1, min(int(os.getenv("AGENT_CHAT_TOOL_MAX_ROUNDS", "4")), 6))

    app.state.definitions = definitions
    app.state.identities = identities
    app.state.accepted_offers = {}
    app.state.completed_offers = {}
    app.state.active_invocations = {}
    app.state.platform_registrations = {}
    app.state.heartbeat_tasks = {}
    app.state.platform_registration_store = platform_registration_store
    app.state.platform_registration_store_lock = threading.Lock()
    app.state.wallet_accounts = wallet_accounts
    app.state.wallet_policies = raw_wallet_policies
    app.state.wallet_request_nonces = wallet_request_nonces
    app.state.wallet_nonce_store = wallet_nonce_store
    app.state.wallet_nonce_lock = threading.Lock()
    app.state.chat_store = chat_store
    app.state.knowledge_index = knowledge_index
    app.state.tool_registry = tool_registry

    @app.get("/experiment/state")
    def experiment_state() -> dict[str, object]:
        if os.getenv("ALGORITHM4_EXPERIMENT_MODE", "false").lower() not in {
            "1",
            "true",
            "yes",
        }:
            raise HTTPException(status_code=404, detail="Not found")
        return {
            "runnerId": runner_id,
            "acceptedOffers": [
                {
                    "offerId": offer_id,
                    **offer,
                }
                for offer_id, offer in app.state.accepted_offers.items()
            ],
            "activeInvocations": list(app.state.active_invocations.values()),
            "completedOfferIds": sorted(app.state.completed_offers),
        }

    def get_agent(slug: str) -> tuple[AgentDefinition, RuntimeIdentity]:
        definition = definitions.get(slug)
        if definition is None:
            raise HTTPException(status_code=404, detail="Agent not found")
        return definition, identities[slug]

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
            heartbeat_response = await client.post(
                f"{base}/api/v1/agent-onboarding/registrations/{profile_id}/heartbeat",
                json={
                    "challengeId": challenge["challengeId"],
                    **heartbeat_state,
                    "signature": identity.sign_message(str(challenge["message"])),
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

    def persist_platform_registrations() -> None:
        store = app.state.platform_registration_store
        if store is None:
            return
        fields = (
            "platformUrl",
            "profileId",
            "status",
            "capacityAvailable",
            "endpointLatencyMs",
            "heartbeatIntervalSeconds",
        )
        payload = {
            slug: {field: registration[field] for field in fields}
            for slug, registration in app.state.platform_registrations.items()
        }
        store.parent.mkdir(parents=True, exist_ok=True)
        temporary = Path(f"{store}.tmp")
        with app.state.platform_registration_store_lock:
            temporary.write_text(json.dumps(payload, indent=2), encoding="utf-8")
            temporary.replace(store)

    def load_platform_registrations() -> dict[str, object]:
        configured: dict[str, object] = {}
        store = app.state.platform_registration_store
        if store is not None and store.exists():
            stored = json.loads(store.read_text(encoding="utf-8"))
            if not isinstance(stored, dict):
                raise ValueError("Persisted platform registrations must be an object")
            configured.update(stored)
        raw = os.getenv("AGENT_PLATFORM_REGISTRATIONS_JSON", "")
        if raw:
            supplied = json.loads(raw)
            if not isinstance(supplied, dict):
                raise ValueError("AGENT_PLATFORM_REGISTRATIONS_JSON must be an object")
            configured.update(supplied)
        return configured

    def start_heartbeat(slug: str, registration: dict[str, object]) -> None:
        previous = app.state.heartbeat_tasks.pop(slug, None)
        if previous is not None:
            previous.cancel()
        app.state.platform_registrations[slug] = registration
        try:
            persist_platform_registrations()
            registration.pop("lastPersistenceError", None)
        except OSError as exc:
            registration["lastPersistenceError"] = str(exc)
        app.state.heartbeat_tasks[slug] = asyncio.create_task(heartbeat_loop(slug))

    @app.on_event("startup")
    async def start_configured_heartbeats() -> None:
        configured = load_platform_registrations()
        for slug, value in configured.items():
            if slug not in definitions or not isinstance(value, dict):
                raise ValueError(f"Invalid platform heartbeat registration for {slug}")
            body = PlatformRegistrationRequest.model_validate(value)
            registration = {
                **body.model_dump(by_alias=True),
                "platformUrl": _platform_url(body.platform_url),
            }
            try:
                result = await send_platform_heartbeat(slug, registration)
                registration["lastHeartbeatAt"] = utc_now()
                registration["lastHeartbeatResult"] = result
                registration["lastError"] = None
            except (httpx.HTTPError, KeyError, TypeError, ValueError) as exc:
                # The Agent remains available while the platform starts and
                # retries the persisted registration on its normal interval.
                registration["lastError"] = str(exc)
            start_heartbeat(slug, registration)

    @app.on_event("shutdown")
    async def stop_heartbeats() -> None:
        tasks = list(app.state.heartbeat_tasks.values())
        for task in tasks:
            task.cancel()
        if tasks:
            await asyncio.gather(*tasks, return_exceptions=True)
        chat_store.close()

    def manifest(slug: str, request: Request) -> dict[str, object]:
        definition, identity = get_agent(slug)
        base_url = str(request.base_url).rstrip("/")
        endpoint = f"{base_url}/agents/{slug}/invoke"
        result = {
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
            "capabilities": [
                {
                    "capabilityId": definition.capability_id,
                    "endpoint": endpoint,
                    "method": "POST",
                    "pricingModel": "fixed",
                    "basePrice": definition.base_price,
                    "currency": definition.currency,
                    "estimatedDurationSeconds": definition.estimated_duration_seconds,
                    "inputSchema": definition.input_schema,
                    "outputSchema": definition.output_schema,
                    "exampleRequest": {"input": definition.example_input},
                }
            ],
        }
        if definition.chat_system_prompt is not None and conversation_provider is not None:
            result["conversation"] = {
                "enabled": True,
                "createSessionEndpoint": f"{base_url}/agents/{slug}/chat/sessions",
                "chatEndpoint": f"{base_url}/agents/{slug}/chat",
                "persistent": chat_database_value != ":memory:",
            }
            result["tools"] = tool_registry.manifest()
        return result

    @app.get("/health")
    async def health() -> dict[str, object]:
        return {
            "status": "ok",
            "service": "agent-crosschain-platform-agent-runner",
            "runtimeId": os.getenv("LOCAL_PLATFORM_RUNTIME_ID"),
            "runnerId": os.getenv("AGENT_RUNNER_ID", "shared-runner"),
            "agentSlugs": sorted(definitions),
            "agents": len(definitions),
            "llmProvider": llm_provider,
            "timestamp": utc_now(),
        }

    @app.get("/agents")
    async def list_agents(request: Request) -> dict[str, object]:
        return {
            "items": [manifest(slug, request) for slug in definitions],
            "total": len(definitions),
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

    def require_chat_agent(slug: str) -> AgentDefinition:
        definition, _ = get_agent(slug)
        if definition.chat_system_prompt is None:
            raise HTTPException(status_code=409, detail="This Agent does not support conversations")
        if conversation_provider is None:
            raise HTTPException(
                status_code=409,
                detail="This Runner provider does not support conversations",
            )
        return definition

    @app.post(
        "/agents/{slug}/chat/sessions",
        dependencies=[Depends(require_invoke_access)],
    )
    async def create_chat_session(
        slug: str, body: ChatSessionCreateRequest
    ) -> dict[str, object]:
        require_chat_agent(slug)
        return chat_store.create_session(
            agent_slug=slug,
            owner_id=body.owner_id,
            title=body.title,
        )

    @app.get(
        "/agents/{slug}/chat/sessions/{session_id}",
        dependencies=[Depends(require_invoke_access)],
    )
    async def get_chat_session(slug: str, session_id: str) -> dict[str, object]:
        require_chat_agent(slug)
        try:
            return chat_store.get_session(session_id=session_id, agent_slug=slug)
        except ChatSessionNotFound as exc:
            raise HTTPException(status_code=404, detail="Chat session not found") from exc

    @app.delete(
        "/agents/{slug}/chat/sessions/{session_id}",
        dependencies=[Depends(require_invoke_access)],
    )
    async def delete_chat_session(slug: str, session_id: str) -> dict[str, object]:
        require_chat_agent(slug)
        try:
            chat_store.delete_session(session_id=session_id, agent_slug=slug)
        except ChatSessionNotFound as exc:
            raise HTTPException(status_code=404, detail="Chat session not found") from exc
        return {"deleted": True, "sessionId": session_id}

    @app.post(
        "/agents/{slug}/chat",
        dependencies=[Depends(require_invoke_access)],
    )
    async def chat(slug: str, body: ChatRequest) -> dict[str, object]:
        definition = require_chat_agent(slug)
        try:
            history = chat_store.conversation_messages(
                session_id=body.session_id,
                agent_slug=slug,
                max_turns=chat_max_turns,
            )
        except ChatSessionNotFound as exc:
            raise HTTPException(status_code=404, detail="Chat session not found") from exc

        evidence, context_audit = await tool_registry.execute(
            "read_project_context", {"query": body.message}
        )
        tool_invocations = [context_audit]
        messages: list[dict[str, object]] = [
            {"role": "system", "content": str(definition.chat_system_prompt)}
        ]
        messages.append(
            {
                "role": "system",
                "content": (
                    "Use the available read-only tools when current platform, chain, transaction, "
                    "or external evidence is needed. Never invent a tool result. Project documents "
                    "remain authoritative for project-specific implementation claims."
                ),
            }
        )
        evidence_content = str(evidence.get("content", ""))
        if evidence_content:
            messages.append(
                {
                    "role": "system",
                    "content": (
                        "Retrieved project evidence follows. Treat it as the only source for "
                        "project-specific implementation claims.\n\n" + evidence_content
                    ),
                }
            )
        messages.extend(history)
        messages.append({"role": "user", "content": body.message})
        usage: dict[str, int] = {}
        completion: dict[str, object] = {}
        answer = ""
        try:
            for round_index in range(chat_tool_max_rounds + 1):
                completion = await conversation_provider.complete_chat(  # type: ignore[union-attr]
                    messages=messages,
                    tools=tool_registry.specifications(),
                )
                raw_usage = completion.get("usage")
                if isinstance(raw_usage, dict):
                    for key, value in raw_usage.items():
                        if isinstance(value, int):
                            usage[str(key)] = usage.get(str(key), 0) + value
                raw_calls = completion.get("toolCalls")
                if not isinstance(raw_calls, list) or not raw_calls:
                    answer = str(completion.get("content", "")).strip()
                    break
                if round_index >= chat_tool_max_rounds:
                    raise RuntimeError("Conversation exceeded the controlled tool-call round limit")
                assistant_message = completion.get("message")
                if not isinstance(assistant_message, dict):
                    assistant_message = {
                        "role": "assistant",
                        "content": completion.get("content", ""),
                        "tool_calls": raw_calls,
                    }
                messages.append(assistant_message)
                for index, raw_call in enumerate(raw_calls):
                    if not isinstance(raw_call, dict):
                        continue
                    function = raw_call.get("function")
                    function = function if isinstance(function, dict) else {}
                    name = str(function.get("name", ""))
                    raw_arguments = function.get("arguments", "{}")
                    try:
                        arguments = json.loads(raw_arguments) if isinstance(raw_arguments, str) else raw_arguments
                    except json.JSONDecodeError:
                        arguments = {}
                    if not isinstance(arguments, dict):
                        arguments = {}
                    tool_result, audit_item = await tool_registry.execute(name, arguments)
                    tool_invocations.append(audit_item)
                    messages.append(
                        {
                            "role": "tool",
                            "tool_call_id": str(raw_call.get("id") or f"tool-{index}"),
                            "name": name,
                            "content": json.dumps(tool_result, ensure_ascii=False)[:30_000],
                        }
                    )
            if not answer:
                raise RuntimeError("Conversation provider returned an empty answer")
        except RuntimeError as exc:
            raise HTTPException(
                status_code=status.HTTP_502_BAD_GATEWAY,
                detail={"code": "conversation_provider_error", "message": str(exc)},
            ) from exc
        updated_at = chat_store.append_exchange(
            session_id=body.session_id,
            agent_slug=slug,
            user_message=body.message,
            assistant_message=answer,
        )
        return {
            "sessionId": body.session_id,
            "agentId": slug,
            "message": answer,
            "provider": completion.get("provider"),
            "model": completion.get("model"),
            "usage": usage or completion.get("usage", {}),
            "sources": evidence.get("sources", []),
            "toolInvocations": tool_invocations,
            "updatedAt": updated_at,
        }

    @app.post("/agents/{slug}/identity-signatures", dependencies=[Depends(require_invoke_access)])
    async def sign_identity_message(slug: str, body: IdentitySignRequest) -> dict[str, object]:
        _, identity = get_agent(slug)
        allowed_prefixes = (
            "Agent onboarding ownership challenge\n",
            "Agent heartbeat challenge\n",
        )
        if not body.message.startswith(allowed_prefixes):
            raise HTTPException(status_code=422, detail="Unsupported identity signing purpose")
        return {
            "did": identity.did,
            "algorithm": "ES256K",
            "publicKeyHex": identity.public_key_hex,
            "signature": identity.sign_message(body.message),
        }

    @app.get("/agents/{slug}/wallet", dependencies=[Depends(require_invoke_access)])
    async def get_wallet(slug: str) -> dict[str, object]:
        get_agent(slug)
        account = app.state.wallet_accounts.get(slug)
        policy = app.state.wallet_policies.get(slug)
        if account is None or not isinstance(policy, dict):
            raise HTTPException(status_code=409, detail="Agent home wallet is not configured")
        if account.address.lower() != str(policy.get("homeWallet", "")).lower():
            raise HTTPException(status_code=409, detail="Agent private key does not match policy homeWallet")
        return {
            "agentId": slug,
            "address": account.address,
            "chainId": int(policy.get("chainId", 0)),
            "transactionSigningEnabled": True,
        }

    @app.post("/agents/{slug}/wallet-signatures", dependencies=[Depends(require_invoke_access)])
    async def sign_wallet_message(slug: str, body: WalletSignRequest) -> dict[str, object]:
        _, identity = get_agent(slug)
        account = app.state.wallet_accounts.get(slug)
        policy = app.state.wallet_policies.get(slug)
        if account is None or not isinstance(policy, dict):
            raise HTTPException(status_code=409, detail="Agent home wallet is not configured")
        expected_wallet = str(policy.get("homeWallet", ""))
        expected_chain_id = str(int(policy.get("chainId", 0)))
        fields = {}
        lines = body.message.splitlines()
        if not lines or lines[0] != "Agent onboarding ownership challenge":
            raise HTTPException(status_code=422, detail="Unsupported wallet signing purpose")
        for line in lines[1:]:
            key, separator, value = line.partition("=")
            if separator:
                fields[key] = value
        if (
            fields.get("agentKey") != slug
            or fields.get("did") != identity.did
            or fields.get("homeWallet", "").lower() != expected_wallet.lower()
            or fields.get("chainId") != expected_chain_id
            or not fields.get("profileId")
            or not fields.get("nonce")
            or not fields.get("expiresAt")
        ):
            raise HTTPException(status_code=422, detail="Wallet challenge does not match this Agent")
        if account.address.lower() != expected_wallet.lower():
            raise HTTPException(status_code=409, detail="Agent private key does not match policy homeWallet")
        signed = Account.sign_message(encode_defunct(text=body.message), account.key)
        return {
            "agentId": slug,
            "algorithm": "EIP-191",
            "address": account.address,
            "signature": signed.signature.hex(),
        }

    @app.post("/agents/{slug}/platform-registration", dependencies=[Depends(require_invoke_access)])
    async def register_with_platform(slug: str, body: PlatformRegistrationRequest) -> dict[str, object]:
        get_agent(slug)
        try:
            registration = {
                **body.model_dump(by_alias=True),
                "platformUrl": _platform_url(body.platform_url),
            }
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
    async def sign_wallet_transaction(slug: str, body: WalletTransactionSignRequest) -> dict[str, object]:
        get_agent(slug)
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

    @app.post("/agents/{slug}/task-offers", dependencies=[Depends(require_invoke_access)])
    async def accept_task_offer(slug: str, body: TaskOfferRequest) -> dict[str, object]:
        definition, identity = get_agent(slug)
        if not api_token:
            raise HTTPException(status_code=503, detail="Signed task offers require AGENT_RUNNER_API_TOKEN")
        unsigned_offer = body.model_dump(by_alias=True, exclude={"coordinator_signature"})
        expected_signature = _b64url_hmac(api_token, unsigned_offer)
        if not secrets.compare_digest(body.coordinator_signature, expected_signature):
            raise HTTPException(status_code=401, detail="Task offer signature is invalid")
        if body.capability_id != definition.capability_id:
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
        return {
            **signed_payload,
            "signature": {
                "algorithm": "ES256K",
                "publicKeyHex": identity.public_key_hex,
                "value": identity.sign(signed_payload),
            },
        }

    @app.post("/agents/{slug}/test-invoke", dependencies=[Depends(require_invoke_access)])
    @app.post("/agents/{slug}/invoke", dependencies=[Depends(require_invoke_access)])
    async def invoke(slug: str, body: InvokeRequest) -> dict[str, object]:
        definition, identity = get_agent(slug)
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
            deadline = datetime.fromisoformat(str(body.deadline).replace("Z", "+00:00"))
            if deadline <= datetime.now(timezone.utc):
                raise HTTPException(status_code=409, detail="Accepted task offer has expired")
        try:
            Draft202012Validator(definition.input_schema).validate(body.input)
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

        if body.offer_id is not None:
            app.state.active_invocations[body.offer_id] = body.model_dump(by_alias=True)
        if os.getenv("ALGORITHM4_EXPERIMENT_MODE", "false").lower() in {
            "1",
            "true",
            "yes",
        }:
            delay = max(0.0, min(float(os.getenv("ALGORITHM4_INVOKE_DELAY_SECONDS", "0")), 60.0))
            if delay:
                await asyncio.sleep(delay)

        request_id = body.request_id or str(uuid.uuid4())
        handler_input = body.input
        # Any research-capable Agent gets the local project evidence injected.
        # This used to be hardcoded to a single Kimi slug, which left every other
        # research Agent with no retrieval source at all.
        if knowledge_index is not None and definition.capability_id == "research":
            handler_input = json.loads(json.dumps(body.input))
            query = str(handler_input.get("objective", ""))
            evidence = knowledge_index.search(query, max_sections=4, max_characters=12_000)
            if evidence.get("content"):
                nested_input = handler_input.get("input")
                nested_input = dict(nested_input) if isinstance(nested_input, dict) else {}
                nested_input["projectEvidence"] = {
                    "content": evidence["content"],
                    "sources": evidence["sources"],
                }
                handler_input["input"] = nested_input
        try:
            output = await definition.handler(handler_input)
            Draft202012Validator(definition.output_schema).validate(output)
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
        output_hash = canonical_sha256(output)
        signed_payload = {
            "agentId": slug,
            "capabilityId": definition.capability_id,
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
        response = {
            **signed_payload,
            "output": output,
            "signature": {
                "algorithm": "ES256K",
                "verificationMethod": f"{identity.did}#operational-1",
                "publicKeyHex": identity.public_key_hex,
                "publicKeyMultibase": identity.public_key_multibase,
                "value": identity.sign(signed_payload),
            },
        }
        assert_canonical_sha256(response["output"], str(response["outputHash"]))
        if body.offer_id is not None:
            app.state.completed_offers[body.offer_id] = response
            app.state.accepted_offers.pop(body.offer_id, None)
            app.state.active_invocations.pop(body.offer_id, None)
        return response

    return app


app = build_app()
