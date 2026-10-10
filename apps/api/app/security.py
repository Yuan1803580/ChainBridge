from __future__ import annotations

import base64
import binascii
import hashlib
import os
import re
import secrets
from datetime import datetime, timedelta

import jwt
from cryptography.exceptions import InvalidSignature
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import ec
from fastapi import Depends, Header, HTTPException, Request, status
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from sqlalchemy import select
from sqlalchemy.orm import Session

from .database import get_session
from .models import Agent, AuthChallenge, utcnow


SECP256K1_ORDER = int("FFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFEBAAEDCE6AF48A03BBFD25E8CD0364141", 16)
bearer = HTTPBearer(auto_error=False)

_PROTOCOL_WORKER_PATHS = (
    re.compile(r"^/api/v1/protocol-jobs(?:/[^/]+)?$"),
    re.compile(r"^/api/v1/route-deployments/[^/]+/complete$"),
    re.compile(r"^/api/v1/workflows/[^/]+/nodes/[^/]+/attempts/[^/]+/(?:results|failures|result-proof-confirmations|settlement-confirmations)$"),
    re.compile(r"^/api/v1/workflows/[^/]+/(?:failed-refund-confirmations|finalization-confirmations)$"),
)


def _protocol_worker_path_allowed(request: Request) -> bool:
    return any(pattern.fullmatch(request.url.path) for pattern in _PROTOCOL_WORKER_PATHS)


def derive_demo_private_key(slug: str) -> ec.EllipticCurvePrivateKey:
    """Deterministically derive development-only keys without persisting key material."""
    value = int.from_bytes(hashlib.sha256(f"agent-market-demo:{slug}".encode()).digest(), "big")
    value = (value % (SECP256K1_ORDER - 1)) + 1
    return ec.derive_private_key(value, ec.SECP256K1())


def public_key_hex(private_key: ec.EllipticCurvePrivateKey) -> str:
    raw = private_key.public_key().public_bytes(
        serialization.Encoding.X962,
        serialization.PublicFormat.UncompressedPoint,
    )
    return "0x" + raw.hex()


def demo_evm_wallet(slug: str) -> str:
    from eth_account import Account

    value = derive_demo_private_key(slug).private_numbers().private_value
    return Account.from_key(value.to_bytes(32, "big")).address


def sign_demo_message(slug: str, message: str) -> str:
    signature = derive_demo_private_key(slug).sign(message.encode(), ec.ECDSA(hashes.SHA256()))
    return base64.urlsafe_b64encode(signature).decode().rstrip("=")


def verify_signature(public_key_value: str, message: str, signature: str) -> bool:
    try:
        normalized_public_key = public_key_value.removeprefix("es256k:").removeprefix("0x")
        public_bytes = bytes.fromhex(normalized_public_key)
        public_key = ec.EllipticCurvePublicKey.from_encoded_point(ec.SECP256K1(), public_bytes)
        normalized_signature = signature.removeprefix("0x")
        if normalized_signature and len(normalized_signature) % 2 == 0 and all(
            character in "0123456789abcdefABCDEF" for character in normalized_signature
        ):
            signature_bytes = bytes.fromhex(normalized_signature)
        else:
            padded = signature + "=" * (-len(signature) % 4)
            signature_bytes = base64.urlsafe_b64decode(padded)
        public_key.verify(signature_bytes, message.encode(), ec.ECDSA(hashes.SHA256()))
        return True
    except (ValueError, InvalidSignature, TypeError, binascii.Error):
        return False


def create_challenge(session: Session, did: str, audience: str, ttl_seconds: int) -> AuthChallenge:
    now = utcnow()
    nonce = secrets.token_urlsafe(24)
    expires_at = now + timedelta(seconds=ttl_seconds)
    message = "\n".join(
        [
            "Agent Market DID Authentication",
            f"DID: {did}",
            f"Nonce: {nonce}",
            f"Audience: {audience}",
            f"Issued At: {now.isoformat()}",
            f"Expiration Time: {expires_at.isoformat()}",
        ]
    )
    challenge = AuthChallenge(
        did=did,
        nonce=nonce,
        audience=audience,
        message=message,
        expires_at=expires_at,
    )
    session.add(challenge)
    session.commit()
    session.refresh(challenge)
    return challenge


def issue_token(agent: Agent, secret: str, ttl_seconds: int) -> tuple[str, datetime]:
    now = utcnow()
    expires_at = now + timedelta(seconds=ttl_seconds)
    token = jwt.encode(
        {
            "sub": agent.id,
            "did": agent.did,
            "roles": agent.roles,
            "iat": int(now.timestamp()),
            "exp": int(expires_at.timestamp()),
            "iss": "agent-market-api",
            "aud": "agent-market",
        },
        secret,
        algorithm="HS256",
    )
    return token, expires_at


def get_current_agent(
    request: Request,
    credentials: HTTPAuthorizationCredentials | None = Depends(bearer),
    session: Session = Depends(get_session),
) -> Agent:
    if credentials is None:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Authentication required")
    worker_token_hash = os.getenv("PROTOCOL_WORKER_API_TOKEN_SHA256", "").strip().lower()
    if worker_token_hash and _protocol_worker_path_allowed(request):
        provided_hash = hashlib.sha256(credentials.credentials.encode()).hexdigest()
        if secrets.compare_digest(provided_hash, worker_token_hash):
            admin_did = os.getenv("PLATFORM_ADMIN_DID", "did:local:platform-admin")
            worker = session.scalar(select(Agent).where(Agent.did == admin_did, Agent.status == "active"))
            if worker is None:
                raise HTTPException(status_code=status.HTTP_503_SERVICE_UNAVAILABLE, detail="Protocol Worker service identity is unavailable")
            return worker
    try:
        payload = jwt.decode(
            credentials.credentials,
            request.app.state.settings.app_secret,
            algorithms=["HS256"],
            audience="agent-market",
            issuer="agent-market-api",
        )
    except jwt.PyJWTError as exc:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Invalid or expired token") from exc
    agent = session.get(Agent, payload.get("sub"))
    if agent is None or agent.status != "active":
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Agent is not active")
    return agent


def require_role(*roles: str):
    def dependency(agent: Agent = Depends(get_current_agent)) -> Agent:
        if not set(roles).intersection(agent.roles):
            raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail=f"Role required: {', '.join(roles)}")
        return agent

    return dependency


def idempotency_header(idempotency_key: str | None = Header(default=None, alias="Idempotency-Key")) -> str | None:
    if idempotency_key is not None and not (8 <= len(idempotency_key) <= 255):
        raise HTTPException(status_code=422, detail="Idempotency-Key must contain 8 to 255 characters")
    return idempotency_key
