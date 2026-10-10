from __future__ import annotations

import base64
import hashlib
import ipaddress
import json
import os
import re
import secrets
from datetime import datetime, timezone
from typing import Literal

from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import ec
from eth_account.messages import encode_defunct
from eth_utils import keccak, to_checksum_address
from fastapi import FastAPI, Header, HTTPException, Request
from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy import select, text
from sqlalchemy.exc import IntegrityError

from app.database import Base, build_engine, session_factory
from app.models import CanonicalResult, NonceReservation, SignedTransaction, WalletNonce, utcnow
from app.security import (
    AgentSecrets,
    ProvisioningConflict,
    canonical_json,
    load_agents,
    provision_agent_files,
    sha256_hex,
    storage_cipher,
    verify_worker_signature,
)


BYTES32 = r"^0x[a-fA-F0-9]{64}$"
ADDRESS = r"^0x[a-fA-F0-9]{40}$"
SUBMIT_RESULT_SELECTOR = "0x" + keccak(text="submitResult(bytes32,bytes32,bytes32,bytes32)")[:4].hex()


class AgentRequest(BaseModel):
    model_config = ConfigDict(populate_by_name=True)
    runtime_id: str = Field(alias="runtimeId", min_length=3, max_length=128)
    did: str = Field(min_length=8, max_length=255)


class ProvisionAgentRequest(BaseModel):
    model_config = ConfigDict(populate_by_name=True, extra="forbid")

    runtime_id: str = Field(alias="runtimeId", pattern=r"^[A-Za-z0-9][A-Za-z0-9._-]{2,127}$")
    agent_slug: str = Field(alias="agentSlug", pattern=r"^[a-z0-9][a-z0-9-]{1,79}$")
    did: str = Field(min_length=8, max_length=255)
    home_chain_id: int = Field(alias="homeChainId", ge=1)
    route_key: str = Field(alias="routeKey", pattern=r"^[A-Z][A-Z0-9_-]{2,79}$")
    route_version: int = Field(alias="routeVersion", ge=1)
    allowed_contracts: list[str] = Field(alias="allowedContracts", min_length=1, max_length=10)


class IdentityMessageRequest(AgentRequest):
    message: str = Field(min_length=16, max_length=10000)


class CanonicalResultRequest(AgentRequest):
    workflow_id: str = Field(alias="workflowId", min_length=3, max_length=128)
    attempt_id: str = Field(alias="attemptId", min_length=3, max_length=128)
    result_message_id: str = Field(alias="resultMessageId", pattern=BYTES32)
    result_hash: str = Field(alias="resultHash", pattern=BYTES32)
    proof_hash: str = Field(alias="proofHash", pattern=BYTES32)
    route_key: str = Field(alias="routeKey", min_length=3, max_length=80)
    route_version: int = Field(alias="routeVersion", ge=1)


class RuntimeResultRequest(AgentRequest):
    payload: dict[str, object]


class Eip191Request(AgentRequest):
    message: str = Field(min_length=16, max_length=10000)


class TransactionIntent(AgentRequest):
    model_config = ConfigDict(populate_by_name=True, extra="forbid")

    intent_id: str = Field(alias="intentId", min_length=8, max_length=128)
    request_id: str = Field(alias="requestId", min_length=8, max_length=128)
    purpose: Literal["submit_result", "replace", "cancel"]
    chain_id: int = Field(alias="chainId", ge=1)
    to: str = Field(pattern=ADDRESS)
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
    replaces_request_id: str | None = Field(default=None, alias="replacesRequestId", min_length=8, max_length=128)
    original_status: Literal["PENDING", "DROPPED"] | None = Field(default=None, alias="originalStatus")


class ChainStatusRequest(AgentRequest):
    model_config = ConfigDict(populate_by_name=True, extra="forbid")

    status: Literal["BROADCAST", "CONFIRMED", "DROPPED", "REVERTED", "NONCE_CONFLICT"]
    transaction_hash: str = Field(alias="transactionHash", pattern=BYTES32)
    observed_pending_nonce: int = Field(alias="observedPendingNonce", ge=0)
    observed_block_number: int = Field(alias="observedBlockNumber", ge=0)
    observed_at: datetime = Field(alias="observedAt")


def _signature(key, payload: object) -> str:
    value = key.sign(canonical_json(payload), ec.ECDSA(hashes.SHA256()))
    return base64.urlsafe_b64encode(value).rstrip(b"=").decode()


def _public_key_hex(key: ec.EllipticCurvePrivateKey) -> str:
    value = key.public_key().public_bytes(
        serialization.Encoding.X962,
        serialization.PublicFormat.UncompressedPoint,
    )
    return "0x" + value.hex()


def _aware(value: datetime) -> datetime:
    return value if value.tzinfo else value.replace(tzinfo=timezone.utc)


def _decode_submit_result(data: str) -> tuple[str, str, str, str]:
    if data[:10].lower() != SUBMIT_RESULT_SELECTOR or len(data) != 10 + 64 * 4:
        raise ValueError("Transaction is not the exact submitResult(bytes32,bytes32,bytes32,bytes32) call")
    values = tuple("0x" + data[10 + index * 64:10 + (index + 1) * 64].lower() for index in range(4))
    return values  # type: ignore[return-value]


def _hex0x(value: bytes) -> str:
    encoded = value.hex()
    return encoded if encoded.startswith("0x") else "0x" + encoded


def build_app() -> FastAPI:
    app = FastAPI(title="OpenClaw Agent Signer Sidecar", version="1.0.0")
    engine = build_engine()
    if os.getenv("APP_ENV", "development").lower() == "test":
        Base.metadata.create_all(engine)
    app.state.engine = engine
    app.state.sessions = session_factory(engine)
    app.state.agents = load_agents()
    app.state.cipher = storage_cipher()

    def agent_access(body: AgentRequest, authorization: str | None) -> AgentSecrets:
        # The deployment-generated RemoteWorkBoard address is added to the local
        # policy after route deployment, so reload the operator-owned file without
        # requiring a signing-service restart.
        app.state.agents = load_agents()
        agent = app.state.agents.get(body.runtime_id)
        token = authorization.removeprefix("Bearer ").strip() if authorization else ""
        if not agent or agent.did != body.did or not token or not secrets.compare_digest(sha256_hex(token), agent.credential_sha256):
            raise HTTPException(status_code=401, detail="Agent signer credential is invalid")
        return agent

    def require_scope(agent: AgentSecrets, scope: str) -> None:
        if scope not in agent.scopes:
            raise HTTPException(status_code=403, detail=f"Signer credential lacks {scope} scope")

    @app.post("/v1/provision", status_code=201)
    def provision_agent(
        body: ProvisionAgentRequest,
        request: Request,
        authorization: str | None = Header(default=None, alias="Authorization"),
    ) -> dict[str, object]:
        client_host = request.client.host if request.client else ""
        if os.getenv("APP_ENV", "development").lower() == "test" and client_host == "testclient":
            client_host = "127.0.0.1"
        try:
            if not ipaddress.ip_address(client_host).is_loopback:
                raise HTTPException(status_code=403, detail="Provisioning is available only over loopback")
        except ValueError as exc:
            raise HTTPException(status_code=403, detail="Provisioning is available only over loopback") from exc
        expected = os.getenv("SIDECAR_PROVISIONING_TOKEN", "")
        supplied = authorization.removeprefix("Bearer ").strip() if authorization else ""
        if not expected:
            raise HTTPException(status_code=503, detail="Sidecar provisioning is not configured")
        if not supplied or not secrets.compare_digest(supplied, expected):
            raise HTTPException(status_code=401, detail="Sidecar provisioning credential is invalid")
        normalized_contracts = [value.lower() for value in body.allowed_contracts]
        if any(not re.fullmatch(ADDRESS, value) for value in normalized_contracts):
            raise HTTPException(status_code=422, detail="allowedContracts contains an invalid EVM address")
        try:
            result = provision_agent_files(
                runtime_id=body.runtime_id,
                agent_slug=body.agent_slug,
                did=body.did,
                home_chain_id=body.home_chain_id,
                route_key=body.route_key,
                route_version=body.route_version,
                allowed_contracts=normalized_contracts,
            )
        except ProvisioningConflict as exc:
            raise HTTPException(status_code=409, detail=str(exc)) from exc
        except (OSError, ValueError, TimeoutError, json.JSONDecodeError) as exc:
            raise HTTPException(status_code=503, detail=f"Sidecar provisioning failed: {exc}") from exc
        app.state.agents = load_agents()
        return result

    @app.get("/health")
    def health() -> dict[str, object]:
        app.state.agents = load_agents()
        return {"status": "ok", "rpcConnected": False, "agents": len(app.state.agents)}

    @app.post("/v1/sign/identity-message")
    def sign_identity_message(
        body: IdentityMessageRequest,
        authorization: str | None = Header(default=None, alias="Authorization"),
    ) -> dict[str, str]:
        agent = agent_access(body, authorization)
        require_scope(agent, "identity:sign")
        allowed = (
            "Agent onboarding ownership challenge\n",
            "Agent heartbeat challenge\n",
            "Agent Market DID Authentication\n",
        )
        if not body.message.startswith(allowed):
            raise HTTPException(status_code=422, detail="Unsupported identity signing purpose")
        value = agent.identity_key().sign(body.message.encode("utf-8"), ec.ECDSA(hashes.SHA256()))
        return {"did": agent.did, "algorithm": "ES256K", "signature": base64.urlsafe_b64encode(value).rstrip(b"=").decode()}

    @app.post("/v1/sign/canonical-result")
    def sign_canonical_result(
        body: CanonicalResultRequest,
        authorization: str | None = Header(default=None, alias="Authorization"),
    ) -> dict[str, str]:
        agent = agent_access(body, authorization)
        require_scope(agent, "result:sign")
        if body.route_key != agent.route_key or body.route_version != agent.route_version:
            raise HTTPException(status_code=422, detail="Canonical result route is outside signer policy")
        payload = body.model_dump(by_alias=True)
        signature = _signature(agent.identity_key(), payload)
        with app.state.sessions() as session:
            existing = session.scalar(select(CanonicalResult).where(
                CanonicalResult.runtime_id == body.runtime_id,
                CanonicalResult.attempt_id == body.attempt_id,
            ))
            if existing:
                if existing.payload != payload:
                    raise HTTPException(status_code=409, detail="attemptId is already bound to another canonical result")
                return {"did": agent.did, "algorithm": "ES256K", "publicKeyHex": _public_key_hex(agent.identity_key()), "signature": existing.signature}
            session.add(CanonicalResult(
                runtime_id=body.runtime_id,
                workflow_id=body.workflow_id,
                attempt_id=body.attempt_id,
                result_message_id=body.result_message_id.lower(),
                result_hash=body.result_hash.lower(),
                proof_hash=body.proof_hash.lower(),
                route_key=body.route_key,
                route_version=body.route_version,
                payload=payload,
                signature=signature,
            ))
            session.commit()
        return {"did": agent.did, "algorithm": "ES256K", "publicKeyHex": _public_key_hex(agent.identity_key()), "signature": signature}

    @app.post("/v1/sign/runtime-result")
    def sign_runtime_result(
        body: RuntimeResultRequest,
        authorization: str | None = Header(default=None, alias="Authorization"),
    ) -> dict[str, str]:
        agent = agent_access(body, authorization)
        require_scope(agent, "result:sign")
        required = {"agentId", "capabilityId", "completedAt", "did", "outputHash", "requestId"}
        allowed = required | {"workflowId", "nodeId", "attemptId", "attemptNumber", "reservationId", "deadline", "nonce", "offerId"}
        if set(body.payload) - allowed or not required.issubset(body.payload):
            raise HTTPException(status_code=422, detail="Runtime result contains unsupported signed fields")
        if body.payload.get("did") != agent.did or body.payload.get("agentId") != agent.agent_slug:
            raise HTTPException(status_code=422, detail="Runtime result identity binding is invalid")
        output_hash = str(body.payload.get("outputHash", ""))
        if not output_hash.startswith("sha256:") or len(output_hash) != 71:
            raise HTTPException(status_code=422, detail="Runtime result outputHash is invalid")
        return {"did": agent.did, "algorithm": "ES256K", "signature": _signature(agent.identity_key(), body.payload)}

    @app.post("/v1/sign/eip191")
    def sign_eip191(
        body: Eip191Request,
        authorization: str | None = Header(default=None, alias="Authorization"),
    ) -> dict[str, str]:
        agent = agent_access(body, authorization)
        require_scope(agent, "wallet:sign")
        allowed = ("Agent onboarding ownership challenge\n", "Agent heartbeat challenge\n")
        if not body.message.startswith(allowed):
            raise HTTPException(status_code=422, detail="Unsupported EIP-191 signing purpose")
        signed = agent.wallet_account().sign_message(encode_defunct(text=body.message))
        return {"wallet": agent.home_wallet, "signature": "0x" + signed.signature.hex()}

    @app.post("/v1/sign/transaction")
    def sign_transaction(
        raw_body: dict[str, object],
        authorization: str | None = Header(default=None, alias="Authorization"),
        worker_signature: str | None = Header(default=None, alias="X-Worker-Intent-Signature"),
    ) -> dict[str, object]:
        try:
            body = TransactionIntent.model_validate(raw_body)
        except ValueError as exc:
            raise HTTPException(status_code=422, detail=f"Invalid transaction intent: {exc}") from exc
        agent = agent_access(body, authorization)
        require_scope(agent, "wallet:sign")
        payload = dict(raw_body)
        try:
            verify_worker_signature(payload, worker_signature)
        except PermissionError as exc:
            raise HTTPException(status_code=401, detail=str(exc)) from exc
        except ValueError as exc:
            raise HTTPException(status_code=503, detail=str(exc)) from exc
        session = app.state.sessions()
        try:
            if app.state.engine.dialect.name == "sqlite":
                session.execute(text("BEGIN IMMEDIATE"))
            existing = session.scalar(select(SignedTransaction).where(SignedTransaction.request_id == body.request_id))
            if existing:
                if existing.runtime_id != body.runtime_id or existing.intent != payload:
                    raise HTTPException(status_code=409, detail="requestId is already bound to a different transaction intent")
                raw = app.state.cipher.decrypt(existing.raw_transaction_encrypted.encode()).decode()
                reservation = session.get(NonceReservation, existing.reservation_id)
                session.rollback()
                return {
                    "requestId": body.request_id,
                    "assignedNonce": reservation.nonce,
                    "rawTransaction": raw,
                    "transactionHash": existing.transaction_hash,
                    "signer": agent.home_wallet,
                    "status": existing.status,
                }

            now = datetime.now(timezone.utc)
            if _aware(body.expires_at) <= now or abs((now - _aware(body.observed_at)).total_seconds()) > 120:
                raise HTTPException(status_code=409, detail="Transaction intent or nonce observation is stale")
            if body.route_key != agent.route_key or body.route_version != agent.route_version:
                raise HTTPException(status_code=422, detail="Transaction route is outside signer policy")
            if body.chain_id != agent.home_chain_id:
                raise HTTPException(status_code=422, detail="Transaction chain is outside signer policy")
            if body.to.lower() not in agent.allowed_contracts and body.purpose != "cancel":
                raise HTTPException(status_code=422, detail="Transaction target is outside signer policy")
            max_fee = int(body.max_fee_per_gas)
            priority_fee = int(body.max_priority_fee_per_gas)
            if max_fee < priority_fee or max_fee > agent.max_fee_per_gas or priority_fee > agent.max_priority_fee_per_gas:
                raise HTTPException(status_code=422, detail="EIP-1559 fee exceeds signer policy")
            if body.gas_limit > agent.max_gas_limit or body.gas_limit * max_fee > agent.max_total_fee_wei:
                raise HTTPException(status_code=422, detail="Maximum transaction cost exceeds signer policy")

            original: SignedTransaction | None = None
            if body.purpose == "submit_result":
                result_message_id, attempt_id, result_hash, proof_hash = _decode_submit_result(body.data)
                canonical = session.scalar(select(CanonicalResult).where(
                    CanonicalResult.runtime_id == body.runtime_id,
                    CanonicalResult.attempt_id == attempt_id,
                ))
                if not canonical or (
                    canonical.result_message_id != result_message_id
                    or canonical.result_hash != result_hash
                    or canonical.proof_hash != proof_hash
                ):
                    raise HTTPException(status_code=422, detail="submitResult calldata does not match the signed canonical result")
                wallet_nonce = session.scalar(select(WalletNonce).where(
                    WalletNonce.chain_id == body.chain_id,
                    WalletNonce.wallet == agent.home_wallet.lower(),
                ))
                assigned_nonce = max(body.observed_pending_nonce, wallet_nonce.next_nonce if wallet_nonce else 0)
                if wallet_nonce:
                    wallet_nonce.next_nonce = assigned_nonce + 1
                else:
                    session.add(WalletNonce(chain_id=body.chain_id, wallet=agent.home_wallet.lower(), next_nonce=assigned_nonce + 1))
                reservation = NonceReservation(
                    chain_id=body.chain_id,
                    wallet=agent.home_wallet.lower(),
                    nonce=assigned_nonce,
                    original_request_id=body.request_id,
                )
                session.add(reservation)
                session.flush()
            else:
                if not body.replaces_request_id or body.original_status not in {"PENDING", "DROPPED"}:
                    raise HTTPException(status_code=422, detail="replace/cancel requires a pending original request")
                original = session.scalar(select(SignedTransaction).where(
                    SignedTransaction.request_id == body.replaces_request_id,
                    SignedTransaction.runtime_id == body.runtime_id,
                ))
                if not original or original.status == "CONFIRMED":
                    raise HTTPException(status_code=409, detail="Original transaction is missing or already confirmed")
                reservation = session.get(NonceReservation, original.reservation_id)
                assigned_nonce = reservation.nonce
                original_intent = original.intent
                old_max_fee = int(original_intent["maxFeePerGas"])
                old_priority = int(original_intent["maxPriorityFeePerGas"])
                if max_fee * 10000 < old_max_fee * 11250 or priority_fee * 10000 < old_priority * 11250:
                    raise HTTPException(status_code=422, detail="Replacement fee bump must be at least 12.5 percent")
                if body.purpose == "replace":
                    for field in ("chainId", "to", "data", "value", "gasLimit"):
                        if payload[field] != original_intent[field]:
                            raise HTTPException(status_code=422, detail=f"Replacement may not change {field}")
                elif not (body.to.lower() == agent.home_wallet.lower() and body.data == "0x" and body.value == "0"):
                    raise HTTPException(status_code=422, detail="Cancel must be a zero-value self transaction")

            transaction = {
                "type": 2,
                "chainId": body.chain_id,
                "to": to_checksum_address(body.to),
                "data": body.data,
                "value": int(body.value),
                "nonce": assigned_nonce,
                "gas": body.gas_limit,
                "maxFeePerGas": max_fee,
                "maxPriorityFeePerGas": priority_fee,
            }
            signed = agent.wallet_account().sign_transaction(transaction)
            raw = _hex0x(signed.raw_transaction)
            tx_hash = _hex0x(signed.hash)
            row = SignedTransaction(
                request_id=body.request_id,
                runtime_id=body.runtime_id,
                reservation_id=reservation.id,
                purpose=body.purpose,
                replaces_request_id=body.replaces_request_id,
                intent=payload,
                raw_transaction_encrypted=app.state.cipher.encrypt(raw.encode()).decode(),
                transaction_hash=tx_hash,
                status="SIGNED",
                status_evidence={},
            )
            session.add(row)
            session.commit()
            return {"requestId": body.request_id, "assignedNonce": assigned_nonce, "rawTransaction": raw, "transactionHash": tx_hash, "signer": agent.home_wallet, "status": "SIGNED"}
        except HTTPException:
            session.rollback()
            raise
        except IntegrityError as exc:
            session.rollback()
            raise HTTPException(status_code=409, detail="Nonce or requestId was already reserved") from exc
        finally:
            session.close()

    def update_status(
        request_id: str,
        body: ChainStatusRequest,
        payload: dict[str, object],
        signature: str | None,
        agent: AgentSecrets,
    ) -> dict[str, str]:
        require_scope(agent, "status:write")
        try:
            verify_worker_signature(payload, signature)
        except PermissionError as exc:
            raise HTTPException(status_code=401, detail=str(exc)) from exc
        with app.state.sessions() as session:
            transaction = session.scalar(select(SignedTransaction).where(
                SignedTransaction.request_id == request_id,
                SignedTransaction.runtime_id == body.runtime_id,
            ))
            if not transaction or transaction.transaction_hash.lower() != body.transaction_hash.lower():
                raise HTTPException(status_code=404, detail="Signed transaction was not found")
            if transaction.status == "CONFIRMED" and body.status != "CONFIRMED":
                raise HTTPException(status_code=409, detail="Confirmed transaction status is final")
            transaction.status = body.status
            transaction.broadcast_tx_hash = body.transaction_hash.lower()
            transaction.status_evidence = payload
            transaction.updated_at = utcnow()
            session.commit()
        return {"requestId": request_id, "status": body.status}

    @app.post("/v1/transactions/{request_id}/broadcast-confirmation")
    def broadcast_confirmation(
        request_id: str,
        raw_body: dict[str, object],
        authorization: str | None = Header(default=None, alias="Authorization"),
        worker_signature: str | None = Header(default=None, alias="X-Worker-Intent-Signature"),
    ) -> dict[str, str]:
        try:
            body = ChainStatusRequest.model_validate(raw_body)
        except ValueError as exc:
            raise HTTPException(status_code=422, detail=f"Invalid chain status: {exc}") from exc
        agent = agent_access(body, authorization)
        if body.status != "BROADCAST":
            raise HTTPException(status_code=422, detail="Broadcast confirmation status must be BROADCAST")
        return update_status(request_id, body, dict(raw_body), worker_signature, agent)

    @app.post("/v1/transactions/{request_id}/chain-status")
    def chain_status(
        request_id: str,
        raw_body: dict[str, object],
        authorization: str | None = Header(default=None, alias="Authorization"),
        worker_signature: str | None = Header(default=None, alias="X-Worker-Intent-Signature"),
    ) -> dict[str, str]:
        try:
            body = ChainStatusRequest.model_validate(raw_body)
        except ValueError as exc:
            raise HTTPException(status_code=422, detail=f"Invalid chain status: {exc}") from exc
        agent = agent_access(body, authorization)
        return update_status(request_id, body, dict(raw_body), worker_signature, agent)

    return app


app = build_app()
