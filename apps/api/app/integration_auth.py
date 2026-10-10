from __future__ import annotations

import hashlib
import hmac
import time
from datetime import timedelta

from fastapi import HTTPException, Request
from sqlalchemy import delete
from sqlalchemy.exc import IntegrityError

from .models import IntegrationRequestNonce, utcnow


def listener_signature(
    secret: str,
    method: str,
    timestamp: str,
    nonce: str,
    path: str,
    body: bytes,
) -> str:
    payload = b"\n".join((method.upper().encode(), timestamp.encode(), nonce.encode(), path.encode(), body))
    return "sha256=" + hmac.new(secret.encode(), payload, hashlib.sha256).hexdigest()


async def require_listener_auth(request: Request) -> str:
    settings = request.app.state.settings
    timestamp = request.headers.get("X-Listener-Timestamp", "")
    signature = request.headers.get("X-Listener-Signature", "")
    listener_id = request.headers.get("X-Listener-Id", "")
    nonce = request.headers.get("X-Listener-Nonce", "")
    if not listener_id or not timestamp or not nonce or not signature:
        raise HTTPException(status_code=401, detail="Listener HMAC headers are required")
    try:
        timestamp_value = int(timestamp)
    except ValueError as exc:
        raise HTTPException(status_code=401, detail="Invalid listener timestamp") from exc
    if abs(int(time.time()) - timestamp_value) > settings.listener_signature_window_seconds:
        raise HTTPException(status_code=401, detail="Listener signature has expired")
    configured = dict(settings.listener_hmac_keys)
    if configured:
        secret = configured.get(listener_id)
        if secret is None:
            raise HTTPException(status_code=401, detail="Unknown listener identity")
    elif settings.app_env in {"development", "test"}:
        secret = settings.listener_hmac_secret
    else:
        raise HTTPException(status_code=503, detail="Listener identities are not configured")
    body = await request.body()
    expected = listener_signature(secret, request.method, timestamp, nonce, request.url.path, body)
    if not hmac.compare_digest(signature, expected):
        raise HTTPException(status_code=401, detail="Invalid listener signature")
    expires_at = utcnow() + timedelta(seconds=settings.listener_signature_window_seconds)
    with request.app.state.session_factory() as session:
        session.execute(delete(IntegrationRequestNonce).where(IntegrationRequestNonce.expires_at < utcnow()))
        session.add(IntegrationRequestNonce(nonce=nonce, listener_id=listener_id, expires_at=expires_at))
        try:
            session.commit()
        except IntegrityError as exc:
            session.rollback()
            raise HTTPException(status_code=409, detail="Listener request nonce was already used") from exc
    return listener_id
