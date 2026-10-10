from __future__ import annotations

import re
from datetime import datetime
from decimal import Decimal, InvalidOperation
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator


def to_camel(value: str) -> str:
    parts = value.split("_")
    return parts[0] + "".join(part.capitalize() for part in parts[1:])


class ApiModel(BaseModel):
    model_config = ConfigDict(alias_generator=to_camel, populate_by_name=True, extra="forbid")


MoneyString = str


def validate_money(value: str) -> str:
    if not isinstance(value, str):
        raise ValueError("money must be a decimal string")
    if not re.fullmatch(r"(?:0|[1-9]\d*)(?:\.\d{1,8})?", value):
        raise ValueError("money must be a non-negative decimal string with at most 8 decimal places")
    try:
        if Decimal(value) < 0:
            raise ValueError("money cannot be negative")
    except InvalidOperation as exc:
        raise ValueError("invalid decimal money value") from exc
    return value


def validate_optional_money(value: str | None) -> str | None:
    if value is None:
        return None
    return validate_money(value)


def validate_token_amount(value: str) -> str:
    if not isinstance(value, str):
        raise ValueError("token amount must be a decimal string")
    if not re.fullmatch(r"(?:0|[1-9]\d*)(?:\.\d{1,77})?", value):
        raise ValueError("token amount must be a non-negative decimal string with at most 77 decimal places")
    try:
        if Decimal(value) < 0:
            raise ValueError("token amount cannot be negative")
    except InvalidOperation as exc:
        raise ValueError("invalid decimal token amount") from exc
    return value


def validate_optional_token_amount(value: str | None) -> str | None:
    if value is None:
        return None
    return validate_token_amount(value)


def validate_native_amount(value: str) -> str:
    if not isinstance(value, str):
        raise ValueError("native amount must be a decimal string")
    if not re.fullmatch(r"(?:0|[1-9]\d*)(?:\.\d{1,18})?", value):
        raise ValueError("native amount must be a non-negative decimal string with at most 18 decimal places")
    try:
        if Decimal(value) < 0:
            raise ValueError("native amount cannot be negative")
    except InvalidOperation as exc:
        raise ValueError("invalid decimal native amount") from exc
    return value


def validate_optional_native_amount(value: str | None) -> str | None:
    if value is None:
        return None
    return validate_native_amount(value)


class ChallengeRequest(ApiModel):
    did: str = Field(min_length=8, max_length=255)
    audience: str = Field(default="agent-market", min_length=3, max_length=255)


class VerifyRequest(ApiModel):
    challenge_id: str
    did: str
    signature: str
    audience: str = "agent-market"


class DevLoginRequest(ApiModel):
    did: str


class ConsoleLoginRequest(ApiModel):
    did: str = Field(min_length=8, max_length=255)
    secret: str = Field(min_length=1, max_length=512)


class CapabilityCreate(ApiModel):
    name: str = Field(min_length=2, max_length=160)
    slug: str = Field(pattern=r"^[a-z0-9]+(?:-[a-z0-9]+)*$", max_length=100)
    description: str = Field(min_length=5, max_length=5000)
    input_schema: dict[str, Any]
    output_schema: dict[str, Any]
    example_input: dict[str, Any] = Field(default_factory=dict)
    endpoint: str = Field(min_length=4, max_length=500)
    pricing_model: str = "fixed"
    base_price: MoneyString
    currency: str = Field(default="USDC", min_length=2, max_length=32)
    estimated_duration_seconds: int = Field(default=60, ge=1, le=604800)
    availability: Literal["available", "busy", "offline"] = "available"
    version: str = "1.0.0"

    _money = field_validator("base_price")(validate_money)


class DirectOrderCreate(ApiModel):
    input_payload: dict[str, Any]
    task_description: str | None = Field(default=None, max_length=10000)
    acceptance_criteria: list[str] = Field(default_factory=list, max_length=50)
    budget_max: MoneyString | None = None
    deadline: datetime | None = None
    delivery_format: str = Field(default="artifact_uri_and_hash", min_length=3, max_length=100)
    delivery_instructions: str = Field(default="", max_length=5000)
    dispute_window_seconds: int = Field(default=86400, ge=300, le=2592000)
    pricing_mode: Literal["budget_cap_actual"] = "budget_cap_actual"
    payer_account: str | None = Field(default=None, max_length=255)
    payee_account: str | None = Field(default=None, max_length=255)
    asset_id: str | None = Field(default=None, max_length=255)
    source_chain: str | None = Field(default=None, max_length=100)
    destination_chain: str | None = Field(default=None, max_length=100)
    source_asset: str = Field(default="ETH", min_length=2, max_length=64)
    settlement_token: str = Field(default="USDC", min_length=2, max_length=64)
    destination_local_asset: str = Field(default="STC", min_length=2, max_length=64)

    _budget_money = field_validator("budget_max")(validate_optional_money)


class ProviderOrderConfirmation(ApiModel):
    note: str = Field(default="", max_length=5000)


class AcceptDelivery(ApiModel):
    payable_amount: MoneyString | None = None
    verification_score: int | None = Field(default=None, ge=0, le=100)
    verification_summary: str = Field(default="", max_length=5000)

    _payable_money = field_validator("payable_amount")(validate_optional_money)


class InvokeRequest(ApiModel):
    input_payload: dict[str, Any]


class DemandCreate(ApiModel):
    title: str = Field(min_length=3, max_length=200)
    description: str = Field(min_length=5, max_length=10000)
    input_payload: dict[str, Any] = Field(default_factory=dict)
    acceptance_criteria: str = Field(min_length=3, max_length=5000)
    budget: MoneyString
    currency: str = Field(default="USDC", min_length=2, max_length=32)
    deadline: datetime | None = None
    status: Literal["draft", "published"] = "published"

    _money = field_validator("budget")(validate_money)


class BidCreate(ApiModel):
    amount: MoneyString
    currency: str = Field(default="USDC", min_length=2, max_length=32)
    proposal: str = Field(min_length=3, max_length=10000)
    estimated_duration_seconds: int = Field(ge=1, le=604800)
    capability_id: str | None = None

    _money = field_validator("amount")(validate_money)


class FundRequest(ApiModel):
    payer_account: str | None = Field(default=None, max_length=255)
    payee_account: str | None = Field(default=None, max_length=255)
    asset_id: str | None = Field(default=None, max_length=255)
    source_chain: str | None = Field(default=None, max_length=100)
    destination_chain: str | None = Field(default=None, max_length=100)
    quoted_settlement_amount: MoneyString | None = None
    min_settlement_amount: MoneyString | None = None
    max_slippage_bps: int | None = Field(default=None, ge=0, le=10000)
    quote_deadline: datetime | None = None
    quote_hash: str | None = Field(default=None, pattern=r"^0x[a-fA-F0-9]{64}$")
    quote_id: str | None = None
    swap_transaction_hash: str | None = Field(default=None, pattern=r"^0x[a-fA-F0-9]{64}$")

    _quoted_money = field_validator("quoted_settlement_amount", "min_settlement_amount")(validate_optional_money)


class SettlementQuoteRequest(ApiModel):
    source_asset: str = Field(default="ETH-like", min_length=2, max_length=255)
    settlement_token: str = Field(default="USDC", min_length=2, max_length=255)
    source_amount: MoneyString
    max_slippage_bps: int = Field(default=100, ge=0, le=10000)
    source_decimals: int = Field(default=18, ge=0, le=77)
    settlement_decimals: int = Field(default=6, ge=0, le=77)

    _source_money = field_validator("source_amount")(validate_money)


class SettlementQuoteResponse(ApiModel):
    source_asset: str
    settlement_token: str
    source_amount: MoneyString
    quoted_settlement_amount: MoneyString
    min_settlement_amount: MoneyString
    max_slippage_bps: int
    quote_deadline: datetime
    quote_hash: str
    rate: MoneyString
    terms_hash: str
    base_terms_hash: str
    onchain_order_id: str


class MessageCreate(ApiModel):
    content: str = Field(min_length=1, max_length=20000)


class DeliveryCreate(ApiModel):
    artifact_uri: str = Field(min_length=4, max_length=1000)
    content_hash: str = Field(min_length=16, max_length=128)
    summary: str = Field(default="", max_length=5000)
    output_payload: dict[str, Any] | None = None


class DisputeCreate(ApiModel):
    reason: str = Field(min_length=3, max_length=5000)


class RefundCreate(ApiModel):
    reason: str = Field(min_length=3, max_length=5000)


class ArbitrationResolve(ApiModel):
    outcome: Literal["release", "refund"]
    payable_amount: MoneyString | None = None
    rationale: str = Field(min_length=3, max_length=5000)
    evidence_hashes: list[str] = Field(default_factory=list, max_length=100)

    _payable_money = field_validator("payable_amount")(validate_optional_money)


class SettlementChainEventCreate(ApiModel):
    event_id: str = Field(min_length=3, max_length=160)
    event_type: Literal[
        "funds_locked",
        "release_submitted",
        "settlement_received",
        "release_confirmed",
        "settlement_failed",
        "refund_confirmed",
    ]
    onchain_settlement_id: str = Field(pattern=r"^0x[a-fA-F0-9]{64}$")
    transaction_hash: str = Field(pattern=r"^0x[a-fA-F0-9]{64}$")
    ccip_message_id: str | None = Field(default=None, pattern=r"^0x[a-fA-F0-9]{64}$")
    token_address: str | None = Field(default=None, pattern=r"^0x[a-fA-F0-9]{40}$")
    amount: MoneyString | None = None
    chain_selector: str | None = Field(default=None, pattern=r"^[0-9]+$")
    block_number: int | None = Field(default=None, ge=0)
    block_hash: str | None = Field(default=None, pattern=r"^0x[a-fA-F0-9]{64}$")
    payload_hash: str | None = Field(default=None, pattern=r"^0x[a-fA-F0-9]{64}$")
    confirmations: int = Field(default=1, ge=1)
    failure_reason: str | None = Field(default=None, max_length=5000)

    _event_money = field_validator("amount")(validate_optional_money)


class DexQuoteCreate(ApiModel):
    side: Literal["funding", "payout"]
    wallet_address: str = Field(pattern=r"^0x[a-fA-F0-9]{40}$")
    max_slippage_bps: int = Field(default=100, ge=1, le=500)
    sell_amount: MoneyString | None = None

    _sell_money = field_validator("sell_amount")(validate_optional_money)


class DexQuoteExecution(ApiModel):
    transaction_hash: str = Field(pattern=r"^0x[a-fA-F0-9]{64}$")


class RelayerTaskLease(ApiModel):
    worker_id: str = Field(min_length=3, max_length=160)
    lease_seconds: int = Field(default=300, ge=30, le=900)


class RelayerTaskHeartbeat(ApiModel):
    worker_id: str = Field(min_length=3, max_length=160)
    lease_token: str = Field(min_length=36, max_length=36)
    lease_seconds: int = Field(default=300, ge=30, le=900)


class RelayerTaskResult(ApiModel):
    worker_id: str = Field(min_length=3, max_length=160)
    lease_token: str = Field(min_length=36, max_length=36)
    outcome: Literal["submitted", "retry", "failed"]
    transaction_hash: str | None = Field(default=None, pattern=r"^0x[a-fA-F0-9]{64}$")
    transaction_nonce: int | None = Field(default=None, ge=0)
    error: str | None = Field(default=None, max_length=5000)


class StalledSettlementCheck(ApiModel):
    threshold_seconds: int = Field(default=3600, ge=60, le=604800)


class OrphanedChainEvent(ApiModel):
    event_id: str = Field(min_length=3, max_length=160)
    observed_block_hash: str = Field(pattern=r"^0x[a-fA-F0-9]{64}$")
    canonical_block_hash: str = Field(pattern=r"^0x[a-fA-F0-9]{64}$")


class ReviewCreate(ApiModel):
    rating: int = Field(ge=1, le=5)
    comment: str = Field(default="", max_length=5000)


class WebhookCreate(ApiModel):
    url: str = Field(pattern=r"^https?://", max_length=1000)
    events: list[str] = Field(min_length=1, max_length=50)
