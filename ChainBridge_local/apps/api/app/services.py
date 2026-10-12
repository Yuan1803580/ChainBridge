from __future__ import annotations

import hashlib
import os
import statistics
from typing import Any, Callable

from fastapi import HTTPException
from jsonschema import Draft202012Validator, SchemaError, ValidationError
from sqlalchemy import delete, select
from sqlalchemy.orm import Session

from .models import (
    Agent,
    AuditLog,
    Capability,
    IdempotencyRecord,
    Notification,
    Order,
    OrderEvent,
)
from .jcs import canonical_sha256


def canonical_hash(value: Any) -> str:
    return canonical_sha256(value)


def validate_json_schema(schema: dict[str, Any], payload: dict[str, Any]) -> None:
    try:
        Draft202012Validator.check_schema(schema)
        Draft202012Validator(schema).validate(payload)
    except SchemaError as exc:
        raise HTTPException(status_code=422, detail=f"Invalid capability JSON Schema: {exc.message}") from exc
    except ValidationError as exc:
        path = ".".join(str(item) for item in exc.absolute_path) or "$"
        raise HTTPException(status_code=422, detail=f"Input schema validation failed at {path}: {exc.message}") from exc


def audit(
    session: Session,
    actor_id: str | None,
    action: str,
    resource_type: str,
    resource_id: str | None,
    details: dict[str, Any] | None = None,
) -> None:
    session.add(
        AuditLog(
            actor_id=actor_id,
            action=action,
            resource_type=resource_type,
            resource_id=resource_id,
            details=details or {},
        )
    )


def notify(
    session: Session,
    agent_id: str,
    kind: str,
    title: str,
    body: str,
    resource_type: str | None = None,
    resource_id: str | None = None,
) -> None:
    session.add(
        Notification(
            agent_id=agent_id,
            kind=kind,
            title=title,
            body=body,
            resource_type=resource_type,
            resource_id=resource_id,
        )
    )


def add_order_event(
    session: Session,
    order: Order,
    actor: Agent,
    event_type: str,
    from_status: str | None,
    to_status: str,
    metadata: dict[str, Any] | None = None,
) -> None:
    session.add(
        OrderEvent(
            order_id=order.id,
            actor_id=actor.id,
            event_type=event_type,
            from_status=from_status,
            to_status=to_status,
            metadata_json=metadata or {},
        )
    )
    audit(session, actor.id, event_type, "order", order.id, {"fromStatus": from_status, "toStatus": to_status})


def transition_order(
    session: Session,
    order: Order,
    actor: Agent,
    allowed_from: set[str],
    to_status: str,
    event_type: str,
    metadata: dict[str, Any] | None = None,
) -> None:
    if order.status not in allowed_from:
        raise HTTPException(
            status_code=409,
            detail=f"Cannot transition order from {order.status} to {to_status}",
        )
    previous = order.status
    order.status = to_status
    add_order_event(session, order, actor, event_type, previous, to_status, metadata)


def require_order_party(order: Order, actor: Agent) -> None:
    if actor.id not in {order.requester_id, order.provider_id} and "admin" not in actor.roles:
        raise HTTPException(status_code=403, detail="Only order participants may perform this action")


def get_idempotent_response(
    session: Session,
    actor_id: str,
    route: str,
    key: str | None,
    payload: dict[str, Any],
) -> dict[str, Any] | None:
    if key is None:
        return None
    request_hash = canonical_hash(payload).removeprefix("0x")
    record = session.scalar(
        select(IdempotencyRecord).where(
            IdempotencyRecord.agent_id == actor_id,
            IdempotencyRecord.route == route,
            IdempotencyRecord.idempotency_key == key,
        )
    )
    if record is None:
        return None
    if record.request_hash != request_hash:
        raise HTTPException(status_code=409, detail="Idempotency-Key was already used with a different payload")
    return record.response_body


def save_idempotent_response(
    session: Session,
    actor_id: str,
    route: str,
    key: str | None,
    payload: dict[str, Any],
    response: dict[str, Any],
) -> None:
    if key is None:
        return
    if route.startswith("dynamic_workflows.") and route.endswith(".coordinate"):
        try:
            limit = max(1, min(int(os.getenv("WORKFLOW_COORDINATE_IDEMPOTENCY_LIMIT", "64")), 1000))
        except ValueError:
            limit = 64
        stale_ids = list(
            session.scalars(
                select(IdempotencyRecord.id)
                .where(
                    IdempotencyRecord.agent_id == actor_id,
                    IdempotencyRecord.route == route,
                )
                .order_by(IdempotencyRecord.created_at.desc(), IdempotencyRecord.id.desc())
                .offset(max(0, limit - 1))
            )
        )
        if stale_ids:
            session.execute(delete(IdempotencyRecord).where(IdempotencyRecord.id.in_(stale_ids)))
    session.add(
        IdempotencyRecord(
            agent_id=actor_id,
            route=route,
            idempotency_key=key,
            request_hash=canonical_hash(payload).removeprefix("0x"),
            response_body=response,
        )
    )


def execute_demo_capability(capability: Capability, payload: dict[str, Any]) -> dict[str, Any]:
    if capability.slug == "text-summary-v1":
        text = payload["text"]
        maximum = payload["maxLength"]
        summary = text if len(text) <= maximum else text[: max(0, maximum - 3)] + "..."
        return {"summary": summary, "originalLength": len(text), "language": payload["language"]}
    if capability.slug == "data-statistics-v1":
        numbers = payload["numbers"]
        calculators: dict[str, Callable[[], int | float]] = {
            "count": lambda: len(numbers),
            "mean": lambda: statistics.fmean(numbers),
            "median": lambda: statistics.median(numbers),
            "min": lambda: min(numbers),
            "max": lambda: max(numbers),
        }
        return {metric: calculators[metric]() for metric in payload["metrics"]}
    if capability.slug == "hash-audit-v1":
        digest = hashlib.new(payload["algorithm"], payload["content"].encode()).hexdigest()
        return {"algorithm": payload["algorithm"], "digest": digest}
    if capability.slug == "code-review-v1":
        finding = {
            "severity": "info",
            "title": "Mock review completed",
            "message": f"Reviewed {payload['language']} code for {payload['focus']} concerns.",
        }
        return {"provider": "mock", "findings": [finding]}
    return {"status": "accepted", "endpoint": capability.endpoint, "inputHash": canonical_hash(payload)}
