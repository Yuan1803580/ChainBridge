from __future__ import annotations

import hashlib
import hmac
import json
import time
from collections.abc import Callable, Mapping
from dataclasses import dataclass
from typing import Any, Protocol

from agent_market_sdk.errors import MarketplaceSDKError


class InvalidWebhookError(MarketplaceSDKError):
    """Raised for malformed, forged, expired, or replayed webhook messages."""


@dataclass(frozen=True)
class WebhookEvent:
    event_id: str
    event_type: str
    created_at: str
    data: dict[str, Any]
    raw: dict[str, Any]


class ReplayStore(Protocol):
    def contains(self, event_id: str) -> bool: ...

    def add(self, event_id: str) -> None: ...


class InMemoryReplayStore:
    def __init__(self) -> None:
        self._event_ids: set[str] = set()

    def contains(self, event_id: str) -> bool:
        return event_id in self._event_ids

    def add(self, event_id: str) -> None:
        self._event_ids.add(event_id)


class WebhookVerifier:
    def __init__(self, secret: str | bytes, *, tolerance_seconds: int = 300) -> None:
        if not secret:
            raise ValueError("Webhook secret must not be empty")
        if tolerance_seconds < 0:
            raise ValueError("Webhook tolerance must not be negative")
        self._secret = secret.encode("utf-8") if isinstance(secret, str) else secret
        self._tolerance = tolerance_seconds

    def sign(self, body: bytes, *, timestamp: int | None = None) -> str:
        issued_at = int(time.time()) if timestamp is None else timestamp
        signed = str(issued_at).encode("ascii") + b"." + body
        digest = hmac.new(self._secret, signed, hashlib.sha256).hexdigest()
        return f"t={issued_at},v1={digest}"

    def verify(
        self,
        body: bytes,
        signature_header: str,
        *,
        now: int | None = None,
    ) -> None:
        parts: dict[str, list[str]] = {}
        for item in signature_header.split(","):
            key, separator, value = item.strip().partition("=")
            if separator and key and value:
                parts.setdefault(key, []).append(value)
        try:
            timestamp = int(parts["t"][0])
            signatures = parts["v1"]
        except (KeyError, ValueError, IndexError) as exc:
            raise InvalidWebhookError("Malformed webhook signature header") from exc

        current = int(time.time()) if now is None else now
        if abs(current - timestamp) > self._tolerance:
            raise InvalidWebhookError(
                "Webhook timestamp is outside the allowed tolerance"
            )

        signed = str(timestamp).encode("ascii") + b"." + body
        expected = hmac.new(self._secret, signed, hashlib.sha256).hexdigest()
        if not any(
            hmac.compare_digest(expected, candidate) for candidate in signatures
        ):
            raise InvalidWebhookError("Webhook signature does not match")


WebhookHandler = Callable[[WebhookEvent], object]


class WebhookConsumer:
    def __init__(
        self,
        verifier: WebhookVerifier,
        *,
        handlers: Mapping[str, WebhookHandler] | None = None,
        replay_store: ReplayStore | None = None,
    ) -> None:
        self._verifier = verifier
        self._handlers = dict(handlers or {})
        self._replay_store = replay_store or InMemoryReplayStore()

    def consume(
        self,
        body: bytes,
        signature_header: str,
        *,
        now: int | None = None,
    ) -> WebhookEvent:
        self._verifier.verify(body, signature_header, now=now)
        try:
            payload = json.loads(body)
        except (UnicodeDecodeError, json.JSONDecodeError) as exc:
            raise InvalidWebhookError("Webhook body is not valid JSON") from exc
        if not isinstance(payload, dict):
            raise InvalidWebhookError("Webhook body must be a JSON object")

        event_id = payload.get("eventId")
        event_type = payload.get("type")
        created_at = payload.get("createdAt")
        data = payload.get("data", {})
        if not all(
            isinstance(value, str) and value
            for value in (event_id, event_type, created_at)
        ):
            raise InvalidWebhookError("Webhook is missing eventId, type, or createdAt")
        if not isinstance(data, dict):
            raise InvalidWebhookError("Webhook data must be a JSON object")
        if self._replay_store.contains(event_id):
            raise InvalidWebhookError("Webhook event has already been consumed")

        event = WebhookEvent(event_id, event_type, created_at, data, payload)
        handler = self._handlers.get(event_type)
        if handler:
            handler(event)
        self._replay_store.add(event_id)
        return event
