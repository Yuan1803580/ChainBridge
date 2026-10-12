from __future__ import annotations

import hashlib
import json
import os
import secrets
from datetime import timedelta, timezone
from decimal import Decimal, ROUND_DOWN
from typing import Any

from fastapi import HTTPException
from jsonschema import Draft202012Validator, SchemaError, ValidationError
from sqlalchemy import Integer, cast, func, select
from sqlalchemy.orm import Session

from .models import Agent, utcnow
from .reputation import recompute_agent_reputation
from .result_quality import acceptance_coverage, apply_attempt_quality, extract_reported_score
from .review_security import (
    build_arbitration_vote_typed_data,
    build_review_approval_typed_data,
    recover_typed_signer,
)
from .security import derive_demo_private_key, public_key_hex
from .serializers import decimal_string
from .services import canonical_hash
from .workflow_models import (
    AgentMatchCandidate,
    AgentMatchRound,
    ArbitrationVote,
    BudgetReservation,
    ChainRoute,
    CrossChainMessage,
    CoordinatorDecision,
    FeeLedgerEntry,
    FailureIncident,
    GasDeposit,
    LedgerEntry,
    NodeAttempt,
    ProtocolRuntimeConfig,
    ProtocolJob,
    ReviewApproval,
    ReviewChallenge,
    SettlementRecoveryRecord,
    RouteDeployment,
    Workflow,
    WorkflowAgentProfile,
    WorkflowEvent,
    WorkflowNode,
)
from .workflow_schemas import (
    AttemptResultCreate,
    ResultProofConfirmation,
    SettlementConfirmation,
    WorkflowCreate,
    WorkflowFinalizationConfirmation,
    WorkflowFundingConfirmation,
    WorkflowNodeCreate,
    WorkflowPlanRequest,
)


CHAIN_A = int(os.getenv("DYNAMIC_SOURCE_CHAIN_ID", "31337"))
CHAIN_B = 31338
CHAIN_C = 31339
CHAIN_D = 31340
LOCAL_CHAINS = (CHAIN_A, CHAIN_B, CHAIN_C, CHAIN_D)
MATCH_POLICY_VERSION = "MATCH_V1"
MATCH_CANDIDATE_LIMIT = 3
PLANNER_POLICY_VERSION = "RULE_PLANNER_V1"
LLM_PROPOSAL_VERSION = "LLM_DAG_PROPOSAL_V1"
CORE_VALIDATOR_VERSION = "CORE_VALIDATOR_V1"
PAYOUT_ROUTE_CONFIG = {
    CHAIN_B: {"asset": "BTK", "decimals": 18, "rate": Decimal("2")},
    CHAIN_C: {"asset": "CTK", "decimals": 8, "rate": Decimal("3")},
    CHAIN_D: {"asset": "DTK", "decimals": 6, "rate": Decimal("4")},
}

FAILURE_POLICIES: dict[str, dict[str, Any]] = {
    "AGENT_TIMEOUT": {"party": "AGENT", "retryable": True, "agentFault": True, "routePaused": False, "gasTreatment": "AGENT_ATTEMPT_DEPOSIT"},
    "AGENT_EXECUTION_ERROR": {"party": "AGENT", "retryable": True, "agentFault": True, "routePaused": False, "gasTreatment": "AGENT_ATTEMPT_DEPOSIT"},
    "AGENT_EXECUTION_POLICY_LIMIT": {"party": "PLATFORM", "retryable": True, "agentFault": False, "routePaused": False, "gasTreatment": "PLATFORM_GAS_TREASURY"},
    "AGENT_INVALID_SIGNATURE": {"party": "AGENT", "retryable": True, "agentFault": True, "routePaused": False, "gasTreatment": "AGENT_ATTEMPT_DEPOSIT"},
    "AGENT_RESULT_REJECTED": {"party": "AGENT", "retryable": True, "agentFault": True, "routePaused": False, "gasTreatment": "AGENT_ATTEMPT_DEPOSIT"},
    "RESULT_CANONICALIZATION_MISMATCH": {"party": "PLATFORM", "retryable": True, "agentFault": False, "routePaused": False, "gasTreatment": "PLATFORM_GAS_TREASURY"},
    "RESULT_PROOF_DELIVERY_PENDING": {"party": "ROUTE", "retryable": True, "agentFault": False, "routePaused": False, "gasTreatment": "HOLD_PENDING"},
    "CCIP_ROUTE_FAILURE": {"party": "ROUTE", "retryable": True, "agentFault": False, "routePaused": True, "gasTreatment": "PLATFORM_GAS_TREASURY"},
    "RPC_FAILURE": {"party": "PLATFORM", "retryable": True, "agentFault": False, "routePaused": True, "gasTreatment": "PLATFORM_GAS_TREASURY"},
    "WORKER_FAILURE": {"party": "PLATFORM", "retryable": True, "agentFault": False, "routePaused": False, "gasTreatment": "PLATFORM_GAS_TREASURY"},
    "COORDINATOR_REVIEW_ERROR": {"party": "PLATFORM", "retryable": True, "agentFault": False, "routePaused": False, "gasTreatment": "PLATFORM_GAS_TREASURY"},
    "SETTLEMENT_PENDING": {"party": "ROUTE", "retryable": True, "agentFault": False, "routePaused": False, "gasTreatment": "HOLD_PENDING"},
    "SETTLEMENT_FAILED": {"party": "PLATFORM", "retryable": True, "agentFault": False, "routePaused": True, "gasTreatment": "PLATFORM_GAS_TREASURY"},
    "DISPUTED": {"party": "DISPUTED", "retryable": False, "agentFault": False, "routePaused": False, "gasTreatment": "HOLD_FOR_DISPUTE"},
}


def _allow_seeded_local_agents() -> bool:
    configured = os.getenv("MATCH_ALLOW_SEEDED_LOCAL_AGENTS")
    if configured is not None:
        return configured.lower() in {"1", "true", "yes"}
    return os.getenv("APP_ENV", "development").lower() == "test"


def _algorithm4_experiment_flag(name: str) -> bool:
    experiment_mode = os.getenv("ALGORITHM4_EXPERIMENT_MODE", "false").lower() in {
        "1",
        "true",
        "yes",
    }
    return experiment_mode and os.getenv(name, "false").lower() in {"1", "true", "yes"}


def _zero_address(seed: int) -> str:
    return f"0x{seed:040x}"


def seed_workflow_runtime(session: Session) -> None:
    """Seed only local runtime metadata; workflow graph logic is defined here."""
    is_local = os.getenv("APP_ENV", "development").lower() in {
        "development",
        "dev",
        "test",
    }
    local_agent_slugs = (
        "workflow-research-b",
        "workflow-verification-c",
        "workflow-verification-d",
    )
    if is_local:
        runner_base = os.getenv("AGENT_RUNNER_BASE_URL", "http://127.0.0.1:8010").rstrip("/")
        configured_runner_endpoints = os.getenv("AGENT_RUNNER_ENDPOINTS_JSON", "").strip()
        runner_endpoints: dict[str, str] = {}
        if configured_runner_endpoints:
            parsed_runner_endpoints = json.loads(configured_runner_endpoints)
            if not isinstance(parsed_runner_endpoints, dict) or not all(
                isinstance(key, str) and isinstance(value, str)
                for key, value in parsed_runner_endpoints.items()
            ):
                raise ValueError("AGENT_RUNNER_ENDPOINTS_JSON must be a string map")
            runner_endpoints = {
                key: value.rstrip("/")
                for key, value in parsed_runner_endpoints.items()
            }

        def runtime_endpoint(slug: str) -> str:
            return f"{runner_endpoints.get(slug, runner_base)}/agents/{slug}"

        existing_agents = {
            agent.slug: agent
            for agent in session.scalars(select(Agent).where(Agent.slug.in_(local_agent_slugs)))
        }
        for slug in local_agent_slugs:
            agent = existing_agents.get(slug)
            if agent is None:
                agent = Agent(
                    did=f"did:local:{slug}",
                    slug=slug,
                    display_name=slug.replace("-", " ").title(),
                    description="Local workflow Agent Runner identity.",
                    roles=["provider"],
                    status="active",
                    operational_public_key=public_key_hex(derive_demo_private_key(slug)),
                )
                session.add(agent)
                session.flush()
                existing_agents[slug] = agent
            agent.endpoint = runtime_endpoint(slug)
            agent.status = "active"
    if session.scalar(select(func.count()).select_from(ChainRoute)) == 0:
        session.add_all(
            [
                ChainRoute(
                    route_key="A_TO_B",
                    route_version=1,
                    source_chain_id=CHAIN_A,
                    destination_chain_id=CHAIN_B,
                    rpc_url=os.getenv("LOCAL_CHAIN_B_RPC_URL", "http://127.0.0.1:8546"),
                    source_gateway_address=_zero_address(0xA1),
                    remote_work_board_address=_zero_address(0xB1),
                    payout_receiver_address=_zero_address(0xB2),
                    token_address=_zero_address(0xB3),
                    settlement_asset="USDC",
                    payout_asset="BTK",
                    payout_decimals=18,
                    payout_rate=Decimal("2"),
                    payout_slippage_bps=100,
                ),
                ChainRoute(
                    route_key="A_TO_C",
                    route_version=1,
                    source_chain_id=CHAIN_A,
                    destination_chain_id=CHAIN_C,
                    rpc_url=os.getenv("LOCAL_CHAIN_C_RPC_URL", "http://127.0.0.1:8547"),
                    source_gateway_address=_zero_address(0xA1),
                    remote_work_board_address=_zero_address(0xC1),
                    payout_receiver_address=_zero_address(0xC2),
                    token_address=_zero_address(0xC3),
                    settlement_asset="USDC",
                    payout_asset="CTK",
                    payout_decimals=8,
                    payout_rate=Decimal("3"),
                    payout_slippage_bps=100,
                ),
                ChainRoute(
                    route_key="A_TO_D",
                    route_version=1,
                    source_chain_id=CHAIN_A,
                    destination_chain_id=CHAIN_D,
                    rpc_url=os.getenv("LOCAL_CHAIN_D_RPC_URL", "http://127.0.0.1:8548"),
                    source_gateway_address=_zero_address(0xA1),
                    remote_work_board_address=_zero_address(0xD1),
                    payout_receiver_address=_zero_address(0xD2),
                    token_address=_zero_address(0xD3),
                    settlement_asset="USDC",
                    payout_asset="DTK",
                    payout_decimals=6,
                    payout_rate=Decimal("4"),
                    payout_slippage_bps=100,
                ),
            ]
        )
        session.flush()

    if session.scalar(select(func.count()).select_from(WorkflowAgentProfile)) == 0:
        agent_by_slug = {
            agent.slug: agent
            for agent in session.scalars(
                select(Agent).where(
                    Agent.slug.in_(
                        [
                            "workflow-research-b",
                            "workflow-verification-c",
                            "workflow-verification-d",
                        ]
                    )
                )
            )
        }
        definitions = [
            ("Agent-B1", "workflow-research-b", CHAIN_B, "0x3C44CdDdB6a900fa2b585dd299e03d12FA4293BC", ["research"], 10, "4.20", 9600, 92, 180),
            ("Agent-C1", "workflow-verification-c", CHAIN_C, "0x90F79bf6EB2c4f870365E785982E1f101E93b906", ["verification"], 10, "6.80", 9400, 90, 240),
            ("Agent-D1", "workflow-verification-d", CHAIN_D, "0x15d34AAf54267DB7D7c367839AAf71A00a2C6A65", ["verification"], 20, "6.80", 9200, 88, 310),
        ]
        for agent_key, slug, chain_id, home_wallet, capabilities, priority, price, success_rate, trust, latency in definitions:
            agent = agent_by_slug.get(slug)
            if agent is None:
                continue
            if is_local:
                agent.endpoint = runtime_endpoint(slug)
            session.add(
                WorkflowAgentProfile(
                    agent_id=agent.id,
                    agent_key=agent_key,
                    chain_id=chain_id,
                    home_wallet=home_wallet,
                    capabilities=capabilities,
                    priority=priority,
                    admission_status="APPROVED",
                    chain_status="READY",
                    price_floor=Decimal(price),
                    success_rate_bps=success_rate,
                    trust_score=trust,
                    endpoint_latency_ms=latency,
                    availability="ONLINE",
                    capacity_available=True,
                    gas_ready=True,
                    last_heartbeat_at=utcnow(),
                    metadata_json={"localV1": True},
                    credential_status="ACTIVE" if is_local else "UNCONFIGURED",
                    credential_ref=(
                        f"local-runtime:{slug}" if is_local else None
                    ),
                )
            )
    elif is_local:
        replacement_slugs = {
            "Agent-B1": "workflow-research-b",
            "Agent-C1": "workflow-verification-c",
            "Agent-D1": "workflow-verification-d",
        }
        local_profiles = list(
            session.scalars(
                select(WorkflowAgentProfile).where(
                    WorkflowAgentProfile.agent_key.in_(
                        ["Agent-B1", "Agent-C1", "Agent-D1"]
                    )
                )
            )
        )
        for profile in local_profiles:
            replacement = session.scalar(
                select(Agent).where(Agent.slug == replacement_slugs[profile.agent_key])
            )
            if replacement is not None:
                profile.agent_id = replacement.id
            profile.active = True
            profile.admission_status = "APPROVED"
            profile.chain_status = "READY"
            profile.availability = "ONLINE"
            profile.capacity_available = True
            profile.gas_ready = True
            profile.last_heartbeat_at = utcnow()
            profile.credential_status = "ACTIVE"
            profile.credential_ref = profile.credential_ref or (
                f"local-runtime:{profile.agent_key}"
            )
    session.commit()


def _event(
    session: Session,
    workflow: Workflow,
    event_type: str,
    *,
    actor: str = "Coordinator",
    node: WorkflowNode | None = None,
    attempt: NodeAttempt | None = None,
    payload: dict[str, Any] | None = None,
) -> WorkflowEvent:
    sequence = (
        session.scalar(
            select(func.max(WorkflowEvent.sequence)).where(WorkflowEvent.workflow_id == workflow.id)
        )
        or 0
    ) + 1
    item = WorkflowEvent(
        workflow_id=workflow.id,
        sequence=sequence,
        event_type=event_type,
        node_id=node.id if node else None,
        attempt_id=attempt.id if attempt else None,
        actor=actor,
        payload=payload or {},
    )
    session.add(item)
    session.flush()
    return item


def _refresh_agent_reputation(
    session: Session,
    workflow: Workflow,
    node: WorkflowNode,
    attempt: NodeAttempt,
) -> dict[str, Any] | None:
    profile = session.get(WorkflowAgentProfile, attempt.agent_profile_id)
    if profile is None:
        return None
    summary, changed = recompute_agent_reputation(
        session,
        profile,
        trigger_attempt_id=attempt.id,
    )
    if changed:
        _event(
            session,
            workflow,
            "AGENT_REPUTATION_UPDATED",
            actor="ReputationEngine",
            node=node,
            attempt=attempt,
            payload={"agentKey": profile.agent_key, **summary},
        )
    return summary


def _ledger(
    session: Session,
    workflow: Workflow,
    entry_type: str,
    amount: Decimal,
    from_account: str,
    to_account: str,
    *,
    asset_kind: str = "TOKEN",
    asset_symbol: str | None = None,
    chain_id: int = CHAIN_A,
    node: WorkflowNode | None = None,
    attempt: NodeAttempt | None = None,
    transaction_hash: str | None = None,
    metadata: dict[str, Any] | None = None,
) -> LedgerEntry:
    sequence = (
        session.scalar(
            select(func.max(LedgerEntry.sequence)).where(LedgerEntry.workflow_id == workflow.id)
        )
        or 0
    ) + 1
    item = LedgerEntry(
        workflow_id=workflow.id,
        sequence=sequence,
        entry_type=entry_type,
        asset_kind=asset_kind,
        asset_symbol=asset_symbol or workflow.currency,
        amount=amount,
        from_account=from_account,
        to_account=to_account,
        chain_id=chain_id,
        node_id=node.id if node else None,
        attempt_id=attempt.id if attempt else None,
        transaction_hash=transaction_hash,
        metadata_json=metadata or {},
    )
    session.add(item)
    session.flush()
    return item


def _default_nodes() -> list[WorkflowNodeCreate]:
    # This graph implements the product design directly. It does not originate
    # from projects.zip or its TaskDAG sample.
    return [
        WorkflowNodeCreate(
            nodeKey="N1",
            title="Research and structure source facts",
            description="Produce a structured evidence summary for downstream verification.",
            capability="research",
            reward="4.20",
            acceptanceCriteria={"requiredFields": ["summary"], "minScore": 80},
            inputPayload={"source": "workflow.input"},
            maxAttempts=2,
        ),
        WorkflowNodeCreate(
            nodeKey="N2",
            title="Verify evidence and publish report",
            description="Verify the settled N1 evidence against immutable workflow criteria.",
            capability="verification",
            dependsOn=["N1"],
            reward="6.80",
            acceptanceCriteria={"requiredFields": ["verified", "report"], "mustEqual": {"verified": True}},
            inputPayload={"evidence": "N1.output"},
            maxAttempts=3,
        ),
    ]


def _assess_node_risk(spec: WorkflowNodeCreate) -> dict[str, Any]:
    score = 0
    reasons: list[str] = []
    searchable = f"{spec.title} {spec.capability}".lower()
    high_impact_keywords = ("security", "contract", "settlement", "payment", "financial", "legal", "安全", "合约", "结算", "支付", "金融", "法律")
    review_keywords = ("verification", "audit", "analysis", "review", "验证", "审计", "分析", "复核")
    if any(keyword in searchable for keyword in high_impact_keywords):
        score += 55
        reasons.append("high-impact capability or task domain")
    elif any(keyword in searchable for keyword in review_keywords):
        score += 20
        reasons.append("verification or analytical capability")
    if spec.depends_on:
        dependency_score = min(30, 15 * len(spec.depends_on))
        score += dependency_score
        reasons.append(f"depends on {len(spec.depends_on)} settled node(s)")
    criteria = spec.acceptance_criteria or {}
    required_fields = criteria.get("requiredFields", [])
    if required_fields:
        score += min(15, 5 * len(required_fields))
        reasons.append(f"checks {len(required_fields)} required result field(s)")
    if criteria.get("mustEqual"):
        score += 15
        reasons.append("contains exact-value acceptance constraints")
    if criteria.get("minScore") is not None:
        score += 10
        reasons.append("contains a minimum quality score")
    if Decimal(spec.reward) >= Decimal("25"):
        score += 15
        reasons.append("high node reward exposure")
    elif Decimal(spec.reward) >= Decimal("10"):
        score += 8
        reasons.append("moderate node reward exposure")
    score = min(score, 100)
    risk_level = "HIGH" if score >= 70 else "MEDIUM" if score >= 30 else "LOW"
    return {"score": score, "riskLevel": risk_level, "reasons": reasons or ["no elevated risk indicator"]}


def _prepare_review_policy(spec: WorkflowNodeCreate) -> tuple[WorkflowNodeCreate, dict[str, Any]]:
    assessment = _assess_node_risk(spec)
    if "risk_level" in spec.model_fields_set:
        return spec, {**assessment, "riskLevel": spec.risk_level, "assignmentMode": "MANUAL"}

    risk_level = assessment["riskLevel"]
    app_env = os.getenv("APP_ENV", "development").lower()
    medium_sampling_default = "1000" if app_env == "production" else "0"
    policy_defaults = {
        "LOW": {"requiredApprovals": 0, "challengePeriodSeconds": 0, "samplingRateBps": 0},
        "MEDIUM": {
            "requiredApprovals": 1,
            "challengePeriodSeconds": 0,
            "samplingRateBps": int(os.getenv("AUTO_MEDIUM_SAMPLING_BPS", medium_sampling_default)),
        },
        "HIGH": {
            "requiredApprovals": 2,
            "challengePeriodSeconds": int(os.getenv("AUTO_HIGH_CHALLENGE_SECONDS", "3600")),
            "samplingRateBps": 0,
        },
    }[risk_level]
    prepared = WorkflowNodeCreate.model_validate({
        **spec.model_dump(by_alias=True),
        "riskLevel": risk_level,
        **policy_defaults,
    })
    return prepared, {**assessment, "assignmentMode": "AUTO"}


def _review_policy_snapshot(spec: WorkflowNodeCreate, assessment: dict[str, Any]) -> dict[str, Any]:
    return {
        "riskLevel": spec.risk_level,
        "riskAssignmentMode": assessment["assignmentMode"],
        "riskScore": assessment["score"],
        "riskReasons": assessment["reasons"],
        "reviewPolicyVersion": spec.review_policy_version,
        "requiredApprovals": spec.required_approvals,
        "challengePeriodSeconds": spec.challenge_period_seconds,
        "samplingRateBps": spec.sampling_rate_bps,
        "reviewBudget": str(spec.review_budget),
    }


def _topologically_sorted(nodes: list[WorkflowNodeCreate]) -> list[WorkflowNodeCreate]:
    dependencies = {node.node_key: set(node.depends_on) for node in nodes}
    remaining = set(dependencies)
    ordered: list[WorkflowNodeCreate] = []
    while remaining:
        ready = {key for key in remaining if not (dependencies[key] & remaining)}
        if not ready:
            raise HTTPException(status_code=422, detail="Workflow node dependencies contain a cycle")
        ordered.extend(node for node in nodes if node.node_key in ready)
        remaining -= ready
    return ordered


def _planning_node_specs(
    body: WorkflowPlanRequest,
) -> tuple[list[WorkflowNodeCreate], dict[str, str], str]:
    if body.proposed_nodes is None:
        return _default_nodes(), {}, PLANNER_POLICY_VERSION

    specs: list[WorkflowNodeCreate] = []
    rationales: dict[str, str] = {}
    for proposal in body.proposed_nodes:
        if Decimal(proposal.suggested_reward) <= 0:
            raise HTTPException(
                status_code=422,
                detail=f"Proposed reward for node {proposal.node_key} must be positive",
            )
        # Only business-level fields cross the trust boundary. Risk, Agent,
        # wallet, route and settlement authority are always assigned by Core.
        specs.append(
            WorkflowNodeCreate.model_validate(
                {
                    "nodeKey": proposal.node_key,
                    "title": proposal.title,
                    "description": proposal.description,
                    "capability": proposal.capability,
                    "dependsOn": proposal.depends_on,
                    "acceptanceCriteria": proposal.acceptance_criteria,
                    "inputPayload": proposal.input_payload,
                    "reward": proposal.suggested_reward,
                    "maxAttempts": proposal.max_attempts,
                }
            )
        )
        rationales[proposal.node_key] = proposal.rationale
    return (
        _topologically_sorted(specs),
        rationales,
        f"{LLM_PROPOSAL_VERSION}+{CORE_VALIDATOR_VERSION}",
    )


def _decision(
    session: Session,
    decision_type: str,
    payload: dict[str, Any],
    *,
    workflow: Workflow | None = None,
    node: WorkflowNode | None = None,
    attempt: NodeAttempt | None = None,
    policy_version: str,
) -> CoordinatorDecision:
    input_hash = canonical_hash(payload.get("input", payload))
    decision_hash = canonical_hash({"type": decision_type, "policy": policy_version, "payload": payload})
    existing = session.scalar(select(CoordinatorDecision).where(CoordinatorDecision.decision_hash == decision_hash))
    if existing is not None:
        return existing
    item = CoordinatorDecision(
        workflow_id=workflow.id if workflow else None,
        node_id=node.id if node else None,
        attempt_id=attempt.id if attempt else None,
        decision_type=decision_type,
        policy_version=policy_version,
        input_hash=input_hash,
        decision_hash=decision_hash,
        payload=payload,
    )
    session.add(item)
    session.flush()
    return item


def plan_workflow(session: Session, body: WorkflowPlanRequest) -> dict[str, Any]:
    raw_nodes, rationales, planner_version = _planning_node_specs(body)
    prepared_nodes = [_prepare_review_policy(spec) for spec in raw_nodes]
    node_specs = [spec for spec, _ in prepared_nodes]
    budget = Decimal(body.budget)
    fee = Decimal(body.platform_fee)
    rewards = sum((Decimal(item.reward) for item in node_specs), Decimal("0"))
    review_budgets = sum((Decimal(item.review_budget) for item in node_specs), Decimal("0"))
    if rewards + review_budgets + fee > budget:
        raise HTTPException(status_code=422, detail="Planned rewards, review budgets and platformFee exceed workflow budget")

    profiles = list(session.scalars(select(WorkflowAgentProfile).order_by(WorkflowAgentProfile.agent_key)))
    plan_nodes: list[dict[str, Any]] = []
    warnings: list[dict[str, Any]] = []
    for spec, assessment in prepared_nodes:
        candidates: list[dict[str, Any]] = []
        market_quotes: list[Decimal] = []
        preview_node = WorkflowNode(capability=spec.capability, reward=Decimal(spec.reward))
        for profile in profiles:
            route = _active_route(session, profile.chain_id)
            if _profile_exclusion_reason(
                session,
                preview_node,
                profile,
                route,
                set(),
                enforce_price=False,
            ) is not None:
                continue
            quote = Decimal(str(profile.price_floor))
            market_quotes.append(quote)
            if quote > Decimal(spec.reward):
                continue
            score, breakdown = _score_profile(profile, preview_node)
            candidates.append({
                "agentProfileId": profile.id,
                "agentKey": profile.agent_key,
                "homeChainId": profile.chain_id,
                "homeWallet": profile.home_wallet,
                "routeId": route.id,
                "routeKey": route.route_key,
                "routeVersion": route.route_version,
                "deploymentStatus": route.deployment_status,
                "score": str(score),
                "scoreBreakdown": breakdown,
                "quoteAmount": str(profile.price_floor),
                "credentialStatus": profile.credential_status,
                "settlementAsset": route.settlement_asset,
                "payoutAsset": route.payout_asset,
                "payoutRate": str(route.payout_rate),
            })
        candidates.sort(key=lambda item: (-Decimal(item["score"]), item["agentKey"]))
        lowest_quote = min(market_quotes) if market_quotes else None
        if candidates:
            market_status = "MATCHABLE"
        elif lowest_quote is not None:
            market_status = "BELOW_MARKET_PRICE"
            warnings.append(
                {
                    "code": "NODE_REWARD_BELOW_MARKET",
                    "nodeKey": spec.node_key,
                    "message": (
                        f"Node {spec.node_key} reward {spec.reward} is below the lowest "
                        f"currently matchable quote {decimal_string(lowest_quote)}"
                    ),
                }
            )
        else:
            market_status = "PENDING_AGENT"
            warnings.append(
                {
                    "code": "NO_MATCHABLE_AGENT",
                    "nodeKey": spec.node_key,
                    "message": f"Node {spec.node_key} currently has no matchable Agent and may wait for admission or route readiness",
                }
            )
        plan_nodes.append({
            **spec.model_dump(by_alias=True),
            "proposalRationale": rationales.get(spec.node_key, ""),
            "riskAssessment": assessment,
            "criteriaHash": canonical_hash(spec.acceptance_criteria),
            "marketQuote": {
                "status": market_status,
                "suggestedReward": spec.reward,
                "lowestAvailableQuote": decimal_string(lowest_quote) if lowest_quote is not None else None,
                "matchableCandidateCount": len(candidates),
            },
            "candidates": [{**candidate, "rank": index + 1} for index, candidate in enumerate(candidates[:MATCH_CANDIDATE_LIMIT])],
        })
    payload = {
        "plannerVersion": planner_version,
        "input": {"objective": body.objective, "budget": body.budget, "inputPayload": body.input_payload},
        "proposal": {
            "source": "LLM" if body.proposed_nodes is not None else "CORE_RULE_FALLBACK",
            "declaredVersion": body.proposal_version,
        },
        "nodes": plan_nodes,
        "validation": {
            "status": "VALID_WITH_WARNINGS" if warnings else "VALID",
            "validatorVersion": CORE_VALIDATOR_VERSION,
            "warnings": warnings,
        },
        "funding": {"mode": "RESERVED", "status": "MODEL_ONLY", "currency": body.currency},
    }
    decision = _decision(session, "WORKFLOW_PLAN", payload, policy_version=planner_version)
    payload["decisionId"] = decision.id
    payload["decisionHash"] = decision.decision_hash
    return payload


def create_workflow(session: Session, requester: Agent, body: WorkflowCreate) -> Workflow:
    if body.source_chain_id != CHAIN_A:
        raise HTTPException(status_code=422, detail=f"Dynamic workflows currently use source chain {CHAIN_A}")
    raw_node_specs = _topologically_sorted(body.nodes or _default_nodes())
    prepared_nodes = [_prepare_review_policy(spec) for spec in raw_node_specs]
    node_specs = [spec for spec, _ in prepared_nodes]
    budget = Decimal(body.budget)
    platform_fee = Decimal(body.platform_fee)
    rewards = sum((Decimal(node.reward) for node in node_specs), Decimal("0"))
    review_budgets = sum((Decimal(node.review_budget) for node in node_specs), Decimal("0"))
    if rewards + review_budgets + platform_fee > budget:
        raise HTTPException(status_code=422, detail="Node rewards, review budgets and platformFee exceed workflow budget")

    highest_reference_number = session.scalar(
        select(func.max(cast(func.substr(Workflow.reference_code, 4), Integer)))
    ) or 1000
    reference_code = f"WF-{highest_reference_number + 1}"
    terms = {
        "title": body.title,
        "objective": body.objective,
        "budget": body.budget,
        "platformFee": body.platform_fee,
        "currency": body.currency,
        "sourceChainId": body.source_chain_id,
        "inputPayload": body.input_payload,
        "nodes": [
            {**spec.model_dump(by_alias=True), "riskAssessment": assessment}
            for spec, assessment in prepared_nodes
        ],
    }
    workflow = Workflow(
        reference_code=reference_code,
        requester_id=requester.id,
        title=body.title,
        objective=body.objective,
        status="AWAITING_FUNDING" if body.funding_mode == "ONCHAIN" else "RUNNING",
        currency=body.currency,
        budget=budget,
        platform_fee=platform_fee,
        source_chain_id=body.source_chain_id,
        terms_hash=canonical_hash(terms),
        input_payload=body.input_payload,
        funding_mode=body.funding_mode,
        funding_status="AWAITING_CONFIRMATION" if body.funding_mode == "ONCHAIN" else "MODEL_RESERVED",
        funding_wallet=body.payer_address,
        contract_sync_status="PENDING_DEPLOYMENT" if body.funding_mode == "ONCHAIN" else "MODEL_ONLY",
    )
    session.add(workflow)
    session.flush()
    workflow.onchain_workflow_id = "0x" + workflow.id.replace("-", "").ljust(64, "0")

    for spec, assessment in prepared_nodes:
        policy_snapshot = _review_policy_snapshot(spec, assessment)
        node = WorkflowNode(
            workflow_id=workflow.id,
            node_key=spec.node_key,
            title=spec.title,
            description=spec.description,
            capability=spec.capability,
            status="READY" if not spec.depends_on else "BLOCKED",
            depends_on=spec.depends_on,
            acceptance_criteria=spec.acceptance_criteria,
            input_payload=spec.input_payload,
            reward=Decimal(spec.reward),
            risk_level=spec.risk_level,
            risk_assignment_mode=assessment["assignmentMode"],
            risk_score=assessment["score"],
            risk_reasons=assessment["reasons"],
            review_policy_version=spec.review_policy_version,
            review_policy_hash=canonical_hash(policy_snapshot),
            required_approvals=spec.required_approvals,
            challenge_period_seconds=spec.challenge_period_seconds,
            sampling_rate_bps=spec.sampling_rate_bps,
            review_budget=Decimal(spec.review_budget),
            max_attempts=spec.max_attempts,
        )
        session.add(node)
    session.flush()
    _event(
        session,
        workflow,
        "WORKFLOW_CREATED",
        actor=requester.did,
        payload={"referenceCode": reference_code, "nodeCount": len(node_specs), "budget": body.budget},
    )
    if body.funding_mode == "ONCHAIN":
        _event(
            session,
            workflow,
            "FUNDING_REQUIRED",
            actor=requester.did,
            payload={"amount": body.budget, "chainId": CHAIN_A, "payerAddress": body.payer_address},
        )
    else:
        _ledger(
            session,
            workflow,
            "MODEL_RESERVATION",
            budget,
            f"requester:{requester.id}:funding-model",
            f"escrow:{workflow.id}:available-model",
            metadata={"termsHash": workflow.terms_hash, "notOnchainFunds": True},
        )
        _event(
            session,
            workflow,
            "FUNDING_MODEL_RESERVED",
            payload={"amount": body.budget, "chainId": CHAIN_A, "notOnchainFunds": True},
        )
    _decision(
        session,
        "WORKFLOW_CREATED",
        {"input": terms, "referenceCode": reference_code, "fundingMode": body.funding_mode},
        workflow=workflow,
        policy_version=PLANNER_POLICY_VERSION,
    )
    session.flush()
    return workflow


def confirm_workflow_funding(
    session: Session,
    workflow: Workflow,
    body: WorkflowFundingConfirmation,
) -> dict[str, Any]:
    if workflow.funding_mode != "ONCHAIN" or workflow.funding_status != "AWAITING_CONFIRMATION":
        raise HTTPException(status_code=409, detail="Workflow is not awaiting an on-chain funding confirmation")
    if workflow.funding_wallet and workflow.funding_wallet.lower() != body.wallet_address.lower():
        raise HTTPException(status_code=422, detail="Funding wallet does not match the immutable workflow payer")
    workflow.funding_wallet = body.wallet_address
    workflow.approve_tx_hash = body.approve_tx_hash
    workflow.lock_tx_hash = body.lock_tx_hash
    workflow.source_gas_tx_hash = body.source_gas_tx_hash
    workflow.deployment_id = body.deployment_id
    workflow.source_gas_deposit = Decimal(body.source_gas_amount)
    workflow.funding_status = "CONFIRMED"
    workflow.status = "RUNNING"
    workflow.lock_confirmed_at = utcnow()
    workflow.contract_sync_status = "QUEUED"
    _ledger(
        session,
        workflow,
        "LOCK",
        Decimal(str(workflow.budget)),
        f"wallet:{body.wallet_address}",
        f"task-escrow:{workflow.onchain_workflow_id}",
        transaction_hash=body.lock_tx_hash,
        metadata={
            "termsHash": workflow.terms_hash,
            "approveTxHash": body.approve_tx_hash,
            "deploymentId": body.deployment_id,
            "verificationMode": body.verification_mode,
        },
    )
    if Decimal(body.source_gas_amount) > 0:
        session.add(
            GasDeposit(
                workflow_id=workflow.id,
                chain_id=CHAIN_A,
                depositor=body.wallet_address,
                asset="ETH",
                deposited_amount=Decimal(body.source_gas_amount),
                status="ACTIVE",
                deposit_tx_hash=body.source_gas_tx_hash,
            )
        )
    job = ProtocolJob(
        workflow_id=workflow.id,
        job_type="REGISTER_WORKFLOW",
        payload={
            "onchainWorkflowId": workflow.onchain_workflow_id,
            "termsHash": workflow.terms_hash,
            "payerAddress": body.wallet_address,
            "approveTxHash": body.approve_tx_hash,
            "lockTxHash": body.lock_tx_hash,
            "sourceGasTxHash": body.source_gas_tx_hash,
            "deploymentId": body.deployment_id,
        },
    )
    session.add(job)
    _event(
        session,
        workflow,
        "BUDGET_LOCKED_ONCHAIN",
        actor=body.wallet_address,
        payload={
            "amount": str(workflow.budget),
            "chainId": CHAIN_A,
            "approveTxHash": body.approve_tx_hash,
            "lockTxHash": body.lock_tx_hash,
            "sourceGasTxHash": body.source_gas_tx_hash,
            "deploymentId": body.deployment_id,
        },
    )
    return {"fundingStatus": workflow.funding_status, "protocolJobId": job.id}


def _queue_route_deployment(
    session: Session,
    workflow: Workflow,
    node: WorkflowNode,
    profile: WorkflowAgentProfile,
    route: ChainRoute,
    candidate: AgentMatchCandidate,
) -> ProtocolJob:
    deployment = session.scalar(
        select(RouteDeployment).where(
            RouteDeployment.route_id == route.id,
            RouteDeployment.route_version == route.route_version,
        )
    )
    if deployment is None:
        deployment = RouteDeployment(
            route_id=route.id,
            route_version=route.route_version,
            home_chain_id=profile.chain_id,
            trigger_agent_profile_id=profile.id,
            status="PLANNED",
        )
        session.add(deployment)
        session.flush()
    existing = session.scalar(
        select(ProtocolJob).where(
            ProtocolJob.route_id == route.id,
            ProtocolJob.route_version == route.route_version,
            ProtocolJob.job_type == "DEPLOY_ROUTE_STACK",
            ProtocolJob.status.in_(["QUEUED", "RUNNING"]),
        )
    )
    if existing is not None:
        return existing
    route.deployment_status = "DEPLOYING"
    deployment.status = "QUEUED"
    job = ProtocolJob(
        workflow_id=workflow.id,
        node_id=node.id,
        route_id=route.id,
        route_version=route.route_version,
        job_type="DEPLOY_ROUTE_STACK",
        payload={
            "deploymentId": deployment.id,
            "homeChainId": profile.chain_id,
            "homeWallet": profile.home_wallet,
            "agentProfileId": profile.id,
            "agentKey": profile.agent_key,
            "routeKey": route.route_key,
            "routeVersion": route.route_version,
            "credentialStatus": profile.credential_status,
            "credentialRef": profile.credential_ref,
            "candidateId": candidate.id,
        },
    )
    session.add(job)
    return job


def _active_route(session: Session, chain_id: int) -> ChainRoute | None:
    return session.scalar(
        select(ChainRoute)
        .where(
            ChainRoute.source_chain_id == CHAIN_A,
            ChainRoute.destination_chain_id == chain_id,
            ChainRoute.status == "ACTIVE",
        )
        .order_by(ChainRoute.route_version.desc())
    )


def _quote_expired(profile: WorkflowAgentProfile) -> bool:
    if profile.quote_expires_at is None:
        return False
    now = utcnow()
    if profile.quote_expires_at.tzinfo is None:
        now = now.replace(tzinfo=None)
    return profile.quote_expires_at <= now


def _excluded_attempted_agent_keys(
    session: Session,
    node: WorkflowNode,
) -> set[str]:
    """Exclude prior Agents unless policy permits a same-Agent recovery attempt."""
    try:
        max_timeout_attempts = max(
            1,
            int(os.getenv("AGENT_TIMEOUT_MAX_ATTEMPTS_PER_AGENT", "1")),
        )
    except ValueError:
        max_timeout_attempts = 1
    attempts = list(
        session.scalars(
            select(NodeAttempt)
            .where(NodeAttempt.node_id == node.id)
            .order_by(NodeAttempt.attempt_number)
        )
    )
    attempts_by_agent: dict[str, list[NodeAttempt]] = {}
    for attempt in attempts:
        attempts_by_agent.setdefault(attempt.agent_key, []).append(attempt)

    excluded: set[str] = set()
    for agent_key, agent_attempts in attempts_by_agent.items():
        latest = agent_attempts[-1]
        transient_retry_available = (
            latest.status == "FAILED"
            and latest.failure_reason_code == "AGENT_TIMEOUT"
            and len(agent_attempts) < max_timeout_attempts
        )
        integration_retry_available = (
            latest.status == "FAILED"
            and latest.failure_responsible_party == "PLATFORM"
        )
        if not (transient_retry_available or integration_retry_available):
            excluded.add(agent_key)
    return excluded


def _profile_capability_output_schema(
    profile: WorkflowAgentProfile,
    capability: str,
) -> dict[str, Any] | None:
    metadata = profile.metadata_json or {}
    verification = metadata.get("verification")
    manifest = verification.get("manifest") if isinstance(verification, dict) else None
    raw_capabilities = manifest.get("capabilities") if isinstance(manifest, dict) else None
    if not isinstance(raw_capabilities, list):
        return None
    expected = capability.strip().lower()
    for item in raw_capabilities:
        if not isinstance(item, dict):
            continue
        if str(item.get("capabilityId", "")).strip().lower() != expected:
            continue
        schema = item.get("outputSchema")
        return schema if isinstance(schema, dict) else None
    return None


def _output_schema_supports_acceptance(
    output_schema: dict[str, Any],
    acceptance_criteria: dict[str, Any],
) -> bool:
    required_fields = acceptance_criteria.get("requiredFields", [])
    if not isinstance(required_fields, list) or any(
        not isinstance(field, str) or not field.strip() for field in required_fields
    ):
        return False
    schema_type = output_schema.get("type")
    if schema_type not in (None, "object") and not (
        isinstance(schema_type, list) and "object" in schema_type
    ):
        return False
    schema_required = output_schema.get("required", [])
    if not isinstance(schema_required, list) or any(
        not isinstance(field, str) or not field.strip() for field in schema_required
    ):
        return False
    return set(required_fields).issubset(schema_required)


def _validate_attempt_output_contract(
    profile: WorkflowAgentProfile,
    node: WorkflowNode,
    output: dict[str, Any],
) -> None:
    metadata = profile.metadata_json or {}
    if metadata.get("localV1"):
        return
    output_schema = _profile_capability_output_schema(profile, node.capability)
    if output_schema is None:
        raise HTTPException(
            status_code=422,
            detail={
                "code": "agent_output_schema_unknown",
                "message": "Assigned Agent has no verified output Schema for this capability",
            },
        )
    try:
        Draft202012Validator.check_schema(output_schema)
        Draft202012Validator(output_schema).validate(output)
    except SchemaError as exc:
        raise HTTPException(
            status_code=422,
            detail={
                "code": "agent_output_schema_invalid",
                "message": exc.message,
            },
        ) from exc
    except ValidationError as exc:
        location = ".".join(str(item) for item in exc.absolute_path) or "output"
        raise HTTPException(
            status_code=422,
            detail={
                "code": "agent_output_schema_violation",
                "field": location,
                "message": exc.message,
            },
        ) from exc


def _profile_exclusion_reason(
    session: Session,
    node: WorkflowNode,
    profile: WorkflowAgentProfile,
    route: ChainRoute | None,
    previous_keys: set[str],
    *,
    enforce_price: bool = True,
) -> str | None:
    agent = session.get(Agent, profile.agent_id)
    if not profile.active or agent is None or agent.status != "active":
        return "AGENT_INACTIVE"
    if "provider" not in (agent.roles or []):
        return "AGENT_ROLE_NOT_EXECUTOR"
    metadata = profile.metadata_json or {}
    if str(metadata.get("agentType") or "EXECUTOR").upper() != "EXECUTOR":
        return "AGENT_TYPE_NOT_EXECUTOR"
    if metadata.get("localV1") and not _allow_seeded_local_agents():
        return "SEEDED_LOCAL_AGENT_DISABLED"
    if profile.admission_status != "APPROVED":
        return f"ADMISSION_{profile.admission_status}"
    if profile.chain_status != "READY":
        return f"CHAIN_{profile.chain_status}"
    if profile.agent_key in previous_keys and not _algorithm4_experiment_flag(
        "ALGORITHM4_DISABLE_AGENT_REUSE_GUARD"
    ):
        return "AGENT_ALREADY_ATTEMPTED_NODE"
    if node.capability not in profile.capabilities:
        return "CAPABILITY_MISMATCH"
    if not metadata.get("localV1"):
        output_schema = _profile_capability_output_schema(profile, node.capability)
        if output_schema is None:
            return "OUTPUT_SCHEMA_UNKNOWN"
        if not _output_schema_supports_acceptance(output_schema, node.acceptance_criteria or {}):
            return "OUTPUT_SCHEMA_MISMATCH"
    if profile.credential_status != "ACTIVE":
        return "CREDENTIAL_NOT_ACTIVE"
    if profile.availability != "ONLINE":
        return f"AGENT_{profile.availability}"
    if not metadata.get("localV1"):
        heartbeat_ttl = int(os.getenv("AGENT_HEARTBEAT_TTL_SECONDS", "60"))
        last_heartbeat = profile.last_heartbeat_at
        if last_heartbeat is not None and last_heartbeat.tzinfo is None:
            last_heartbeat = last_heartbeat.replace(tzinfo=timezone.utc)
        if (
            last_heartbeat is None
            or last_heartbeat < utcnow() - timedelta(seconds=heartbeat_ttl)
        ):
            return "AGENT_HEARTBEAT_STALE"
    if not profile.capacity_available:
        return "NO_AVAILABLE_CAPACITY"
    if not profile.gas_ready:
        return "GAS_NOT_READY"
    if _quote_expired(profile):
        return "QUOTE_EXPIRED"
    if enforce_price and Decimal(str(profile.price_floor)) > Decimal(str(node.reward)):
        return "PRICE_EXCEEDS_NODE_BUDGET"
    if route is None or route.status != "ACTIVE":
        return "NO_ACTIVE_ROUTE"
    return None


def _score_profile(profile: WorkflowAgentProfile, node: WorkflowNode) -> tuple[Decimal, dict[str, str]]:
    capability = Decimal("30")
    trust = Decimal(profile.trust_score) * Decimal("0.20")
    success = Decimal(profile.success_rate_bps) * Decimal("0.002")
    latency_ratio = min(Decimal(profile.endpoint_latency_ms) / Decimal("5000"), Decimal("1"))
    latency = (Decimal("1") - latency_ratio) * Decimal("10")
    reward = Decimal(str(node.reward))
    price = Decimal(str(profile.price_floor))
    price_ratio = min(price / reward, Decimal("1")) if reward > 0 else Decimal("1")
    price_fit = Decimal("10") - price_ratio * Decimal("5")
    route_health = Decimal("5")
    readiness = Decimal("5")
    breakdown = {
        "capability": str(capability),
        "trust": str(trust.quantize(Decimal("0.001"))),
        "success": str(success.quantize(Decimal("0.001"))),
        "latency": str(latency.quantize(Decimal("0.001"))),
        "priceFit": str(price_fit.quantize(Decimal("0.001"))),
        "routeHealth": str(route_health),
        "readiness": str(readiness),
    }
    return sum((Decimal(value) for value in breakdown.values()), Decimal("0")).quantize(Decimal("0.001")), breakdown


def _ranked_profiles(
    session: Session,
    node: WorkflowNode,
) -> list[tuple[WorkflowAgentProfile, ChainRoute, Decimal, dict[str, str]]]:
    previous_keys = _excluded_attempted_agent_keys(session, node)
    latest_attempt = session.scalar(
        select(NodeAttempt)
        .where(NodeAttempt.node_id == node.id)
        .order_by(NodeAttempt.attempt_number.desc())
    )
    ranked: list[tuple[WorkflowAgentProfile, ChainRoute, Decimal, dict[str, str]]] = []
    profiles = list(session.scalars(select(WorkflowAgentProfile).order_by(WorkflowAgentProfile.agent_key)))
    for profile in profiles:
        route = _active_route(session, profile.chain_id)
        if _profile_exclusion_reason(session, node, profile, route, previous_keys) is not None or route is None:
            continue
        score, breakdown = _score_profile(profile, node)
        if (
            latest_attempt is not None
            and latest_attempt.agent_key == profile.agent_key
            and latest_attempt.status == "FAILED"
            and latest_attempt.failure_reason_code == "AGENT_TIMEOUT"
        ):
            breakdown["recoveryContinuity"] = "100"
            score += Decimal("100")
        ranked.append((profile, route, score, breakdown))
    ranked.sort(key=lambda item: (-item[2], item[0].priority, item[0].agent_key))
    return ranked[:MATCH_CANDIDATE_LIMIT]


def matching_diagnostics(session: Session, node: WorkflowNode) -> dict[str, Any]:
    previous_keys = _excluded_attempted_agent_keys(session, node)
    exclusion_reasons: dict[str, int] = {}
    matchable_agents: list[dict[str, Any]] = []
    profiles = list(session.scalars(select(WorkflowAgentProfile).order_by(WorkflowAgentProfile.agent_key)))
    eligible_capability_profiles = [
        profile
        for profile in profiles
        if node.capability in profile.capabilities
        and not ((profile.metadata_json or {}).get("localV1") and not _allow_seeded_local_agents())
    ]
    for profile in profiles:
        route = _active_route(session, profile.chain_id)
        reason = _profile_exclusion_reason(session, node, profile, route, previous_keys)
        if reason is None and route is not None:
            matchable_agents.append({
                "agentKey": profile.agent_key,
                "chainId": profile.chain_id,
                "routeVersion": route.route_version,
            })
        else:
            code = reason or "NO_ACTIVE_ROUTE"
            exclusion_reasons[code] = exclusion_reasons.get(code, 0) + 1
    attempts_used = session.scalar(
        select(func.count()).select_from(NodeAttempt).where(NodeAttempt.node_id == node.id)
    ) or 0
    if matchable_agents:
        waiting_reason = "RECOVERY_PENDING"
    elif not eligible_capability_profiles:
        waiting_reason = "NO_AGENT_WITH_REQUIRED_CAPABILITY"
    elif all(profile.agent_key in previous_keys for profile in eligible_capability_profiles):
        waiting_reason = "ALL_CAPABLE_AGENTS_ALREADY_ATTEMPTED"
    else:
        waiting_reason = max(exclusion_reasons, key=exclusion_reasons.get, default="NO_MATCHABLE_AGENT")
    return {
        "capability": node.capability,
        "waitingReason": waiting_reason,
        "registeredAgentCount": len(profiles),
        "matchableAgentCount": len(matchable_agents),
        "matchableAgents": matchable_agents,
        "exclusionReasons": exclusion_reasons,
        "attemptsUsed": attempts_used,
        "maxAttempts": node.max_attempts,
        "remainingAttempts": max(0, node.max_attempts - attempts_used),
        "autoRecoveryEligible": node.status in {"READY", "RETRYING", "WAITING_FOR_AGENT"}
        and attempts_used < node.max_attempts,
    }


def _latest_match_round(session: Session, node: WorkflowNode, *, active_only: bool = False) -> AgentMatchRound | None:
    query = select(AgentMatchRound).where(AgentMatchRound.node_id == node.id)
    if active_only:
        query = query.where(AgentMatchRound.status == "ACTIVE")
    return session.scalar(query.order_by(AgentMatchRound.round_number.desc()))


def _build_match_round(
    session: Session,
    workflow: Workflow,
    node: WorkflowNode,
    ranked: list[tuple[WorkflowAgentProfile, ChainRoute, Decimal, dict[str, str]]],
) -> AgentMatchRound:
    round_count = session.scalar(select(func.count()).select_from(AgentMatchRound).where(AgentMatchRound.node_id == node.id)) or 0
    match_round = AgentMatchRound(
        workflow_id=workflow.id,
        node_id=node.id,
        round_number=round_count + 1,
        policy_version=MATCH_POLICY_VERSION,
        capability=node.capability,
        candidate_limit=MATCH_CANDIDATE_LIMIT,
        status="ACTIVE" if ranked else "EXHAUSTED",
    )
    session.add(match_round)
    session.flush()
    event_candidates: list[dict[str, Any]] = []
    for rank, (profile, route, score, breakdown) in enumerate(ranked, start=1):
        candidate = AgentMatchCandidate(
            match_round_id=match_round.id,
            workflow_id=workflow.id,
            node_id=node.id,
            agent_profile_id=profile.id,
            agent_key=profile.agent_key,
            rank=rank,
            score=score,
            score_breakdown=breakdown,
            quote_amount=node.reward,
            currency=workflow.currency,
            chain_id=profile.chain_id,
            home_wallet=profile.home_wallet,
            route_id=route.id,
            route_version=route.route_version,
        )
        session.add(candidate)
        event_candidates.append({"rank": rank, "agentKey": profile.agent_key, "score": str(score), "chainId": profile.chain_id})
    session.flush()
    _event(
        session,
        workflow,
        "AGENT_CANDIDATES_RANKED" if ranked else "AGENT_POOL_EXHAUSTED",
        node=node,
        payload={
            "matchingRound": match_round.round_number,
            "policyVersion": MATCH_POLICY_VERSION,
            "capability": node.capability,
            "candidates": event_candidates,
        },
    )
    return match_round


def _revalidate_candidate(
    session: Session,
    node: WorkflowNode,
    candidate: AgentMatchCandidate,
) -> tuple[WorkflowAgentProfile, ChainRoute] | None:
    profile = session.get(WorkflowAgentProfile, candidate.agent_profile_id)
    route = session.get(ChainRoute, candidate.route_id)
    previous_keys = _excluded_attempted_agent_keys(session, node)
    if profile is None:
        candidate.status = "UNAVAILABLE"
        candidate.exclusion_reason = "AGENT_PROFILE_REMOVED"
        return None
    reason = _profile_exclusion_reason(session, node, profile, route, previous_keys)
    if reason is not None:
        candidate.status = "UNAVAILABLE"
        candidate.exclusion_reason = reason
        return None
    return profile, route


def _select_from_round(
    session: Session,
    node: WorkflowNode,
    match_round: AgentMatchRound,
) -> tuple[WorkflowAgentProfile, ChainRoute, AgentMatchCandidate] | None:
    candidates = list(
        session.scalars(
            select(AgentMatchCandidate)
            .where(
                AgentMatchCandidate.match_round_id == match_round.id,
                AgentMatchCandidate.status == "AVAILABLE",
            )
            .order_by(AgentMatchCandidate.rank)
        )
    )
    for candidate in candidates:
        match = _revalidate_candidate(session, node, candidate)
        if match is None:
            continue
        profile, route = match
        candidate.status = "SELECTED"
        candidate.selected_at = utcnow()
        return profile, route, candidate
    match_round.status = "EXHAUSTED"
    return None


def _matching_candidate(
    session: Session,
    workflow: Workflow,
    node: WorkflowNode,
) -> tuple[WorkflowAgentProfile, ChainRoute, AgentMatchCandidate] | None:
    active_round = _latest_match_round(session, node, active_only=True)
    if active_round is not None:
        selected = _select_from_round(session, node, active_round)
        if selected is not None:
            return selected

    ranked = _ranked_profiles(session, node)
    latest_round = _latest_match_round(session, node)
    if not ranked:
        if latest_round is None or latest_round.status != "EXHAUSTED":
            _build_match_round(session, workflow, node, ranked)
        return None
    return _select_from_round(session, node, _build_match_round(session, workflow, node, ranked))


def _message_id(workflow: Workflow, node: WorkflowNode, attempt_number: int, message_type: str) -> str:
    raw = f"{workflow.id}:{node.id}:{attempt_number}:{message_type}".encode()
    return "0x" + hashlib.sha256(raw).hexdigest()


def dispatch_node(session: Session, workflow: Workflow, node: WorkflowNode) -> dict[str, Any]:
    attempt_count = session.scalar(select(func.count()).select_from(NodeAttempt).where(NodeAttempt.node_id == node.id)) or 0
    if attempt_count >= node.max_attempts:
        node.status = "FAILED"
        workflow.status = "FAILED"
        workflow.failure_reason = f"Node {node.node_key} exhausted its attempts"
        _event(session, workflow, "NODE_ATTEMPTS_EXHAUSTED", node=node)
        return {"action": "workflow_failed", "nodeId": node.id}

    match = _matching_candidate(session, workflow, node)
    if match is None:
        was_waiting = node.status == "WAITING_FOR_AGENT"
        node.status = "WAITING_FOR_AGENT"
        if not was_waiting:
            _event(
                session,
                workflow,
                "WAITING_FOR_AGENT",
                node=node,
                payload={"capability": node.capability, "retryable": True},
            )
        return {"action": "waiting_for_agent", "nodeId": node.id, "capability": node.capability}

    profile, route, candidate = match
    if workflow.funding_mode == "ONCHAIN" and route.deployment_status != "READY":
        candidate.status = "AVAILABLE"
        candidate.selected_at = None
        job = _queue_route_deployment(session, workflow, node, profile, route, candidate)
        node.status = "WAITING_FOR_ROUTE_DEPLOYMENT"
        _event(
            session,
            workflow,
            "ROUTE_DEPLOYMENT_QUEUED",
            node=node,
            payload={
                "jobId": job.id,
                "agentKey": profile.agent_key,
                "homeChainId": profile.chain_id,
                "routeId": route.id,
                "routeVersion": route.route_version,
            },
        )
        return {"action": "route_deployment_queued", "nodeId": node.id, "jobId": job.id}
    if not (profile.metadata_json or {}).get("localV1"):
        profile.availability = "BUSY"
        profile.capacity_available = False
    attempt_number = attempt_count + 1
    message_id = _message_id(workflow, node, attempt_number, "DISPATCH")
    payout_swap_enabled = bool(route.payout_token_address and route.payout_swap_address)
    attempt = NodeAttempt(
        node_id=node.id,
        attempt_number=attempt_number,
        status="DISPATCHED",
        agent_profile_id=profile.id,
        agent_key=profile.agent_key,
        agent_id=profile.agent_id,
        chain_id=profile.chain_id,
        home_wallet=candidate.home_wallet or profile.home_wallet,
        route_id=route.id,
        route_version=route.route_version,
        settlement_asset=route.settlement_asset,
        payout_asset=route.payout_asset if payout_swap_enabled else route.settlement_asset,
        payout_token_address=route.payout_token_address if payout_swap_enabled else route.token_address,
        payout_decimals=route.payout_decimals if payout_swap_enabled else 6,
        payout_rate=route.payout_rate if payout_swap_enabled else Decimal("1"),
        payout_slippage_bps=route.payout_slippage_bps if payout_swap_enabled else 0,
        payout_status="PENDING" if payout_swap_enabled else "NOT_REQUIRED",
        review_policy_hash=node.review_policy_hash,
        match_candidate_id=candidate.id,
        matching_trust_score=Decimal(str(profile.trust_score)),
        matching_trust_source="MATCH_TIME_PROFILE_SNAPSHOT",
        dispatch_message_id=message_id,
    )
    session.add(attempt)
    session.flush()
    node.current_attempt_id = attempt.id
    node.status = "ASSIGNED"
    reservation = BudgetReservation(
        workflow_id=workflow.id,
        node_id=node.id,
        attempt_id=attempt.id,
        amount=node.reward,
        currency=workflow.currency,
    )
    session.add(reservation)
    session.add(
        CrossChainMessage(
            message_id=message_id,
            workflow_id=workflow.id,
            node_id=node.id,
            attempt_id=attempt.id,
            message_type="DISPATCH",
            source_chain_id=CHAIN_A,
            destination_chain_id=profile.chain_id,
            route_id=route.id,
            route_version=route.route_version,
            payload={
                "workflowId": workflow.id,
                "nodeId": node.id,
                "attemptId": attempt.id,
                "attemptNumber": attempt_number,
                "criteriaHash": canonical_hash(node.acceptance_criteria),
                "riskLevel": node.risk_level,
                "reviewPolicyHash": node.review_policy_hash,
                "reward": str(node.reward),
                "settlementAsset": attempt.settlement_asset,
                "payoutAsset": attempt.payout_asset,
            },
        )
    )
    _ledger(
        session,
        workflow,
        "RESERVE",
        Decimal(str(node.reward)),
        f"escrow:{workflow.id}:available",
        f"escrow:{workflow.id}:reserved:{attempt.id}",
        node=node,
        attempt=attempt,
    )
    _event(
        session,
        workflow,
        "NODE_DISPATCHED",
        node=node,
        attempt=attempt,
        payload={
            "agentKey": profile.agent_key,
            "chainId": profile.chain_id,
            "routeId": route.id,
            "routeVersion": route.route_version,
            "messageId": message_id,
            "matchingRoundId": candidate.match_round_id,
            "candidateRank": candidate.rank,
            "matchScore": str(candidate.score),
        },
    )
    _decision(
        session,
        "AGENT_DISPATCH",
        {
            "input": {"nodeId": node.id, "capability": node.capability},
            "agentKey": profile.agent_key,
            "homeChainId": profile.chain_id,
            "routeId": route.id,
            "routeVersion": route.route_version,
            "candidateRank": candidate.rank,
            "matchScore": str(candidate.score),
        },
        workflow=workflow,
        node=node,
        attempt=attempt,
        policy_version=MATCH_POLICY_VERSION,
    )
    if workflow.funding_mode == "ONCHAIN":
        selected_agent = session.get(Agent, profile.agent_id)
        if selected_agent is None:
            raise HTTPException(status_code=409, detail="Selected Agent identity no longer exists")
        session.add(
            ProtocolJob(
                workflow_id=workflow.id,
                node_id=node.id,
                attempt_id=attempt.id,
                route_id=route.id,
                route_version=route.route_version,
                job_type="DISPATCH_ASSIGNMENT",
                payload={
                    "messageId": message_id,
                    "onchainWorkflowId": workflow.onchain_workflow_id,
                    "nodeId": node.id,
                    "attemptId": attempt.id,
                    "agentKey": profile.agent_key,
                    "agentDid": selected_agent.did,
                    "agentSlug": selected_agent.slug,
                    "agentRuntimeEndpoint": selected_agent.endpoint,
                    "agentOperationalPublicKey": selected_agent.operational_public_key,
                    "agentRoles": selected_agent.roles,
                    "agentCapabilities": profile.capabilities,
                    "homeChainId": profile.chain_id,
                    "homeWallet": attempt.home_wallet,
                    "criteriaHash": canonical_hash(node.acceptance_criteria),
                    "riskLevel": node.risk_level,
                    "reviewPolicyHash": node.review_policy_hash,
                    "reward": str(node.reward),
                },
            )
        )
    return {
        "action": "node_dispatched",
        "nodeId": node.id,
        "attemptId": attempt.id,
        "agentKey": profile.agent_key,
        "chainId": profile.chain_id,
        "routeVersion": route.route_version,
        "candidateRank": candidate.rank,
        "matchScore": str(candidate.score),
    }


def submit_attempt_result(
    session: Session,
    workflow: Workflow,
    node: WorkflowNode,
    attempt: NodeAttempt,
    body: AttemptResultCreate,
) -> dict[str, Any]:
    stale_guard_disabled = _algorithm4_experiment_flag("ALGORITHM4_DISABLE_STALE_ATTEMPT_GUARD")
    if not stale_guard_disabled and (
        node.current_attempt_id != attempt.id or attempt.status not in {"DISPATCHED", "EXECUTING"}
    ):
        _event(
            session,
            workflow,
            "STALE_ATTEMPT_REJECTED",
            actor=attempt.agent_key,
            node=node,
            attempt=attempt,
            payload={"currentAttemptId": node.current_attempt_id, "rejectedAttemptId": attempt.id},
        )
        return {"accepted": False, "code": "STALE_ATTEMPT", "attemptId": attempt.id}

    if workflow.funding_mode == "ONCHAIN" and body.transaction_hash is None:
        raise HTTPException(status_code=422, detail="ONCHAIN result submission requires the execution-chain transactionHash")

    result_payload = body.output
    profile = session.get(WorkflowAgentProfile, attempt.agent_profile_id)
    if profile is None:
        raise HTTPException(status_code=409, detail="Attempt Agent profile no longer exists")
    _validate_attempt_output_contract(profile, node, result_payload)
    result_hash = canonical_hash(result_payload)
    message_id = body.result_message_id or _message_id(workflow, node, attempt.attempt_number, "RESULT")
    requires_relay = workflow.funding_mode == "ONCHAIN"
    attempt.status = "RESULT_PROOF_PENDING" if requires_relay else "RESULT_SUBMITTED"
    attempt.result_payload = result_payload
    attempt.result_hash = result_hash
    attempt.proof_hash = body.proof_hash
    attempt.reported_score = extract_reported_score(result_payload)
    attempt.result_message_id = message_id
    attempt.submitted_at = utcnow()
    attempt.result_proof_status = "QUEUED" if requires_relay else "DELIVERED"
    node.status = "RESULT_PROOF_PENDING" if requires_relay else "REVIEWING"
    session.add(
        CrossChainMessage(
            message_id=message_id,
            workflow_id=workflow.id,
            node_id=node.id,
            attempt_id=attempt.id,
            message_type="RESULT_PROOF",
            source_chain_id=attempt.chain_id,
            destination_chain_id=CHAIN_A,
            route_id=attempt.route_id,
            route_version=attempt.route_version,
            status="QUEUED" if requires_relay else "DELIVERED",
            payload={"resultHash": result_hash, "proofHash": body.proof_hash},
            transaction_hash=body.transaction_hash,
            relay_attempts=0 if requires_relay else 1,
            delivered_at=None if requires_relay else utcnow(),
        )
    )
    if body.gas_cost is not None and Decimal(body.gas_cost) > 0:
        session.add(
            FeeLedgerEntry(
                workflow_id=workflow.id,
                node_id=node.id,
                attempt_id=attempt.id,
                category="AGENT_RESULT_SUBMISSION_GAS",
                initial_payer=f"agent:{attempt.agent_key}",
                final_responsible_party="AGENT",
                quoted_amount=Decimal(body.gas_cost),
                charged_amount=Decimal(body.gas_cost),
                asset=body.gas_symbol,
                transaction_hash=body.transaction_hash,
                message_id=message_id,
                status="CHARGED",
            )
        )
        _ledger(
            session,
            workflow,
            "RESULT_SUBMISSION_GAS",
            Decimal(body.gas_cost),
            f"agent:{attempt.agent_key}",
            f"network:{attempt.chain_id}:gas",
            asset_kind="GAS",
            asset_symbol=body.gas_symbol,
            chain_id=attempt.chain_id,
            node=node,
            attempt=attempt,
            transaction_hash=body.transaction_hash,
            metadata={"responsibility": "AGENT", "homeChainId": attempt.chain_id},
        )
    _event(
        session,
        workflow,
        "RESULT_PROOF_QUEUED" if requires_relay else "RESULT_SUBMITTED",
        actor=attempt.agent_key,
        node=node,
        attempt=attempt,
        payload={"resultHash": result_hash, "proofHash": body.proof_hash, "messageId": message_id},
    )
    if requires_relay:
        session.add(
            ProtocolJob(
                workflow_id=workflow.id,
                node_id=node.id,
                attempt_id=attempt.id,
                route_id=attempt.route_id,
                route_version=attempt.route_version,
                job_type="RELAY_RESULT_PROOF",
                payload={
                    "messageId": message_id,
                    "executionTxHash": body.transaction_hash,
                    "resultHash": result_hash,
                    "proofHash": body.proof_hash,
                    "sourceChainId": attempt.chain_id,
                },
            )
        )
    return {
        "accepted": True,
        "code": "RESULT_PROOF_PENDING" if requires_relay else "RESULT_ACCEPTED",
        "attemptId": attempt.id,
        "resultHash": result_hash,
    }


def confirm_result_proof(
    session: Session,
    workflow: Workflow,
    node: WorkflowNode,
    attempt: NodeAttempt,
    body: ResultProofConfirmation,
) -> dict[str, Any]:
    if attempt.status != "RESULT_PROOF_PENDING" or node.current_attempt_id != attempt.id:
        raise HTTPException(status_code=409, detail="Attempt is not awaiting a current RESULT_PROOF")
    if attempt.result_hash != body.result_hash or attempt.proof_hash != body.proof_hash:
        raise HTTPException(status_code=422, detail="Relayed proof hashes do not match the execution-chain result")
    message = session.scalar(
        select(CrossChainMessage).where(
            CrossChainMessage.attempt_id == attempt.id,
            CrossChainMessage.message_type == "RESULT_PROOF",
            CrossChainMessage.message_id == body.message_id,
        )
    )
    if message is None:
        raise HTTPException(status_code=404, detail="RESULT_PROOF message was not found")
    message.status = "DELIVERED"
    message.transaction_hash = body.transaction_hash
    message.relay_attempts += 1
    message.delivered_at = utcnow()
    attempt.result_proof_status = "DELIVERED"
    attempt.result_proof_tx_hash = body.transaction_hash
    attempt.status = "RESULT_SUBMITTED"
    node.status = "REVIEWING"
    if Decimal(body.gas_cost) > 0:
        session.add(
            FeeLedgerEntry(
                workflow_id=workflow.id,
                node_id=node.id,
                attempt_id=attempt.id,
                category="RESULT_PROOF_RELAY_FEE",
                initial_payer="platform-relayer",
                final_responsible_party="USER_SOURCE_GAS_DEPOSIT",
                quoted_amount=Decimal(body.gas_cost),
                charged_amount=Decimal(body.gas_cost),
                asset=body.gas_symbol,
                transaction_hash=body.transaction_hash,
                message_id=body.message_id,
                status="CHARGED",
            )
        )
        _ledger(
            session,
            workflow,
            "RELAY_GAS",
            Decimal(body.gas_cost),
            f"workflow-gas-treasury:{workflow.id}",
            f"network:{CHAIN_A}:result-proof",
            asset_kind="GAS",
            asset_symbol=body.gas_symbol,
            chain_id=CHAIN_A,
            node=node,
            attempt=attempt,
            transaction_hash=body.transaction_hash,
            metadata={"responsibility": "USER_SOURCE_GAS_DEPOSIT", "messageId": body.message_id},
        )
    _event(
        session,
        workflow,
        "RESULT_PROOF_DELIVERED",
        actor="ProtocolWorker",
        node=node,
        attempt=attempt,
        payload={"messageId": body.message_id, "transactionHash": body.transaction_hash},
    )
    return {"accepted": True, "code": "RESULT_PROOF_DELIVERED", "attemptId": attempt.id}


def _review_result(node: WorkflowNode, attempt: NodeAttempt) -> tuple[bool, str]:
    coverage = acceptance_coverage(node.acceptance_criteria, attempt.result_payload)
    failed_checks = [check for check in coverage["checks"] if not check["passed"]]
    if failed_checks:
        failed_fields = ", ".join(str(check["field"]) for check in failed_checks)
        return False, f"Immutable acceptance checks failed: {failed_fields}"
    return True, "Immutable acceptance criteria passed"


def _match_candidate_for_attempt(session: Session, attempt: NodeAttempt) -> AgentMatchCandidate | None:
    if not attempt.match_candidate_id:
        return None
    return session.get(AgentMatchCandidate, attempt.match_candidate_id)


def report_attempt_failure(
    session: Session,
    workflow: Workflow,
    node: WorkflowNode,
    attempt: NodeAttempt,
    reason_code: str,
    evidence: dict[str, Any],
) -> dict[str, Any]:
    policy = FAILURE_POLICIES.get(reason_code)
    if policy is None:
        raise HTTPException(status_code=422, detail="Unsupported failure reason code")
    if policy["agentFault"] and attempt.settlement_status != "NONE":
        raise HTTPException(
            status_code=409,
            detail="Agent-fault rollback is forbidden after settlement commitment; use custody recovery",
        )
    existing = session.scalar(
        select(FailureIncident).where(
            FailureIncident.attempt_id == attempt.id,
            FailureIncident.reason_code == reason_code,
            FailureIncident.resolved_at.is_(None),
        )
    )
    if existing is not None:
        return {"action": "failure_already_recorded", "incidentId": existing.id}
    incident = FailureIncident(
        workflow_id=workflow.id,
        node_id=node.id,
        attempt_id=attempt.id,
        reason_code=reason_code,
        responsible_party=policy["party"],
        retryable=policy["retryable"],
        agent_fault=policy["agentFault"],
        route_paused=policy["routePaused"],
        gas_treatment=policy["gasTreatment"],
        evidence=evidence,
    )
    session.add(incident)
    session.flush()
    attempt.failure_reason_code = reason_code
    attempt.failure_responsible_party = policy["party"]
    if policy["routePaused"]:
        route = session.get(ChainRoute, attempt.route_id)
        if route is not None:
            route.status = "PAUSED"
    if reason_code == "DISPUTED":
        attempt.status = "DISPUTED"
        node.status = "DISPUTED"
        workflow.status = "DISPUTED"
    elif policy["agentFault"]:
        reservation = session.scalar(select(BudgetReservation).where(BudgetReservation.attempt_id == attempt.id))
        if reservation is not None and reservation.status == "RESERVED":
            if _algorithm4_experiment_flag("ALGORITHM4_DISABLE_RESERVATION_RELEASE"):
                reservation.status = "HELD_BY_ABLATION"
            else:
                reservation.status = "RELEASED"
                reservation.released_at = utcnow()
                _ledger(
                    session,
                    workflow,
                    "RELEASE_RESERVATION",
                    Decimal(str(reservation.amount)),
                    f"escrow:{workflow.id}:reserved:{attempt.id}",
                    f"escrow:{workflow.id}:available",
                    node=node,
                    attempt=attempt,
                    metadata={"failureReasonCode": reason_code},
                )
        attempt.status = "FAILED"
        quality = apply_attempt_quality(attempt, node, review_passed=False, final=True)
        retryable = policy["retryable"] and attempt.attempt_number < node.max_attempts
        node.status = "RETRYING" if retryable else "FAILED"
        if not retryable:
            workflow.status = "FAILED"
            workflow.failure_reason = f"{node.node_key}: {reason_code}"
    else:
        attempt.status = "SYSTEM_RETRY"
        node.status = "SYSTEM_RETRY"
        session.add(
            ProtocolJob(
                workflow_id=workflow.id,
                node_id=node.id,
                attempt_id=attempt.id,
                route_id=attempt.route_id,
                route_version=attempt.route_version,
                job_type="SYSTEM_RETRY",
                payload={"reasonCode": reason_code, "evidence": evidence},
            )
        )
    _event(
        session,
        workflow,
        "FAILURE_CLASSIFIED",
        actor="FailureClassifier",
        node=node,
        attempt=attempt,
        payload={
            "incidentId": incident.id,
            "reasonCode": reason_code,
            **policy,
            **({
                "qualityScore": str(quality["qualityScore"]),
                "qualityStatus": quality["qualityStatus"],
                "qualityPolicyVersion": quality["qualityPolicyVersion"],
                "qualityEvidenceHash": quality["qualityEvidenceHash"],
            } if policy["agentFault"] else {}),
        },
    )
    _decision(
        session,
        "FAILURE_CLASSIFICATION",
        {"input": {"reasonCode": reason_code, "evidence": evidence}, "classification": policy},
        workflow=workflow,
        node=node,
        attempt=attempt,
        policy_version="FAILURE_POLICY_V1",
    )
    if policy["agentFault"]:
        _refresh_agent_reputation(session, workflow, node, attempt)
    return {"action": "failure_classified", "incidentId": incident.id, "classification": policy}


def _record_agent_failure(
    session: Session,
    workflow: Workflow,
    node: WorkflowNode,
    attempt: NodeAttempt,
    reason: str,
    retryable: bool,
) -> None:
    policy = FAILURE_POLICIES["AGENT_RESULT_REJECTED"]
    attempt.failure_reason_code = "AGENT_RESULT_REJECTED"
    attempt.failure_responsible_party = policy["party"]
    session.add(
        FailureIncident(
            workflow_id=workflow.id,
            node_id=node.id,
            attempt_id=attempt.id,
            reason_code="AGENT_RESULT_REJECTED",
            responsible_party=policy["party"],
            retryable=retryable,
            agent_fault=policy["agentFault"],
            gas_treatment=policy["gasTreatment"],
            route_paused=policy["routePaused"],
            evidence={"reviewReason": reason, "resultHash": attempt.result_hash, "proofHash": attempt.proof_hash},
        )
    )
    candidate = _match_candidate_for_attempt(session, attempt)
    if candidate is None:
        return
    candidate.status = "FAILED"
    candidate.exclusion_reason = "AGENT_RESULT_REJECTED"
    match_round = session.get(AgentMatchRound, candidate.match_round_id)
    if match_round is None:
        return
    available = session.scalar(
        select(func.count())
        .select_from(AgentMatchCandidate)
        .where(
            AgentMatchCandidate.match_round_id == match_round.id,
            AgentMatchCandidate.status == "AVAILABLE",
        )
    ) or 0
    if available == 0:
        match_round.status = "EXHAUSTED"


def _approval_snapshot(session: Session, attempt: NodeAttempt) -> list[dict[str, Any]]:
    return [
        {
            "reviewerDid": approval.reviewer_did,
            "reviewerWallet": approval.reviewer_wallet,
            "reviewerOrganizationId": approval.reviewer_organization_id,
            "decision": approval.decision,
            "evidenceHash": approval.evidence_hash,
            "notesHash": approval.notes_hash,
            "signatureScheme": approval.signature_scheme,
            "signedPayloadHash": approval.signed_payload_hash,
            "nonce": approval.nonce,
        }
        for approval in session.scalars(
            select(ReviewApproval)
            .where(ReviewApproval.attempt_id == attempt.id)
            .order_by(ReviewApproval.created_at, ReviewApproval.id)
        )
    ]


def _queue_review_sync(
    session: Session,
    workflow: Workflow,
    node: WorkflowNode,
    attempt: NodeAttempt,
    expected_status: str,
) -> None:
    if workflow.funding_mode != "ONCHAIN":
        return
    session.add(
        ProtocolJob(
            workflow_id=workflow.id,
            node_id=node.id,
            attempt_id=attempt.id,
            route_id=attempt.route_id,
            route_version=attempt.route_version,
            job_type="SYNC_REVIEW_V2",
            payload={
                "expectedStatus": expected_status,
                "resultHash": attempt.result_hash,
                "reviewPolicyHash": node.review_policy_hash,
                "requiredApprovals": max(1, node.required_approvals),
                "challengePeriodSeconds": node.challenge_period_seconds if node.risk_level == "HIGH" else 0,
                "arbitrationThreshold": int(os.getenv("ARBITRATION_THRESHOLD", "2")),
                "coordinatorReviewHash": attempt.coordinator_review_hash,
            },
        )
    )


def _record_final_review(
    session: Session,
    workflow: Workflow,
    node: WorkflowNode,
    attempt: NodeAttempt,
    *,
    passed: bool,
    reason: str,
    source: str,
) -> None:
    approvals = _approval_snapshot(session, attempt)
    challenge = session.scalar(select(ReviewChallenge).where(ReviewChallenge.attempt_id == attempt.id))
    challenge_snapshot = None if challenge is None else {
        "status": challenge.status,
        "evidenceHash": challenge.evidence_hash,
        "resolution": challenge.resolution,
    }
    review_payload = {
        "attemptId": attempt.id,
        "criteriaHash": canonical_hash(node.acceptance_criteria),
        "resultHash": attempt.result_hash,
        "proofHash": attempt.proof_hash,
        "riskLevel": node.risk_level,
        "reviewPolicyHash": node.review_policy_hash,
        "reviewSampled": attempt.review_sampled,
        "randomnessMode": attempt.randomness_mode,
        "randomnessStatus": attempt.randomness_status,
        "randomnessRequestId": attempt.randomness_request_id,
        "randomnessCommitment": attempt.randomness_commitment,
        "randomnessProofHash": attempt.randomness_proof_hash,
        "requiredApprovals": node.required_approvals,
        "approvals": approvals,
        "challenge": challenge_snapshot,
        "passed": passed,
        "reason": reason,
        "source": source,
    }
    attempt.reviewed_at = utcnow()
    attempt.review_reason = reason
    attempt.coordinator_review_hash = canonical_hash(review_payload)
    _decision(
        session,
        "RESULT_REVIEW",
        {
            "input": review_payload,
            "passed": passed,
            "reason": reason,
            "reviewHash": attempt.coordinator_review_hash,
        },
        workflow=workflow,
        node=node,
        attempt=attempt,
        policy_version=node.review_policy_version,
    )
    if workflow.funding_mode == "ONCHAIN":
        session.add(
            ProtocolJob(
                workflow_id=workflow.id,
                node_id=node.id,
                attempt_id=attempt.id,
                route_id=attempt.route_id,
                route_version=attempt.route_version,
                job_type="RECORD_REVIEW",
                payload={
                    "passed": passed,
                    "reason": reason,
                    "criteriaHash": canonical_hash(node.acceptance_criteria),
                    "resultHash": attempt.result_hash,
                    "proofHash": attempt.proof_hash,
                    "reviewPolicyHash": node.review_policy_hash,
                    "reviewHash": attempt.coordinator_review_hash,
                },
            )
        )
        _queue_review_sync(
            session,
            workflow,
            node,
            attempt,
            "APPROVED" if passed else "REJECTED",
        )


def _complete_review_pass(
    session: Session,
    workflow: Workflow,
    node: WorkflowNode,
    attempt: NodeAttempt,
    reason: str,
    *,
    source: str,
) -> dict[str, Any]:
    _record_final_review(session, workflow, node, attempt, passed=True, reason=reason, source=source)
    attempt.status = "REVIEW_PASSED"
    attempt.review_status = "PASSED"
    node.status = "SUCCEEDED"
    node.output_payload = attempt.result_payload
    quality = apply_attempt_quality(attempt, node, review_passed=True, final=False)
    _event(
        session,
        workflow,
        "REVIEW_PASSED",
        node=node,
        attempt=attempt,
        payload={
            "reason": reason,
            "riskLevel": node.risk_level,
            "reviewPolicyHash": node.review_policy_hash,
            "qualityScore": str(quality["qualityScore"]),
            "qualityStatus": quality["qualityStatus"],
            "qualityPolicyVersion": quality["qualityPolicyVersion"],
            "qualityEvidenceHash": quality["qualityEvidenceHash"],
        },
    )
    return {"action": "review_passed", "nodeId": node.id, "attemptId": attempt.id}


def _complete_review_failure(
    session: Session,
    workflow: Workflow,
    node: WorkflowNode,
    attempt: NodeAttempt,
    reason: str,
    *,
    source: str,
) -> dict[str, Any]:
    _record_final_review(session, workflow, node, attempt, passed=False, reason=reason, source=source)
    attempt.status = "REVIEW_FAILED"
    attempt.review_status = "FAILED"
    quality = apply_attempt_quality(attempt, node, review_passed=False, final=True)
    if attempt.payout_status == "PENDING":
        attempt.payout_status = "NOT_EXECUTED"
    reservation = session.scalar(select(BudgetReservation).where(BudgetReservation.attempt_id == attempt.id))
    if reservation is not None and reservation.status == "RESERVED":
        reservation.status = "RELEASED"
        reservation.released_at = utcnow()
        _ledger(
            session,
            workflow,
            "RELEASE_RESERVATION",
            Decimal(str(reservation.amount)),
            f"escrow:{workflow.id}:reserved:{attempt.id}",
            f"escrow:{workflow.id}:available",
            node=node,
            attempt=attempt,
        )
    retryable = attempt.attempt_number < node.max_attempts
    _record_agent_failure(session, workflow, node, attempt, reason, retryable)
    node.status = "RETRYING" if retryable else "FAILED"
    if node.status == "FAILED":
        workflow.status = "FAILED"
        workflow.failure_reason = f"Node {node.node_key} failed review after all attempts"
    _event(
        session,
        workflow,
        "REVIEW_FAILED",
        node=node,
        attempt=attempt,
        payload={
            "reason": reason,
            "source": source,
            "willRetry": node.status == "RETRYING",
            "qualityScore": str(quality["qualityScore"]),
            "qualityStatus": quality["qualityStatus"],
            "qualityPolicyVersion": quality["qualityPolicyVersion"],
            "qualityEvidenceHash": quality["qualityEvidenceHash"],
        },
    )
    _refresh_agent_reputation(session, workflow, node, attempt)
    return {
        "action": "review_failed",
        "nodeId": node.id,
        "attemptId": attempt.id,
        "willRetry": node.status == "RETRYING",
    }


def _is_v1_sampled_for_review(workflow: Workflow, node: WorkflowNode, attempt: NodeAttempt) -> bool:
    if node.sampling_rate_bps <= 0:
        return False
    sample_hash = canonical_hash({
        "workflowId": workflow.id,
        "nodeId": node.id,
        "attemptId": attempt.id,
        "reviewPolicyHash": node.review_policy_hash,
        "resultHash": attempt.result_hash,
        "proofHash": attempt.proof_hash,
    })
    return int(sample_hash[2:10], 16) % 10000 < node.sampling_rate_bps


def _randomness_mode() -> str:
    configured = os.getenv("REVIEW_RANDOMNESS_MODE", "").upper()
    if configured:
        return configured
    return "VRF" if os.getenv("APP_ENV", "development").lower() == "production" else "MOCK"


def _request_review_randomness(
    session: Session,
    workflow: Workflow,
    node: WorkflowNode,
    attempt: NodeAttempt,
) -> None:
    mode = _randomness_mode()
    if os.getenv("APP_ENV", "development").lower() == "production" and mode != "VRF":
        raise HTTPException(status_code=503, detail="Production review sampling requires REVIEW_RANDOMNESS_MODE=VRF")
    attempt.randomness_mode = mode
    attempt.randomness_status = "REQUESTED"
    attempt.randomness_request_id = f"review-rng-{secrets.token_urlsafe(24)}"
    attempt.randomness_requested_at = utcnow()
    attempt.randomness_commitment = canonical_hash({
        "requestId": attempt.randomness_request_id,
        "workflowId": workflow.id,
        "nodeId": node.id,
        "attemptId": attempt.id,
        "resultHash": attempt.result_hash,
        "reviewPolicyHash": node.review_policy_hash,
    })
    _event(
        session,
        workflow,
        "REVIEW_RANDOMNESS_REQUESTED",
        node=node,
        attempt=attempt,
        payload={
            "requestId": attempt.randomness_request_id,
            "mode": mode,
            "commitment": attempt.randomness_commitment,
        },
    )
    if mode == "MOCK":
        word = str(secrets.randbits(256))
        attempt.random_word = word
        attempt.randomness_proof_hash = canonical_hash({
            "provider": "LOCAL_POST_RESULT_MOCK",
            "requestId": attempt.randomness_request_id,
            "randomWord": word,
        })
        attempt.randomness_status = "FULFILLED"
        attempt.randomness_fulfilled_at = utcnow()


def fulfill_review_randomness(
    session: Session,
    attempt: NodeAttempt,
    *,
    request_id: str,
    random_word: str,
    provider_proof_hash: str,
    provider_id: str,
) -> dict[str, Any]:
    if attempt.randomness_request_id != request_id:
        raise HTTPException(status_code=409, detail="Randomness request does not match this attempt")
    if attempt.randomness_mode != "VRF":
        raise HTTPException(status_code=409, detail="Only VRF randomness can be externally fulfilled")
    if attempt.randomness_status == "FULFILLED":
        if attempt.random_word != random_word or attempt.randomness_proof_hash != provider_proof_hash:
            raise HTTPException(status_code=409, detail="Randomness request was already fulfilled with different data")
        return {"action": "randomness_already_fulfilled", "attemptId": attempt.id}
    if attempt.randomness_status != "REQUESTED":
        raise HTTPException(status_code=409, detail="Randomness request is not pending")
    attempt.random_word = str(int(random_word, 16) if random_word.startswith("0x") else int(random_word))
    attempt.randomness_proof_hash = provider_proof_hash
    attempt.randomness_status = "FULFILLED"
    attempt.randomness_fulfilled_at = utcnow()
    if attempt.status == "RANDOMNESS_PENDING":
        attempt.status = "RESULT_SUBMITTED"
    session.add(
        CoordinatorDecision(
            workflow_id=None,
            node_id=attempt.node_id,
            attempt_id=attempt.id,
            coordinator_did=f"vrf:{provider_id}",
            decision_type="REVIEW_RANDOMNESS_FULFILLED",
            policy_version="RISK_REVIEW_V2",
            input_hash=attempt.randomness_commitment or canonical_hash(request_id),
            decision_hash=canonical_hash({"requestId": request_id, "proofHash": provider_proof_hash}),
            payload={"requestId": request_id, "providerProofHash": provider_proof_hash},
        )
    )
    return {"action": "randomness_fulfilled", "attemptId": attempt.id, "requestId": request_id}


def _is_v2_sampled_for_review(node: WorkflowNode, attempt: NodeAttempt) -> bool:
    if attempt.randomness_status in {"FAILED", "TIMED_OUT"}:
        return True
    if attempt.randomness_status != "FULFILLED" or attempt.random_word is None:
        raise HTTPException(status_code=409, detail="Post-result randomness has not been fulfilled")
    return int(attempt.random_word) % 10000 < node.sampling_rate_bps


def review_attempt(session: Session, workflow: Workflow, node: WorkflowNode, attempt: NodeAttempt) -> dict[str, Any]:
    passed, reason = _review_result(node, attempt)
    attempt.review_started_at = attempt.review_started_at or utcnow()
    attempt.review_policy_hash = node.review_policy_hash
    if not passed:
        return _complete_review_failure(
            session,
            workflow,
            node,
            attempt,
            reason,
            source="DETERMINISTIC_CHECK",
        )

    if node.risk_level == "LOW":
        return _complete_review_pass(
            session,
            workflow,
            node,
            attempt,
            reason,
            source="COORDINATOR_AUTOMATIC",
        )

    if node.risk_level == "MEDIUM" and node.sampling_rate_bps > 0:
        if node.review_policy_version == "RISK_REVIEW_V1":
            attempt.review_sampled = _is_v1_sampled_for_review(workflow, node, attempt)
        else:
            if attempt.randomness_status == "NOT_REQUIRED":
                _request_review_randomness(session, workflow, node, attempt)
            if attempt.randomness_status == "REQUESTED":
                attempt.status = "RANDOMNESS_PENDING"
                attempt.review_status = "RANDOMNESS_PENDING"
                node.status = "REVIEW_PENDING"
                return {
                    "action": "review_randomness_requested",
                    "nodeId": node.id,
                    "attemptId": attempt.id,
                    "requestId": attempt.randomness_request_id,
                }
            attempt.review_sampled = _is_v2_sampled_for_review(node, attempt)
    else:
        attempt.review_sampled = node.risk_level == "HIGH"
    if node.risk_level == "MEDIUM" and not attempt.review_sampled:
        return _complete_review_pass(
            session,
            workflow,
            node,
            attempt,
            f"{reason}; post-result random sample did not select this attempt",
            source="COORDINATOR_UNSAMPLED_V2" if node.review_policy_version != "RISK_REVIEW_V1" else "COORDINATOR_UNSAMPLED_V1",
        )

    attempt.status = "INDEPENDENT_REVIEW_PENDING" if node.risk_level == "MEDIUM" else "APPROVALS_PENDING"
    attempt.review_status = attempt.status
    attempt.review_reason = "Waiting for independent review approval"
    node.status = "REVIEW_PENDING"
    _decision(
        session,
        "REVIEW_ROUTING",
        {
            "input": {"resultHash": attempt.result_hash, "reviewPolicyHash": node.review_policy_hash},
            "riskLevel": node.risk_level,
            "sampled": attempt.review_sampled,
            "requiredApprovals": node.required_approvals,
        },
        workflow=workflow,
        node=node,
        attempt=attempt,
        policy_version=node.review_policy_version,
    )
    _event(
        session,
        workflow,
        "INDEPENDENT_REVIEW_REQUIRED",
        node=node,
        attempt=attempt,
        payload={
            "riskLevel": node.risk_level,
            "requiredApprovals": node.required_approvals,
            "reviewPolicyHash": node.review_policy_hash,
        },
    )
    _queue_review_sync(session, workflow, node, attempt, "APPROVALS_PENDING")
    return {
        "action": "independent_review_required",
        "nodeId": node.id,
        "attemptId": attempt.id,
        "requiredApprovals": node.required_approvals,
    }


def _aware_datetime(value: Any) -> Any:
    if value is None or value.tzinfo is not None:
        return value
    return value.replace(tzinfo=timezone.utc)


def _review_manager_address(session: Session, workflow: Workflow) -> str:
    runtime = session.scalar(
        select(ProtocolRuntimeConfig).where(
            ProtocolRuntimeConfig.chain_id == workflow.source_chain_id,
            ProtocolRuntimeConfig.contract_role == "REVIEW_MANAGER_V2",
            ProtocolRuntimeConfig.active.is_(True),
        )
    )
    if runtime is not None:
        return runtime.contract_address
    configured = os.getenv("REVIEW_MANAGER_ADDRESS", "").strip()
    if configured:
        return configured
    if workflow.funding_mode == "ONCHAIN" or os.getenv("APP_ENV", "development").lower() == "production":
        raise HTTPException(status_code=503, detail="ReviewManagerV2 has not been deployed and registered")
    return "0x0000000000000000000000000000000000000000"


def _validate_signature_deadline(deadline: Any) -> None:
    deadline = _aware_datetime(deadline)
    now = utcnow()
    if deadline is None or deadline <= now:
        raise HTTPException(status_code=422, detail="Review signature deadline has expired")
    max_seconds = int(os.getenv("REVIEW_SIGNATURE_MAX_TTL_SECONDS", "900"))
    if deadline > now + timedelta(seconds=max_seconds):
        raise HTTPException(status_code=422, detail="Review signature deadline is too far in the future")


def _ensure_independent_reviewer(
    session: Session,
    workflow: Workflow,
    attempt: NodeAttempt,
    reviewer: Agent,
    *,
    required_role: str,
) -> None:
    if reviewer.id in {workflow.requester_id, attempt.agent_id}:
        raise HTTPException(status_code=403, detail="Requester and execution Agent cannot independently review this attempt")
    if required_role not in reviewer.roles:
        raise HTTPException(status_code=403, detail=f"{required_role.title()} role is required")
    if not reviewer.reviewer_active:
        raise HTTPException(status_code=403, detail="Reviewer credential is inactive")
    if not reviewer.review_wallet or not reviewer.organization_id:
        raise HTTPException(status_code=409, detail="Reviewer wallet and organization must be registered")
    execution_agent = session.get(Agent, attempt.agent_id)
    requester = session.get(Agent, workflow.requester_id)
    for party, label in ((execution_agent, "execution Agent"), (requester, "requester")):
        if party is None:
            continue
        if party.review_wallet and party.review_wallet.lower() == reviewer.review_wallet.lower():
            raise HTTPException(status_code=403, detail=f"Reviewer shares a wallet with the {label}")
        if party.organization_id and party.organization_id == reviewer.organization_id:
            raise HTTPException(status_code=403, detail=f"Reviewer belongs to the same organization as the {label}")


def review_approval_signing_payload(
    session: Session,
    workflow: Workflow,
    node: WorkflowNode,
    attempt: NodeAttempt,
    reviewer: Agent,
    *,
    decision: str,
    evidence_hash: str,
    notes: str,
    nonce: str,
    deadline: Any,
) -> dict[str, Any]:
    if node.current_attempt_id != attempt.id or attempt.status not in {"INDEPENDENT_REVIEW_PENDING", "APPROVALS_PENDING"}:
        raise HTTPException(status_code=409, detail="Attempt is not awaiting review approvals")
    _ensure_independent_reviewer(session, workflow, attempt, reviewer, required_role="reviewer")
    _validate_signature_deadline(deadline)
    typed_data = build_review_approval_typed_data(
        chain_id=workflow.source_chain_id,
        workflow_id=workflow.id,
        node_id=node.id,
        attempt_id=attempt.id,
        result_hash=attempt.result_hash or "",
        review_policy_hash=node.review_policy_hash,
        decision=decision,
        evidence_hash=evidence_hash,
        notes_hash=canonical_hash(notes),
        nonce=nonce,
        deadline=deadline,
        verifying_contract=_review_manager_address(session, workflow),
    )
    return {"typedData": typed_data, "reviewerWallet": reviewer.review_wallet}


def submit_review_approval(
    session: Session,
    workflow: Workflow,
    node: WorkflowNode,
    attempt: NodeAttempt,
    reviewer: Agent,
    *,
    decision: str,
    evidence_hash: str,
    notes: str,
    reviewer_wallet: str | None = None,
    signature: str | None = None,
    signature_scheme: str = "EIP712",
    nonce: str | None = None,
    deadline: Any = None,
) -> dict[str, Any]:
    if node.current_attempt_id != attempt.id or attempt.status not in {"INDEPENDENT_REVIEW_PENDING", "APPROVALS_PENDING"}:
        raise HTTPException(status_code=409, detail="Attempt is not awaiting review approvals")
    is_v2 = node.review_policy_version != "RISK_REVIEW_V1"
    if is_v2:
        _ensure_independent_reviewer(session, workflow, attempt, reviewer, required_role="reviewer")
    else:
        if reviewer.id in {workflow.requester_id, attempt.agent_id}:
            raise HTTPException(status_code=403, detail="Requester and execution Agent cannot independently approve this attempt")
        if not ({"reviewer", "admin"} & set(reviewer.roles)):
            raise HTTPException(status_code=403, detail="Reviewer or admin role is required")
    existing = session.scalar(
        select(ReviewApproval).where(
            ReviewApproval.attempt_id == attempt.id,
            ReviewApproval.reviewer_id == reviewer.id,
        )
    )
    if existing is not None:
        raise HTTPException(status_code=409, detail="Reviewer has already decided this attempt")
    signed_payload_hash = None
    if is_v2:
        if not all((reviewer_wallet, signature, nonce, deadline)):
            raise HTTPException(status_code=422, detail="RISK_REVIEW_V2 requires an EIP-712 wallet signature, nonce and deadline")
        _validate_signature_deadline(deadline)
        if reviewer.review_wallet is None or reviewer_wallet.lower() != reviewer.review_wallet.lower():
            raise HTTPException(status_code=422, detail="Reviewer wallet does not match the registered wallet")
        typed_data = review_approval_signing_payload(
            session,
            workflow,
            node,
            attempt,
            reviewer,
            decision=decision,
            evidence_hash=evidence_hash,
            notes=notes,
            nonce=nonce,
            deadline=deadline,
        )["typedData"]
        try:
            recovered, signed_payload_hash = recover_typed_signer(typed_data, signature)
        except Exception as exc:
            raise HTTPException(status_code=422, detail="Review EIP-712 signature is invalid") from exc
        if recovered.lower() != reviewer_wallet.lower():
            raise HTTPException(status_code=422, detail="Review signature does not match reviewerWallet")
        replay = session.scalar(
            select(ReviewApproval).where(
                ReviewApproval.reviewer_wallet == reviewer.review_wallet,
                ReviewApproval.nonce == nonce,
            )
        )
        if replay is not None:
            raise HTTPException(status_code=409, detail="Review signature nonce has already been used")
    approval = ReviewApproval(
        workflow_id=workflow.id,
        node_id=node.id,
        attempt_id=attempt.id,
        reviewer_id=reviewer.id,
        reviewer_did=reviewer.did,
        reviewer_wallet=reviewer.review_wallet if is_v2 else reviewer_wallet,
        reviewer_organization_id=reviewer.organization_id if is_v2 else None,
        decision=decision,
        evidence_hash=evidence_hash,
        notes_hash=canonical_hash(notes),
        signature=signature,
        signature_scheme=signature_scheme if signature else None,
        signed_payload_hash=signed_payload_hash,
        nonce=nonce,
        deadline=deadline,
    )
    session.add(approval)
    session.flush()
    _event(
        session,
        workflow,
        "REVIEW_APPROVAL_RECORDED",
        actor=reviewer.did,
        node=node,
        attempt=attempt,
        payload={"decision": decision, "evidenceHash": evidence_hash, "approvalId": approval.id},
    )
    if decision == "FAIL":
        return _complete_review_failure(
            session,
            workflow,
            node,
            attempt,
            "Independent reviewer rejected the result",
            source="INDEPENDENT_REVIEW",
        )

    approval_count = session.scalar(
        select(func.count())
        .select_from(ReviewApproval)
        .where(ReviewApproval.attempt_id == attempt.id, ReviewApproval.decision == "PASS")
    ) or 0
    if approval_count < node.required_approvals:
        _queue_review_sync(session, workflow, node, attempt, "APPROVALS_PENDING")
        return {
            "action": "review_approval_recorded",
            "attemptId": attempt.id,
            "approvalCount": approval_count,
            "requiredApprovals": node.required_approvals,
        }
    if node.risk_level == "MEDIUM":
        return _complete_review_pass(
            session,
            workflow,
            node,
            attempt,
            "Deterministic checks and sampled independent review passed",
            source="SAMPLED_INDEPENDENT_REVIEW",
        )

    attempt.status = "CHALLENGE_PERIOD"
    attempt.review_status = "CHALLENGE_PERIOD"
    attempt.review_reason = "Required approvals passed; challenge period is open"
    attempt.challenge_expires_at = utcnow() + timedelta(seconds=node.challenge_period_seconds)
    node.status = "CHALLENGE_PERIOD"
    _event(
        session,
        workflow,
        "REVIEW_CHALLENGE_PERIOD_OPENED",
        node=node,
        attempt=attempt,
        payload={"expiresAt": attempt.challenge_expires_at.isoformat(), "approvalCount": approval_count},
    )
    _queue_review_sync(session, workflow, node, attempt, "CHALLENGE_OPEN")
    return {
        "action": "challenge_period_opened",
        "attemptId": attempt.id,
        "challengeExpiresAt": attempt.challenge_expires_at,
    }


def submit_review_challenge(
    session: Session,
    workflow: Workflow,
    node: WorkflowNode,
    attempt: NodeAttempt,
    challenger: Agent,
    *,
    reason: str,
    evidence_hash: str,
) -> dict[str, Any]:
    expires_at = attempt.challenge_expires_at
    if expires_at is not None and expires_at.tzinfo is None:
        expires_at = expires_at.replace(tzinfo=timezone.utc)
    if node.current_attempt_id != attempt.id or attempt.status != "CHALLENGE_PERIOD" or expires_at is None or utcnow() >= expires_at:
        raise HTTPException(status_code=409, detail="Attempt does not have an open challenge period")
    if challenger.id != workflow.requester_id and "admin" not in challenger.roles:
        raise HTTPException(status_code=403, detail="Only the requester or an admin can challenge a review")
    existing = session.scalar(select(ReviewChallenge).where(ReviewChallenge.attempt_id == attempt.id))
    if existing is not None:
        raise HTTPException(status_code=409, detail="Attempt already has a review challenge")
    challenge = ReviewChallenge(
        workflow_id=workflow.id,
        node_id=node.id,
        attempt_id=attempt.id,
        challenger_id=challenger.id,
        challenger_did=challenger.did,
        reason=reason,
        evidence_hash=evidence_hash,
        arbitration_threshold=int(os.getenv("ARBITRATION_THRESHOLD", "2")),
        onchain_review_id=canonical_hash({"workflowId": workflow.id, "nodeId": node.id, "attemptId": attempt.id}),
    )
    session.add(challenge)
    session.flush()
    attempt.status = "DISPUTED"
    attempt.review_status = "DISPUTED"
    attempt.review_reason = reason
    node.status = "DISPUTED"
    _event(
        session,
        workflow,
        "REVIEW_CHALLENGED",
        actor=challenger.did,
        node=node,
        attempt=attempt,
        payload={"challengeId": challenge.id, "evidenceHash": evidence_hash},
    )
    _queue_review_sync(session, workflow, node, attempt, "DISPUTED")
    return {"action": "review_challenged", "challengeId": challenge.id, "attemptId": attempt.id}


def arbitration_vote_signing_payload(
    session: Session,
    workflow: Workflow,
    node: WorkflowNode,
    attempt: NodeAttempt,
    challenge: ReviewChallenge,
    voter: Agent,
    *,
    resolution: str,
    reason: str,
    nonce: str,
    deadline: Any,
) -> dict[str, Any]:
    if challenge.attempt_id != attempt.id or challenge.status != "OPEN" or attempt.status != "DISPUTED":
        raise HTTPException(status_code=409, detail="Review challenge is not open")
    _ensure_independent_reviewer(session, workflow, attempt, voter, required_role="arbitrator")
    _validate_signature_deadline(deadline)
    typed_data = build_arbitration_vote_typed_data(
        chain_id=workflow.source_chain_id,
        workflow_id=workflow.id,
        node_id=node.id,
        attempt_id=attempt.id,
        challenge_id=challenge.id,
        result_hash=attempt.result_hash or "",
        resolution=resolution,
        reason_hash=canonical_hash(reason),
        nonce=nonce,
        deadline=deadline,
        verifying_contract=_review_manager_address(session, workflow),
    )
    return {"typedData": typed_data, "voterWallet": voter.review_wallet}


def _finalize_challenge_resolution(
    session: Session,
    workflow: Workflow,
    node: WorkflowNode,
    attempt: NodeAttempt,
    challenge: ReviewChallenge,
    resolver: Agent,
    *,
    resolution: str,
    reason: str,
) -> dict[str, Any]:
    challenge.status = "RESOLVED"
    challenge.resolution = resolution
    challenge.resolution_reason = reason
    challenge.resolved_by_id = resolver.id
    challenge.resolved_at = utcnow()
    _event(
        session,
        workflow,
        "REVIEW_CHALLENGE_RESOLVED",
        actor=resolver.did,
        node=node,
        attempt=attempt,
        payload={"challengeId": challenge.id, "resolution": resolution},
    )
    if resolution == "REJECT_RESULT":
        return _complete_review_failure(
            session,
            workflow,
            node,
            attempt,
            f"Arbitration rejected the result: {reason}",
            source="THRESHOLD_ARBITRATION",
        )
    return _complete_review_pass(
        session,
        workflow,
        node,
        attempt,
        f"Challenge resolved and approved result upheld: {reason}",
        source="THRESHOLD_ARBITRATION",
    )


def submit_arbitration_vote(
    session: Session,
    workflow: Workflow,
    node: WorkflowNode,
    attempt: NodeAttempt,
    challenge: ReviewChallenge,
    voter: Agent,
    *,
    resolution: str,
    reason: str,
    voter_wallet: str,
    signature: str,
    signature_scheme: str,
    nonce: str,
    deadline: Any,
) -> dict[str, Any]:
    payload = arbitration_vote_signing_payload(
        session,
        workflow,
        node,
        attempt,
        challenge,
        voter,
        resolution=resolution,
        reason=reason,
        nonce=nonce,
        deadline=deadline,
    )
    if voter.review_wallet is None or voter.review_wallet.lower() != voter_wallet.lower():
        raise HTTPException(status_code=422, detail="Arbitration wallet does not match the registered wallet")
    try:
        recovered, signed_payload_hash = recover_typed_signer(payload["typedData"], signature)
    except Exception as exc:
        raise HTTPException(status_code=422, detail="Arbitration EIP-712 signature is invalid") from exc
    if recovered.lower() != voter_wallet.lower():
        raise HTTPException(status_code=422, detail="Arbitration signature does not match voterWallet")
    if session.scalar(select(ArbitrationVote).where(ArbitrationVote.challenge_id == challenge.id, ArbitrationVote.voter_id == voter.id)):
        raise HTTPException(status_code=409, detail="Arbitrator has already voted on this challenge")
    if session.scalar(select(ArbitrationVote).where(ArbitrationVote.voter_wallet == voter_wallet, ArbitrationVote.nonce == nonce)):
        raise HTTPException(status_code=409, detail="Arbitration signature nonce has already been used")
    if session.scalar(
        select(ArbitrationVote).where(
            ArbitrationVote.challenge_id == challenge.id,
            ArbitrationVote.voter_organization_id == voter.organization_id,
        )
    ):
        raise HTTPException(status_code=403, detail="This organization has already voted on the challenge")
    vote = ArbitrationVote(
        workflow_id=workflow.id,
        node_id=node.id,
        attempt_id=attempt.id,
        challenge_id=challenge.id,
        voter_id=voter.id,
        voter_did=voter.did,
        voter_wallet=voter_wallet,
        voter_organization_id=voter.organization_id or "",
        resolution=resolution,
        reason_hash=canonical_hash(reason),
        signature=signature,
        signature_scheme=signature_scheme,
        signed_payload_hash=signed_payload_hash,
        nonce=nonce,
        deadline=deadline,
    )
    session.add(vote)
    session.flush()
    _event(
        session,
        workflow,
        "ARBITRATION_VOTE_RECORDED",
        actor=voter.did,
        node=node,
        attempt=attempt,
        payload={"challengeId": challenge.id, "resolution": resolution, "voteId": vote.id},
    )
    vote_count = session.scalar(
        select(func.count()).select_from(ArbitrationVote).where(
            ArbitrationVote.challenge_id == challenge.id,
            ArbitrationVote.resolution == resolution,
        )
    ) or 0
    if vote_count < challenge.arbitration_threshold:
        _queue_review_sync(session, workflow, node, attempt, "DISPUTED")
        return {
            "action": "arbitration_vote_recorded",
            "challengeId": challenge.id,
            "voteCount": vote_count,
            "threshold": challenge.arbitration_threshold,
        }
    return _finalize_challenge_resolution(
        session,
        workflow,
        node,
        attempt,
        challenge,
        voter,
        resolution=resolution,
        reason=f"Threshold {vote_count}/{challenge.arbitration_threshold}: {reason}",
    )


def resolve_review_challenge(
    session: Session,
    workflow: Workflow,
    node: WorkflowNode,
    attempt: NodeAttempt,
    challenge: ReviewChallenge,
    resolver: Agent,
    *,
    resolution: str,
    reason: str,
) -> dict[str, Any]:
    if challenge.attempt_id != attempt.id or challenge.status != "OPEN" or attempt.status != "DISPUTED":
        raise HTTPException(status_code=409, detail="Review challenge is not open")
    if node.review_policy_version != "RISK_REVIEW_V1":
        raise HTTPException(status_code=409, detail="RISK_REVIEW_V2 challenges require threshold arbitration votes")
    if "admin" not in resolver.roles:
        raise HTTPException(status_code=403, detail="Admin arbitration is required")
    return _finalize_challenge_resolution(
        session,
        workflow,
        node,
        attempt,
        challenge,
        resolver,
        resolution=resolution,
        reason=reason,
    )


def _unlock_dependents(session: Session, workflow: Workflow) -> list[str]:
    nodes = list(session.scalars(select(WorkflowNode).where(WorkflowNode.workflow_id == workflow.id)))
    status_by_key = {node.node_key: node.status for node in nodes}
    unlocked: list[str] = []
    for candidate in nodes:
        if candidate.status != "BLOCKED":
            continue
        # SUCCEEDED is deliberately insufficient; every predecessor must have
        # completed settlement before a dependent node becomes READY.
        if candidate.depends_on and all(status_by_key.get(key) == "SETTLED" for key in candidate.depends_on):
            candidate.status = "READY"
            unlocked.append(candidate.node_key)
            _event(session, workflow, "NODE_UNLOCKED", node=candidate, payload={"dependsOn": candidate.depends_on})
    return unlocked


def settle_node(session: Session, workflow: Workflow, node: WorkflowNode, attempt: NodeAttempt) -> dict[str, Any]:
    if workflow.funding_mode == "ONCHAIN":
        review_sync = session.scalar(
            select(ProtocolJob)
            .where(
                ProtocolJob.attempt_id == attempt.id,
                ProtocolJob.job_type == "SYNC_REVIEW_V2",
            )
            .order_by(ProtocolJob.created_at.desc(), ProtocolJob.id.desc())
        )
        expected_hash = attempt.coordinator_review_hash
        synced_hash = review_sync.payload.get("coordinatorReviewHash") if review_sync is not None else None
        if (
            review_sync is None
            or review_sync.status != "COMPLETED"
            or review_sync.payload.get("expectedStatus") != "APPROVED"
            or synced_hash != expected_hash
        ):
            return {
                "action": "review_onchain_pending",
                "nodeId": node.id,
                "attemptId": attempt.id,
                "protocolJobId": review_sync.id if review_sync is not None else None,
            }
    reservation = session.scalar(select(BudgetReservation).where(BudgetReservation.attempt_id == attempt.id))
    if reservation is None or reservation.status != "RESERVED":
        raise HTTPException(status_code=409, detail="Current attempt has no payable reservation")
    if workflow.funding_mode == "ONCHAIN" and attempt.settlement_status == "NONE":
        amount = Decimal(str(reservation.amount)).quantize(Decimal("0.00000001"), rounding=ROUND_DOWN)
        payout_mode = "SWAP" if attempt.payout_status == "PENDING" else "DIRECT"
        payout_rate = (
            Decimal(str(attempt.payout_rate)).quantize(Decimal("0.000000000001"), rounding=ROUND_DOWN)
            if payout_mode == "SWAP"
            else Decimal("1")
        )
        payout_unit = Decimal(1).scaleb(-attempt.payout_decimals)
        payout_quote = (amount * payout_rate).quantize(payout_unit, rounding=ROUND_DOWN)
        payout_min = (
            payout_quote * Decimal(10_000 - attempt.payout_slippage_bps) / Decimal(10_000)
        ).quantize(payout_unit, rounding=ROUND_DOWN)
        quote_expires_at = utcnow() + timedelta(minutes=10)
        attempt.payout_quote_amount = decimal_string(payout_quote)
        attempt.payout_min_amount = decimal_string(payout_min)
        attempt.payout_quote_expires_at = quote_expires_at
        if payout_mode == "SWAP":
            attempt.payout_status = "QUOTED"
        message_id = _message_id(workflow, node, attempt.attempt_number, "SETTLEMENT")
        attempt.settlement_status = "QUEUED"
        attempt.settlement_message_id = message_id
        attempt.status = "SETTLEMENT_PENDING"
        node.status = "SETTLEMENT_PENDING"
        route = session.get(ChainRoute, attempt.route_id)
        session.add(
            SettlementRecoveryRecord(
                workflow_id=workflow.id,
                node_id=node.id,
                attempt_id=attempt.id,
                phase="COMMITTED",
                custody_chain_id=workflow.source_chain_id,
                custody_contract=route.source_gateway_address if route else None,
                asset=attempt.settlement_asset,
                token_address=route.token_address if route else None,
                amount=amount,
                transport_message_id=message_id,
            )
        )
        session.add(
            CrossChainMessage(
                message_id=message_id,
                workflow_id=workflow.id,
                node_id=node.id,
                attempt_id=attempt.id,
                message_type="SETTLEMENT",
                source_chain_id=CHAIN_A,
                destination_chain_id=attempt.chain_id,
                route_id=attempt.route_id,
                route_version=attempt.route_version,
                status="QUEUED",
                payload={
                    "onchainWorkflowId": workflow.onchain_workflow_id,
                    "attemptId": attempt.id,
                    "amount": str(reservation.amount),
                    "agentKey": attempt.agent_key,
                    "settlementAsset": attempt.settlement_asset,
                    "payoutMode": payout_mode,
                    "payoutAsset": attempt.payout_asset,
                    "payoutTokenAddress": attempt.payout_token_address,
                    "payoutRate": decimal_string(payout_rate),
                    "payoutQuoteAmount": decimal_string(payout_quote),
                    "payoutMinAmount": decimal_string(payout_min),
                    "payoutQuoteDeadline": int(quote_expires_at.timestamp()),
                },
            )
        )
        session.add(
            ProtocolJob(
                workflow_id=workflow.id,
                node_id=node.id,
                attempt_id=attempt.id,
                route_id=attempt.route_id,
                route_version=attempt.route_version,
                job_type="CROSS_CHAIN_SETTLEMENT",
                payload={
                    "messageId": message_id,
                    "onchainWorkflowId": workflow.onchain_workflow_id,
                    "attemptId": attempt.id,
                    "amount": str(reservation.amount),
                    "destinationChainId": attempt.chain_id,
                    "agentKey": attempt.agent_key,
                    "homeWallet": attempt.home_wallet,
                    "settlementAsset": attempt.settlement_asset,
                    "payoutMode": payout_mode,
                    "payoutAsset": attempt.payout_asset,
                    "payoutTokenAddress": attempt.payout_token_address,
                    "payoutDecimals": attempt.payout_decimals,
                    "payoutRate": decimal_string(payout_rate),
                    "payoutQuoteAmount": decimal_string(payout_quote),
                    "payoutMinAmount": decimal_string(payout_min),
                    "payoutSlippageBps": attempt.payout_slippage_bps,
                    "payoutQuoteDeadline": int(quote_expires_at.timestamp()),
                },
            )
        )
        session.add(
            FeeLedgerEntry(
                workflow_id=workflow.id,
                node_id=node.id,
                attempt_id=attempt.id,
                category="SETTLEMENT_CCIP_FEE",
                initial_payer="source-gas-treasury",
                final_responsible_party="PENDING",
                quoted_amount=Decimal("0"),
                charged_amount=Decimal("0"),
                asset="ETH",
                message_id=message_id,
                status="QUOTED",
            )
        )
        _event(
            session,
            workflow,
            "SETTLEMENT_PENDING",
            node=node,
            attempt=attempt,
            payload={
                "messageId": message_id,
                "destinationChainId": attempt.chain_id,
                "amount": str(reservation.amount),
                "settlementAsset": attempt.settlement_asset,
                "payoutMode": payout_mode,
                "payoutAsset": attempt.payout_asset,
                "payoutQuoteAmount": decimal_string(payout_quote),
                "payoutMinAmount": decimal_string(payout_min),
            },
        )
        return {"action": "settlement_pending", "nodeId": node.id, "attemptId": attempt.id, "messageId": message_id}
    if workflow.funding_mode == "ONCHAIN" and attempt.settlement_status != "CONFIRMED":
        raise HTTPException(status_code=409, detail="Cross-chain settlement has not been confirmed from the Agent home chain")
    amount = Decimal(str(reservation.amount))
    reservation.status = "PAID"
    reservation.paid_at = utcnow()
    attempt.status = "SETTLED"
    attempt.settlement_status = "SETTLED" if workflow.funding_mode == "ONCHAIN" else "LOCAL_PAID"
    attempt.settled_at = utcnow()
    node.status = "SETTLED"
    node.settled_at = utcnow()
    quality = apply_attempt_quality(attempt, node, review_passed=True, final=True)
    workflow.paid_amount = Decimal(str(workflow.paid_amount)) + amount
    selected_candidate = _match_candidate_for_attempt(session, attempt)
    if selected_candidate is not None:
        selected_candidate.status = "SETTLED"
        match_round = session.get(AgentMatchRound, selected_candidate.match_round_id)
        if match_round is not None:
            match_round.status = "COMPLETED"
            for candidate in session.scalars(
                select(AgentMatchCandidate).where(
                    AgentMatchCandidate.match_round_id == match_round.id,
                    AgentMatchCandidate.status == "AVAILABLE",
                )
            ):
                candidate.status = "NOT_NEEDED"
                candidate.exclusion_reason = "NODE_SETTLED"
    payout_target = f"agent:{attempt.agent_key}"
    if attempt.payout_status == "SWAPPED":
        payout_target = f"payout-swap:{attempt.chain_id}:{attempt.id}"
    _ledger(
        session,
        workflow,
        "PAYOUT",
        amount,
        f"escrow:{workflow.id}:reserved:{attempt.id}",
        payout_target,
        chain_id=attempt.chain_id,
        node=node,
        attempt=attempt,
        transaction_hash=attempt.destination_tx_hash,
        metadata={
            "routeVersion": attempt.route_version,
            "settlementMessageId": attempt.settlement_message_id,
            "sourceConfirmTxHash": attempt.source_confirm_tx_hash,
            "payoutStatus": attempt.payout_status,
            "payoutAsset": attempt.payout_asset,
            "payoutAmount": str(attempt.payout_quote_amount or amount),
            "payoutTxHash": attempt.payout_tx_hash,
        },
    )
    if attempt.payout_status == "SWAPPED" and attempt.payout_quote_amount is not None:
        _ledger(
            session,
            workflow,
            "PAYOUT_SWAP",
            Decimal(str(attempt.payout_quote_amount)),
            f"payout-pool:{attempt.chain_id}:{attempt.payout_asset}",
            f"agent:{attempt.agent_key}",
            asset_symbol=attempt.payout_asset,
            chain_id=attempt.chain_id,
            node=node,
            attempt=attempt,
            transaction_hash=attempt.payout_tx_hash,
            metadata={
                "settlementAsset": attempt.settlement_asset,
                "settlementAmount": str(amount),
                "payoutRate": str(attempt.payout_rate),
                "minPayoutAmount": str(attempt.payout_min_amount),
            },
        )
    _event(
        session,
        workflow,
        "NODE_SETTLED",
        node=node,
        attempt=attempt,
        payload={
            "amount": str(amount),
            "settlementAsset": attempt.settlement_asset,
            "payoutAmount": str(attempt.payout_quote_amount or amount),
            "payoutAsset": attempt.payout_asset,
            "payoutStatus": attempt.payout_status,
            "agentKey": attempt.agent_key,
            "qualityScore": str(quality["qualityScore"]),
            "qualityStatus": quality["qualityStatus"],
            "qualityPolicyVersion": quality["qualityPolicyVersion"],
            "qualityEvidenceHash": quality["qualityEvidenceHash"],
            "chainId": attempt.chain_id,
        },
    )
    _refresh_agent_reputation(session, workflow, node, attempt)
    unlocked = _unlock_dependents(session, workflow)
    return {"action": "node_settled", "nodeId": node.id, "attemptId": attempt.id, "unlocked": unlocked}


def confirm_settlement(
    session: Session,
    workflow: Workflow,
    node: WorkflowNode,
    attempt: NodeAttempt,
    body: SettlementConfirmation,
) -> dict[str, Any]:
    if attempt.settlement_status != "QUEUED" or attempt.settlement_message_id != body.message_id:
        raise HTTPException(status_code=409, detail="Attempt is not awaiting this settlement confirmation")
    recovery = session.scalar(select(SettlementRecoveryRecord).where(SettlementRecoveryRecord.attempt_id == attempt.id))
    if recovery is None:
        raise HTTPException(status_code=409, detail="Settlement custody record is missing")
    if os.getenv("APP_ENV", "development").lower() == "production" and recovery.phase != "PAYOUT_PENDING":
        raise HTTPException(status_code=409, detail="Production settlement requires verified ARRIVED and PAYOUT_PENDING custody checkpoints")
    reservation = session.scalar(select(BudgetReservation).where(BudgetReservation.attempt_id == attempt.id))
    if reservation is None or Decimal(str(reservation.amount)) != Decimal(body.received_amount):
        raise HTTPException(status_code=422, detail="Destination receipt amount does not match the reserved reward")
    if not attempt.home_wallet or attempt.home_wallet.lower() != body.home_wallet.lower():
        raise HTTPException(status_code=422, detail="Destination receipt wallet does not match the immutable Agent home wallet")
    if body.settlement_asset != attempt.settlement_asset:
        raise HTTPException(status_code=422, detail="Destination receipt settlement asset does not match the attempt snapshot")
    if attempt.payout_status == "QUOTED":
        required = (body.payout_asset, body.payout_token_address, body.payout_amount, body.min_payout_amount, body.payout_rate, body.payout_tx_hash)
        if any(value is None for value in required):
            raise HTTPException(status_code=422, detail="Destination payout conversion confirmation is incomplete")
        if body.payout_asset != attempt.payout_asset:
            raise HTTPException(status_code=422, detail="Destination payout asset does not match the attempt snapshot")
        if not attempt.payout_token_address or body.payout_token_address.lower() != attempt.payout_token_address.lower():
            raise HTTPException(status_code=422, detail="Destination payout token does not match the attempt snapshot")
        if Decimal(body.payout_rate) != Decimal(str(attempt.payout_rate)):
            raise HTTPException(status_code=422, detail="Destination payout rate does not match the route snapshot")
        if Decimal(body.min_payout_amount) != Decimal(str(attempt.payout_min_amount)):
            raise HTTPException(status_code=422, detail="Destination minimum payout does not match the signed quote")
        if Decimal(body.payout_amount) != Decimal(str(attempt.payout_quote_amount)):
            raise HTTPException(status_code=422, detail="Destination payout amount does not match the local route quote")
        attempt.payout_status = "SWAPPED"
        attempt.payout_tx_hash = body.payout_tx_hash
    else:
        attempt.payout_status = "DIRECT"
        attempt.payout_quote_amount = body.received_amount
        attempt.payout_min_amount = body.received_amount
        attempt.payout_tx_hash = body.payout_tx_hash or body.destination_tx_hash
    message = session.scalar(
        select(CrossChainMessage).where(
            CrossChainMessage.message_id == body.message_id,
            CrossChainMessage.attempt_id == attempt.id,
            CrossChainMessage.message_type == "SETTLEMENT",
        )
    )
    if message is None:
        raise HTTPException(status_code=404, detail="Settlement message was not found")
    message.status = "DELIVERED"
    message.transaction_hash = body.destination_tx_hash
    if body.transport_message_id:
        message.payload = {**message.payload, "transportMessageId": body.transport_message_id}
    message.relay_attempts += 1
    message.delivered_at = utcnow()
    attempt.settlement_status = "CONFIRMED"
    attempt.destination_tx_hash = body.destination_tx_hash
    attempt.source_confirm_tx_hash = body.source_confirm_tx_hash
    recovery.phase = "PAID"
    recovery.custody_chain_id = attempt.chain_id
    recovery.custody_contract = attempt.home_wallet
    recovery.asset = attempt.payout_asset
    recovery.token_address = attempt.payout_token_address
    recovery.amount = Decimal(str(attempt.payout_quote_amount or body.received_amount))
    recovery.last_transaction_hash = attempt.payout_tx_hash
    recovery.proof_hash = canonical_hash({
        "destinationTxHash": body.destination_tx_hash,
        "sourceConfirmTxHash": body.source_confirm_tx_hash,
        "payoutTxHash": attempt.payout_tx_hash,
    })
    recovery.state_version += 1
    fee = session.scalar(
        select(FeeLedgerEntry).where(
            FeeLedgerEntry.attempt_id == attempt.id,
            FeeLedgerEntry.category == "SETTLEMENT_CCIP_FEE",
            FeeLedgerEntry.message_id == body.message_id,
        )
    )
    if fee is not None:
        fee.charged_amount = Decimal(body.settlement_fee)
        fee.final_responsible_party = "USER_SOURCE_GAS_DEPOSIT"
        fee.transaction_hash = body.source_confirm_tx_hash
        fee.status = "CHARGED"
    _event(
        session,
        workflow,
        "SOURCE_CONFIRM_DELIVERED",
        actor="ProtocolWorker",
        node=node,
        attempt=attempt,
        payload={
            "messageId": body.message_id,
            "transportMessageId": body.transport_message_id,
            "homeWallet": body.home_wallet,
            "destinationTxHash": body.destination_tx_hash,
            "sourceConfirmTxHash": body.source_confirm_tx_hash,
            "settlementAsset": attempt.settlement_asset,
            "payoutAsset": attempt.payout_asset,
            "payoutAmount": str(attempt.payout_quote_amount),
            "payoutStatus": attempt.payout_status,
            "payoutTxHash": attempt.payout_tx_hash,
        },
    )
    return settle_node(session, workflow, node, attempt)


SETTLEMENT_RECOVERY_TRANSITIONS: dict[str, set[str]] = {
    "COMMITTED": {"IN_TRANSIT"},
    "IN_TRANSIT": {"ARRIVED", "RECOVERY_REQUIRED"},
    "ARRIVED": {"SWAP_PENDING", "PAYOUT_PENDING", "RECOVERY_REQUIRED", "REVERSE_BRIDGE_PENDING"},
    "SWAP_PENDING": {"PAYOUT_PENDING", "SWAP_FAILED"},
    "SWAP_FAILED": {"SWAP_PENDING", "RECOVERY_REQUIRED", "REVERSE_BRIDGE_PENDING"},
    "RECOVERY_REQUIRED": {"SWAP_PENDING", "PAYOUT_PENDING", "REVERSE_BRIDGE_PENDING"},
    "PAYOUT_PENDING": {"PAID", "RECOVERY_REQUIRED"},
    "REVERSE_BRIDGE_PENDING": {"REFUNDED", "RECOVERY_REQUIRED"},
    "PAID": set(),
    "REFUNDED": set(),
}


def update_settlement_custody(
    session: Session,
    workflow: Workflow,
    node: WorkflowNode,
    attempt: NodeAttempt,
    *,
    phase: str,
    custody_chain_id: int,
    custody_contract: str | None,
    asset: str,
    token_address: str | None,
    amount: str,
    transport_message_id: str | None,
    transaction_hash: str | None,
    proof_hash: str,
    failure_code: str | None,
    recovery_action: str | None,
    expected_state_version: int,
    worker_id: str,
) -> dict[str, Any]:
    recovery = session.scalar(select(SettlementRecoveryRecord).where(SettlementRecoveryRecord.attempt_id == attempt.id))
    if recovery is None:
        raise HTTPException(status_code=404, detail="Settlement custody record not found")
    if recovery.state_version != expected_state_version:
        raise HTTPException(status_code=409, detail="Settlement custody state version is stale")
    if phase not in SETTLEMENT_RECOVERY_TRANSITIONS.get(recovery.phase, set()):
        raise HTTPException(status_code=409, detail=f"Invalid settlement custody transition {recovery.phase} -> {phase}")
    if recovery.phase == "IN_TRANSIT" and phase == "RECOVERY_REQUIRED" and recovery.transport_message_id:
        recovery_action = recovery_action or "HOLD_IN_VAULT"
    if phase == "SWAP_FAILED" and not failure_code:
        raise HTTPException(status_code=422, detail="SWAP_FAILED requires failureCode")
    recovery.phase = phase
    recovery.custody_chain_id = custody_chain_id
    recovery.custody_contract = custody_contract
    recovery.asset = asset
    recovery.token_address = token_address
    recovery.amount = Decimal(amount)
    recovery.transport_message_id = transport_message_id or recovery.transport_message_id
    recovery.last_transaction_hash = transaction_hash
    recovery.proof_hash = proof_hash
    recovery.failure_code = failure_code
    recovery.recovery_action = recovery_action
    recovery.state_version += 1
    if phase == "SWAP_FAILED":
        attempt.payout_status = "SWAP_FAILED"
    elif phase == "PAYOUT_PENDING":
        attempt.payout_status = "PAYOUT_PENDING"
    _event(
        session,
        workflow,
        f"SETTLEMENT_{phase}",
        actor=f"ProtocolWorker:{worker_id}",
        node=node,
        attempt=attempt,
        payload={
            "custodyChainId": custody_chain_id,
            "custodyContract": custody_contract,
            "asset": asset,
            "amount": amount,
            "proofHash": proof_hash,
            "stateVersion": recovery.state_version,
        },
    )
    return {"action": "settlement_custody_updated", "phase": phase, "stateVersion": recovery.state_version}


def _finalize(
    session: Session,
    workflow: Workflow,
    *,
    platform_fee_tx_hash: str | None = None,
    token_refund_tx_hash: str | None = None,
    gas_refund_tx_hash: str | None = None,
    source_gas_refund_amount: Decimal | None = None,
) -> dict[str, Any]:
    fee = Decimal(str(workflow.platform_fee))
    paid = Decimal(str(workflow.paid_amount))
    budget = Decimal(str(workflow.budget))
    refund = budget - paid - fee
    if refund < 0:
        raise HTTPException(status_code=409, detail="Workflow ledger would overdraw locked budget")
    if workflow.funding_mode == "ONCHAIN" and workflow.contract_sync_status != "FINALIZED":
        existing = session.scalar(
            select(ProtocolJob).where(
                ProtocolJob.workflow_id == workflow.id,
                ProtocolJob.job_type == "FINALIZE_WORKFLOW",
                ProtocolJob.status.in_(["QUEUED", "RUNNING"]),
            )
        )
        if existing is None:
            existing = ProtocolJob(
                workflow_id=workflow.id,
                job_type="FINALIZE_WORKFLOW",
                payload={
                    "onchainWorkflowId": workflow.onchain_workflow_id,
                    "platformFee": str(fee),
                    "tokenRefund": str(refund),
                    "sourceGasRefund": str(Decimal(str(workflow.source_gas_deposit)) - Decimal(str(workflow.source_gas_refunded))),
                },
            )
            session.add(existing)
            session.flush()
        workflow.status = "COMPLETING"
        workflow.contract_sync_status = "FINALIZATION_QUEUED"
        _event(
            session,
            workflow,
            "WORKFLOW_FINALIZATION_QUEUED",
            payload={"jobId": existing.id, "platformFee": str(fee), "tokenRefund": str(refund)},
        )
        return {"action": "workflow_finalization_pending", "workflowId": workflow.id, "jobId": existing.id}
    if fee > 0:
        _ledger(
            session,
            workflow,
            "PLATFORM_FEE",
            fee,
            f"escrow:{workflow.id}:available",
            "platform:fees",
            transaction_hash=platform_fee_tx_hash,
        )
    if refund > 0:
        _ledger(
            session,
            workflow,
            "REFUND",
            refund,
            f"escrow:{workflow.id}:available",
            f"requester:{workflow.requester_id}",
            transaction_hash=token_refund_tx_hash,
        )
    gas_refund = source_gas_refund_amount if source_gas_refund_amount is not None else Decimal("0")
    available_gas = Decimal(str(workflow.source_gas_deposit)) - Decimal(str(workflow.source_gas_refunded))
    if gas_refund < 0 or gas_refund > available_gas:
        raise HTTPException(status_code=422, detail="Confirmed source Gas refund exceeds the deposited balance")
    if gas_refund > 0:
        workflow.source_gas_refunded = Decimal(str(workflow.source_gas_refunded)) + gas_refund
        _ledger(
            session,
            workflow,
            "GAS_REFUND",
            gas_refund,
            f"gas-treasury:{workflow.id}",
            f"requester:{workflow.requester_id}",
            asset_kind="GAS",
            asset_symbol="ETH",
            chain_id=CHAIN_A,
            transaction_hash=gas_refund_tx_hash,
        )
    source_gas_charged = available_gas - gas_refund
    source_deposit = session.scalar(
        select(GasDeposit).where(GasDeposit.workflow_id == workflow.id, GasDeposit.attempt_id.is_(None))
    )
    if source_deposit is not None:
        source_deposit.charged_amount = source_gas_charged
        source_deposit.refunded_amount = gas_refund
        source_deposit.refund_tx_hash = gas_refund_tx_hash
        source_deposit.status = "REFUNDED"
    if source_gas_charged > 0:
        _ledger(
            session,
            workflow,
            "SOURCE_PROTOCOL_GAS",
            source_gas_charged,
            f"gas-treasury:{workflow.id}",
            "protocol-relayer",
            asset_kind="GAS",
            asset_symbol="ETH",
            chain_id=CHAIN_A,
            metadata={"measuredFromOnchainTreasury": True},
        )
    workflow.refunded_amount = refund
    workflow.status = "COMPLETED"
    workflow.completed_at = utcnow()
    _event(
        session,
        workflow,
        "WORKFLOW_COMPLETED",
        payload={"paid": str(paid), "platformFee": str(fee), "refunded": str(refund)},
    )
    return {"action": "workflow_completed", "workflowId": workflow.id, "refund": str(refund)}


def complete_workflow_finalization(
    session: Session,
    workflow: Workflow,
    body: WorkflowFinalizationConfirmation,
) -> dict[str, Any]:
    if workflow.funding_mode != "ONCHAIN" or workflow.status != "COMPLETING":
        raise HTTPException(status_code=409, detail="Workflow is not awaiting on-chain finalization")
    workflow.contract_sync_status = "FINALIZED"
    return _finalize(
        session,
        workflow,
        platform_fee_tx_hash=body.platform_fee_tx_hash,
        token_refund_tx_hash=body.token_refund_tx_hash,
        gas_refund_tx_hash=body.gas_refund_tx_hash,
        source_gas_refund_amount=Decimal(body.source_gas_refund_amount),
    )


def _start_failed_refund(session: Session, workflow: Workflow) -> dict[str, Any]:
    reservations = list(
        session.scalars(
            select(BudgetReservation).where(
                BudgetReservation.workflow_id == workflow.id,
                BudgetReservation.status == "RESERVED",
            )
        )
    )
    for reservation in reservations:
        reservation.status = "RELEASED"
        reservation.released_at = utcnow()
        node = session.get(WorkflowNode, reservation.node_id)
        attempt = session.get(NodeAttempt, reservation.attempt_id)
        _ledger(
            session,
            workflow,
            "RELEASE_RESERVATION",
            Decimal(str(reservation.amount)),
            f"escrow:{workflow.id}:reserved:{reservation.attempt_id}",
            f"escrow:{workflow.id}:available",
            node=node,
            attempt=attempt,
            metadata={"failureRefund": True},
        )
    workflow.status = "FAILED_REFUNDING"
    _event(
        session,
        workflow,
        "WORKFLOW_FAILED_REFUNDING",
        payload={"releasedReservations": len(reservations), "failureReason": workflow.failure_reason},
    )
    if workflow.funding_mode == "ONCHAIN":
        session.add(
            ProtocolJob(
                workflow_id=workflow.id,
                job_type="REFUND_FAILED_WORKFLOW",
                payload={
                    "onchainWorkflowId": workflow.onchain_workflow_id,
                    "tokenRefund": str(Decimal(str(workflow.budget)) - Decimal(str(workflow.paid_amount))),
                    "sourceGasRefund": str(workflow.source_gas_deposit),
                },
            )
        )
    return {"action": "workflow_failed_refunding", "workflowId": workflow.id}


def complete_failed_refund(
    session: Session,
    workflow: Workflow,
    *,
    token_refund_tx_hash: str | None = None,
    gas_refund_tx_hash: str | None = None,
    source_gas_refund_amount: Decimal | None = None,
) -> dict[str, Any]:
    if workflow.status != "FAILED_REFUNDING":
        raise HTTPException(status_code=409, detail="Workflow is not awaiting failed-workflow refunds")
    paid = Decimal(str(workflow.paid_amount))
    refund = Decimal(str(workflow.budget)) - paid
    if refund < 0:
        raise HTTPException(status_code=409, detail="Failed workflow refund would overdraw the locked budget")
    if refund > 0:
        _ledger(
            session,
            workflow,
            "REFUND",
            refund,
            f"escrow:{workflow.id}:available",
            f"requester:{workflow.requester_id}",
            transaction_hash=token_refund_tx_hash,
            metadata={"failedWorkflow": True},
        )
        session.add(
            FeeLedgerEntry(
                workflow_id=workflow.id,
                category="TOKEN_REFUND",
                initial_payer=f"escrow:{workflow.id}",
                final_responsible_party="USER",
                quoted_amount=refund,
                charged_amount=refund,
                asset=workflow.currency,
                transaction_hash=token_refund_tx_hash,
                status="REFUNDED",
            )
        )
    gas_refund = source_gas_refund_amount if source_gas_refund_amount is not None else Decimal("0")
    available_gas = Decimal(str(workflow.source_gas_deposit)) - Decimal(str(workflow.source_gas_refunded))
    if gas_refund < 0 or gas_refund > available_gas:
        raise HTTPException(status_code=422, detail="Confirmed source Gas refund exceeds the deposited balance")
    if gas_refund > 0:
        workflow.source_gas_refunded = gas_refund
        _ledger(
            session,
            workflow,
            "GAS_REFUND",
            gas_refund,
            f"gas-treasury:{workflow.id}",
            f"requester:{workflow.requester_id}",
            asset_kind="GAS",
            asset_symbol="ETH",
            transaction_hash=gas_refund_tx_hash,
            metadata={"failedWorkflow": True},
        )
        deposit = session.scalar(
            select(GasDeposit).where(GasDeposit.workflow_id == workflow.id, GasDeposit.attempt_id.is_(None))
        )
        if deposit is not None:
            deposit.charged_amount = available_gas - gas_refund
            deposit.refunded_amount = gas_refund
            deposit.refund_tx_hash = gas_refund_tx_hash
            deposit.status = "REFUNDED"
    workflow.refunded_amount = refund
    workflow.status = "FAILED_REFUNDED"
    workflow.failure_refunded_at = utcnow()
    workflow.completed_at = utcnow()
    _event(
        session,
        workflow,
        "WORKFLOW_FAILED_REFUNDED",
        payload={"tokenRefund": str(refund), "sourceGasRefund": str(gas_refund)},
    )
    return {"action": "workflow_failed_refunded", "workflowId": workflow.id, "refund": str(refund), "gasRefund": str(gas_refund)}


def coordinate_once(session: Session, workflow: Workflow) -> dict[str, Any]:
    if workflow.status in {"COMPLETED", "REFUNDED", "FAILED_REFUNDED", "CANCELLED"}:
        return {"action": "idle", "reason": f"workflow is {workflow.status}"}
    if workflow.status == "FAILED":
        return _start_failed_refund(session, workflow)
    if workflow.status == "FAILED_REFUNDING":
        if workflow.funding_mode == "ONCHAIN":
            return {"action": "waiting_failed_refund_confirmation", "workflowId": workflow.id}
        return complete_failed_refund(session, workflow)
    if workflow.status == "AWAITING_FUNDING":
        return {"action": "funding_required", "workflowId": workflow.id}

    nodes = list(
        session.scalars(
            select(WorkflowNode).where(WorkflowNode.workflow_id == workflow.id).order_by(WorkflowNode.created_at)
        )
    )
    for node in nodes:
        if not node.current_attempt_id:
            continue
        attempt = session.get(NodeAttempt, node.current_attempt_id)
        if attempt is not None and attempt.status == "RESULT_SUBMITTED":
            return review_attempt(session, workflow, node, attempt)
        if attempt is not None and attempt.status == "CHALLENGE_PERIOD" and attempt.challenge_expires_at is not None:
            challenge_expires_at = attempt.challenge_expires_at
            if challenge_expires_at.tzinfo is None:
                challenge_expires_at = challenge_expires_at.replace(tzinfo=timezone.utc)
            if utcnow() >= challenge_expires_at:
                return _complete_review_pass(
                    session,
                    workflow,
                    node,
                    attempt,
                    "Required approvals passed and the challenge period expired without a dispute",
                    source="CHALLENGE_PERIOD_EXPIRY",
                )
    for node in nodes:
        if node.status != "SUCCEEDED" or not node.current_attempt_id:
            continue
        attempt = session.get(NodeAttempt, node.current_attempt_id)
        if attempt is not None and attempt.status == "REVIEW_PASSED":
            return settle_node(session, workflow, node, attempt)
    for node in nodes:
        if node.status in {"READY", "RETRYING", "WAITING_FOR_AGENT"}:
            return dispatch_node(session, workflow, node)
    if nodes and all(node.status == "SETTLED" for node in nodes):
        return _finalize(session, workflow)
    return {"action": "idle", "reason": "waiting for an active attempt or settled dependency"}


def coordinate(session: Session, workflow: Workflow, max_actions: int) -> list[dict[str, Any]]:
    actions: list[dict[str, Any]] = []
    for _ in range(max_actions):
        action = coordinate_once(session, workflow)
        actions.append(action)
        if action["action"] in {
            "idle",
            "node_dispatched",
            "waiting_for_agent",
            "route_deployment_queued",
            "route_configuration_required",
            "settlement_pending",
            "review_onchain_pending",
            "funding_required",
            "workflow_failed",
            "workflow_failed_refunding",
            "workflow_failed_refunded",
            "waiting_failed_refund_confirmation",
            "workflow_finalization_pending",
            "workflow_completed",
        }:
            break
    return actions


def workflow_ledger_summary(session: Session, workflow: Workflow) -> dict[str, Any]:
    entries = list(
        session.scalars(
            select(LedgerEntry).where(LedgerEntry.workflow_id == workflow.id).order_by(LedgerEntry.sequence)
        )
    )
    by_asset: dict[tuple[str, str], dict[str, Decimal]] = {}
    for entry in entries:
        key = (entry.asset_kind, entry.asset_symbol)
        values = by_asset.setdefault(key, {"debits": Decimal("0"), "credits": Decimal("0")})
        values["debits"] += Decimal(str(entry.amount))
        values["credits"] += Decimal(str(entry.amount))
    assets = [
        {
            "assetKind": kind,
            "assetSymbol": symbol,
            "debits": str(values["debits"]),
            "credits": str(values["credits"]),
            "balanced": values["debits"] == values["credits"],
        }
        for (kind, symbol), values in sorted(by_asset.items())
    ]
    return {"assets": assets, "balanced": all(item["balanced"] for item in assets), "entryCount": len(entries)}
