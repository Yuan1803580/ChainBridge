from __future__ import annotations

import json

import pytest

from agent_market_sdk import InvalidWebhookError, WebhookConsumer, WebhookVerifier


def test_verifies_and_dispatches_webhook_once() -> None:
    timestamp = 1_800_000_000
    body = json.dumps(
        {
            "eventId": "event-1",
            "type": "order.delivered",
            "createdAt": "2027-01-15T08:00:00Z",
            "data": {"orderId": "order-1"},
        },
        separators=(",", ":"),
    ).encode()
    verifier = WebhookVerifier("development-secret")
    signature = verifier.sign(body, timestamp=timestamp)
    received = []
    consumer = WebhookConsumer(
        verifier,
        handlers={"order.delivered": lambda event: received.append(event.event_id)},
    )

    event = consumer.consume(body, signature, now=timestamp)
    assert event.data["orderId"] == "order-1"
    assert received == ["event-1"]
    with pytest.raises(InvalidWebhookError, match="already"):
        consumer.consume(body, signature, now=timestamp)


def test_rejects_tampered_and_expired_webhooks() -> None:
    verifier = WebhookVerifier("development-secret", tolerance_seconds=60)
    body = b'{"eventId":"event-1"}'
    signature = verifier.sign(body, timestamp=1000)

    with pytest.raises(InvalidWebhookError, match="does not match"):
        verifier.verify(body + b" ", signature, now=1000)
    with pytest.raises(InvalidWebhookError, match="tolerance"):
        verifier.verify(body, signature, now=1061)
