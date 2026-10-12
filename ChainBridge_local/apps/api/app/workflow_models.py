from __future__ import annotations

from datetime import datetime
from decimal import Decimal
from typing import Any

from sqlalchemy import Boolean, DateTime, ForeignKey, Integer, JSON, Numeric, String, Text, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column

from .database import Base
from .models import TimestampMixin, utcnow, uuid4_str


class Workflow(Base, TimestampMixin):
    __tablename__ = "workflows"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=uuid4_str)
    reference_code: Mapped[str] = mapped_column(String(32), unique=True, index=True)
    requester_id: Mapped[str] = mapped_column(ForeignKey("agents.id"), index=True)
    title: Mapped[str] = mapped_column(String(200))
    objective: Mapped[str] = mapped_column(Text)
    status: Mapped[str] = mapped_column(String(32), default="PLANNED", index=True)
    currency: Mapped[str] = mapped_column(String(32), default="USDC")
    budget: Mapped[Decimal] = mapped_column(Numeric(24, 8))
    platform_fee: Mapped[Decimal] = mapped_column(Numeric(24, 8), default=Decimal("0"))
    paid_amount: Mapped[Decimal] = mapped_column(Numeric(24, 8), default=Decimal("0"))
    refunded_amount: Mapped[Decimal] = mapped_column(Numeric(24, 8), default=Decimal("0"))
    source_chain_id: Mapped[int] = mapped_column(Integer, default=31337)
    terms_hash: Mapped[str] = mapped_column(String(66))
    input_payload: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict)
    funding_mode: Mapped[str] = mapped_column(String(24), default="RESERVED")
    funding_status: Mapped[str] = mapped_column(String(32), default="NOT_REQUIRED", index=True)
    funding_wallet: Mapped[str | None] = mapped_column(String(42), nullable=True)
    onchain_workflow_id: Mapped[str | None] = mapped_column(String(66), nullable=True, unique=True)
    approve_tx_hash: Mapped[str | None] = mapped_column(String(66), nullable=True, unique=True)
    lock_tx_hash: Mapped[str | None] = mapped_column(String(66), nullable=True, unique=True)
    source_gas_tx_hash: Mapped[str | None] = mapped_column(String(66), nullable=True, unique=True)
    deployment_id: Mapped[str | None] = mapped_column(String(128), nullable=True)
    source_gas_deposit: Mapped[Decimal] = mapped_column(Numeric(38, 18), default=Decimal("0"))
    source_gas_refunded: Mapped[Decimal] = mapped_column(Numeric(38, 18), default=Decimal("0"))
    lock_confirmed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    failure_refunded_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    contract_sync_status: Mapped[str] = mapped_column(String(32), default="NOT_REQUIRED", index=True)
    failure_reason: Mapped[str | None] = mapped_column(Text, nullable=True)
    completed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)


class LocalFaucetIssuance(Base, TimestampMixin):
    __tablename__ = "local_faucet_issuances"
    __table_args__ = (
        UniqueConstraint("deployment_id", "wallet_address", name="uq_local_faucet_deployment_wallet"),
    )

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=uuid4_str)
    request_id: Mapped[str] = mapped_column(String(128), unique=True, index=True)
    deployment_id: Mapped[str] = mapped_column(String(128), index=True)
    actor_id: Mapped[str] = mapped_column(ForeignKey("agents.id"), index=True)
    wallet_address: Mapped[str] = mapped_column(String(42), index=True)
    token_amount: Mapped[Decimal] = mapped_column(Numeric(24, 8))
    native_amount: Mapped[Decimal] = mapped_column(Numeric(38, 18))
    token_tx_hash: Mapped[str | None] = mapped_column(String(66), nullable=True, unique=True)
    native_tx_hash: Mapped[str | None] = mapped_column(String(66), nullable=True, unique=True)
    status: Mapped[str] = mapped_column(String(24), default="PENDING", index=True)


class WorkflowNode(Base, TimestampMixin):
    __tablename__ = "workflow_nodes"
    __table_args__ = (UniqueConstraint("workflow_id", "node_key", name="uq_workflow_node_key"),)

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=uuid4_str)
    workflow_id: Mapped[str] = mapped_column(ForeignKey("workflows.id"), index=True)
    node_key: Mapped[str] = mapped_column(String(32))
    title: Mapped[str] = mapped_column(String(200))
    description: Mapped[str] = mapped_column(Text, default="")
    capability: Mapped[str] = mapped_column(String(100), index=True)
    status: Mapped[str] = mapped_column(String(32), default="BLOCKED", index=True)
    depends_on: Mapped[list[str]] = mapped_column(JSON, default=list)
    acceptance_criteria: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict)
    input_payload: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict)
    output_payload: Mapped[dict[str, Any] | None] = mapped_column(JSON, nullable=True)
    reward: Mapped[Decimal] = mapped_column(Numeric(24, 8))
    risk_level: Mapped[str] = mapped_column(String(16), default="LOW", index=True)
    risk_assignment_mode: Mapped[str] = mapped_column(String(16), default="AUTO", index=True)
    risk_score: Mapped[int] = mapped_column(Integer, default=0)
    risk_reasons: Mapped[list[str]] = mapped_column(JSON, default=list)
    review_policy_version: Mapped[str] = mapped_column(String(64), default="RISK_REVIEW_V1")
    review_policy_hash: Mapped[str] = mapped_column(String(66))
    required_approvals: Mapped[int] = mapped_column(Integer, default=0)
    challenge_period_seconds: Mapped[int] = mapped_column(Integer, default=0)
    sampling_rate_bps: Mapped[int] = mapped_column(Integer, default=0)
    review_budget: Mapped[Decimal] = mapped_column(Numeric(24, 8), default=Decimal("0"))
    max_attempts: Mapped[int] = mapped_column(Integer, default=3)
    current_attempt_id: Mapped[str | None] = mapped_column(String(36), nullable=True)
    settled_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)


class NodeAttempt(Base, TimestampMixin):
    __tablename__ = "node_attempts"
    __table_args__ = (UniqueConstraint("node_id", "attempt_number", name="uq_node_attempt_number"),)

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=uuid4_str)
    node_id: Mapped[str] = mapped_column(ForeignKey("workflow_nodes.id"), index=True)
    attempt_number: Mapped[int] = mapped_column(Integer)
    status: Mapped[str] = mapped_column(String(32), default="CREATED", index=True)
    agent_profile_id: Mapped[str] = mapped_column(ForeignKey("workflow_agent_profiles.id"), index=True)
    agent_key: Mapped[str] = mapped_column(String(80), index=True)
    agent_id: Mapped[str] = mapped_column(ForeignKey("agents.id"), index=True)
    chain_id: Mapped[int] = mapped_column(Integer, index=True)
    home_wallet: Mapped[str | None] = mapped_column(String(42), nullable=True)
    route_id: Mapped[str] = mapped_column(ForeignKey("chain_routes.id"), index=True)
    route_version: Mapped[int] = mapped_column(Integer)
    settlement_asset: Mapped[str] = mapped_column(String(32), default="USDC")
    payout_asset: Mapped[str] = mapped_column(String(32), default="USDC")
    payout_token_address: Mapped[str | None] = mapped_column(String(42), nullable=True)
    payout_decimals: Mapped[int] = mapped_column(Integer, default=6)
    payout_rate: Mapped[Decimal] = mapped_column(Numeric(30, 12), default=Decimal("1"))
    payout_slippage_bps: Mapped[int] = mapped_column(Integer, default=100)
    payout_quote_amount: Mapped[str | None] = mapped_column(String(80), nullable=True)
    payout_min_amount: Mapped[str | None] = mapped_column(String(80), nullable=True)
    payout_quote_expires_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    payout_status: Mapped[str] = mapped_column(String(24), default="NOT_REQUIRED", index=True)
    payout_tx_hash: Mapped[str | None] = mapped_column(String(66), nullable=True)
    match_candidate_id: Mapped[str | None] = mapped_column(String(36), nullable=True, index=True)
    dispatch_message_id: Mapped[str | None] = mapped_column(String(128), nullable=True)
    result_message_id: Mapped[str | None] = mapped_column(String(128), nullable=True)
    result_payload: Mapped[dict[str, Any] | None] = mapped_column(JSON, nullable=True)
    result_hash: Mapped[str | None] = mapped_column(String(66), nullable=True)
    proof_hash: Mapped[str | None] = mapped_column(String(66), nullable=True)
    reported_score: Mapped[Decimal | None] = mapped_column(Numeric(5, 2), nullable=True)
    matching_trust_score: Mapped[Decimal | None] = mapped_column(Numeric(5, 2), nullable=True)
    matching_trust_source: Mapped[str | None] = mapped_column(String(64), nullable=True)
    quality_score: Mapped[Decimal | None] = mapped_column(Numeric(5, 2), nullable=True)
    quality_status: Mapped[str] = mapped_column(String(24), default="NOT_CALCULATED", index=True)
    quality_breakdown: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict)
    quality_policy_version: Mapped[str | None] = mapped_column(String(64), nullable=True)
    quality_evidence_hash: Mapped[str | None] = mapped_column(String(66), nullable=True, index=True)
    quality_calculated_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    review_reason: Mapped[str | None] = mapped_column(Text, nullable=True)
    failure_reason_code: Mapped[str | None] = mapped_column(String(64), nullable=True, index=True)
    failure_responsible_party: Mapped[str | None] = mapped_column(String(24), nullable=True)
    result_proof_status: Mapped[str] = mapped_column(String(32), default="NOT_REQUIRED", index=True)
    result_proof_tx_hash: Mapped[str | None] = mapped_column(String(66), nullable=True)
    coordinator_review_hash: Mapped[str | None] = mapped_column(String(66), nullable=True)
    review_status: Mapped[str] = mapped_column(String(32), default="NOT_STARTED", index=True)
    review_policy_hash: Mapped[str | None] = mapped_column(String(66), nullable=True)
    review_sampled: Mapped[bool] = mapped_column(Boolean, default=False)
    randomness_mode: Mapped[str] = mapped_column(String(24), default="NOT_REQUIRED", index=True)
    randomness_status: Mapped[str] = mapped_column(String(24), default="NOT_REQUIRED", index=True)
    randomness_request_id: Mapped[str | None] = mapped_column(String(128), nullable=True, unique=True)
    randomness_commitment: Mapped[str | None] = mapped_column(String(66), nullable=True)
    random_word: Mapped[str | None] = mapped_column(String(78), nullable=True)
    randomness_proof_hash: Mapped[str | None] = mapped_column(String(66), nullable=True)
    randomness_requested_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    randomness_fulfilled_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    review_started_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    challenge_expires_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    settlement_status: Mapped[str] = mapped_column(String(32), default="NONE", index=True)
    settlement_message_id: Mapped[str | None] = mapped_column(String(128), nullable=True)
    destination_tx_hash: Mapped[str | None] = mapped_column(String(66), nullable=True)
    source_confirm_tx_hash: Mapped[str | None] = mapped_column(String(66), nullable=True)
    submitted_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    reviewed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    settled_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)


class BudgetReservation(Base, TimestampMixin):
    __tablename__ = "budget_reservations"
    __table_args__ = (UniqueConstraint("attempt_id", name="uq_reservation_attempt"),)

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=uuid4_str)
    workflow_id: Mapped[str] = mapped_column(ForeignKey("workflows.id"), index=True)
    node_id: Mapped[str] = mapped_column(ForeignKey("workflow_nodes.id"), index=True)
    attempt_id: Mapped[str] = mapped_column(ForeignKey("node_attempts.id"), index=True)
    amount: Mapped[Decimal] = mapped_column(Numeric(24, 8))
    currency: Mapped[str] = mapped_column(String(32), default="USDC")
    status: Mapped[str] = mapped_column(String(24), default="RESERVED", index=True)
    released_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    paid_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)


class ChainRoute(Base, TimestampMixin):
    __tablename__ = "chain_routes"
    __table_args__ = (UniqueConstraint("route_key", "route_version", name="uq_route_key_version"),)

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=uuid4_str)
    route_key: Mapped[str] = mapped_column(String(80), index=True)
    route_version: Mapped[int] = mapped_column(Integer)
    source_chain_id: Mapped[int] = mapped_column(Integer, default=31337)
    destination_chain_id: Mapped[int] = mapped_column(Integer, index=True)
    status: Mapped[str] = mapped_column(String(24), default="ACTIVE", index=True)
    transport: Mapped[str] = mapped_column(String(32), default="LOCAL_MOCK", index=True)
    rpc_url: Mapped[str] = mapped_column(String(255))
    source_chain_selector: Mapped[str | None] = mapped_column(String(32), nullable=True)
    destination_chain_selector: Mapped[str | None] = mapped_column(String(32), nullable=True)
    source_router_address: Mapped[str | None] = mapped_column(String(42), nullable=True)
    destination_router_address: Mapped[str | None] = mapped_column(String(42), nullable=True)
    source_gateway_address: Mapped[str | None] = mapped_column(String(42), nullable=True)
    remote_work_board_address: Mapped[str | None] = mapped_column(String(42), nullable=True)
    payout_receiver_address: Mapped[str | None] = mapped_column(String(42), nullable=True)
    token_address: Mapped[str | None] = mapped_column(String(42), nullable=True)
    settlement_asset: Mapped[str] = mapped_column(String(32), default="USDC")
    payout_asset: Mapped[str] = mapped_column(String(32), default="USDC")
    payout_token_address: Mapped[str | None] = mapped_column(String(42), nullable=True)
    payout_swap_address: Mapped[str | None] = mapped_column(String(42), nullable=True)
    payout_decimals: Mapped[int] = mapped_column(Integer, default=6)
    payout_rate: Mapped[Decimal] = mapped_column(Numeric(30, 12), default=Decimal("1"))
    payout_slippage_bps: Mapped[int] = mapped_column(Integer, default=100)
    confirmation_blocks: Mapped[int] = mapped_column(Integer, default=1)
    deployment_status: Mapped[str] = mapped_column(String(32), default="PLACEHOLDER", index=True)
    deployment_manifest_hash: Mapped[str | None] = mapped_column(String(66), nullable=True)


class WorkflowAgentProfile(Base, TimestampMixin):
    __tablename__ = "workflow_agent_profiles"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=uuid4_str)
    agent_id: Mapped[str] = mapped_column(ForeignKey("agents.id"), index=True)
    agent_key: Mapped[str] = mapped_column(String(80), unique=True, index=True)
    chain_id: Mapped[int] = mapped_column(Integer, index=True)
    home_wallet: Mapped[str | None] = mapped_column(String(42), nullable=True)
    capabilities: Mapped[list[str]] = mapped_column(JSON, default=list)
    priority: Mapped[int] = mapped_column(Integer, default=100)
    active: Mapped[bool] = mapped_column(Boolean, default=True, index=True)
    admission_status: Mapped[str] = mapped_column(String(32), default="APPROVED", index=True)
    chain_status: Mapped[str] = mapped_column(String(32), default="READY", index=True)
    price_floor: Mapped[Decimal] = mapped_column(Numeric(24, 8), default=Decimal("0"))
    currency: Mapped[str] = mapped_column(String(32), default="USDC")
    success_rate_bps: Mapped[int] = mapped_column(Integer, default=9000)
    trust_score: Mapped[int] = mapped_column(Integer, default=80)
    endpoint_latency_ms: Mapped[int] = mapped_column(Integer, default=500)
    availability: Mapped[str] = mapped_column(String(24), default="ONLINE", index=True)
    capacity_available: Mapped[bool] = mapped_column(Boolean, default=True, index=True)
    gas_ready: Mapped[bool] = mapped_column(Boolean, default=True, index=True)
    last_heartbeat_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    quote_expires_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    metadata_json: Mapped[dict[str, Any]] = mapped_column("metadata", JSON, default=dict)
    credential_status: Mapped[str] = mapped_column(String(24), default="UNCONFIGURED", index=True)
    credential_ref: Mapped[str | None] = mapped_column(String(255), nullable=True)
    credential_fingerprint: Mapped[str | None] = mapped_column(String(128), nullable=True)


class ExternalAgentOnboardingChallenge(Base):
    __tablename__ = "external_agent_onboarding_challenges"
    __table_args__ = (UniqueConstraint("nonce", name="uq_external_agent_onboarding_nonce"),)

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=uuid4_str)
    profile_id: Mapped[str] = mapped_column(ForeignKey("workflow_agent_profiles.id"), index=True)
    purpose: Mapped[str] = mapped_column(String(32), default="OWNERSHIP_PROOF", index=True)
    nonce: Mapped[str] = mapped_column(String(96), unique=True, index=True)
    message: Mapped[str] = mapped_column(Text)
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), index=True)
    used_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)


class AgentMatchRound(Base, TimestampMixin):
    __tablename__ = "agent_match_rounds"
    __table_args__ = (UniqueConstraint("node_id", "round_number", name="uq_agent_match_round_number"),)

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=uuid4_str)
    workflow_id: Mapped[str] = mapped_column(ForeignKey("workflows.id"), index=True)
    node_id: Mapped[str] = mapped_column(ForeignKey("workflow_nodes.id"), index=True)
    round_number: Mapped[int] = mapped_column(Integer)
    policy_version: Mapped[str] = mapped_column(String(32), default="MATCH_V1")
    capability: Mapped[str] = mapped_column(String(100), index=True)
    candidate_limit: Mapped[int] = mapped_column(Integer, default=3)
    status: Mapped[str] = mapped_column(String(24), default="ACTIVE", index=True)


class AgentMatchCandidate(Base, TimestampMixin):
    __tablename__ = "agent_match_candidates"
    __table_args__ = (
        UniqueConstraint("match_round_id", "agent_profile_id", name="uq_match_round_agent"),
        UniqueConstraint("match_round_id", "rank", name="uq_match_round_rank"),
    )

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=uuid4_str)
    match_round_id: Mapped[str] = mapped_column(ForeignKey("agent_match_rounds.id"), index=True)
    workflow_id: Mapped[str] = mapped_column(ForeignKey("workflows.id"), index=True)
    node_id: Mapped[str] = mapped_column(ForeignKey("workflow_nodes.id"), index=True)
    agent_profile_id: Mapped[str] = mapped_column(ForeignKey("workflow_agent_profiles.id"), index=True)
    agent_key: Mapped[str] = mapped_column(String(80), index=True)
    rank: Mapped[int] = mapped_column(Integer)
    score: Mapped[Decimal] = mapped_column(Numeric(8, 3))
    score_breakdown: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict)
    quote_amount: Mapped[Decimal] = mapped_column(Numeric(24, 8))
    currency: Mapped[str] = mapped_column(String(32), default="USDC")
    chain_id: Mapped[int] = mapped_column(Integer, index=True)
    home_wallet: Mapped[str | None] = mapped_column(String(42), nullable=True)
    route_id: Mapped[str] = mapped_column(ForeignKey("chain_routes.id"), index=True)
    route_version: Mapped[int] = mapped_column(Integer)
    status: Mapped[str] = mapped_column(String(24), default="AVAILABLE", index=True)
    selected_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    exclusion_reason: Mapped[str | None] = mapped_column(Text, nullable=True)


class FailureIncident(Base, TimestampMixin):
    __tablename__ = "workflow_failure_incidents"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=uuid4_str)
    workflow_id: Mapped[str] = mapped_column(ForeignKey("workflows.id"), index=True)
    node_id: Mapped[str] = mapped_column(ForeignKey("workflow_nodes.id"), index=True)
    attempt_id: Mapped[str | None] = mapped_column(ForeignKey("node_attempts.id"), nullable=True, index=True)
    reason_code: Mapped[str] = mapped_column(String(64), index=True)
    responsible_party: Mapped[str] = mapped_column(String(24), index=True)
    retryable: Mapped[bool] = mapped_column(Boolean, default=False)
    agent_fault: Mapped[bool] = mapped_column(Boolean, default=False)
    gas_treatment: Mapped[str] = mapped_column(String(64), default="POLICY_REVIEW")
    route_paused: Mapped[bool] = mapped_column(Boolean, default=False)
    evidence: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict)
    resolved_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)


class CrossChainMessage(Base, TimestampMixin):
    __tablename__ = "cross_chain_messages"
    __table_args__ = (UniqueConstraint("message_id", name="uq_cross_chain_message_id"),)

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=uuid4_str)
    message_id: Mapped[str] = mapped_column(String(128), unique=True, index=True)
    workflow_id: Mapped[str] = mapped_column(ForeignKey("workflows.id"), index=True)
    node_id: Mapped[str] = mapped_column(ForeignKey("workflow_nodes.id"), index=True)
    attempt_id: Mapped[str] = mapped_column(ForeignKey("node_attempts.id"), index=True)
    message_type: Mapped[str] = mapped_column(String(32), index=True)
    source_chain_id: Mapped[int] = mapped_column(Integer)
    destination_chain_id: Mapped[int] = mapped_column(Integer)
    route_id: Mapped[str] = mapped_column(ForeignKey("chain_routes.id"))
    route_version: Mapped[int] = mapped_column(Integer)
    status: Mapped[str] = mapped_column(String(24), default="QUEUED", index=True)
    payload: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict)
    transaction_hash: Mapped[str | None] = mapped_column(String(66), nullable=True)
    relay_attempts: Mapped[int] = mapped_column(Integer, default=0)
    last_error: Mapped[str | None] = mapped_column(Text, nullable=True)
    delivered_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)


class WorkflowEvent(Base):
    __tablename__ = "workflow_events"
    __table_args__ = (UniqueConstraint("workflow_id", "sequence", name="uq_workflow_event_sequence"),)

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=uuid4_str)
    workflow_id: Mapped[str] = mapped_column(ForeignKey("workflows.id"), index=True)
    sequence: Mapped[int] = mapped_column(Integer)
    event_type: Mapped[str] = mapped_column(String(64), index=True)
    node_id: Mapped[str | None] = mapped_column(ForeignKey("workflow_nodes.id"), nullable=True, index=True)
    attempt_id: Mapped[str | None] = mapped_column(ForeignKey("node_attempts.id"), nullable=True, index=True)
    actor: Mapped[str] = mapped_column(String(100))
    payload: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)


class LedgerEntry(Base):
    __tablename__ = "workflow_ledger_entries"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=uuid4_str)
    workflow_id: Mapped[str] = mapped_column(ForeignKey("workflows.id"), index=True)
    sequence: Mapped[int] = mapped_column(Integer)
    entry_type: Mapped[str] = mapped_column(String(32), index=True)
    asset_kind: Mapped[str] = mapped_column(String(16), index=True)
    asset_symbol: Mapped[str] = mapped_column(String(32))
    amount: Mapped[Decimal] = mapped_column(Numeric(30, 12))
    from_account: Mapped[str] = mapped_column(String(160))
    to_account: Mapped[str] = mapped_column(String(160))
    chain_id: Mapped[int] = mapped_column(Integer)
    node_id: Mapped[str | None] = mapped_column(ForeignKey("workflow_nodes.id"), nullable=True)
    attempt_id: Mapped[str | None] = mapped_column(ForeignKey("node_attempts.id"), nullable=True)
    transaction_hash: Mapped[str | None] = mapped_column(String(66), nullable=True)
    metadata_json: Mapped[dict[str, Any]] = mapped_column("metadata", JSON, default=dict)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)


class GasDeposit(Base, TimestampMixin):
    __tablename__ = "workflow_gas_deposits"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=uuid4_str)
    workflow_id: Mapped[str] = mapped_column(ForeignKey("workflows.id"), index=True)
    node_id: Mapped[str | None] = mapped_column(ForeignKey("workflow_nodes.id"), nullable=True, index=True)
    attempt_id: Mapped[str | None] = mapped_column(ForeignKey("node_attempts.id"), nullable=True, index=True)
    route_id: Mapped[str | None] = mapped_column(ForeignKey("chain_routes.id"), nullable=True, index=True)
    route_version: Mapped[int | None] = mapped_column(Integer, nullable=True)
    chain_id: Mapped[int] = mapped_column(Integer, index=True)
    depositor: Mapped[str] = mapped_column(String(160))
    asset: Mapped[str] = mapped_column(String(32), default="ETH")
    deposited_amount: Mapped[Decimal] = mapped_column(Numeric(38, 18), default=Decimal("0"))
    reserved_amount: Mapped[Decimal] = mapped_column(Numeric(38, 18), default=Decimal("0"))
    charged_amount: Mapped[Decimal] = mapped_column(Numeric(38, 18), default=Decimal("0"))
    refunded_amount: Mapped[Decimal] = mapped_column(Numeric(38, 18), default=Decimal("0"))
    status: Mapped[str] = mapped_column(String(24), default="PENDING", index=True)
    deposit_tx_hash: Mapped[str | None] = mapped_column(String(66), nullable=True)
    refund_tx_hash: Mapped[str | None] = mapped_column(String(66), nullable=True)


class FeeLedgerEntry(Base, TimestampMixin):
    __tablename__ = "workflow_fee_ledger_entries"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=uuid4_str)
    workflow_id: Mapped[str] = mapped_column(ForeignKey("workflows.id"), index=True)
    node_id: Mapped[str | None] = mapped_column(ForeignKey("workflow_nodes.id"), nullable=True, index=True)
    attempt_id: Mapped[str | None] = mapped_column(ForeignKey("node_attempts.id"), nullable=True, index=True)
    category: Mapped[str] = mapped_column(String(64), index=True)
    failure_reason_code: Mapped[str | None] = mapped_column(String(64), nullable=True, index=True)
    initial_payer: Mapped[str] = mapped_column(String(160))
    final_responsible_party: Mapped[str] = mapped_column(String(32), index=True)
    quoted_amount: Mapped[Decimal] = mapped_column(Numeric(30, 12), default=Decimal("0"))
    charged_amount: Mapped[Decimal] = mapped_column(Numeric(30, 12), default=Decimal("0"))
    asset: Mapped[str] = mapped_column(String(32), default="ETH")
    transaction_hash: Mapped[str | None] = mapped_column(String(66), nullable=True)
    message_id: Mapped[str | None] = mapped_column(String(128), nullable=True)
    status: Mapped[str] = mapped_column(String(24), default="QUOTED", index=True)


class CoordinatorDecision(Base, TimestampMixin):
    __tablename__ = "coordinator_decisions"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=uuid4_str)
    workflow_id: Mapped[str | None] = mapped_column(ForeignKey("workflows.id"), nullable=True, index=True)
    node_id: Mapped[str | None] = mapped_column(ForeignKey("workflow_nodes.id"), nullable=True, index=True)
    attempt_id: Mapped[str | None] = mapped_column(ForeignKey("node_attempts.id"), nullable=True, index=True)
    coordinator_did: Mapped[str] = mapped_column(String(160), default="did:local:coordinator")
    decision_type: Mapped[str] = mapped_column(String(64), index=True)
    policy_version: Mapped[str] = mapped_column(String(64))
    input_hash: Mapped[str] = mapped_column(String(66))
    decision_hash: Mapped[str] = mapped_column(String(66), unique=True, index=True)
    payload: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict)


class ReviewApproval(Base, TimestampMixin):
    __tablename__ = "workflow_review_approvals"
    __table_args__ = (
        UniqueConstraint("attempt_id", "reviewer_id", name="uq_review_approval_attempt_reviewer"),
        UniqueConstraint("reviewer_wallet", "nonce", name="uq_review_approval_wallet_nonce"),
    )

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=uuid4_str)
    workflow_id: Mapped[str] = mapped_column(ForeignKey("workflows.id"), index=True)
    node_id: Mapped[str] = mapped_column(ForeignKey("workflow_nodes.id"), index=True)
    attempt_id: Mapped[str] = mapped_column(ForeignKey("node_attempts.id"), index=True)
    reviewer_id: Mapped[str] = mapped_column(ForeignKey("agents.id"), index=True)
    reviewer_did: Mapped[str] = mapped_column(String(160))
    reviewer_wallet: Mapped[str | None] = mapped_column(String(42), nullable=True, index=True)
    reviewer_organization_id: Mapped[str | None] = mapped_column(String(128), nullable=True, index=True)
    decision: Mapped[str] = mapped_column(String(16), index=True)
    evidence_hash: Mapped[str] = mapped_column(String(66))
    notes_hash: Mapped[str] = mapped_column(String(66))
    signature: Mapped[str | None] = mapped_column(Text, nullable=True)
    signature_scheme: Mapped[str | None] = mapped_column(String(24), nullable=True)
    signed_payload_hash: Mapped[str | None] = mapped_column(String(66), nullable=True)
    nonce: Mapped[str | None] = mapped_column(String(66), nullable=True)
    deadline: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)


class ReviewChallenge(Base, TimestampMixin):
    __tablename__ = "workflow_review_challenges"
    __table_args__ = (UniqueConstraint("attempt_id", name="uq_review_challenge_attempt"),)

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=uuid4_str)
    workflow_id: Mapped[str] = mapped_column(ForeignKey("workflows.id"), index=True)
    node_id: Mapped[str] = mapped_column(ForeignKey("workflow_nodes.id"), index=True)
    attempt_id: Mapped[str] = mapped_column(ForeignKey("node_attempts.id"), index=True)
    challenger_id: Mapped[str] = mapped_column(ForeignKey("agents.id"), index=True)
    challenger_did: Mapped[str] = mapped_column(String(160))
    status: Mapped[str] = mapped_column(String(24), default="OPEN", index=True)
    reason: Mapped[str] = mapped_column(Text)
    evidence_hash: Mapped[str] = mapped_column(String(66))
    resolution: Mapped[str | None] = mapped_column(String(32), nullable=True)
    resolution_reason: Mapped[str | None] = mapped_column(Text, nullable=True)
    resolved_by_id: Mapped[str | None] = mapped_column(ForeignKey("agents.id"), nullable=True, index=True)
    resolved_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    arbitration_threshold: Mapped[int] = mapped_column(Integer, default=2)
    onchain_review_id: Mapped[str | None] = mapped_column(String(66), nullable=True)
    challenge_open_tx_hash: Mapped[str | None] = mapped_column(String(66), nullable=True)


class ArbitrationVote(Base, TimestampMixin):
    __tablename__ = "workflow_arbitration_votes"
    __table_args__ = (
        UniqueConstraint("challenge_id", "voter_id", name="uq_arbitration_vote_challenge_voter"),
        UniqueConstraint("voter_wallet", "nonce", name="uq_arbitration_vote_wallet_nonce"),
    )

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=uuid4_str)
    workflow_id: Mapped[str] = mapped_column(ForeignKey("workflows.id"), index=True)
    node_id: Mapped[str] = mapped_column(ForeignKey("workflow_nodes.id"), index=True)
    attempt_id: Mapped[str] = mapped_column(ForeignKey("node_attempts.id"), index=True)
    challenge_id: Mapped[str] = mapped_column(ForeignKey("workflow_review_challenges.id"), index=True)
    voter_id: Mapped[str] = mapped_column(ForeignKey("agents.id"), index=True)
    voter_did: Mapped[str] = mapped_column(String(160))
    voter_wallet: Mapped[str] = mapped_column(String(42), index=True)
    voter_organization_id: Mapped[str] = mapped_column(String(128), index=True)
    resolution: Mapped[str] = mapped_column(String(32), index=True)
    reason_hash: Mapped[str] = mapped_column(String(66))
    signature: Mapped[str] = mapped_column(Text)
    signature_scheme: Mapped[str] = mapped_column(String(24), default="EIP712")
    signed_payload_hash: Mapped[str] = mapped_column(String(66))
    nonce: Mapped[str] = mapped_column(String(66))
    deadline: Mapped[datetime] = mapped_column(DateTime(timezone=True))


class SettlementRecoveryRecord(Base, TimestampMixin):
    __tablename__ = "workflow_settlement_recovery"
    __table_args__ = (UniqueConstraint("attempt_id", name="uq_settlement_recovery_attempt"),)

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=uuid4_str)
    workflow_id: Mapped[str] = mapped_column(ForeignKey("workflows.id"), index=True)
    node_id: Mapped[str] = mapped_column(ForeignKey("workflow_nodes.id"), index=True)
    attempt_id: Mapped[str] = mapped_column(ForeignKey("node_attempts.id"), unique=True, index=True)
    phase: Mapped[str] = mapped_column(String(32), default="COMMITTED", index=True)
    custody_chain_id: Mapped[int] = mapped_column(Integer, index=True)
    custody_contract: Mapped[str | None] = mapped_column(String(42), nullable=True)
    asset: Mapped[str] = mapped_column(String(32))
    token_address: Mapped[str | None] = mapped_column(String(42), nullable=True)
    amount: Mapped[Decimal] = mapped_column(Numeric(30, 12))
    transport_message_id: Mapped[str | None] = mapped_column(String(128), nullable=True)
    last_transaction_hash: Mapped[str | None] = mapped_column(String(66), nullable=True)
    proof_hash: Mapped[str | None] = mapped_column(String(66), nullable=True)
    failure_code: Mapped[str | None] = mapped_column(String(64), nullable=True)
    recovery_action: Mapped[str | None] = mapped_column(String(32), nullable=True)
    state_version: Mapped[int] = mapped_column(Integer, default=1)


class ProtocolRuntimeConfig(Base, TimestampMixin):
    __tablename__ = "workflow_protocol_runtime_config"
    __table_args__ = (UniqueConstraint("chain_id", "contract_role", name="uq_protocol_runtime_chain_role"),)

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=uuid4_str)
    chain_id: Mapped[int] = mapped_column(Integer, index=True)
    contract_role: Mapped[str] = mapped_column(String(64), index=True)
    contract_address: Mapped[str] = mapped_column(String(42))
    code_hash: Mapped[str] = mapped_column(String(66))
    manifest_hash: Mapped[str] = mapped_column(String(66))
    active: Mapped[bool] = mapped_column(Boolean, default=True, index=True)


class RouteDeployment(Base, TimestampMixin):
    __tablename__ = "route_deployments"
    __table_args__ = (UniqueConstraint("route_id", "route_version", name="uq_route_deployment_version"),)

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=uuid4_str)
    route_id: Mapped[str] = mapped_column(ForeignKey("chain_routes.id"), index=True)
    route_version: Mapped[int] = mapped_column(Integer)
    home_chain_id: Mapped[int] = mapped_column(Integer, index=True)
    trigger_agent_profile_id: Mapped[str | None] = mapped_column(ForeignKey("workflow_agent_profiles.id"), nullable=True, index=True)
    status: Mapped[str] = mapped_column(String(32), default="PLANNED", index=True)
    contracts: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict)
    transaction_hashes: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict)
    code_hashes: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict)
    manifest_hash: Mapped[str | None] = mapped_column(String(66), nullable=True)
    failure_reason: Mapped[str | None] = mapped_column(Text, nullable=True)
    deployed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)


class ProtocolJob(Base, TimestampMixin):
    __tablename__ = "workflow_protocol_jobs"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=uuid4_str)
    workflow_id: Mapped[str | None] = mapped_column(ForeignKey("workflows.id"), nullable=True, index=True)
    node_id: Mapped[str | None] = mapped_column(ForeignKey("workflow_nodes.id"), nullable=True, index=True)
    attempt_id: Mapped[str | None] = mapped_column(ForeignKey("node_attempts.id"), nullable=True, index=True)
    route_id: Mapped[str | None] = mapped_column(ForeignKey("chain_routes.id"), nullable=True, index=True)
    route_version: Mapped[int | None] = mapped_column(Integer, nullable=True)
    job_type: Mapped[str] = mapped_column(String(64), index=True)
    status: Mapped[str] = mapped_column(String(24), default="QUEUED", index=True)
    payload: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict)
    transaction_hash: Mapped[str | None] = mapped_column(String(66), nullable=True)
    attempts: Mapped[int] = mapped_column(Integer, default=0)
    last_error: Mapped[str | None] = mapped_column(Text, nullable=True)
    available_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow, index=True)
    completed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
