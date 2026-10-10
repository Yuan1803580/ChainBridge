from __future__ import annotations

from datetime import datetime
import os
from typing import Any, Literal

from pydantic import Field, field_validator, model_validator

from .schemas import (
    ApiModel,
    MoneyString,
    validate_money,
    validate_native_amount,
    validate_optional_money,
    validate_optional_native_amount,
    validate_optional_token_amount,
)


class WorkflowNodeCreate(ApiModel):
    node_key: str = Field(pattern=r"^[A-Z][A-Z0-9_-]{0,15}$")
    title: str = Field(min_length=2, max_length=200)
    description: str = Field(default="", max_length=5000)
    capability: str = Field(min_length=2, max_length=100)
    depends_on: list[str] = Field(default_factory=list, max_length=20)
    acceptance_criteria: dict[str, Any] = Field(default_factory=dict)
    input_payload: dict[str, Any] = Field(default_factory=dict)
    reward: MoneyString
    risk_level: Literal["LOW", "MEDIUM", "HIGH"] = "LOW"
    review_policy_version: str = Field(default="RISK_REVIEW_V2", min_length=3, max_length=64)
    required_approvals: int | None = Field(default=None, ge=0, le=10)
    challenge_period_seconds: int | None = Field(default=None, ge=0, le=2592000)
    sampling_rate_bps: int | None = Field(default=None, ge=0, le=10000)
    review_budget: MoneyString = "0"
    max_attempts: int = Field(default=3, ge=1, le=10)

    _reward_money = field_validator("reward", "review_budget")(validate_money)

    @model_validator(mode="after")
    def normalize_review_policy(self) -> "WorkflowNodeCreate":
        if self.risk_level == "LOW":
            self.required_approvals = 0 if self.required_approvals is None else self.required_approvals
            self.challenge_period_seconds = 0 if self.challenge_period_seconds is None else self.challenge_period_seconds
            self.sampling_rate_bps = 0 if self.sampling_rate_bps is None else self.sampling_rate_bps
            if self.required_approvals or self.challenge_period_seconds or self.sampling_rate_bps:
                raise ValueError("LOW risk nodes use Coordinator automatic review without sampling or challenge")
        elif self.risk_level == "MEDIUM":
            self.required_approvals = 1 if self.required_approvals is None else self.required_approvals
            self.challenge_period_seconds = 0 if self.challenge_period_seconds is None else self.challenge_period_seconds
            self.sampling_rate_bps = 1000 if self.sampling_rate_bps is None else self.sampling_rate_bps
            if self.required_approvals != 1:
                raise ValueError("MEDIUM risk nodes require exactly one independent approval when sampled")
            if self.challenge_period_seconds != 0:
                raise ValueError("MEDIUM risk nodes do not use a challenge period")
        else:
            self.required_approvals = 2 if self.required_approvals is None else self.required_approvals
            self.challenge_period_seconds = 3600 if self.challenge_period_seconds is None else self.challenge_period_seconds
            self.sampling_rate_bps = 0 if self.sampling_rate_bps is None else self.sampling_rate_bps
            if self.required_approvals < 2:
                raise ValueError("HIGH risk nodes require at least two independent approvals")
            if self.challenge_period_seconds < 60:
                raise ValueError("HIGH risk nodes require a challenge period of at least 60 seconds")
            if self.sampling_rate_bps != 0:
                raise ValueError("HIGH risk nodes are always reviewed and cannot use sampling")
        return self


class WorkflowCreate(ApiModel):
    title: str = Field(min_length=3, max_length=200)
    objective: str = Field(min_length=10, max_length=10000)
    budget: MoneyString
    platform_fee: MoneyString = "1.10"
    currency: str = Field(default="USDC", min_length=2, max_length=32)
    source_chain_id: int = Field(default_factory=lambda: int(os.getenv("DYNAMIC_SOURCE_CHAIN_ID", "31337")), ge=1)
    input_payload: dict[str, Any] = Field(default_factory=dict)
    nodes: list[WorkflowNodeCreate] = Field(default_factory=list, max_length=50)
    funding_mode: Literal["RESERVED", "ONCHAIN"] = "RESERVED"
    payer_address: str | None = Field(default=None, pattern=r"^0x[a-fA-F0-9]{40}$")

    _money = field_validator("budget", "platform_fee")(validate_money)

    @model_validator(mode="after")
    def validate_node_graph(self) -> "WorkflowCreate":
        if self.funding_mode == "ONCHAIN" and self.payer_address is None:
            raise ValueError("payerAddress is required for ONCHAIN funding")
        keys = [node.node_key for node in self.nodes]
        if len(keys) != len(set(keys)):
            raise ValueError("nodeKey values must be unique")
        key_set = set(keys)
        for node in self.nodes:
            unknown = set(node.depends_on) - key_set
            if unknown:
                raise ValueError(f"node {node.node_key} has unknown dependencies: {sorted(unknown)}")
            if node.node_key in node.depends_on:
                raise ValueError(f"node {node.node_key} cannot depend on itself")
        return self


class WorkflowPlanRequest(ApiModel):
    objective: str = Field(min_length=10, max_length=10000)
    budget: MoneyString
    platform_fee: MoneyString = "1.10"
    currency: str = Field(default="USDC", min_length=2, max_length=32)
    input_payload: dict[str, Any] = Field(default_factory=dict)

    _money = field_validator("budget", "platform_fee")(validate_money)


class WorkflowFundingConfirmation(ApiModel):
    wallet_address: str = Field(pattern=r"^0x[a-fA-F0-9]{40}$")
    lock_tx_hash: str = Field(pattern=r"^0x[a-fA-F0-9]{64}$")
    source_gas_tx_hash: str | None = Field(default=None, pattern=r"^0x[a-fA-F0-9]{64}$")
    source_gas_amount: MoneyString = "0"
    verification_mode: Literal["RPC", "TEST"] = "RPC"

    _gas_money = field_validator("source_gas_amount")(validate_native_amount)


class ResultProofConfirmation(ApiModel):
    message_id: str = Field(min_length=3, max_length=128)
    transaction_hash: str = Field(pattern=r"^0x[a-fA-F0-9]{64}$")
    result_hash: str = Field(pattern=r"^0x[a-fA-F0-9]{64}$")
    proof_hash: str = Field(pattern=r"^0x[a-fA-F0-9]{64}$")
    gas_cost: MoneyString = "0"
    gas_symbol: str = Field(default="ETH", min_length=2, max_length=16)

    _gas_money = field_validator("gas_cost")(validate_native_amount)


class SettlementConfirmation(ApiModel):
    message_id: str = Field(min_length=3, max_length=128)
    transport_message_id: str | None = Field(default=None, pattern=r"^0x[a-fA-F0-9]{64}$")
    destination_tx_hash: str = Field(pattern=r"^0x[a-fA-F0-9]{64}$")
    source_confirm_tx_hash: str = Field(pattern=r"^0x[a-fA-F0-9]{64}$")
    received_amount: MoneyString
    home_wallet: str = Field(pattern=r"^0x[a-fA-F0-9]{40}$")
    settlement_fee: MoneyString = "0"
    settlement_asset: str = Field(default="USDC", min_length=2, max_length=32)
    payout_asset: str | None = Field(default=None, min_length=2, max_length=32)
    payout_token_address: str | None = Field(default=None, pattern=r"^0x[a-fA-F0-9]{40}$")
    payout_amount: MoneyString | None = None
    min_payout_amount: MoneyString | None = None
    payout_rate: MoneyString | None = None
    payout_tx_hash: str | None = Field(default=None, pattern=r"^0x[a-fA-F0-9]{64}$")

    _received_money = field_validator("received_amount")(validate_money)
    _gas_money = field_validator("settlement_fee")(validate_native_amount)
    _optional_money = field_validator("payout_amount", "min_payout_amount", "payout_rate")(validate_optional_token_amount)


class WorkflowFailureCreate(ApiModel):
    reason_code: Literal[
        "AGENT_TIMEOUT",
        "AGENT_INVALID_SIGNATURE",
        "AGENT_RESULT_REJECTED",
        "RESULT_PROOF_DELIVERY_PENDING",
        "CCIP_ROUTE_FAILURE",
        "RPC_FAILURE",
        "WORKER_FAILURE",
        "COORDINATOR_REVIEW_ERROR",
        "SETTLEMENT_PENDING",
        "SETTLEMENT_FAILED",
        "DISPUTED",
    ]
    evidence_hash: str | None = Field(default=None, pattern=r"^0x[a-fA-F0-9]{64}$")
    detail: str = Field(default="", max_length=5000)


class ReviewApprovalCreate(ApiModel):
    decision: Literal["PASS", "FAIL"]
    evidence_hash: str = Field(pattern=r"^0x[a-fA-F0-9]{64}$")
    notes: str = Field(default="", max_length=5000)
    reviewer_wallet: str | None = Field(default=None, pattern=r"^0x[a-fA-F0-9]{40}$")
    signature: str | None = Field(default=None, min_length=132, max_length=132)
    signature_scheme: Literal["EIP712"] = "EIP712"
    nonce: str | None = Field(default=None, pattern=r"^0x[a-fA-F0-9]{64}$")
    deadline: datetime | None = None


class ReviewApprovalPayloadRequest(ApiModel):
    decision: Literal["PASS", "FAIL"]
    evidence_hash: str = Field(pattern=r"^0x[a-fA-F0-9]{64}$")
    notes: str = Field(default="", max_length=5000)
    nonce: str = Field(pattern=r"^0x[a-fA-F0-9]{64}$")
    deadline: datetime


class ReviewChallengeCreate(ApiModel):
    reason: str = Field(min_length=3, max_length=5000)
    evidence_hash: str = Field(pattern=r"^0x[a-fA-F0-9]{64}$")


class ReviewChallengeResolution(ApiModel):
    resolution: Literal["UPHOLD_PASS", "REJECT_RESULT"]
    reason: str = Field(min_length=3, max_length=5000)


class ArbitrationVoteCreate(ApiModel):
    resolution: Literal["UPHOLD_PASS", "REJECT_RESULT"]
    reason: str = Field(min_length=3, max_length=5000)
    voter_wallet: str = Field(pattern=r"^0x[a-fA-F0-9]{40}$")
    signature: str = Field(min_length=132, max_length=132)
    signature_scheme: Literal["EIP712"] = "EIP712"
    nonce: str = Field(pattern=r"^0x[a-fA-F0-9]{64}$")
    deadline: datetime


class ArbitrationVotePayloadRequest(ApiModel):
    resolution: Literal["UPHOLD_PASS", "REJECT_RESULT"]
    reason: str = Field(min_length=3, max_length=5000)
    nonce: str = Field(pattern=r"^0x[a-fA-F0-9]{64}$")
    deadline: datetime


class RandomnessFulfillment(ApiModel):
    request_id: str = Field(min_length=8, max_length=128)
    random_word: str = Field(pattern=r"^(0x[a-fA-F0-9]{64}|[0-9]{1,78})$")
    provider_proof_hash: str = Field(pattern=r"^0x[a-fA-F0-9]{64}$")


class SettlementCustodyUpdate(ApiModel):
    phase: Literal[
        "IN_TRANSIT",
        "ARRIVED",
        "SWAP_PENDING",
        "SWAP_FAILED",
        "PAYOUT_PENDING",
        "PAID",
        "RECOVERY_REQUIRED",
        "REVERSE_BRIDGE_PENDING",
        "REFUNDED",
    ]
    custody_chain_id: int = Field(ge=1)
    custody_contract: str | None = Field(default=None, pattern=r"^0x[a-fA-F0-9]{40}$")
    asset: str = Field(min_length=2, max_length=32)
    token_address: str | None = Field(default=None, pattern=r"^0x[a-fA-F0-9]{40}$")
    amount: MoneyString
    transport_message_id: str | None = Field(default=None, min_length=3, max_length=128)
    transaction_hash: str | None = Field(default=None, pattern=r"^0x[a-fA-F0-9]{64}$")
    proof_hash: str = Field(pattern=r"^0x[a-fA-F0-9]{64}$")
    failure_code: str | None = Field(default=None, max_length=64)
    recovery_action: Literal["RETRY_SWAP", "HOLD_IN_VAULT", "REVERSE_BRIDGE", "REFUND"] | None = None
    expected_state_version: int = Field(ge=1)

    _amount_money = field_validator("amount")(validate_money)


class FailedRefundConfirmation(ApiModel):
    token_refund_tx_hash: str = Field(pattern=r"^0x[a-fA-F0-9]{64}$")
    gas_refund_tx_hash: str | None = Field(default=None, pattern=r"^0x[a-fA-F0-9]{64}$")
    source_gas_refund_amount: MoneyString = "0"

    _gas_refund_money = field_validator("source_gas_refund_amount")(validate_native_amount)


class WorkflowFinalizationConfirmation(ApiModel):
    platform_fee_tx_hash: str | None = Field(default=None, pattern=r"^0x[a-fA-F0-9]{64}$")
    token_refund_tx_hash: str = Field(pattern=r"^0x[a-fA-F0-9]{64}$")
    gas_refund_tx_hash: str | None = Field(default=None, pattern=r"^0x[a-fA-F0-9]{64}$")
    source_gas_refund_amount: MoneyString = "0"

    _gas_refund_money = field_validator("source_gas_refund_amount")(validate_native_amount)


class RouteDeploymentCompletion(ApiModel):
    status: Literal["READY", "FAILED"]
    contracts: dict[str, str] = Field(default_factory=dict)
    transaction_hashes: dict[str, str] = Field(default_factory=dict)
    code_hashes: dict[str, str] = Field(default_factory=dict)
    manifest_hash: str | None = Field(default=None, pattern=r"^0x[a-fA-F0-9]{64}$")
    failure_reason: str | None = Field(default=None, max_length=5000)

    @model_validator(mode="after")
    def validate_deployment(self) -> "RouteDeploymentCompletion":
        if self.status == "READY" and not self.contracts:
            raise ValueError("contracts are required for a READY deployment")
        if self.status == "FAILED" and not self.failure_reason:
            raise ValueError("failureReason is required for a FAILED deployment")
        return self


class ProtocolRuntimeContractUpdate(ApiModel):
    chain_id: int = Field(ge=1)
    contract_address: str = Field(pattern=r"^0x[a-fA-F0-9]{40}$")
    code_hash: str = Field(pattern=r"^0x[a-fA-F0-9]{64}$")
    manifest_hash: str = Field(pattern=r"^0x[a-fA-F0-9]{64}$")


class ReviewChainStateConfirmation(ApiModel):
    chain_id: int = Field(ge=1)
    contract_address: str = Field(pattern=r"^0x[a-fA-F0-9]{40}$")
    status: Literal["APPROVALS_PENDING", "CHALLENGE_OPEN", "DISPUTED", "APPROVED", "REJECTED"]
    challenge_deadline: datetime | None = None
    transaction_hash: str | None = Field(default=None, pattern=r"^0x[a-fA-F0-9]{64}$")


class ProtocolJobUpdate(ApiModel):
    status: Literal["RUNNING", "COMPLETED", "RETRY", "FAILED"]
    transaction_hash: str | None = Field(default=None, pattern=r"^0x[a-fA-F0-9]{64}$")
    error: str | None = Field(default=None, max_length=5000)
    retry_after_seconds: int | None = Field(default=None, ge=1, le=2592000)


class AgentTransactionCancelRequest(ApiModel):
    reason: str = Field(default="Operator requested nonce recovery", min_length=3, max_length=500)


class CoordinateRequest(ApiModel):
    max_actions: int = Field(default=1, ge=1, le=100)


class AttemptResultCreate(ApiModel):
    output: dict[str, Any]
    proof_hash: str = Field(pattern=r"^0x[a-fA-F0-9]{64}$")
    runtime_envelope: dict[str, Any] | None = None
    result_message_id: str | None = Field(default=None, min_length=3, max_length=128)
    gas_cost: MoneyString | None = None
    gas_symbol: str = Field(default="ETH", min_length=2, max_length=16)
    transaction_hash: str | None = Field(default=None, pattern=r"^0x[a-fA-F0-9]{64}$")

    _gas_money = field_validator("gas_cost")(validate_optional_native_amount)


class AttemptGasDepositConfirmation(ApiModel):
    transaction_hash: str = Field(pattern=r"^0x[a-fA-F0-9]{64}$")
    amount: MoneyString

    _amount_money = field_validator("amount")(validate_native_amount)


class AttemptGasClosureConfirmation(ApiModel):
    charged_amount: MoneyString
    refunded_amount: MoneyString
    refund_transaction_hash: str = Field(pattern=r"^0x[a-fA-F0-9]{64}$")

    _amount_money = field_validator("charged_amount", "refunded_amount")(validate_native_amount)


class RouteUpdate(ApiModel):
    status: Literal["ACTIVE", "PAUSED", "RETIRED"] | None = None
    rpc_url: str | None = Field(default=None, min_length=8, max_length=255)
    source_gateway_address: str | None = Field(default=None, pattern=r"^0x[a-fA-F0-9]{40}$")
    remote_work_board_address: str | None = Field(default=None, pattern=r"^0x[a-fA-F0-9]{40}$")
    payout_receiver_address: str | None = Field(default=None, pattern=r"^0x[a-fA-F0-9]{40}$")
    token_address: str | None = Field(default=None, pattern=r"^0x[a-fA-F0-9]{40}$")
    source_token_address: str | None = Field(default=None, pattern=r"^0x[a-fA-F0-9]{40}$")
    destination_token_address: str | None = Field(default=None, pattern=r"^0x[a-fA-F0-9]{40}$")
    token_decimals: int | None = Field(default=None, ge=0, le=36)
    settlement_mode: Literal["mock", "ccip_direct", "ccip_dex"] | None = None
    confirmation_blocks: int | None = Field(default=None, ge=1, le=100)


class ChainRouteCreate(ApiModel):
    route_key: str = Field(pattern=r"^[A-Z][A-Z0-9_-]{2,79}$")
    source_chain_id: int = Field(default=31337, ge=1)
    destination_chain_id: int = Field(ge=1)
    rpc_url: str = Field(pattern=r"^(?:https?://[^\s]+|secretref:DESTINATION_RPC_URL)$", max_length=255)
    transport: Literal["LOCAL_MOCK", "CHAINLINK_CCIP"] = "LOCAL_MOCK"
    source_chain_selector: str | None = Field(default=None, pattern=r"^[1-9][0-9]{0,31}$")
    destination_chain_selector: str | None = Field(default=None, pattern=r"^[1-9][0-9]{0,31}$")
    source_router_address: str | None = Field(default=None, pattern=r"^0x[a-fA-F0-9]{40}$")
    destination_router_address: str | None = Field(default=None, pattern=r"^0x[a-fA-F0-9]{40}$")
    token_address: str | None = Field(default=None, pattern=r"^0x[a-fA-F0-9]{40}$")
    source_token_address: str | None = Field(default=None, pattern=r"^0x[a-fA-F0-9]{40}$")
    destination_token_address: str | None = Field(default=None, pattern=r"^0x[a-fA-F0-9]{40}$")
    token_decimals: int = Field(default=6, ge=0, le=36)
    settlement_mode: Literal["mock", "ccip_direct", "ccip_dex"] = "mock"
    settlement_asset: str = Field(default="USDC", min_length=2, max_length=32)
    payout_asset: str = Field(default="USDC", min_length=2, max_length=32)
    payout_decimals: int = Field(default=6, ge=0, le=36)
    payout_rate: MoneyString = "1"
    payout_slippage_bps: int = Field(default=100, ge=0, le=5000)
    confirmation_blocks: int = Field(default=1, ge=1, le=100)

    _rate_money = field_validator("payout_rate")(validate_money)

    @model_validator(mode="after")
    def validate_chain_pair(self) -> "ChainRouteCreate":
        if self.source_chain_id == self.destination_chain_id:
            raise ValueError("sourceChainId and destinationChainId must differ")
        if self.transport == "CHAINLINK_CCIP" and not all((
            self.source_chain_selector,
            self.destination_chain_selector,
            self.source_router_address,
            self.destination_router_address,
            self.destination_token_address or self.token_address,
        )):
            raise ValueError("CHAINLINK_CCIP routes require both selectors, Router addresses, and the destination token address")
        if self.transport == "CHAINLINK_CCIP" and not self.source_token_address:
            raise ValueError("CHAINLINK_CCIP routes require sourceTokenAddress")
        if self.transport == "CHAINLINK_CCIP" and self.payout_asset != self.settlement_asset:
            raise ValueError("CHAINLINK_CCIP V1 requires the Agent to receive the bridged settlement asset directly")
        if self.transport == "CHAINLINK_CCIP" and self.settlement_mode != "ccip_direct":
            raise ValueError("CHAINLINK_CCIP V1 requires settlementMode=ccip_direct")
        if self.transport == "LOCAL_MOCK" and self.settlement_mode != "mock":
            raise ValueError("LOCAL_MOCK routes require settlementMode=mock")
        return self


class ExternalAgentOnboardingCreate(ApiModel):
    invite_code: str | None = Field(default=None, min_length=24, max_length=256)
    owner_name: str = Field(min_length=2, max_length=120)
    owner_email: str = Field(pattern=r"^[^@\s]+@[^@\s]+\.[^@\s]+$", max_length=255)
    organization: str | None = Field(default=None, max_length=160)
    agent_name: str = Field(min_length=2, max_length=160)
    agent_key: str = Field(pattern=r"^[A-Za-z][A-Za-z0-9_-]{1,79}$")
    did: str | None = Field(default=None, min_length=8, max_length=255)
    endpoint: str | None = Field(default=None, pattern=r"^https?://[^\s]+$", max_length=500)
    public_key: str = Field(min_length=8, max_length=5000)
    llm_provider: str = Field(default="deepseek", min_length=2, max_length=80)
    llm_model: str = Field(default="deepseek-chat", min_length=2, max_length=120)
    system_prompt: str = Field(default="", max_length=5000)
    capabilities: list[str] = Field(default_factory=list, max_length=50)
    preferred_chain_id: int | None = Field(default=None, ge=1)
    preferred_route_key: str | None = Field(default=None, pattern=r"^[A-Z][A-Z0-9_-]{2,79}$")
    preferred_route_version: int | None = Field(default=None, ge=1)
    payout_asset: str = Field(default="BTK", min_length=2, max_length=32)
    settlement_asset: str = Field(default="USDC", min_length=2, max_length=32)
    home_wallet: str | None = Field(default=None, pattern=r"^0x[a-fA-F0-9]{40}$")
    price_floor: MoneyString = "0"
    currency: str = Field(default="USDC", min_length=2, max_length=32)
    runtime_id: str | None = Field(default=None, min_length=3, max_length=128)
    signer_url: str | None = Field(default=None, pattern=r"^http://(?:127\.0\.0\.1|localhost|\[::1\])(?::[0-9]+)?(?:/.*)?$")
    signer_credential_ref: str | None = Field(default=None, pattern=r"^(?:env|file):.+$", max_length=500)
    executor_type: Literal["llm", "http"] = "llm"
    executor_config: dict[str, Any] = Field(default_factory=dict)
    roles: list[str] = Field(default_factory=lambda: ["provider"], min_length=1, max_length=8)

    _price_money = field_validator("price_floor")(validate_money)

    @field_validator("roles")
    @classmethod
    def normalize_roles(cls, value: list[str]) -> list[str]:
        normalized = sorted({str(item).strip().lower() for item in value if str(item).strip()})
        allowed = {"provider", "requester"}
        if not normalized or not set(normalized).issubset(allowed):
            raise ValueError("roles must be a non-empty subset of: provider, requester")
        return normalized

    @model_validator(mode="after")
    def validate_role_requirements(self) -> "ExternalAgentOnboardingCreate":
        if "provider" in self.roles and not any(item.strip() for item in self.capabilities):
            raise ValueError("Provider registrations require at least one capability")
        return self


class AgentDescriptorOnboardingCreate(ApiModel):
    """声明式接入：提交一份描述文件（JSON / Markdown 内嵌 ```json 块 / YAML frontmatter），
    由平台解析后等价转换为 ExternalAgentOnboardingCreate，实现「一份 md 文件一键接入」。

    roles 可自由填写 provider / requester 的任意组合（含两者兼得）。
    """

    descriptor: str = Field(min_length=2, max_length=20000)
    format: Literal["json", "markdown", "auto"] = "auto"


class AgentInvitationCreate(ApiModel):
    route_key: str = Field(pattern=r"^[A-Z][A-Z0-9_-]{2,79}$")
    min_route_version: int = Field(default=1, ge=1)
    max_route_version: int = Field(default=1, ge=1)
    capabilities: list[str] = Field(default_factory=list, max_length=50)
    roles: list[str] = Field(default_factory=lambda: ["provider"], min_length=1, max_length=2)
    executor_type: Literal["llm", "http"] = "llm"
    executor_credential_ref: str | None = Field(
        default="env:DEEPSEEK_API_KEY",
        pattern=r"^(?:env|file):.+$",
        max_length=500,
    )
    expires_in_hours: int = Field(default=24, ge=1, le=720)

    @field_validator("roles")
    @classmethod
    def normalize_roles(cls, value: list[str]) -> list[str]:
        normalized = sorted({str(item).strip().lower() for item in value if str(item).strip()})
        if not normalized or not set(normalized).issubset({"provider", "requester"}):
            raise ValueError("roles must be a non-empty subset of: provider, requester")
        return normalized

    @model_validator(mode="after")
    def validate_versions(self) -> "AgentInvitationCreate":
        if self.max_route_version < self.min_route_version:
            raise ValueError("maxRouteVersion must be at least minRouteVersion")
        if "provider" in self.roles and not any(item.strip() for item in self.capabilities):
            raise ValueError("Provider invitations require at least one capability")
        return self


class SelfServiceInvitationRequest(ApiModel):
    agent_key: str = Field(pattern=r"^[A-Za-z][A-Za-z0-9_-]{1,79}$")
    did: str = Field(min_length=8, max_length=255)
    runtime_id: str = Field(pattern=r"^[A-Za-z0-9][A-Za-z0-9._-]{2,127}$")
    home_wallet: str = Field(pattern=r"^0x[a-fA-F0-9]{40}$")
    public_key: str = Field(min_length=8, max_length=5000)
    roles: list[str] = Field(default_factory=lambda: ["provider"], min_length=1, max_length=2)

    @field_validator("roles")
    @classmethod
    def normalize_roles(cls, value: list[str]) -> list[str]:
        normalized = sorted({str(item).strip().lower() for item in value if str(item).strip()})
        if not normalized or not set(normalized).issubset({"provider", "requester"}):
            raise ValueError("roles must be a non-empty subset of: provider, requester")
        return normalized


class ExternalAgentOwnershipVerify(ApiModel):
    challenge_id: str = Field(min_length=36, max_length=36)
    identity_signature: str = Field(min_length=16, max_length=2048)
    wallet_signature: str = Field(min_length=16, max_length=2048)


class ExternalAgentHeartbeatChallengeRequest(ApiModel):
    status: Literal["ONLINE", "BUSY", "OFFLINE"] = "ONLINE"
    capacity_available: bool = True
    endpoint_latency_ms: int = Field(default=500, ge=0, le=60000)


class ExternalAgentHeartbeat(ApiModel):
    challenge_id: str = Field(min_length=36, max_length=36)
    status: Literal["ONLINE", "BUSY", "OFFLINE"] = "ONLINE"
    capacity_available: bool = True
    endpoint_latency_ms: int = Field(default=500, ge=0, le=60000)
    signature: str = Field(min_length=16, max_length=2048)


class ExternalAgentActivationReview(ApiModel):
    notes: str = Field(default="", max_length=5000)


class ExternalAgentTechnicalCheckRequest(ApiModel):
    runtime_access_token: str = Field(min_length=16, max_length=2048)


class ExternalAgentSelfServiceCompletion(ApiModel):
    runtime_access_token: str | None = Field(default=None, min_length=16, max_length=2048)


class WorkflowAgentProfileCreate(ApiModel):
    agent_id: str = Field(min_length=3, max_length=36)
    agent_key: str = Field(pattern=r"^[A-Za-z][A-Za-z0-9_-]{1,79}$")
    chain_id: int = Field(ge=1)
    home_wallet: str | None = Field(default=None, pattern=r"^0x[a-fA-F0-9]{40}$")
    capabilities: list[str] = Field(min_length=1, max_length=50)
    priority: int = Field(default=100, ge=1, le=1000)
    price_floor: MoneyString = "0"
    currency: str = Field(default="USDC", min_length=2, max_length=32)
    success_rate_bps: int = Field(default=9000, ge=0, le=10000)
    trust_score: int = Field(default=80, ge=0, le=100)
    endpoint_latency_ms: int = Field(default=500, ge=0, le=60000)
    availability: Literal["ONLINE", "BUSY", "OFFLINE"] = "ONLINE"
    capacity_available: bool = True
    gas_ready: bool = True
    last_heartbeat_at: datetime | None = None
    quote_expires_at: datetime | None = None
    metadata: dict[str, Any] = Field(default_factory=dict)
    credential_status: Literal["UNCONFIGURED", "REFERENCE_ONLY", "ACTIVE", "REVOKED"] = "UNCONFIGURED"
    credential_ref: str | None = Field(default=None, min_length=8, max_length=255)
    credential_fingerprint: str | None = Field(default=None, min_length=8, max_length=128)

    _price_money = field_validator("price_floor")(validate_money)


class WorkflowAgentProfileUpdate(ApiModel):
    chain_id: int | None = Field(default=None, ge=1)
    home_wallet: str | None = Field(default=None, pattern=r"^0x[a-fA-F0-9]{40}$")
    capabilities: list[str] | None = Field(default=None, min_length=1, max_length=50)
    priority: int | None = Field(default=None, ge=1, le=1000)
    active: bool | None = None
    price_floor: MoneyString | None = None
    currency: str | None = Field(default=None, min_length=2, max_length=32)
    success_rate_bps: int | None = Field(default=None, ge=0, le=10000)
    trust_score: int | None = Field(default=None, ge=0, le=100)
    endpoint_latency_ms: int | None = Field(default=None, ge=0, le=60000)
    availability: Literal["ONLINE", "BUSY", "OFFLINE"] | None = None
    capacity_available: bool | None = None
    gas_ready: bool | None = None
    last_heartbeat_at: datetime | None = None
    quote_expires_at: datetime | None = None
    metadata: dict[str, Any] | None = None
    credential_status: Literal["UNCONFIGURED", "REFERENCE_ONLY", "ACTIVE", "REVOKED"] | None = None
    credential_ref: str | None = Field(default=None, min_length=8, max_length=255)
    credential_fingerprint: str | None = Field(default=None, min_length=8, max_length=128)

    _price_money = field_validator("price_floor")(validate_optional_money)
