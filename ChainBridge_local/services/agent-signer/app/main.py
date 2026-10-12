from __future__ import annotations

import base64
import hashlib
import json
import os
import re
import secrets
import sqlite3
import threading
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Annotated, Any, Literal

import httpx
import rfc8785
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import ec
from eth_account import Account
from eth_account.messages import encode_defunct
from eth_utils import keccak, to_checksum_address
from fastapi import Depends, FastAPI, Header, HTTPException, Request, status
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from pydantic import BaseModel, ConfigDict, Field


PROTOCOL_VERSION = "2.0"
SECP256K1_ORDER = int(
    "FFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFEBAAEDCE6AF48A03BBFD25E8CD0364141", 16
)
RESULT_BASE_FIELDS = {
    "protocolVersion",
    "agentId",
    "capabilityId",
    "completedAt",
    "did",
    "outputHash",
    "requestId",
}
WORKFLOW_CONTEXT_FIELDS = {
    "workflowId",
    "nodeId",
    "attemptId",
    "attemptNumber",
    "reservationId",
    "deadline",
    "nonce",
    "offerId",
}
OFFER_ACCEPTANCE_FIELDS = {
    "protocolVersion",
    "accepted",
    "acceptedAt",
    "agentId",
    "attemptId",
    "did",
    "offerId",
}


def canonical_json(value: object) -> bytes:
    return rfc8785.dumps(value)


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


def parse_utc(value: str, field_name: str) -> datetime:
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=f"{field_name} is invalid") from exc
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed


def opaque_id_to_bytes32(value: str) -> str:
    compact = value.replace("-", "")
    if compact.startswith("0x") and len(compact) == 66:
        return compact.lower()
    if compact and len(compact) <= 64 and all(character in "0123456789abcdefABCDEF" for character in compact):
        return "0x" + compact.ljust(64, "0").lower()
    return "0x" + keccak(text=value).hex()


@dataclass(frozen=True)
class Settings:
    agent_slug: str
    chain_id: int
    rpc_url: str
    secrets_path: Path
    policy_path: Path
    database_path: Path
    api_token: str
    max_clock_skew_seconds: int = 30

    @classmethod
    def from_env(cls) -> "Settings":
        required = {
            name: os.getenv(name, "").strip()
            for name in (
                "SIGNER_AGENT_SLUG",
                "SIGNER_RPC_URL",
                "SIGNER_SECRETS_PATH",
                "SIGNER_POLICY_PATH",
                "SIGNER_API_TOKEN",
            )
        }
        missing = [name for name, value in required.items() if not value]
        if missing:
            raise RuntimeError(f"Missing Signer settings: {', '.join(missing)}")
        return cls(
            agent_slug=required["SIGNER_AGENT_SLUG"],
            chain_id=int(os.getenv("SIGNER_CHAIN_ID", "0")),
            rpc_url=required["SIGNER_RPC_URL"].rstrip("/"),
            secrets_path=Path(required["SIGNER_SECRETS_PATH"]).resolve(),
            policy_path=Path(required["SIGNER_POLICY_PATH"]).resolve(),
            database_path=Path(os.getenv("SIGNER_DATABASE_PATH", "./data/signer-audit.db")).resolve(),
            api_token=required["SIGNER_API_TOKEN"],
            max_clock_skew_seconds=max(0, int(os.getenv("SIGNER_MAX_CLOCK_SKEW_SECONDS", "30"))),
        )


@dataclass(frozen=True)
class SignerSecrets:
    identity_key: ec.EllipticCurvePrivateKey
    wallet: Any

    @classmethod
    def load(cls, path: Path, expected_slug: str) -> "SignerSecrets":
        raw = json.loads(path.read_text(encoding="utf-8-sig"))
        if not isinstance(raw, dict):
            raise ValueError("Signer secrets must be a JSON object")
        configured_slug = str(raw.get("agentSlug") or expected_slug)
        if configured_slug != expected_slug:
            raise ValueError("Signer secret file belongs to another Agent")
        identity_hex = str(raw.get("identityPrivateKey") or "").removeprefix("0x")
        wallet_hex = str(raw.get("walletPrivateKey") or "")
        if not identity_hex or not wallet_hex:
            raise ValueError("Signer secret file is incomplete")
        private_value = int(identity_hex, 16)
        if not 0 < private_value < SECP256K1_ORDER:
            raise ValueError("Agent identity private key is out of range")
        return cls(
            identity_key=ec.derive_private_key(private_value, ec.SECP256K1()),
            wallet=Account.from_key(wallet_hex),
        )

    @property
    def public_key_hex(self) -> str:
        value = self.identity_key.public_key().public_bytes(
            serialization.Encoding.X962,
            serialization.PublicFormat.UncompressedPoint,
        )
        return "0x" + value.hex()

    @property
    def public_key_multibase(self) -> str:
        value = self.identity_key.public_key().public_bytes(
            serialization.Encoding.X962,
            serialization.PublicFormat.CompressedPoint,
        )
        return "u" + base64.urlsafe_b64encode(value).rstrip(b"=").decode("ascii")

    def sign_payload(self, payload: object) -> str:
        signature = self.identity_key.sign(canonical_json(payload), ec.ECDSA(hashes.SHA256()))
        return base64.urlsafe_b64encode(signature).rstrip(b"=").decode("ascii")

    def sign_message(self, message: str) -> str:
        signature = self.identity_key.sign(message.encode("utf-8"), ec.ECDSA(hashes.SHA256()))
        return base64.urlsafe_b64encode(signature).rstrip(b"=").decode("ascii")


class AuditStore:
    def __init__(self, path: Path) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        self.connection = sqlite3.connect(path, check_same_thread=False)
        self.lock = threading.Lock()
        with self.connection:
            self.connection.execute("PRAGMA journal_mode=WAL")
            self.connection.execute("PRAGMA busy_timeout=5000")
            self.connection.executescript(
                """
                CREATE TABLE IF NOT EXISTS used_nonces (
                    nonce TEXT PRIMARY KEY,
                    intent_hash TEXT NOT NULL,
                    used_at TEXT NOT NULL
                );
                CREATE TABLE IF NOT EXISTS decisions (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    operation TEXT NOT NULL,
                    allowed INTEGER NOT NULL,
                    reason_code TEXT NOT NULL,
                    intent_hash TEXT NOT NULL,
                    created_at TEXT NOT NULL
                );
                """
            )

    def reserve_nonce(self, nonce: str, intent_hash: str) -> None:
        with self.lock, self.connection:
            try:
                self.connection.execute(
                    "INSERT INTO used_nonces(nonce,intent_hash,used_at) VALUES(?,?,?)",
                    (nonce, intent_hash, utc_now()),
                )
            except sqlite3.IntegrityError as exc:
                raise HTTPException(status_code=409, detail={
                    "code": "signer_nonce_replay",
                    "message": "Signing request nonce was already used",
                }) from exc

    def record(self, operation: str, allowed: bool, reason_code: str, intent_hash: str) -> None:
        with self.lock, self.connection:
            self.connection.execute(
                "INSERT INTO decisions(operation,allowed,reason_code,intent_hash,created_at) VALUES(?,?,?,?,?)",
                (operation, int(allowed), reason_code, intent_hash, utc_now()),
            )

    def metrics(self) -> dict[str, int]:
        with self.lock:
            rows = self.connection.execute(
                "SELECT allowed, COUNT(*) FROM decisions GROUP BY allowed"
            ).fetchall()
        values = {int(allowed): int(count) for allowed, count in rows}
        return {"allowed": values.get(1, 0), "rejected": values.get(0, 0)}


class PayloadSignRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    kind: Literal["result", "offer_acceptance"]
    payload: dict[str, Any]


class MessageSignRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    message: str = Field(min_length=16, max_length=10000)


class TransactionSignRequest(BaseModel):
    model_config = ConfigDict(populate_by_name=True, extra="forbid")

    agent_id: str = Field(alias="agentId", min_length=2, max_length=100)
    chain_id: int = Field(alias="chainId", ge=1)
    to: str = Field(pattern=r"^0x[a-fA-F0-9]{40}$")
    data: str = Field(pattern=r"^0x[a-fA-F0-9]{8,}$")
    value_wei: str = Field(default="0", alias="valueWei", pattern=r"^[0-9]+$")
    nonce: int = Field(ge=0)
    gas: int = Field(ge=21000, le=30000000)
    gas_price_wei: str = Field(alias="gasPriceWei", pattern=r"^[0-9]+$")
    attempt_id: str = Field(alias="attemptId", min_length=3, max_length=128)
    attempt_id_bytes32: str = Field(alias="attemptIdBytes32", pattern=r"^0x[a-fA-F0-9]{64}$")
    request_nonce: str = Field(alias="requestNonce", pattern=r"^0x[a-fA-F0-9]{64}$")
    expires_at: str = Field(alias="expiresAt")
    route_key: str | None = Field(default=None, alias="routeKey", max_length=128)


def load_policy(path: Path, agent_slug: str) -> dict[str, Any]:
    raw = json.loads(path.read_text(encoding="utf-8-sig"))
    if not isinstance(raw, dict):
        raise ValueError("Signer policy must be a JSON object")
    policy = raw.get(agent_slug, raw)
    if not isinstance(policy, dict):
        raise ValueError("Signer policy for Agent is missing")
    return policy


def intent_hash(value: object) -> str:
    return "sha256:" + hashlib.sha256(canonical_json(value)).hexdigest()


def build_app(settings: Settings | None = None) -> FastAPI:
    settings = settings or Settings.from_env()
    signer_secrets = SignerSecrets.load(settings.secrets_path, settings.agent_slug)
    policy = load_policy(settings.policy_path, settings.agent_slug)
    audit = AuditStore(settings.database_path)
    did = str(policy.get("did") or f"did:local:{settings.agent_slug}")

    if int(policy.get("chainId", settings.chain_id)) != settings.chain_id:
        raise ValueError("Signer policy chainId does not match configuration")
    configured_wallet = str(policy.get("homeWallet") or signer_secrets.wallet.address)
    if configured_wallet.lower() != signer_secrets.wallet.address.lower():
        raise ValueError("Signer wallet key does not match policy homeWallet")

    app = FastAPI(title="ChainBridge Agent Signer Sidecar", version="1.0.0")

    def require_access(authorization: Annotated[str | None, Header()] = None) -> None:
        expected = f"Bearer {settings.api_token}"
        if authorization is None or not secrets.compare_digest(authorization, expected):
            raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Missing or invalid Signer token")

    @app.exception_handler(HTTPException)
    async def http_error(request: Request, exc: HTTPException) -> JSONResponse:
        detail = exc.detail
        if isinstance(detail, dict):
            code = str(detail.get("code") or "signer_policy_rejected")
            message = str(detail.get("message") or code)
        else:
            code = "signer_policy_rejected" if exc.status_code in {409, 422} else "signer_request_failed"
            message = str(detail)
        return JSONResponse(status_code=exc.status_code, headers=exc.headers, content={
            "protocolVersion": PROTOCOL_VERSION,
            "error": {"code": code, "message": message, "retryable": False, "path": request.url.path},
        })

    @app.exception_handler(RequestValidationError)
    async def validation_error(request: Request, exc: RequestValidationError) -> JSONResponse:
        return JSONResponse(status_code=422, content={
            "protocolVersion": PROTOCOL_VERSION,
            "error": {
                "code": "signer_request_schema_invalid",
                "message": "Signing request does not match the policy contract",
                "retryable": False,
                "path": request.url.path,
                "fields": [{"location": list(item["loc"]), "message": item["msg"]} for item in exc.errors()],
            },
        })

    @app.get("/health")
    async def health() -> dict[str, object]:
        return {
            "status": "ok",
            "service": "chainbridge-agent-signer-sidecar",
            "protocolVersion": PROTOCOL_VERSION,
            "agentId": settings.agent_slug,
            "chainId": settings.chain_id,
            "wallet": signer_secrets.wallet.address,
            "metrics": audit.metrics(),
            "timestamp": utc_now(),
        }

    @app.get("/v1/public", dependencies=[Depends(require_access)])
    async def public_metadata() -> dict[str, object]:
        return {
            "agentId": settings.agent_slug,
            "did": did,
            "chainId": settings.chain_id,
            "wallet": signer_secrets.wallet.address,
            "publicKeyHex": signer_secrets.public_key_hex,
            "publicKeyMultibase": signer_secrets.public_key_multibase,
            "canonicalization": "RFC8785-JCS",
        }

    @app.post("/v1/sign/payload", dependencies=[Depends(require_access)])
    async def sign_payload(body: PayloadSignRequest) -> dict[str, object]:
        payload = body.payload
        fields = set(payload)
        allowed = RESULT_BASE_FIELDS | WORKFLOW_CONTEXT_FIELDS if body.kind == "result" else OFFER_ACCEPTANCE_FIELDS
        required = RESULT_BASE_FIELDS if body.kind == "result" else OFFER_ACCEPTANCE_FIELDS
        digest = intent_hash({"kind": body.kind, "payload": payload})
        if fields - allowed or not required.issubset(fields):
            audit.record("sign_payload", False, "payload_fields_rejected", digest)
            raise HTTPException(status_code=422, detail={"code": "signer_payload_fields_rejected", "message": "Signed payload fields are outside policy"})
        if payload.get("protocolVersion") != PROTOCOL_VERSION or payload.get("agentId") != settings.agent_slug or payload.get("did") != did:
            audit.record("sign_payload", False, "payload_identity_rejected", digest)
            raise HTTPException(status_code=422, detail={"code": "signer_payload_identity_rejected", "message": "Signed payload identity is outside policy"})
        if body.kind == "result":
            has_context = bool(fields & WORKFLOW_CONTEXT_FIELDS)
            if has_context != WORKFLOW_CONTEXT_FIELDS.issubset(fields):
                audit.record("sign_payload", False, "workflow_context_incomplete", digest)
                raise HTTPException(status_code=422, detail={"code": "signer_workflow_context_incomplete", "message": "Result workflow context is incomplete"})
            if not re.fullmatch(r"sha256:[a-f0-9]{64}", str(payload.get("outputHash") or "")):
                raise HTTPException(status_code=422, detail={"code": "signer_output_hash_invalid", "message": "Result outputHash is invalid"})
            if has_context and parse_utc(str(payload["deadline"]), "deadline") < datetime.now(timezone.utc):
                audit.record("sign_payload", False, "result_deadline_expired", digest)
                raise HTTPException(status_code=409, detail={"code": "signer_result_deadline_expired", "message": "Result deadline has expired"})
        audit.record("sign_payload", True, "allowed", digest)
        return {
            "algorithm": "ES256K",
            "verificationMethod": f"{did}#operational-1",
            "publicKeyHex": signer_secrets.public_key_hex,
            "publicKeyMultibase": signer_secrets.public_key_multibase,
            "value": signer_secrets.sign_payload(payload),
            "intentHash": digest,
        }

    @app.post("/v1/sign/identity-message", dependencies=[Depends(require_access)])
    async def sign_identity_message(body: MessageSignRequest) -> dict[str, str]:
        prefixes = ("Agent onboarding ownership challenge\n", "Agent heartbeat challenge\n")
        digest = intent_hash({"message": body.message})
        if not body.message.startswith(prefixes):
            audit.record("identity_message", False, "challenge_prefix_rejected", digest)
            raise HTTPException(status_code=422, detail={"code": "signer_challenge_rejected", "message": "Identity signing purpose is not allowed"})
        audit.record("identity_message", True, "allowed", digest)
        return {"algorithm": "ES256K", "did": did, "publicKeyHex": signer_secrets.public_key_hex, "signature": signer_secrets.sign_message(body.message)}

    @app.post("/v1/sign/wallet-message", dependencies=[Depends(require_access)])
    async def sign_wallet_message(body: MessageSignRequest) -> dict[str, str]:
        digest = intent_hash({"message": body.message})
        fields: dict[str, str] = {}
        lines = body.message.splitlines()
        for line in lines[1:]:
            key, separator, value = line.partition("=")
            if separator:
                fields[key] = value
        if (
            not lines
            or lines[0] != "Agent onboarding ownership challenge"
            or fields.get("agentKey") != settings.agent_slug
            or fields.get("did") != did
            or fields.get("homeWallet", "").lower() != signer_secrets.wallet.address.lower()
            or fields.get("chainId") != str(settings.chain_id)
            or not all(fields.get(key) for key in ("profileId", "nonce", "expiresAt"))
        ):
            audit.record("wallet_message", False, "wallet_challenge_rejected", digest)
            raise HTTPException(status_code=422, detail={"code": "signer_wallet_challenge_rejected", "message": "Wallet challenge is outside policy"})
        if parse_utc(fields["expiresAt"], "expiresAt") < datetime.now(timezone.utc):
            raise HTTPException(status_code=409, detail={"code": "signer_wallet_challenge_expired", "message": "Wallet challenge has expired"})
        signed = Account.sign_message(encode_defunct(text=body.message), signer_secrets.wallet.key)
        audit.record("wallet_message", True, "allowed", digest)
        return {"algorithm": "EIP-191", "address": signer_secrets.wallet.address, "signature": signed.signature.hex()}

    async def observed_nonces() -> tuple[int, int]:
        async with httpx.AsyncClient(timeout=5, follow_redirects=False) as client:
            async def call(block: str) -> int:
                response = await client.post(settings.rpc_url, json={
                    "jsonrpc": "2.0", "id": block, "method": "eth_getTransactionCount",
                    "params": [signer_secrets.wallet.address, block],
                })
                response.raise_for_status()
                value = response.json()
                if value.get("error"):
                    raise ValueError(str(value["error"]))
                return int(value["result"], 16)
            return await call("latest"), await call("pending")

    @app.post("/v1/sign/transaction", dependencies=[Depends(require_access)])
    async def sign_transaction(body: TransactionSignRequest) -> dict[str, object]:
        request = body.model_dump(by_alias=True)
        digest = intent_hash(request)

        def reject(code: str, message: str, status_code: int = 422) -> None:
            audit.record("transaction", False, code, digest)
            raise HTTPException(status_code=status_code, detail={"code": code, "message": message})

        if body.agent_id != settings.agent_slug or body.chain_id != settings.chain_id:
            reject("signer_transaction_identity_rejected", "Transaction Agent or chain is outside policy")
        expires_at = parse_utc(body.expires_at, "expiresAt")
        now = datetime.now(timezone.utc)
        if expires_at <= now:
            reject("signer_transaction_expired", "Transaction signing intent has expired", 409)
        if (expires_at - now).total_seconds() > int(policy.get("maxTtlSeconds", 300)):
            reject("signer_transaction_ttl_exceeded", "Transaction signing TTL exceeds policy")
        expected_attempt = opaque_id_to_bytes32(body.attempt_id)
        if body.attempt_id_bytes32.lower() != expected_attempt or expected_attempt[2:] not in body.data[10:].lower():
            reject("signer_attempt_binding_rejected", "Transaction calldata is not bound to attemptId")
        allowed_contracts = policy.get("allowedContracts")
        allowed_contracts = allowed_contracts if isinstance(allowed_contracts, dict) else {}
        target_policy = allowed_contracts.get(body.to.lower())
        if not isinstance(target_policy, dict):
            reject("signer_contract_rejected", "Transaction target is outside policy")
        selectors = {str(item).lower() for item in target_policy.get("selectors", [])}
        if body.data[:10].lower() not in selectors:
            reject("signer_selector_rejected", "Transaction function selector is outside policy")
        allowed_routes = {str(item) for item in policy.get("allowedRouteKeys", [])}
        if allowed_routes and body.route_key not in allowed_routes:
            reject("signer_route_rejected", "Transaction Route is outside policy")
        if int(body.value_wei) > int(policy.get("maxValueWei", "0")):
            reject("signer_value_exceeded", "Transaction native value exceeds policy")
        if body.gas > int(policy.get("maxGas", 500000)):
            reject("signer_gas_exceeded", "Transaction Gas exceeds policy")
        if int(body.gas_price_wei) > int(policy.get("maxGasPriceWei", "100000000000")):
            reject("signer_gas_price_exceeded", "Transaction Gas price exceeds policy")
        latest_nonce, pending_nonce = await observed_nonces()
        if body.nonce < latest_nonce or body.nonce > pending_nonce:
            reject("signer_wallet_nonce_rejected", "Transaction nonce does not match the wallet RPC state", 409)
        audit.reserve_nonce(body.request_nonce, digest)
        transaction = {
            "chainId": body.chain_id,
            "to": to_checksum_address(body.to),
            "data": body.data,
            "value": int(body.value_wei),
            "nonce": body.nonce,
            "gas": body.gas,
            "gasPrice": int(body.gas_price_wei),
        }
        signed = signer_secrets.wallet.sign_transaction(transaction)
        raw_transaction = signed.raw_transaction.hex()
        if not raw_transaction.startswith("0x"):
            raw_transaction = "0x" + raw_transaction
        transaction_hash = signed.hash.hex()
        if not transaction_hash.startswith("0x"):
            transaction_hash = "0x" + transaction_hash
        audit.record("transaction", True, "allowed", digest)
        return {
            "agentId": settings.agent_slug,
            "attemptId": body.attempt_id,
            "chainId": body.chain_id,
            "signer": signer_secrets.wallet.address,
            "transactionHash": transaction_hash,
            "rawTransaction": raw_transaction,
            "requestNonce": body.request_nonce,
            "intentHash": digest,
        }

    return app


if os.getenv("SIGNER_AGENT_SLUG", "").strip():
    app = build_app()
else:
    app = FastAPI(title="Unconfigured ChainBridge Agent Signer Sidecar")

    @app.get("/health")
    async def unconfigured_health() -> JSONResponse:
        return JSONResponse(
            status_code=503,
            content={
                "status": "unconfigured",
                "service": "chainbridge-agent-signer-sidecar",
                "protocolVersion": PROTOCOL_VERSION,
            },
        )
