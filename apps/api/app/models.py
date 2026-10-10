from __future__ import annotations

import uuid
from datetime import datetime, timezone
from decimal import Decimal
from typing import Any

from sqlalchemy import Boolean, DateTime, ForeignKey, Integer, JSON, Numeric, String, Text, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column

from .database import Base


def utcnow() -> datetime:
    return datetime.now(timezone.utc)


def uuid4_str() -> str:
    return str(uuid.uuid4())


class TimestampMixin:
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow, onupdate=utcnow)


class Agent(Base, TimestampMixin):
    __tablename__ = "agents"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=uuid4_str)
    did: Mapped[str] = mapped_column(String(255), unique=True, index=True)
    slug: Mapped[str] = mapped_column(String(80), unique=True, index=True)
    display_name: Mapped[str] = mapped_column(String(160))
    description: Mapped[str] = mapped_column(Text, default="")
    roles: Mapped[list[str]] = mapped_column(JSON, default=list)
    status: Mapped[str] = mapped_column(String(32), default="active", index=True)
    endpoint: Mapped[str | None] = mapped_column(String(500), nullable=True)
    operational_public_key: Mapped[str] = mapped_column(Text)
    organization_id: Mapped[str | None] = mapped_column(String(128), nullable=True, index=True)
    review_wallet: Mapped[str | None] = mapped_column(String(42), nullable=True, unique=True)
    reviewer_active: Mapped[bool] = mapped_column(Boolean, default=False, index=True)
    reputation_score: Mapped[Decimal] = mapped_column(Numeric(5, 2), default=Decimal("0"))
    completed_orders: Mapped[int] = mapped_column(Integer, default=0)
    erc8004_registration: Mapped[dict[str, Any] | None] = mapped_column(JSON, nullable=True)


class Credential(Base, TimestampMixin):
    __tablename__ = "credentials"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=uuid4_str)
    agent_id: Mapped[str] = mapped_column(ForeignKey("agents.id"), index=True)
    credential_type: Mapped[str] = mapped_column(String(100))
    issuer_did: Mapped[str] = mapped_column(String(255))
    claims: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict)
    expires_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    revoked: Mapped[bool] = mapped_column(Boolean, default=False)


class Capability(Base, TimestampMixin):
    __tablename__ = "capabilities"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=uuid4_str)
    provider_id: Mapped[str] = mapped_column(ForeignKey("agents.id"), index=True)
    name: Mapped[str] = mapped_column(String(160), index=True)
    slug: Mapped[str] = mapped_column(String(100), unique=True, index=True)
    description: Mapped[str] = mapped_column(Text)
    input_schema: Mapped[dict[str, Any]] = mapped_column(JSON)
    output_schema: Mapped[dict[str, Any]] = mapped_column(JSON)
    example_input: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict)
    endpoint: Mapped[str] = mapped_column(String(500))
    pricing_model: Mapped[str] = mapped_column(String(40), default="fixed")
    base_price: Mapped[Decimal] = mapped_column(Numeric(24, 8))
    currency: Mapped[str] = mapped_column(String(32), default="USDC")
    estimated_duration_seconds: Mapped[int] = mapped_column(Integer, default=60)
    availability: Mapped[str] = mapped_column(String(32), default="available", index=True)
    version: Mapped[str] = mapped_column(String(32), default="1.0.0")


class Demand(Base, TimestampMixin):
    __tablename__ = "demands"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=uuid4_str)
    requester_id: Mapped[str] = mapped_column(ForeignKey("agents.id"), index=True)
    title: Mapped[str] = mapped_column(String(200), index=True)
    description: Mapped[str] = mapped_column(Text)
    input_payload: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict)
    acceptance_criteria: Mapped[str] = mapped_column(Text)
    budget: Mapped[Decimal] = mapped_column(Numeric(24, 8))
    currency: Mapped[str] = mapped_column(String(32), default="USDC")
    deadline: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    status: Mapped[str] = mapped_column(String(32), default="published", index=True)
    awarded_bid_id: Mapped[str | None] = mapped_column(String(36), nullable=True)


class Bid(Base, TimestampMixin):
    __tablename__ = "bids"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=uuid4_str)
    demand_id: Mapped[str] = mapped_column(ForeignKey("demands.id"), index=True)
    provider_id: Mapped[str] = mapped_column(ForeignKey("agents.id"), index=True)
    capability_id: Mapped[str | None] = mapped_column(ForeignKey("capabilities.id"), nullable=True)
    amount: Mapped[Decimal] = mapped_column(Numeric(24, 8))
    currency: Mapped[str] = mapped_column(String(32))
    proposal: Mapped[str] = mapped_column(Text)
    estimated_duration_seconds: Mapped[int] = mapped_column(Integer)
    status: Mapped[str] = mapped_column(String(32), default="pending", index=True)


class Order(Base, TimestampMixin):
    __tablename__ = "orders"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=uuid4_str)
    requester_id: Mapped[str] = mapped_column(ForeignKey("agents.id"), index=True)
    provider_id: Mapped[str] = mapped_column(ForeignKey("agents.id"), index=True)
    demand_id: Mapped[str | None] = mapped_column(ForeignKey("demands.id"), nullable=True)
    bid_id: Mapped[str | None] = mapped_column(ForeignKey("bids.id"), nullable=True)
    capability_id: Mapped[str | None] = mapped_column(ForeignKey("capabilities.id"), nullable=True)
    source_type: Mapped[str] = mapped_column(String(32))
    status: Mapped[str] = mapped_column(String(32), default="awaiting_funding", index=True)
    title: Mapped[str] = mapped_column(String(200))
    terms_snapshot: Mapped[dict[str, Any]] = mapped_column(JSON)
    input_payload: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict)
    amount: Mapped[Decimal] = mapped_column(Numeric(24, 8))
    currency: Mapped[str] = mapped_column(String(32))
    terms_hash: Mapped[str] = mapped_column(String(66))
    settlement_id: Mapped[str | None] = mapped_column(String(36), nullable=True)


class OrderEvent(Base):
    __tablename__ = "order_events"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=uuid4_str)
    order_id: Mapped[str] = mapped_column(ForeignKey("orders.id"), index=True)
    event_type: Mapped[str] = mapped_column(String(64))
    from_status: Mapped[str | None] = mapped_column(String(32), nullable=True)
    to_status: Mapped[str] = mapped_column(String(32))
    actor_id: Mapped[str] = mapped_column(ForeignKey("agents.id"))
    metadata_json: Mapped[dict[str, Any]] = mapped_column("metadata", JSON, default=dict)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)


class Message(Base):
    __tablename__ = "messages"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=uuid4_str)
    order_id: Mapped[str] = mapped_column(ForeignKey("orders.id"), index=True)
    sender_id: Mapped[str] = mapped_column(ForeignKey("agents.id"))
    content: Mapped[str] = mapped_column(Text)
    content_hash: Mapped[str] = mapped_column(String(66))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)


class Delivery(Base):
    __tablename__ = "deliveries"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=uuid4_str)
    order_id: Mapped[str] = mapped_column(ForeignKey("orders.id"), unique=True, index=True)
    provider_id: Mapped[str] = mapped_column(ForeignKey("agents.id"))
    artifact_uri: Mapped[str] = mapped_column(String(1000))
    content_hash: Mapped[str] = mapped_column(String(128))
    summary: Mapped[str] = mapped_column(Text, default="")
    output_payload: Mapped[dict[str, Any] | None] = mapped_column(JSON, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)


class Review(Base):
    __tablename__ = "reviews"
    __table_args__ = (UniqueConstraint("order_id", "reviewer_id", name="uq_review_order_reviewer"),)

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=uuid4_str)
    order_id: Mapped[str] = mapped_column(ForeignKey("orders.id"), index=True)
    reviewer_id: Mapped[str] = mapped_column(ForeignKey("agents.id"))
    reviewee_id: Mapped[str] = mapped_column(ForeignKey("agents.id"), index=True)
    rating: Mapped[int] = mapped_column(Integer)
    comment: Mapped[str] = mapped_column(Text, default="")
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)


class Notification(Base):
    __tablename__ = "notifications"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=uuid4_str)
    agent_id: Mapped[str] = mapped_column(ForeignKey("agents.id"), index=True)
    kind: Mapped[str] = mapped_column(String(64))
    title: Mapped[str] = mapped_column(String(200))
    body: Mapped[str] = mapped_column(Text)
    resource_type: Mapped[str | None] = mapped_column(String(64), nullable=True)
    resource_id: Mapped[str | None] = mapped_column(String(36), nullable=True)
    read_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)


class Settlement(Base, TimestampMixin):
    __tablename__ = "settlements"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=uuid4_str)
    order_id: Mapped[str] = mapped_column(ForeignKey("orders.id"), unique=True, index=True)
    status: Mapped[str] = mapped_column(String(32), default="created", index=True)
    amount: Mapped[Decimal] = mapped_column(Numeric(24, 8))
    currency: Mapped[str] = mapped_column(String(32))
    payer_account: Mapped[str | None] = mapped_column(String(255), nullable=True)
    payee_account: Mapped[str | None] = mapped_column(String(255), nullable=True)
    asset_id: Mapped[str | None] = mapped_column(String(255), nullable=True)
    source_chain: Mapped[str | None] = mapped_column(String(100), nullable=True)
    destination_chain: Mapped[str | None] = mapped_column(String(100), nullable=True)
    transaction_hash: Mapped[str | None] = mapped_column(String(128), nullable=True)
    confirmation_count: Mapped[int] = mapped_column(Integer, default=0)
    ccip_message_id: Mapped[str | None] = mapped_column(String(128), nullable=True)
    failure_reason: Mapped[str | None] = mapped_column(Text, nullable=True)


class PaymentDecision(Base):
    __tablename__ = "payment_decisions"
    __table_args__ = (UniqueConstraint("order_id", name="uq_payment_decision_order"),)

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=uuid4_str)
    order_id: Mapped[str] = mapped_column(ForeignKey("orders.id"), unique=True, index=True)
    settlement_id: Mapped[str] = mapped_column(ForeignKey("settlements.id"), index=True)
    decided_by_id: Mapped[str] = mapped_column(ForeignKey("agents.id"), index=True)
    decision_type: Mapped[str] = mapped_column(String(32))
    payable_amount: Mapped[Decimal] = mapped_column(Numeric(24, 8))
    refund_amount: Mapped[Decimal] = mapped_column(Numeric(24, 8))
    verification_score: Mapped[int | None] = mapped_column(Integer, nullable=True)
    rationale: Mapped[str] = mapped_column(Text, default="")
    delivery_hash: Mapped[str | None] = mapped_column(String(128), nullable=True)
    decision_hash: Mapped[str] = mapped_column(String(66), unique=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)


class SettlementChainEvent(Base):
    __tablename__ = "settlement_chain_events"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=uuid4_str)
    event_id: Mapped[str] = mapped_column(String(160), unique=True, index=True)
    event_type: Mapped[str] = mapped_column(String(48), index=True)
    settlement_id: Mapped[str] = mapped_column(ForeignKey("settlements.id"), index=True)
    chain_selector: Mapped[str | None] = mapped_column(String(32), nullable=True)
    transaction_hash: Mapped[str] = mapped_column(String(128))
    ccip_message_id: Mapped[str | None] = mapped_column(String(128), nullable=True, index=True)
    token_address: Mapped[str | None] = mapped_column(String(42), nullable=True)
    amount: Mapped[Decimal | None] = mapped_column(Numeric(24, 8), nullable=True)
    block_number: Mapped[int | None] = mapped_column(Integer, nullable=True)
    block_hash: Mapped[str | None] = mapped_column(String(66), nullable=True)
    payload_hash: Mapped[str | None] = mapped_column(String(128), nullable=True)
    confirmations: Mapped[int] = mapped_column(Integer, default=1)
    canonical_status: Mapped[str] = mapped_column(String(24), default="confirmed", index=True)
    orphaned_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)


class DexQuote(Base, TimestampMixin):
    __tablename__ = "dex_quotes"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=uuid4_str)
    order_id: Mapped[str] = mapped_column(ForeignKey("orders.id"), index=True)
    side: Mapped[str] = mapped_column(String(16), index=True)
    provider: Mapped[str] = mapped_column(String(32), default="0x")
    chain_id: Mapped[int] = mapped_column(Integer)
    taker: Mapped[str] = mapped_column(String(42))
    sell_token: Mapped[str] = mapped_column(String(42))
    buy_token: Mapped[str] = mapped_column(String(42))
    sell_amount: Mapped[str] = mapped_column(String(100))
    max_sell_amount: Mapped[str] = mapped_column(String(100))
    buy_amount: Mapped[str] = mapped_column(String(100))
    min_buy_amount: Mapped[str] = mapped_column(String(100))
    max_slippage_bps: Mapped[int] = mapped_column(Integer)
    allowance_target: Mapped[str | None] = mapped_column(String(42), nullable=True)
    transaction: Mapped[dict[str, Any]] = mapped_column(JSON)
    quote_hash: Mapped[str] = mapped_column(String(66), unique=True, index=True)
    execution_hash: Mapped[str] = mapped_column(String(66), unique=True, index=True)
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), index=True)
    status: Mapped[str] = mapped_column(String(24), default="quoted", index=True)
    consumed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    swap_transaction_hash: Mapped[str | None] = mapped_column(String(66), nullable=True, unique=True, index=True)


class RelayerTask(Base, TimestampMixin):
    __tablename__ = "relayer_tasks"
    __table_args__ = (UniqueConstraint("settlement_id", "task_type", name="uq_relayer_settlement_task"),)

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=uuid4_str)
    settlement_id: Mapped[str] = mapped_column(ForeignKey("settlements.id"), index=True)
    task_type: Mapped[str] = mapped_column(String(32), index=True)
    status: Mapped[str] = mapped_column(String(24), default="queued", index=True)
    payable_amount: Mapped[Decimal | None] = mapped_column(Numeric(24, 8), nullable=True)
    ccip_message_id: Mapped[str | None] = mapped_column(String(128), nullable=True)
    lease_owner: Mapped[str | None] = mapped_column(String(160), nullable=True)
    lease_token: Mapped[str | None] = mapped_column(String(36), nullable=True)
    lease_expires_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    transaction_hash: Mapped[str | None] = mapped_column(String(128), nullable=True)
    attempts: Mapped[int] = mapped_column(Integer, default=0)
    last_error: Mapped[str | None] = mapped_column(Text, nullable=True)
    first_broadcast_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    last_checked_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    transaction_nonce: Mapped[int | None] = mapped_column(Integer, nullable=True)


class IntegrationRequestNonce(Base):
    __tablename__ = "integration_request_nonces"

    nonce: Mapped[str] = mapped_column(String(100), primary_key=True)
    listener_id: Mapped[str] = mapped_column(String(160), index=True)
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), index=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)


class AuditLog(Base):
    __tablename__ = "audit_logs"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=uuid4_str)
    actor_id: Mapped[str | None] = mapped_column(ForeignKey("agents.id"), nullable=True, index=True)
    action: Mapped[str] = mapped_column(String(100), index=True)
    resource_type: Mapped[str] = mapped_column(String(64))
    resource_id: Mapped[str | None] = mapped_column(String(36), nullable=True)
    details: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)


class AuthChallenge(Base):
    __tablename__ = "auth_challenges"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=uuid4_str)
    did: Mapped[str] = mapped_column(String(255), index=True)
    nonce: Mapped[str] = mapped_column(String(100), unique=True)
    audience: Mapped[str] = mapped_column(String(255))
    message: Mapped[str] = mapped_column(Text)
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    used_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)


class WebhookSubscription(Base, TimestampMixin):
    __tablename__ = "webhook_subscriptions"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=uuid4_str)
    agent_id: Mapped[str] = mapped_column(ForeignKey("agents.id"), index=True)
    url: Mapped[str] = mapped_column(String(1000))
    events: Mapped[list[str]] = mapped_column(JSON, default=list)
    active: Mapped[bool] = mapped_column(Boolean, default=True)


class IdempotencyRecord(Base):
    __tablename__ = "idempotency_records"
    __table_args__ = (
        UniqueConstraint("agent_id", "route", "idempotency_key", name="uq_idem_agent_route_key"),
    )

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=uuid4_str)
    agent_id: Mapped[str] = mapped_column(ForeignKey("agents.id"), index=True)
    route: Mapped[str] = mapped_column(String(255))
    idempotency_key: Mapped[str] = mapped_column(String(255))
    request_hash: Mapped[str] = mapped_column(String(64))
    response_body: Mapped[dict[str, Any]] = mapped_column(JSON)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
