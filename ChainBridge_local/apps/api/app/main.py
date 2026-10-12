from __future__ import annotations

import os
import uuid
from contextlib import asynccontextmanager
from datetime import datetime, timedelta, timezone
from decimal import Decimal
from typing import Any

from fastapi import Depends, FastAPI, HTTPException, Query, Request
from fastapi.exceptions import RequestValidationError
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from jsonschema import Draft202012Validator, SchemaError
from sqlalchemy import and_, func, or_, select, update
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from .config import Settings
from .chain_protocol import opaque_id_to_bytes32, quote_terms_hash
from .database import Base, build_session_factory, get_session
from .dex import DexQuoteError, EvmRpcVerifier, NATIVE_ETH, SwapVerificationError, ZeroExClient, to_base_units
from .did import LocalDIDResolver
from .integration_auth import require_listener_auth
from .models import (
    Agent,
    AuditLog,
    AuthChallenge,
    Bid,
    Capability,
    Credential,
    Delivery,
    Demand,
    DexQuote,
    Message,
    Notification,
    Order,
    OrderEvent,
    PaymentDecision,
    Review,
    RelayerTask,
    Settlement,
    SettlementChainEvent,
    WebhookSubscription,
    utcnow,
)
from .schemas import (
    BidCreate,
    AcceptDelivery,
    ArbitrationResolve,
    CapabilityCreate,
    ChallengeRequest,
    DeliveryCreate,
    DemandCreate,
    DevLoginRequest,
    DirectOrderCreate,
    DexQuoteCreate,
    DexQuoteExecution,
    DisputeCreate,
    FundRequest,
    InvokeRequest,
    MessageCreate,
    ProviderOrderConfirmation,
    RefundCreate,
    RelayerTaskHeartbeat,
    RelayerTaskLease,
    RelayerTaskResult,
    ReviewCreate,
    SettlementQuoteRequest,
    SettlementQuoteResponse,
    SettlementChainEventCreate,
    OrphanedChainEvent,
    StalledSettlementCheck,
    VerifyRequest,
    WebhookCreate,
)
from .security import (
    create_challenge,
    get_current_agent,
    idempotency_header,
    issue_token,
    require_role,
    verify_signature,
)
from .seed import DEMO_AGENTS, seed_database
from .serializers import serialize
from .services import (
    add_order_event,
    audit,
    canonical_hash,
    execute_demo_capability,
    get_idempotent_response,
    notify,
    require_order_party,
    save_idempotent_response,
    transition_order,
    validate_json_schema,
)
from .settlement import ExternalCCIPSettlementAdapter, MockSettlementAdapter, SettlementAdapter
from .coordinator import seed_workflow_runtime
from .reputation import refresh_all_agent_reputations
from .workflow_api import router as workflow_router
from .workflow_recovery import WorkflowRecoveryScheduler
# Importing the dynamic models registers their tables on Base.metadata before
# the development/test create_all lifecycle runs.
from . import workflow_models as workflow_models  # noqa: F401


def _aware(value: datetime) -> datetime:
    return value if value.tzinfo is not None else value.replace(tzinfo=timezone.utc)


def _list_response(items: list[Any], total: int) -> dict[str, Any]:
    return {"items": serialize(items), "total": total}


def _not_found(resource: str) -> HTTPException:
    return HTTPException(status_code=404, detail=f"{resource} not found")


def _order_detail(session: Session, order: Order) -> dict[str, Any]:
    # Session autoflush is disabled so every compound response must expose events and
    # delivery records created by the current state transition.
    session.flush()
    data = serialize(order)
    data["requester"] = serialize(session.get(Agent, order.requester_id))
    data["provider"] = serialize(session.get(Agent, order.provider_id))
    data["events"] = serialize(
        list(session.scalars(select(OrderEvent).where(OrderEvent.order_id == order.id).order_by(OrderEvent.created_at)))
    )
    data["messages"] = serialize(
        list(session.scalars(select(Message).where(Message.order_id == order.id).order_by(Message.created_at)))
    )
    data["delivery"] = serialize(session.scalar(select(Delivery).where(Delivery.order_id == order.id)))
    settlement = session.get(Settlement, order.settlement_id) if order.settlement_id else None
    data["settlement"] = serialize(settlement)
    if settlement is not None:
        data["settlement"]["onchainSettlementId"] = opaque_id_to_bytes32(settlement.id)
        data["settlement"]["onchainOrderId"] = opaque_id_to_bytes32(order.id)
    data["paymentDecision"] = serialize(
        session.scalar(select(PaymentDecision).where(PaymentDecision.order_id == order.id))
    )
    data["settlementEvents"] = serialize(
        list(
            session.scalars(
                select(SettlementChainEvent)
                .where(SettlementChainEvent.settlement_id == order.settlement_id)
                .order_by(SettlementChainEvent.created_at)
            )
        )
    ) if order.settlement_id else []
    return data


def _capability_detail(session: Session, capability: Capability) -> dict[str, Any]:
    data = serialize(capability)
    data["provider"] = serialize(session.get(Agent, capability.provider_id))
    return data


def _demand_detail(session: Session, demand: Demand, include_bids: bool = True) -> dict[str, Any]:
    data = serialize(demand)
    data["requester"] = serialize(session.get(Agent, demand.requester_id))
    bids = list(session.scalars(select(Bid).where(Bid.demand_id == demand.id).order_by(Bid.created_at)))
    data["bidCount"] = len(bids)
    if include_bids:
        bid_items: list[dict[str, Any]] = []
        for bid in bids:
            item = serialize(bid)
            item["provider"] = serialize(session.get(Agent, bid.provider_id))
            bid_items.append(item)
        data["bids"] = bid_items
    return data


def _create_payment_decision(
    session: Session,
    order: Order,
    settlement: Settlement,
    actor: Agent,
    decision_type: str,
    payable_amount: Decimal,
    rationale: str,
    verification_score: int | None = None,
) -> PaymentDecision:
    if session.scalar(select(PaymentDecision).where(PaymentDecision.order_id == order.id)) is not None:
        raise HTTPException(status_code=409, detail="Order already has a payment decision")
    budget = Decimal(str(settlement.amount))
    if payable_amount < 0 or payable_amount > budget:
        raise HTTPException(status_code=422, detail="Payable amount must be between zero and the locked budget")
    if decision_type != "arbitration_refund" and payable_amount == 0:
        raise HTTPException(status_code=422, detail="A release decision must pay more than zero")
    delivery = session.scalar(select(Delivery).where(Delivery.order_id == order.id))
    decision_payload = {
        "orderId": order.id,
        "settlementId": settlement.id,
        "termsHash": order.terms_hash,
        "decisionType": decision_type,
        "decidedByDid": actor.did,
        "deliveryHash": delivery.content_hash if delivery is not None else None,
        "payableAmount": serialize(payable_amount),
        "refundAmount": serialize(budget - payable_amount),
        "verificationScore": verification_score,
        "rationale": rationale,
    }
    decision = PaymentDecision(
        order_id=order.id,
        settlement_id=settlement.id,
        decided_by_id=actor.id,
        decision_type=decision_type,
        payable_amount=payable_amount,
        refund_amount=budget - payable_amount,
        verification_score=verification_score,
        rationale=rationale,
        delivery_hash=delivery.content_hash if delivery is not None else None,
        decision_hash=canonical_hash(decision_payload),
    )
    session.add(decision)
    session.flush()
    return decision


def _settlement_by_onchain_id(session: Session, onchain_id: str) -> Settlement | None:
    # Opaque UUIDs are hashed before entering the ABI, so reconciliation uses the same deterministic mapping.
    for settlement in session.scalars(select(Settlement)):
        if opaque_id_to_bytes32(settlement.id).lower() == onchain_id.lower():
            return settlement
    return None


def _ensure_relayer_task(
    session: Session,
    settlement: Settlement,
    task_type: str,
    *,
    payable_amount: Decimal | None = None,
    ccip_message_id: str | None = None,
) -> RelayerTask:
    task = session.scalar(
        select(RelayerTask).where(
            RelayerTask.settlement_id == settlement.id,
            RelayerTask.task_type == task_type,
        )
    )
    if task is None:
        task = RelayerTask(
            settlement_id=settlement.id,
            task_type=task_type,
            payable_amount=payable_amount,
            ccip_message_id=ccip_message_id,
        )
        session.add(task)
        session.flush()
    return task


def _integration_actor(session: Session) -> Agent:
    actor = session.scalar(select(Agent).where(Agent.slug == "system-integration"))
    if actor is None:
        raise HTTPException(status_code=503, detail="Integration actor is not configured")
    return actor


def _ensure_integration_actor(session: Session) -> None:
    if session.scalar(select(Agent).where(Agent.slug == "system-integration")) is not None:
        return
    session.add(
        Agent(
            did="did:system:crosschain-integration",
            slug="system-integration",
            display_name="Cross-chain Integration",
            description="System identity for authenticated chain reconciliation.",
            roles=["integration"],
            operational_public_key="system-managed",
        )
    )
    session.commit()


def _direct_order(
    session: Session,
    requester: Agent,
    capability: Capability,
    body: DirectOrderCreate,
    adapter: SettlementAdapter,
) -> Order:
    if capability.provider_id == requester.id:
        raise HTTPException(status_code=409, detail="A provider cannot order its own capability")
    if capability.availability != "available":
        raise HTTPException(status_code=409, detail="Capability is not currently available")
    validate_json_schema(capability.input_schema, body.input_payload)
    if body.deadline is not None and _aware(body.deadline) <= utcnow():
        raise HTTPException(status_code=422, detail="Order deadline must be in the future")
    budget = Decimal(body.budget_max) if body.budget_max is not None else Decimal(capability.base_price)
    if budget < Decimal(capability.base_price):
        raise HTTPException(status_code=422, detail="Budget cap cannot be below the capability base price")
    acceptance_criteria = body.acceptance_criteria or [
        f"Output must validate against capability {capability.version} output schema"
    ]
    terms = {
        "capability": serialize(capability),
        "taskDescription": body.task_description or capability.description,
        "inputPayload": body.input_payload,
        "acceptanceCriteria": acceptance_criteria,
        "deadline": serialize(body.deadline),
        "delivery": {
            "format": body.delivery_format,
            "instructions": body.delivery_instructions,
        },
        "pricing": {
            "mode": body.pricing_mode,
            "budgetMax": serialize(budget),
            "currency": capability.currency,
        },
        "settlement": {
            "sourceAsset": body.source_asset,
            "settlementToken": body.settlement_token,
            "destinationLocalAsset": body.destination_local_asset,
            "sourceChain": body.source_chain,
            "destinationChain": body.destination_chain,
            "payerAccount": body.payer_account,
            "payeeAccount": body.payee_account,
            "assetId": body.asset_id,
        },
        "disputeWindowSeconds": body.dispute_window_seconds,
        "sourceType": "direct",
    }
    order = Order(
        requester_id=requester.id,
        provider_id=capability.provider_id,
        capability_id=capability.id,
        source_type="direct",
        status="awaiting_confirmation",
        title=capability.name,
        terms_snapshot=terms,
        input_payload=body.input_payload,
        amount=budget,
        currency=capability.currency,
        terms_hash=canonical_hash(terms),
    )
    session.add(order)
    session.flush()
    adapter.create_intent(session, order, body.model_dump(by_alias=True))
    add_order_event(session, order, requester, "order.created", None, "awaiting_confirmation")
    notify(session, capability.provider_id, "order.created", "收到新的直接订单", capability.name, "order", order.id)
    return order


def create_app(settings: Settings | None = None) -> FastAPI:
    settings = settings or Settings.from_env()
    session_factory = build_session_factory(settings.database_url)
    recovery_scheduler = WorkflowRecoveryScheduler(
        session_factory,
        enabled=settings.workflow_recovery_enabled and settings.app_env != "test",
        interval_seconds=settings.workflow_recovery_interval_seconds,
        max_workflows=settings.workflow_recovery_max_workflows,
        max_actions=settings.workflow_recovery_max_actions,
    )

    @asynccontextmanager
    async def lifespan(application: FastAPI):
        engine = application.state.session_factory.kw["bind"]
        if settings.app_env != "production":
            Base.metadata.create_all(engine)
        if settings.app_env in {"development", "test"}:
            with application.state.session_factory() as session:
                seed_database(session)
        with application.state.session_factory() as session:
            _ensure_integration_actor(session)
            seed_workflow_runtime(session)
            refresh_all_agent_reputations(session)
            session.commit()
        if settings.app_env == "production" and settings.settlement_mode == "external_ccip":
            required_values = {
                "ZERO_EX_API_KEY": settings.zero_ex_api_key,
                "ZERO_EX_ALLOWED_TARGETS": settings.zero_ex_allowed_targets,
                "ZERO_EX_ALLOWED_ALLOWANCE_TARGETS": settings.zero_ex_allowed_allowance_targets,
                "ZERO_EX_ALLOWED_SELECTORS": settings.zero_ex_allowed_selectors,
                "SOURCE_SETTLEMENT_TOKEN": settings.source_usdc_address,
                "DESTINATION_SETTLEMENT_TOKEN": settings.destination_usdc_address,
                "DESTINATION_STC_TOKEN": settings.destination_stc_address,
                "SOURCE_CHAIN_SELECTOR": settings.source_chain_selector,
                "DESTINATION_CHAIN_SELECTOR": settings.destination_chain_selector,
            }
            missing = [name for name, value in required_values.items() if not value]
            if missing:
                raise RuntimeError(f"External settlement configuration is incomplete: {', '.join(missing)}")
            application.state.source_swap_verifier.validate_configuration()
            application.state.destination_swap_verifier.validate_configuration()
        recovery_scheduler.start()
        try:
            yield
        finally:
            recovery_scheduler.stop()

    application = FastAPI(
        title="Agent Cross-chain Shop API",
        version="0.1.0",
        description="Centralized agent discovery and workflow API with DID authentication.",
        docs_url="/api/docs",
        redoc_url=None,
        openapi_url="/api/v1/openapi.json",
        lifespan=lifespan,
    )
    application.state.settings = settings
    application.state.session_factory = session_factory
    application.state.workflow_recovery = recovery_scheduler
    application.state.settlement_adapter = (
        ExternalCCIPSettlementAdapter() if settings.settlement_mode == "external_ccip" else MockSettlementAdapter()
    )
    application.state.dex_client = ZeroExClient(settings)
    application.state.source_swap_verifier = EvmRpcVerifier(settings.source_rpc_url, settings.source_chain_id)
    application.state.destination_swap_verifier = EvmRpcVerifier(
        settings.destination_rpc_url, settings.destination_chain_id
    )
    application.add_middleware(
        CORSMiddleware,
        allow_origins=list(settings.cors_origins),
        allow_credentials=True,
        allow_methods=["*"],
        allow_headers=["*"],
    )
    application.include_router(workflow_router)

    @application.exception_handler(HTTPException)
    async def http_error_handler(request: Request, exc: HTTPException) -> JSONResponse:
        return JSONResponse(
            status_code=exc.status_code,
            content={"error": {"code": f"HTTP_{exc.status_code}", "message": str(exc.detail)}},
            headers=exc.headers,
        )

    @application.exception_handler(RequestValidationError)
    async def validation_error_handler(request: Request, exc: RequestValidationError) -> JSONResponse:
        details = [
            {"path": ".".join(str(part) for part in error["loc"]), "message": error["msg"]}
            for error in exc.errors()
        ]
        return JSONResponse(
            status_code=422,
            content={"error": {"code": "VALIDATION_ERROR", "message": "Request validation failed", "details": details}},
        )

    @application.exception_handler(IntegrityError)
    async def integrity_error_handler(request: Request, exc: IntegrityError) -> JSONResponse:
        return JSONResponse(
            status_code=409,
            content={"error": {"code": "CONFLICT", "message": "A conflicting resource already exists"}},
        )

    @application.get("/api/v1/health", tags=["system"])
    def health(session: Session = Depends(get_session)) -> dict[str, Any]:
        session.scalar(select(func.count()).select_from(Agent))
        return {
            "status": "ok",
            "database": "connected",
            "service": "agent-crosschain-platform-api",
            "runtimeId": os.getenv("LOCAL_PLATFORM_RUNTIME_ID"),
            "timestamp": serialize(utcnow()),
            "workflowRecovery": recovery_scheduler.status(),
        }

    @application.get("/api/v1/meta", tags=["system"])
    def meta() -> dict[str, Any]:
        return {
            "name": "Agent Cross-chain Shop",
            "version": "0.1.0",
            "environment": settings.app_env,
            "settlementMode": settings.settlement_mode,
            "identityMethods": ["did:local"],
            "currencies": ["USDC"],
            "orderStates": [
                "awaiting_confirmation",
                "awaiting_funding",
                "funded",
                "in_progress",
                "delivered",
                "accepted",
                "completed",
                "cancelled",
                "disputed",
                "refund_pending",
                "refunded",
                "settlement_failed",
            ],
        }

    @application.post("/api/v1/auth/challenges", tags=["auth"], status_code=201)
    def auth_challenge(body: ChallengeRequest, session: Session = Depends(get_session)) -> dict[str, Any]:
        if body.audience != "agent-market":
            raise HTTPException(status_code=422, detail="Unsupported challenge audience")
        resolver = LocalDIDResolver(session)
        if resolver.resolve(body.did) is None:
            raise _not_found("DID")
        challenge = create_challenge(session, body.did, body.audience, settings.challenge_ttl_seconds)
        return serialize(challenge)

    @application.post("/api/v1/auth/verify", tags=["auth"])
    def auth_verify(body: VerifyRequest, session: Session = Depends(get_session)) -> dict[str, Any]:
        challenge = session.get(AuthChallenge, body.challenge_id)
        if challenge is None or challenge.did != body.did:
            raise HTTPException(status_code=401, detail="Challenge is invalid")
        if challenge.used_at is not None:
            raise HTTPException(status_code=401, detail="Challenge has already been used")
        if challenge.audience != body.audience:
            raise HTTPException(status_code=401, detail="Challenge audience does not match")
        if _aware(challenge.expires_at) <= utcnow():
            raise HTTPException(status_code=401, detail="Challenge has expired")
        document = LocalDIDResolver(session).resolve(body.did)
        if document is None:
            raise HTTPException(status_code=401, detail="DID cannot be resolved")
        public_key = document["verificationMethod"][0]["publicKeyHex"]
        if not verify_signature(public_key, challenge.message, body.signature):
            raise HTTPException(status_code=401, detail="Signature verification failed")
        agent = session.scalar(select(Agent).where(Agent.did == body.did))
        if agent is None or agent.status != "active":
            raise HTTPException(status_code=401, detail="Agent is not active")
        challenge.used_at = utcnow()
        token, expires_at = issue_token(agent, settings.app_secret, settings.jwt_ttl_seconds)
        audit(session, agent.id, "auth.login", "agent", agent.id, {"did": body.did})
        session.commit()
        return {"accessToken": token, "tokenType": "bearer", "expiresAt": serialize(expires_at), "agent": serialize(agent)}

    @application.get("/api/v1/dev/identities", tags=["development"])
    def dev_identities(session: Session = Depends(get_session)) -> dict[str, Any]:
        if settings.app_env not in {"development", "test"}:
            raise _not_found("Route")
        agents = list(session.scalars(select(Agent).where(Agent.did.like("did:local:%")).order_by(Agent.display_name)))
        return {"items": [{"id": item.id, "did": item.did, "displayName": item.display_name, "roles": item.roles} for item in agents]}

    @application.post("/api/v1/auth/dev-login", tags=["development"])
    def dev_login(body: DevLoginRequest, session: Session = Depends(get_session)) -> dict[str, Any]:
        if settings.app_env not in {"development", "test"}:
            raise _not_found("Route")
        allowed_dids = {f"did:local:{definition['slug']}" for definition in DEMO_AGENTS}
        if body.did not in allowed_dids:
            raise HTTPException(status_code=403, detail="Only seeded development identities may use dev login")
        agent = session.scalar(select(Agent).where(Agent.did == body.did, Agent.status == "active"))
        if agent is None:
            raise _not_found("Development identity")
        token, expires_at = issue_token(agent, settings.app_secret, settings.jwt_ttl_seconds)
        audit(session, agent.id, "auth.dev_login", "agent", agent.id, {"development": True})
        session.commit()
        return {"accessToken": token, "tokenType": "bearer", "expiresAt": serialize(expires_at), "agent": serialize(agent)}

    @application.get("/api/v1/dids/{did:path}", tags=["identity"])
    def resolve_did(did: str, session: Session = Depends(get_session)) -> dict[str, Any]:
        document = LocalDIDResolver(session).resolve(did)
        if document is None:
            raise _not_found("DID")
        return {"didDocument": document, "didResolutionMetadata": {"contentType": "application/did+ld+json"}}

    @application.get("/api/v1/me", tags=["identity"])
    def me(agent: Agent = Depends(get_current_agent)) -> dict[str, Any]:
        return serialize(agent)

    @application.get("/api/v1/agents", tags=["agents"])
    def list_agents(
        role: str | None = None,
        search: str | None = None,
        limit: int = Query(default=50, ge=1, le=100),
        offset: int = Query(default=0, ge=0),
        session: Session = Depends(get_session),
    ) -> dict[str, Any]:
        statement = select(Agent).where(Agent.status == "active")
        if search:
            pattern = f"%{search}%"
            statement = statement.where(or_(Agent.display_name.ilike(pattern), Agent.description.ilike(pattern)))
        agents = list(session.scalars(statement.order_by(Agent.created_at).offset(offset).limit(limit)))
        if role:
            agents = [agent for agent in agents if role in agent.roles]
        return _list_response(agents, len(agents))

    @application.get("/api/v1/agents/{agent_id}", tags=["agents"])
    def get_agent(agent_id: str, session: Session = Depends(get_session)) -> dict[str, Any]:
        agent = session.get(Agent, agent_id)
        if agent is None:
            raise _not_found("Agent")
        data = serialize(agent)
        data["capabilities"] = serialize(
            list(session.scalars(select(Capability).where(Capability.provider_id == agent.id).order_by(Capability.name)))
        )
        return data

    @application.get("/api/v1/agents/{agent_id}/credentials", tags=["agents"])
    def get_agent_credentials(agent_id: str, session: Session = Depends(get_session)) -> dict[str, Any]:
        if session.get(Agent, agent_id) is None:
            raise _not_found("Agent")
        credentials = list(session.scalars(select(Credential).where(Credential.agent_id == agent_id)))
        return _list_response(credentials, len(credentials))

    @application.get("/api/v1/capabilities", tags=["capabilities"])
    def list_capabilities(
        search: str | None = None,
        availability: str | None = None,
        provider_id: str | None = None,
        limit: int = Query(default=50, ge=1, le=100),
        offset: int = Query(default=0, ge=0),
        session: Session = Depends(get_session),
    ) -> dict[str, Any]:
        statement = select(Capability)
        if search:
            pattern = f"%{search}%"
            statement = statement.where(or_(Capability.name.ilike(pattern), Capability.description.ilike(pattern)))
        if availability:
            statement = statement.where(Capability.availability == availability)
        if provider_id:
            statement = statement.where(Capability.provider_id == provider_id)
        total = session.scalar(select(func.count()).select_from(statement.subquery())) or 0
        items = list(session.scalars(statement.order_by(Capability.created_at.desc()).offset(offset).limit(limit)))
        return {"items": [_capability_detail(session, item) for item in items], "total": total}

    @application.post("/api/v1/capabilities", tags=["capabilities"], status_code=201)
    def create_capability(
        body: CapabilityCreate,
        actor: Agent = Depends(require_role("provider")),
        idem: str | None = Depends(idempotency_header),
        session: Session = Depends(get_session),
    ) -> dict[str, Any]:
        payload = body.model_dump(by_alias=True, mode="json")
        replay = get_idempotent_response(session, actor.id, "capabilities.create", idem, payload)
        if replay:
            return replay
        try:
            Draft202012Validator.check_schema(body.input_schema)
            Draft202012Validator.check_schema(body.output_schema)
        except SchemaError as exc:
            raise HTTPException(status_code=422, detail=f"Invalid JSON Schema: {exc.message}") from exc
        validate_json_schema(body.input_schema, body.example_input)
        capability = Capability(provider_id=actor.id, **body.model_dump())
        session.add(capability)
        session.flush()
        response = serialize(capability)
        audit(session, actor.id, "capability.created", "capability", capability.id)
        save_idempotent_response(session, actor.id, "capabilities.create", idem, payload, response)
        session.commit()
        return response

    @application.get("/api/v1/capabilities/{capability_id}", tags=["capabilities"])
    def get_capability(capability_id: str, session: Session = Depends(get_session)) -> dict[str, Any]:
        capability = session.get(Capability, capability_id)
        if capability is None:
            raise _not_found("Capability")
        return _capability_detail(session, capability)

    @application.post("/api/v1/capabilities/{capability_id}/orders", tags=["capabilities"], status_code=201)
    def create_direct_order(
        capability_id: str,
        body: DirectOrderCreate,
        actor: Agent = Depends(require_role("requester")),
        idem: str | None = Depends(idempotency_header),
        session: Session = Depends(get_session),
    ) -> dict[str, Any]:
        payload = body.model_dump(by_alias=True)
        replay = get_idempotent_response(session, actor.id, f"capabilities.{capability_id}.orders", idem, payload)
        if replay:
            return replay
        capability = session.get(Capability, capability_id)
        if capability is None:
            raise _not_found("Capability")
        order = _direct_order(session, actor, capability, body, application.state.settlement_adapter)
        response = _order_detail(session, order)
        save_idempotent_response(session, actor.id, f"capabilities.{capability_id}.orders", idem, payload, response)
        session.commit()
        return response

    @application.post("/api/v1/capabilities/{capability_id}/invoke", tags=["capabilities"])
    def invoke_capability(
        capability_id: str,
        body: InvokeRequest,
        actor: Agent = Depends(get_current_agent),
        idem: str | None = Depends(idempotency_header),
        session: Session = Depends(get_session),
    ) -> dict[str, Any]:
        payload = body.model_dump(by_alias=True)
        replay = get_idempotent_response(session, actor.id, f"capabilities.{capability_id}.invoke", idem, payload)
        if replay:
            return replay
        capability = session.get(Capability, capability_id)
        if capability is None:
            raise _not_found("Capability")
        validate_json_schema(capability.input_schema, body.input_payload)
        output = execute_demo_capability(capability, body.input_payload)
        response = {
            "invocationId": str(uuid.uuid4()),
            "capabilityId": capability.id,
            "status": "completed",
            "output": output,
            "inputHash": canonical_hash(body.input_payload),
        }
        audit(session, actor.id, "capability.invoked", "capability", capability.id, {"inputHash": response["inputHash"]})
        save_idempotent_response(session, actor.id, f"capabilities.{capability_id}.invoke", idem, payload, response)
        session.commit()
        return response

    @application.get("/api/v1/demands", tags=["demands"])
    def list_demands(
        demand_status: str | None = Query(default=None, alias="status"),
        requester_id: str | None = None,
        search: str | None = None,
        limit: int = Query(default=50, ge=1, le=100),
        offset: int = Query(default=0, ge=0),
        session: Session = Depends(get_session),
    ) -> dict[str, Any]:
        statement = select(Demand)
        if demand_status:
            statement = statement.where(Demand.status == demand_status)
        if requester_id:
            statement = statement.where(Demand.requester_id == requester_id)
        if search:
            pattern = f"%{search}%"
            statement = statement.where(or_(Demand.title.ilike(pattern), Demand.description.ilike(pattern)))
        total = session.scalar(select(func.count()).select_from(statement.subquery())) or 0
        items = list(session.scalars(statement.order_by(Demand.created_at.desc()).offset(offset).limit(limit)))
        return {"items": [_demand_detail(session, item, include_bids=False) for item in items], "total": total}

    @application.post("/api/v1/demands", tags=["demands"], status_code=201)
    def create_demand(
        body: DemandCreate,
        actor: Agent = Depends(require_role("requester")),
        idem: str | None = Depends(idempotency_header),
        session: Session = Depends(get_session),
    ) -> dict[str, Any]:
        payload = body.model_dump(by_alias=True, mode="json")
        replay = get_idempotent_response(session, actor.id, "demands.create", idem, payload)
        if replay:
            return replay
        if body.deadline is not None and _aware(body.deadline) <= utcnow():
            raise HTTPException(status_code=422, detail="Demand deadline must be in the future")
        demand = Demand(requester_id=actor.id, budget=Decimal(body.budget), **body.model_dump(exclude={"budget"}))
        session.add(demand)
        session.flush()
        response = serialize(demand)
        audit(session, actor.id, "demand.created", "demand", demand.id)
        save_idempotent_response(session, actor.id, "demands.create", idem, payload, response)
        session.commit()
        return response

    @application.get("/api/v1/demands/{demand_id}", tags=["demands"])
    def get_demand(demand_id: str, session: Session = Depends(get_session)) -> dict[str, Any]:
        demand = session.get(Demand, demand_id)
        if demand is None:
            raise _not_found("Demand")
        return _demand_detail(session, demand)

    @application.post("/api/v1/demands/{demand_id}/bids", tags=["bids"], status_code=201)
    def create_bid(
        demand_id: str,
        body: BidCreate,
        actor: Agent = Depends(require_role("provider")),
        idem: str | None = Depends(idempotency_header),
        session: Session = Depends(get_session),
    ) -> dict[str, Any]:
        payload = body.model_dump(by_alias=True)
        replay = get_idempotent_response(session, actor.id, f"demands.{demand_id}.bids", idem, payload)
        if replay:
            return replay
        demand = session.get(Demand, demand_id)
        if demand is None:
            raise _not_found("Demand")
        if demand.status != "published":
            raise HTTPException(status_code=409, detail="Bids are only accepted for published demands")
        if demand.requester_id == actor.id:
            raise HTTPException(status_code=409, detail="A requester cannot bid on its own demand")
        if body.currency != demand.currency:
            raise HTTPException(status_code=422, detail="Bid currency must match demand currency")
        if body.capability_id:
            capability = session.get(Capability, body.capability_id)
            if capability is None or capability.provider_id != actor.id:
                raise HTTPException(status_code=422, detail="Bid capability must belong to the provider")
        bid = Bid(
            demand_id=demand.id,
            provider_id=actor.id,
            amount=Decimal(body.amount),
            **body.model_dump(exclude={"amount"}),
        )
        session.add(bid)
        session.flush()
        response = serialize(bid)
        audit(session, actor.id, "bid.created", "bid", bid.id)
        notify(session, demand.requester_id, "bid.created", "需求收到新竞标", demand.title, "demand", demand.id)
        save_idempotent_response(session, actor.id, f"demands.{demand_id}.bids", idem, payload, response)
        session.commit()
        return response

    @application.post("/api/v1/bids/{bid_id}/accept", tags=["bids"])
    def accept_bid(
        bid_id: str,
        actor: Agent = Depends(require_role("requester")),
        idem: str | None = Depends(idempotency_header),
        session: Session = Depends(get_session),
    ) -> dict[str, Any]:
        payload: dict[str, Any] = {}
        replay = get_idempotent_response(session, actor.id, f"bids.{bid_id}.accept", idem, payload)
        if replay:
            return replay
        bid = session.get(Bid, bid_id)
        if bid is None:
            raise _not_found("Bid")
        demand = session.get(Demand, bid.demand_id)
        if demand is None:
            raise _not_found("Demand")
        if demand.requester_id != actor.id:
            raise HTTPException(status_code=403, detail="Only the demand requester can accept a bid")
        if demand.status != "published" or bid.status != "pending":
            raise HTTPException(status_code=409, detail="Demand or bid is no longer open")
        competing = list(session.scalars(select(Bid).where(Bid.demand_id == demand.id, Bid.status == "pending")))
        for candidate in competing:
            candidate.status = "accepted" if candidate.id == bid.id else "rejected"
            if candidate.id != bid.id:
                notify(session, candidate.provider_id, "bid.rejected", "竞标未中选", demand.title, "bid", candidate.id)
        demand.status = "awarded"
        demand.awarded_bid_id = bid.id
        terms = {"demand": serialize(demand), "bid": serialize(bid), "sourceType": "bid"}
        order = Order(
            requester_id=actor.id,
            provider_id=bid.provider_id,
            demand_id=demand.id,
            bid_id=bid.id,
            capability_id=bid.capability_id,
            source_type="bid",
            status="awaiting_funding",
            title=demand.title,
            terms_snapshot=terms,
            input_payload=demand.input_payload,
            amount=bid.amount,
            currency=bid.currency,
            terms_hash=canonical_hash(terms),
        )
        session.add(order)
        session.flush()
        application.state.settlement_adapter.create_intent(session, order)
        add_order_event(session, order, actor, "order.created_from_bid", None, "awaiting_funding", {"bidId": bid.id})
        notify(session, bid.provider_id, "bid.accepted", "竞标已中选", demand.title, "order", order.id)
        response = _order_detail(session, order)
        save_idempotent_response(session, actor.id, f"bids.{bid_id}.accept", idem, payload, response)
        session.commit()
        return response

    @application.post("/api/v1/bids/{bid_id}/withdraw", tags=["bids"])
    def withdraw_bid(
        bid_id: str,
        actor: Agent = Depends(require_role("provider")),
        idem: str | None = Depends(idempotency_header),
        session: Session = Depends(get_session),
    ) -> dict[str, Any]:
        payload: dict[str, Any] = {}
        replay = get_idempotent_response(session, actor.id, f"bids.{bid_id}.withdraw", idem, payload)
        if replay:
            return replay
        bid = session.get(Bid, bid_id)
        if bid is None:
            raise _not_found("Bid")
        if bid.provider_id != actor.id:
            raise HTTPException(status_code=403, detail="Only the bid provider can withdraw it")
        if bid.status != "pending":
            raise HTTPException(status_code=409, detail="Only pending bids may be withdrawn")
        bid.status = "withdrawn"
        response = serialize(bid)
        audit(session, actor.id, "bid.withdrawn", "bid", bid.id)
        save_idempotent_response(session, actor.id, f"bids.{bid_id}.withdraw", idem, payload, response)
        session.commit()
        return response

    @application.get("/api/v1/orders", tags=["orders"])
    def list_orders(
        order_status: str | None = Query(default=None, alias="status"),
        actor: Agent = Depends(get_current_agent),
        session: Session = Depends(get_session),
    ) -> dict[str, Any]:
        statement = select(Order)
        if "admin" not in actor.roles:
            statement = statement.where(or_(Order.requester_id == actor.id, Order.provider_id == actor.id))
        if order_status:
            statement = statement.where(Order.status == order_status)
        items = list(session.scalars(statement.order_by(Order.created_at.desc())))
        return {"items": [_order_detail(session, item) for item in items], "total": len(items)}

    @application.get("/api/v1/orders/{order_id}", tags=["orders"])
    def get_order(
        order_id: str,
        actor: Agent = Depends(get_current_agent),
        session: Session = Depends(get_session),
    ) -> dict[str, Any]:
        order = session.get(Order, order_id)
        if order is None:
            raise _not_found("Order")
        require_order_party(order, actor)
        return _order_detail(session, order)

    @application.post("/api/v1/orders/{order_id}/confirm", tags=["orders"])
    def confirm_order_terms(
        order_id: str,
        body: ProviderOrderConfirmation,
        actor: Agent = Depends(require_role("provider")),
        idem: str | None = Depends(idempotency_header),
        session: Session = Depends(get_session),
    ) -> dict[str, Any]:
        payload = body.model_dump(by_alias=True)
        replay = get_idempotent_response(session, actor.id, f"orders.{order_id}.confirm", idem, payload)
        if replay:
            return replay
        order = session.get(Order, order_id)
        if order is None:
            raise _not_found("Order")
        if order.provider_id != actor.id:
            raise HTTPException(status_code=403, detail="Only the selected provider can confirm the order")
        transition_order(
            session,
            order,
            actor,
            {"awaiting_confirmation"},
            "awaiting_funding",
            "order.terms_confirmed",
            {"termsHash": order.terms_hash, "providerDid": actor.did, "note": body.note},
        )
        notify(session, order.requester_id, "order.terms_confirmed", "Provider confirmed the task terms", order.title, "order", order.id)
        response = _order_detail(session, order)
        save_idempotent_response(session, actor.id, f"orders.{order_id}.confirm", idem, payload, response)
        session.commit()
        return response

    @application.post("/api/v1/orders/{order_id}/reject", tags=["orders"])
    def reject_order_terms(
        order_id: str,
        body: ProviderOrderConfirmation,
        actor: Agent = Depends(require_role("provider")),
        idem: str | None = Depends(idempotency_header),
        session: Session = Depends(get_session),
    ) -> dict[str, Any]:
        payload = body.model_dump(by_alias=True)
        replay = get_idempotent_response(session, actor.id, f"orders.{order_id}.reject", idem, payload)
        if replay:
            return replay
        order = session.get(Order, order_id)
        if order is None:
            raise _not_found("Order")
        if order.provider_id != actor.id:
            raise HTTPException(status_code=403, detail="Only the selected provider can reject the order")
        transition_order(
            session,
            order,
            actor,
            {"awaiting_confirmation"},
            "cancelled",
            "order.terms_rejected",
            {"termsHash": order.terms_hash, "providerDid": actor.did, "note": body.note},
        )
        settlement = session.get(Settlement, order.settlement_id)
        if settlement is not None and settlement.status == "created":
            settlement.status = "cancelled"
        notify(session, order.requester_id, "order.terms_rejected", "Provider rejected the task terms", order.title, "order", order.id)
        response = _order_detail(session, order)
        save_idempotent_response(session, actor.id, f"orders.{order_id}.reject", idem, payload, response)
        session.commit()
        return response

    @application.post(
        "/api/v1/orders/{order_id}/settlement-quote",
        tags=["orders"],
        response_model=SettlementQuoteResponse,
    )
    def quote_order_settlement(
        order_id: str,
        body: SettlementQuoteRequest,
        actor: Agent = Depends(require_role("requester")),
        session: Session = Depends(get_session),
    ) -> dict[str, Any]:
        order = session.get(Order, order_id)
        if order is None:
            raise _not_found("Order")
        if order.requester_id != actor.id:
            raise HTTPException(status_code=403, detail="Only the requester can quote settlement funding")
        if order.status != "awaiting_funding":
            raise HTTPException(status_code=409, detail="Only awaiting_funding orders can be quoted")
        try:
            quote = application.state.settlement_adapter.quote_settlement(session, order, body.model_dump())
        except ValueError as exc:
            raise HTTPException(status_code=409, detail=str(exc)) from exc
        session.commit()
        return quote

    @application.post("/api/v1/orders/{order_id}/dex-quotes", tags=["orders"], status_code=201)
    def create_dex_quote(
        order_id: str,
        body: DexQuoteCreate,
        actor: Agent = Depends(get_current_agent),
        session: Session = Depends(get_session),
    ) -> dict[str, Any]:
        order = session.get(Order, order_id)
        if order is None:
            raise _not_found("Order")
        settlement = session.get(Settlement, order.settlement_id) if order.settlement_id else None
        if settlement is None:
            raise _not_found("Settlement")
        wallet = body.wallet_address.lower()
        if body.side == "funding":
            if actor.id != order.requester_id:
                raise HTTPException(status_code=403, detail="Only the requester can quote funding")
            if order.status != "awaiting_funding":
                raise HTTPException(status_code=409, detail="Funding quotes require an awaiting_funding order")
            if settlement.payer_account and settlement.payer_account.lower() != wallet:
                raise HTTPException(status_code=422, detail="Wallet does not match the settlement payer")
            expected_source_chain = f"eip155:{settings.source_chain_id}"
            if settlement.source_chain and settlement.source_chain != expected_source_chain:
                raise HTTPException(status_code=422, detail="Order source chain does not match DEX configuration")
            if not settings.source_usdc_address:
                raise HTTPException(status_code=503, detail="Source USDC address is not configured")
            chain_id = settings.source_chain_id
            sell_token = NATIVE_ETH
            buy_token = settings.source_usdc_address.lower()
            sell_amount = None
            buy_amount = to_base_units(Decimal(str(settlement.amount)), 6)
        else:
            if actor.id != order.provider_id:
                raise HTTPException(status_code=403, detail="Only the provider can quote payout conversion")
            if order.status != "completed" or settlement.status != "released":
                raise HTTPException(status_code=409, detail="Payout conversion requires a completed settlement")
            if settlement.payee_account and settlement.payee_account.lower() != wallet:
                raise HTTPException(status_code=422, detail="Wallet does not match the settlement payee")
            expected_destination_chain = f"eip155:{settings.destination_chain_id}"
            if settlement.destination_chain and settlement.destination_chain != expected_destination_chain:
                raise HTTPException(status_code=422, detail="Order destination chain does not match DEX configuration")
            decision = session.scalar(select(PaymentDecision).where(PaymentDecision.order_id == order.id))
            if decision is None:
                raise _not_found("Payment decision")
            requested = Decimal(body.sell_amount) if body.sell_amount is not None else Decimal(str(decision.payable_amount))
            if requested <= 0 or requested > Decimal(str(decision.payable_amount)):
                raise HTTPException(status_code=422, detail="Payout sellAmount exceeds the paid USDC amount")
            chain_id = settings.destination_chain_id
            sell_token = settings.destination_usdc_address.lower()
            buy_token = settings.destination_stc_address.lower()
            sell_amount = to_base_units(requested, 6)
            buy_amount = None
        if not buy_token or (body.side == "payout" and not sell_token):
            raise HTTPException(status_code=503, detail="DEX token addresses are not configured")
        try:
            payload = application.state.dex_client.quote(
                chain_id=chain_id,
                taker=wallet,
                sell_token=sell_token,
                buy_token=buy_token,
                slippage_bps=body.max_slippage_bps,
                sell_amount=sell_amount,
                buy_amount=buy_amount,
            )
        except DexQuoteError as exc:
            message = str(exc)
            status_code = 409 if message.startswith("no_liquidity") else 502
            raise HTTPException(status_code=status_code, detail=message) from exc
        if body.side == "funding" and int(payload["minBuyAmount"]) < int(buy_amount or "0"):
            raise HTTPException(status_code=409, detail="Quote minimum output is below the USDC budget")
        expires_at = payload["expiresAt"]
        max_sell_amount = str(payload["sellAmount"])
        execution_material = {
            "chainId": payload["chainId"],
            "taker": payload["taker"],
            "recipient": payload["recipient"],
            "sellToken": payload["sellToken"],
            "buyToken": payload["buyToken"],
            "sellAmount": payload["sellAmount"],
            "minBuyAmount": payload["minBuyAmount"],
            "transaction": payload["transaction"],
            "expiresAt": serialize(expires_at),
        }
        execution_hash = canonical_hash(execution_material)
        quote_material = {
            key: payload[key]
            for key in (
                "provider",
                "chainId",
                "taker",
                "sellToken",
                "buyToken",
                "sellAmount",
                "buyAmount",
                "minBuyAmount",
            )
        }
        quote_material["transactionHash"] = canonical_hash(payload["transaction"])
        quote_material["executionHash"] = execution_hash
        quote_material["maxSellAmount"] = max_sell_amount
        quote_material["expiresAt"] = serialize(expires_at)
        quote_hash = canonical_hash(quote_material)
        quote = DexQuote(
            order_id=order.id,
            side=body.side,
            chain_id=chain_id,
            taker=wallet,
            sell_token=sell_token,
            buy_token=buy_token,
            sell_amount=payload["sellAmount"],
            max_sell_amount=max_sell_amount,
            buy_amount=payload["buyAmount"],
            min_buy_amount=payload["minBuyAmount"],
            max_slippage_bps=body.max_slippage_bps,
            allowance_target=payload["allowanceTarget"],
            transaction=payload["transaction"],
            quote_hash=quote_hash,
            execution_hash=execution_hash,
            expires_at=expires_at,
        )
        session.add(quote)
        session.flush()
        response = serialize(quote)
        response["quoteId"] = quote.id
        audit(session, actor.id, "dex.quote_created", "dex_quote", quote.id, quote_material)
        session.commit()
        return response

    @application.post(
        "/api/v1/orders/{order_id}/dex-quotes/{quote_id}/executions",
        tags=["orders"],
    )
    def confirm_dex_quote_execution(
        order_id: str,
        quote_id: str,
        body: DexQuoteExecution,
        actor: Agent = Depends(get_current_agent),
        session: Session = Depends(get_session),
    ) -> dict[str, Any]:
        order = session.get(Order, order_id)
        quote = session.get(DexQuote, quote_id)
        if order is None or quote is None or quote.order_id != order.id:
            raise _not_found("DEX quote")
        if quote.side != "payout" or actor.id != order.provider_id:
            raise HTTPException(status_code=403, detail="Only the provider can confirm payout conversion")
        if quote.status != "quoted" or quote.consumed_at is not None:
            raise HTTPException(status_code=409, detail="DEX quote was already consumed")
        if _aware(quote.expires_at) <= utcnow():
            quote.status = "expired"
            session.commit()
            raise HTTPException(status_code=409, detail="DEX quote has expired")
        try:
            application.state.destination_swap_verifier.verify_swap(quote, body.transaction_hash.lower())
        except SwapVerificationError as exc:
            raise HTTPException(status_code=422, detail=str(exc)) from exc
        quote.status = "consumed"
        quote.consumed_at = utcnow()
        quote.swap_transaction_hash = body.transaction_hash.lower()
        audit(
            session,
            actor.id,
            "dex.payout_executed",
            "dex_quote",
            quote.id,
            {"transactionHash": body.transaction_hash.lower()},
        )
        session.commit()
        return serialize(quote)

    @application.post("/api/v1/orders/{order_id}/fund", tags=["orders"])
    def fund_order(
        order_id: str,
        body: FundRequest,
        actor: Agent = Depends(require_role("requester")),
        idem: str | None = Depends(idempotency_header),
        session: Session = Depends(get_session),
    ) -> dict[str, Any]:
        payload = body.model_dump(by_alias=True, mode="json")
        replay = get_idempotent_response(session, actor.id, f"orders.{order_id}.fund", idem, payload)
        if replay:
            return replay
        order = session.get(Order, order_id)
        if order is None:
            raise _not_found("Order")
        if order.requester_id != actor.id:
            raise HTTPException(status_code=403, detail="Only the requester can fund an order")
        if order.status != "awaiting_funding":
            raise HTTPException(status_code=409, detail="Only awaiting_funding orders can be funded")
        settlement = session.get(Settlement, order.settlement_id)
        if settlement is None:
            raise _not_found("Settlement")
        funding_details = body.model_dump()
        if settings.settlement_mode == "external_ccip":
            if body.quote_id is None or body.swap_transaction_hash is None:
                raise HTTPException(
                    status_code=422,
                    detail="External funding requires quoteId and swapTransactionHash",
                )
            quote = session.get(DexQuote, body.quote_id)
            if quote is None or quote.order_id != order.id or quote.side != "funding":
                raise HTTPException(status_code=422, detail="Funding quote does not belong to this order")
            if quote.status != "quoted" or quote.consumed_at is not None:
                raise HTTPException(status_code=409, detail="Funding quote was already consumed")
            if _aware(quote.expires_at) <= utcnow():
                quote.status = "expired"
                session.commit()
                raise HTTPException(status_code=409, detail="Funding quote has expired")
            payer = (body.payer_account or quote.taker).lower()
            if payer != quote.taker.lower():
                raise HTTPException(status_code=422, detail="Funding wallet does not match quote taker")
            expected_source_chain = f"eip155:{settings.source_chain_id}"
            expected_destination_chain = f"eip155:{settings.destination_chain_id}"
            if body.source_chain and body.source_chain != expected_source_chain:
                raise HTTPException(status_code=422, detail="Funding source chain does not match configuration")
            if body.destination_chain and body.destination_chain != expected_destination_chain:
                raise HTTPException(status_code=422, detail="Funding destination chain does not match configuration")
            if body.asset_id and body.asset_id.lower() != settings.source_usdc_address.lower():
                raise HTTPException(status_code=422, detail="Funding asset is not the configured source USDC")
            if quote.chain_id != settings.source_chain_id or quote.buy_token.lower() != settings.source_usdc_address.lower():
                raise HTTPException(status_code=422, detail="Funding quote route does not match configuration")
            reused_swap = session.scalar(
                select(DexQuote).where(
                    DexQuote.swap_transaction_hash == body.swap_transaction_hash.lower(),
                    DexQuote.id != quote.id,
                )
            )
            if reused_swap is not None:
                raise HTTPException(status_code=409, detail="Funding swap was already used")
            try:
                application.state.source_swap_verifier.verify_swap(quote, body.swap_transaction_hash.lower())
            except SwapVerificationError as exc:
                raise HTTPException(status_code=422, detail=str(exc)) from exc

            base_terms_hash = order.terms_hash
            onchain_order_id = opaque_id_to_bytes32(order.id)
            terms_hash = quote_terms_hash(onchain_order_id, base_terms_hash, quote.quote_hash)
            budget_text = serialize(Decimal(str(settlement.amount)))
            settlement_quote = {
                "provider": quote.provider,
                "quoteId": quote.id,
                "quoteHash": quote.quote_hash,
                "executionHash": quote.execution_hash,
                "baseTermsHash": base_terms_hash,
                "onchainOrderId": onchain_order_id,
                "termsHash": terms_hash,
                "quotedSettlementAmount": budget_text,
                "minSettlementAmount": budget_text,
                "budgetAmountBaseUnits": to_base_units(Decimal(str(settlement.amount)), 6),
                "minBuyAmount": quote.min_buy_amount,
                "maxSellAmount": quote.max_sell_amount,
                "maxSlippageBps": quote.max_slippage_bps,
                "quoteDeadline": serialize(quote.expires_at),
                "swapTransactionHash": body.swap_transaction_hash.lower(),
            }
            order.terms_snapshot = {**order.terms_snapshot, "settlementQuote": settlement_quote}
            order.terms_hash = terms_hash
            settlement.payer_account = payer
            settlement.payee_account = body.payee_account or settlement.payee_account
            settlement.asset_id = settings.source_usdc_address.lower()
            settlement.source_chain = expected_source_chain
            settlement.destination_chain = expected_destination_chain
            quote.status = "consumed"
            quote.consumed_at = utcnow()
            quote.swap_transaction_hash = body.swap_transaction_hash.lower()
            funding_details.update(
                {
                    "quote_hash": quote.quote_hash,
                    "quote_deadline": quote.expires_at,
                    "quoted_settlement_amount": budget_text,
                    "min_settlement_amount": budget_text,
                    "max_slippage_bps": quote.max_slippage_bps,
                }
            )
        else:
            settlement_fields = {"payer_account", "payee_account", "asset_id", "source_chain", "destination_chain"}
            for key, value in body.model_dump().items():
                if value is not None and key in settlement_fields:
                    setattr(settlement, key, value)
        try:
            application.state.settlement_adapter.lock_funds(session, settlement, order, funding_details)
        except ValueError as exc:
            raise HTTPException(status_code=409, detail=str(exc)) from exc
        if settlement.status == "locked":
            transition_order(session, order, actor, {"awaiting_funding"}, "funded", "order.funded")
        else:
            add_order_event(
                session,
                order,
                actor,
                "order.funding_submitted",
                order.status,
                order.status,
                {"onchainSettlementId": opaque_id_to_bytes32(settlement.id)},
            )
        notify(session, order.provider_id, "order.funded", "订单资金已锁定", order.title, "order", order.id)
        response = _order_detail(session, order)
        save_idempotent_response(session, actor.id, f"orders.{order_id}.fund", idem, payload, response)
        session.commit()
        return response

    @application.post("/api/v1/orders/{order_id}/start", tags=["orders"])
    def start_order(
        order_id: str,
        actor: Agent = Depends(require_role("provider")),
        idem: str | None = Depends(idempotency_header),
        session: Session = Depends(get_session),
    ) -> dict[str, Any]:
        replay = get_idempotent_response(session, actor.id, f"orders.{order_id}.start", idem, {})
        if replay:
            return replay
        order = session.get(Order, order_id)
        if order is None:
            raise _not_found("Order")
        if order.provider_id != actor.id:
            raise HTTPException(status_code=403, detail="Only the provider can start an order")
        transition_order(session, order, actor, {"funded"}, "in_progress", "order.started")
        notify(session, order.requester_id, "order.started", "服务方已开始执行", order.title, "order", order.id)
        response = _order_detail(session, order)
        save_idempotent_response(session, actor.id, f"orders.{order_id}.start", idem, {}, response)
        session.commit()
        return response

    @application.post("/api/v1/orders/{order_id}/messages", tags=["orders"], status_code=201)
    def create_message(
        order_id: str,
        body: MessageCreate,
        actor: Agent = Depends(get_current_agent),
        idem: str | None = Depends(idempotency_header),
        session: Session = Depends(get_session),
    ) -> dict[str, Any]:
        payload = body.model_dump(by_alias=True)
        replay = get_idempotent_response(session, actor.id, f"orders.{order_id}.messages", idem, payload)
        if replay:
            return replay
        order = session.get(Order, order_id)
        if order is None:
            raise _not_found("Order")
        require_order_party(order, actor)
        message = Message(
            order_id=order.id,
            sender_id=actor.id,
            content=body.content,
            content_hash=canonical_hash({"content": body.content}),
        )
        session.add(message)
        session.flush()
        recipient = order.provider_id if actor.id == order.requester_id else order.requester_id
        notify(session, recipient, "order.message", "订单收到新消息", body.content[:120], "order", order.id)
        response = serialize(message)
        audit(session, actor.id, "order.message_created", "order", order.id, {"messageId": message.id})
        save_idempotent_response(session, actor.id, f"orders.{order_id}.messages", idem, payload, response)
        session.commit()
        return response

    @application.post("/api/v1/orders/{order_id}/deliver", tags=["orders"])
    def deliver_order(
        order_id: str,
        body: DeliveryCreate,
        actor: Agent = Depends(require_role("provider")),
        idem: str | None = Depends(idempotency_header),
        session: Session = Depends(get_session),
    ) -> dict[str, Any]:
        payload = body.model_dump(by_alias=True)
        replay = get_idempotent_response(session, actor.id, f"orders.{order_id}.deliver", idem, payload)
        if replay:
            return replay
        order = session.get(Order, order_id)
        if order is None:
            raise _not_found("Order")
        if order.provider_id != actor.id:
            raise HTTPException(status_code=403, detail="Only the provider can deliver an order")
        if session.scalar(select(Delivery).where(Delivery.order_id == order.id)) is not None:
            raise HTTPException(status_code=409, detail="Order already has a delivery")
        transition_order(session, order, actor, {"in_progress"}, "delivered", "order.delivered")
        delivery = Delivery(order_id=order.id, provider_id=actor.id, **body.model_dump())
        session.add(delivery)
        session.flush()
        notify(session, order.requester_id, "order.delivered", "服务方已提交交付物", order.title, "order", order.id)
        response = _order_detail(session, order)
        save_idempotent_response(session, actor.id, f"orders.{order_id}.deliver", idem, payload, response)
        session.commit()
        return response

    @application.post("/api/v1/orders/{order_id}/accept", tags=["orders"])
    def accept_delivery(
        order_id: str,
        body: AcceptDelivery | None = None,
        actor: Agent = Depends(require_role("requester")),
        idem: str | None = Depends(idempotency_header),
        session: Session = Depends(get_session),
    ) -> dict[str, Any]:
        body = body or AcceptDelivery()
        payload = body.model_dump(by_alias=True)
        replay = get_idempotent_response(session, actor.id, f"orders.{order_id}.accept", idem, payload)
        if replay:
            return replay
        order = session.get(Order, order_id)
        if order is None:
            raise _not_found("Order")
        if order.requester_id != actor.id:
            raise HTTPException(status_code=403, detail="Only the requester can accept a delivery")
        transition_order(session, order, actor, {"delivered"}, "accepted", "order.accepted")
        settlement = session.get(Settlement, order.settlement_id)
        if settlement is None:
            raise _not_found("Settlement")
        payable_amount = Decimal(body.payable_amount) if body.payable_amount is not None else Decimal(str(settlement.amount))
        decision = _create_payment_decision(
            session,
            order,
            settlement,
            actor,
            "requester_acceptance",
            payable_amount,
            body.verification_summary,
            body.verification_score,
        )
        try:
            application.state.settlement_adapter.release_funds(session, settlement, payable_amount)
        except ValueError as exc:
            raise HTTPException(status_code=409, detail=str(exc)) from exc
        if settlement.status == "release_pending":
            _ensure_relayer_task(session, settlement, "release", payable_amount=payable_amount)
        audit(
            session,
            actor.id,
            "settlement.payment_decided",
            "payment_decision",
            decision.id,
            {"payableAmount": serialize(decision.payable_amount), "refundAmount": serialize(decision.refund_amount)},
        )
        if settlement.status != "released":
            notify(
                session,
                order.provider_id,
                "order.release_pending",
                "Order accepted; cross-chain payment is pending",
                order.title,
                "order",
                order.id,
            )
            response = _order_detail(session, order)
            save_idempotent_response(session, actor.id, f"orders.{order_id}.accept", idem, payload, response)
            session.commit()
            return response
        transition_order(session, order, actor, {"accepted"}, "completed", "order.completed")
        provider = session.get(Agent, order.provider_id)
        if provider is not None:
            provider.completed_orders += 1
        notify(session, order.provider_id, "order.completed", "订单已验收并放款", order.title, "order", order.id)
        response = _order_detail(session, order)
        save_idempotent_response(session, actor.id, f"orders.{order_id}.accept", idem, payload, response)
        session.commit()
        return response

    @application.post("/api/v1/orders/{order_id}/dispute", tags=["orders"])
    def dispute_order(
        order_id: str,
        body: DisputeCreate,
        actor: Agent = Depends(get_current_agent),
        idem: str | None = Depends(idempotency_header),
        session: Session = Depends(get_session),
    ) -> dict[str, Any]:
        payload = body.model_dump(by_alias=True)
        replay = get_idempotent_response(session, actor.id, f"orders.{order_id}.dispute", idem, payload)
        if replay:
            return replay
        order = session.get(Order, order_id)
        if order is None:
            raise _not_found("Order")
        if actor.id not in {order.requester_id, order.provider_id}:
            raise HTTPException(status_code=403, detail="Only an order participant can dispute an order")
        transition_order(
            session,
            order,
            actor,
            {"funded", "in_progress", "delivered"},
            "disputed",
            "order.disputed",
            {"reason": body.reason},
        )
        admins = list(session.scalars(select(Agent).where(Agent.status == "active")))
        for admin in admins:
            if "admin" in admin.roles:
                notify(session, admin.id, "order.disputed", "订单需要仲裁", order.title, "order", order.id)
        response = _order_detail(session, order)
        save_idempotent_response(session, actor.id, f"orders.{order_id}.dispute", idem, payload, response)
        session.commit()
        return response

    @application.post("/api/v1/orders/{order_id}/refund", tags=["orders"])
    def refund_order(
        order_id: str,
        body: RefundCreate,
        actor: Agent = Depends(require_role("admin")),
        idem: str | None = Depends(idempotency_header),
        session: Session = Depends(get_session),
    ) -> dict[str, Any]:
        payload = body.model_dump(by_alias=True)
        replay = get_idempotent_response(session, actor.id, f"orders.{order_id}.refund", idem, payload)
        if replay:
            return replay
        order = session.get(Order, order_id)
        if order is None:
            raise _not_found("Order")
        transition_order(session, order, actor, {"disputed"}, "refund_pending", "order.refund_pending", {"reason": body.reason})
        settlement = session.get(Settlement, order.settlement_id)
        if settlement is None:
            raise _not_found("Settlement")
        try:
            application.state.settlement_adapter.refund(session, settlement)
        except ValueError as exc:
            raise HTTPException(status_code=409, detail=str(exc)) from exc
        if settlement.status != "refunded":
            response = _order_detail(session, order)
            save_idempotent_response(session, actor.id, f"orders.{order_id}.refund", idem, payload, response)
            session.commit()
            return response
        transition_order(session, order, actor, {"refund_pending"}, "refunded", "order.refunded")
        notify(session, order.requester_id, "order.refunded", "订单资金已退款", order.title, "order", order.id)
        notify(session, order.provider_id, "order.refunded", "争议订单已退款", order.title, "order", order.id)
        response = _order_detail(session, order)
        save_idempotent_response(session, actor.id, f"orders.{order_id}.refund", idem, payload, response)
        session.commit()
        return response

    @application.post("/api/v1/orders/{order_id}/resolve", tags=["orders"])
    def resolve_dispute(
        order_id: str,
        body: ArbitrationResolve,
        actor: Agent = Depends(require_role("admin")),
        idem: str | None = Depends(idempotency_header),
        session: Session = Depends(get_session),
    ) -> dict[str, Any]:
        payload = body.model_dump(by_alias=True)
        replay = get_idempotent_response(session, actor.id, f"orders.{order_id}.resolve", idem, payload)
        if replay:
            return replay
        order = session.get(Order, order_id)
        if order is None:
            raise _not_found("Order")
        if order.status != "disputed":
            raise HTTPException(status_code=409, detail="Only disputed orders can be arbitrated")
        settlement = session.get(Settlement, order.settlement_id)
        if settlement is None:
            raise _not_found("Settlement")

        if body.outcome == "refund":
            if body.payable_amount not in {None, "0"}:
                raise HTTPException(status_code=422, detail="Refund outcome cannot include a positive payable amount")
            _create_payment_decision(
                session, order, settlement, actor, "arbitration_refund", Decimal("0"), body.rationale
            )
            transition_order(
                session,
                order,
                actor,
                {"disputed"},
                "refund_pending",
                "order.arbitration_refund",
                {"rationale": body.rationale, "evidenceHashes": body.evidence_hashes},
            )
            try:
                application.state.settlement_adapter.refund(session, settlement)
            except ValueError as exc:
                raise HTTPException(status_code=409, detail=str(exc)) from exc
            if settlement.status == "refunded":
                transition_order(session, order, actor, {"refund_pending"}, "refunded", "order.refunded")
        else:
            if body.payable_amount is None:
                raise HTTPException(status_code=422, detail="Release outcome requires payableAmount")
            payable_amount = Decimal(body.payable_amount)
            _create_payment_decision(
                session, order, settlement, actor, "arbitration_release", payable_amount, body.rationale
            )
            transition_order(
                session,
                order,
                actor,
                {"disputed"},
                "accepted",
                "order.arbitration_release",
                {
                    "payableAmount": body.payable_amount,
                    "rationale": body.rationale,
                    "evidenceHashes": body.evidence_hashes,
                },
            )
            try:
                application.state.settlement_adapter.release_funds(session, settlement, payable_amount)
            except ValueError as exc:
                raise HTTPException(status_code=409, detail=str(exc)) from exc
            if settlement.status == "release_pending":
                _ensure_relayer_task(session, settlement, "release", payable_amount=payable_amount)
            if settlement.status == "released":
                transition_order(session, order, actor, {"accepted"}, "completed", "order.completed")
                provider = session.get(Agent, order.provider_id)
                if provider is not None:
                    provider.completed_orders += 1

        audit(session, actor.id, "order.dispute_resolved", "order", order.id, payload)
        response = _order_detail(session, order)
        save_idempotent_response(session, actor.id, f"orders.{order_id}.resolve", idem, payload, response)
        session.commit()
        return response

    @application.post("/api/v1/orders/{order_id}/reviews", tags=["orders"], status_code=201)
    def create_review(
        order_id: str,
        body: ReviewCreate,
        actor: Agent = Depends(require_role("requester")),
        idem: str | None = Depends(idempotency_header),
        session: Session = Depends(get_session),
    ) -> dict[str, Any]:
        payload = body.model_dump(by_alias=True)
        replay = get_idempotent_response(session, actor.id, f"orders.{order_id}.reviews", idem, payload)
        if replay:
            return replay
        order = session.get(Order, order_id)
        if order is None:
            raise _not_found("Order")
        if order.requester_id != actor.id:
            raise HTTPException(status_code=403, detail="Only the requester can review this order")
        if order.status != "completed":
            raise HTTPException(status_code=409, detail="Only completed orders can be reviewed")
        review = Review(
            order_id=order.id,
            reviewer_id=actor.id,
            reviewee_id=order.provider_id,
            rating=body.rating,
            comment=body.comment,
        )
        session.add(review)
        session.flush()
        ratings = list(session.scalars(select(Review.rating).where(Review.reviewee_id == order.provider_id)))
        provider = session.get(Agent, order.provider_id)
        if provider is not None and ratings:
            provider.reputation_score = Decimal(str(round(sum(ratings) / len(ratings), 2)))
        response = serialize(review)
        audit(session, actor.id, "review.created", "review", review.id, {"orderId": order.id})
        notify(session, order.provider_id, "review.created", "收到新的订单评价", order.title, "review", review.id)
        save_idempotent_response(session, actor.id, f"orders.{order_id}.reviews", idem, payload, response)
        session.commit()
        return response

    @application.get("/api/v1/settlements/{settlement_id}", tags=["integration"])
    def get_settlement(
        settlement_id: str,
        actor: Agent = Depends(get_current_agent),
        session: Session = Depends(get_session),
    ) -> dict[str, Any]:
        settlement = application.state.settlement_adapter.get_status(session, settlement_id)
        if settlement is None:
            raise _not_found("Settlement")
        order = session.get(Order, settlement.order_id)
        if order is None:
            raise _not_found("Order")
        require_order_party(order, actor)
        data = serialize(settlement)
        data["onchainSettlementId"] = opaque_id_to_bytes32(settlement.id)
        data["onchainOrderId"] = opaque_id_to_bytes32(order.id)
        data["paymentDecision"] = serialize(
            session.scalar(select(PaymentDecision).where(PaymentDecision.order_id == order.id))
        )
        return data

    @application.post("/api/v1/integration/relayer-tasks/lease", tags=["integration"])
    def lease_relayer_task(
        body: RelayerTaskLease,
        listener_id: str = Depends(require_listener_auth),
        session: Session = Depends(get_session),
    ) -> dict[str, Any]:
        if listener_id != body.worker_id:
            raise HTTPException(status_code=403, detail="Worker identity does not match lease request")
        now = utcnow()
        lease_seconds = min(body.lease_seconds, settings.relayer_lease_seconds)
        eligible = or_(
            RelayerTask.status == "queued",
            and_(RelayerTask.status == "leased", RelayerTask.lease_expires_at <= now),
        )
        task = None
        for _ in range(4):
            candidate_id = session.scalar(
                select(RelayerTask.id)
                .where(eligible)
                .order_by(RelayerTask.created_at, RelayerTask.id)
                .limit(1)
            )
            if candidate_id is None:
                return {"task": None}
            lease_token = str(uuid.uuid4())
            claimed_id = session.scalar(
                update(RelayerTask)
                .where(RelayerTask.id == candidate_id, eligible)
                .values(
                    status="leased",
                    lease_owner=body.worker_id,
                    lease_token=lease_token,
                    lease_expires_at=now + timedelta(seconds=lease_seconds),
                    attempts=RelayerTask.attempts + 1,
                    last_checked_at=now,
                )
                .returning(RelayerTask.id)
            )
            if claimed_id is not None:
                task = session.get(RelayerTask, claimed_id)
                break
            session.expire_all()
        if task is None:
            raise HTTPException(status_code=409, detail="Relayer lease contention; retry request")
        settlement = session.get(Settlement, task.settlement_id)
        if settlement is None:
            raise _not_found("Settlement")
        order = session.get(Order, settlement.order_id)
        if order is None:
            raise _not_found("Order")
        response = serialize(task)
        response.update(
            {
                "onchainSettlementId": opaque_id_to_bytes32(settlement.id),
                "onchainOrderId": opaque_id_to_bytes32(order.id),
                "sourceChain": settlement.source_chain,
                "destinationChain": settlement.destination_chain,
            }
        )
        session.commit()
        return {"task": response}

    @application.post("/api/v1/integration/relayer-tasks/{task_id}/heartbeat", tags=["integration"])
    def heartbeat_relayer_task(
        task_id: str,
        body: RelayerTaskHeartbeat,
        listener_id: str = Depends(require_listener_auth),
        session: Session = Depends(get_session),
    ) -> dict[str, Any]:
        if listener_id != body.worker_id:
            raise HTTPException(status_code=403, detail="Worker identity does not match heartbeat")
        task = session.get(RelayerTask, task_id)
        if (
            task is None
            or task.status != "leased"
            or task.lease_owner != body.worker_id
            or task.lease_token != body.lease_token
        ):
            raise HTTPException(status_code=409, detail="Relayer task lease is no longer active")
        task.lease_expires_at = utcnow() + timedelta(
            seconds=min(body.lease_seconds, settings.relayer_lease_seconds)
        )
        task.last_checked_at = utcnow()
        session.commit()
        return serialize(task)

    @application.post("/api/v1/integration/relayer-tasks/{task_id}/result", tags=["integration"])
    def report_relayer_task_result(
        task_id: str,
        body: RelayerTaskResult,
        listener_id: str = Depends(require_listener_auth),
        session: Session = Depends(get_session),
    ) -> dict[str, Any]:
        task = session.get(RelayerTask, task_id)
        if task is None:
            raise _not_found("Relayer task")
        if listener_id != body.worker_id:
            raise HTTPException(status_code=403, detail="Worker identity does not match result")
        if (
            task.status != "leased"
            or task.lease_owner != body.worker_id
            or task.lease_token != body.lease_token
        ):
            raise HTTPException(status_code=409, detail="Relayer task is not leased by this worker")
        if body.outcome == "submitted" and body.transaction_hash is None:
            raise HTTPException(status_code=422, detail="Submitted relayer task requires transactionHash")
        task.transaction_hash = body.transaction_hash or task.transaction_hash
        task.transaction_nonce = body.transaction_nonce if body.transaction_nonce is not None else task.transaction_nonce
        if body.transaction_hash and task.first_broadcast_at is None:
            task.first_broadcast_at = utcnow()
        task.last_checked_at = utcnow()
        task.last_error = body.error
        task.lease_owner = None
        task.lease_token = None
        task.lease_expires_at = None
        task.status = "submitted" if body.outcome == "submitted" else body.outcome
        if body.outcome == "retry":
            task.status = "queued"
        actor = _integration_actor(session)
        audit(session, actor.id, "relayer.task_reported", "relayer_task", task.id, body.model_dump(by_alias=True))
        session.commit()
        return serialize(task)

    @application.post("/api/v1/integration/settlement-events", tags=["integration"])
    def reconcile_settlement_event(
        body: SettlementChainEventCreate,
        _authenticated: None = Depends(require_listener_auth),
        session: Session = Depends(get_session),
    ) -> dict[str, Any]:
        actor = _integration_actor(session)
        duplicate = session.scalar(
            select(SettlementChainEvent).where(SettlementChainEvent.event_id == body.event_id)
        )
        if duplicate is not None:
            same_event = (
                duplicate.event_type == body.event_type
                and opaque_id_to_bytes32(duplicate.settlement_id).lower() == body.onchain_settlement_id.lower()
                and duplicate.transaction_hash.lower() == body.transaction_hash.lower()
                and (duplicate.ccip_message_id or "").lower() == (body.ccip_message_id or "").lower()
                and (duplicate.token_address or "").lower() == (body.token_address or "").lower()
                and duplicate.amount == (Decimal(body.amount) if body.amount is not None else None)
                and duplicate.chain_selector == body.chain_selector
                and duplicate.block_number == body.block_number
                and (duplicate.block_hash or "").lower() == (body.block_hash or "").lower()
                and (duplicate.payload_hash or "").lower() == (body.payload_hash or "").lower()
            )
            if not same_event:
                raise HTTPException(status_code=409, detail="eventId was already used with different chain data")
            if body.confirmations > duplicate.confirmations:
                duplicate.confirmations = body.confirmations
                session.commit()
            return {"duplicate": True, "event": serialize(duplicate)}

        settlement = _settlement_by_onchain_id(session, body.onchain_settlement_id)
        if settlement is None:
            raise _not_found("Settlement")
        order = session.get(Order, settlement.order_id)
        if order is None:
            raise _not_found("Order")
        amount = Decimal(body.amount) if body.amount is not None else None
        decision = session.scalar(select(PaymentDecision).where(PaymentDecision.order_id == order.id))
        source_events = {"funds_locked", "release_submitted", "release_confirmed", "refund_confirmed"}
        expected_selector = (
            settings.source_chain_selector if body.event_type in source_events else settings.destination_chain_selector
        )
        if expected_selector and body.chain_selector != expected_selector:
            raise HTTPException(status_code=422, detail="Chain event selector does not match configuration")
        if settings.app_env == "production" and body.block_number is not None and body.block_hash is None:
            raise HTTPException(status_code=422, detail="Confirmed chain events require blockHash")
        if body.event_type == "funds_locked":
            if body.token_address is None or body.token_address.lower() != settings.source_usdc_address.lower():
                raise HTTPException(status_code=422, detail="Locked token is not the configured source USDC")
        if body.event_type == "settlement_received":
            if body.token_address is None or body.token_address.lower() != settings.destination_usdc_address.lower():
                raise HTTPException(status_code=422, detail="Received token is not the configured destination USDC")

        if body.event_type == "funds_locked":
            if settlement.status != "lock_pending":
                raise HTTPException(status_code=409, detail="Settlement is not waiting for a lock event")
            if amount != Decimal(str(settlement.amount)):
                raise HTTPException(status_code=422, detail="Locked amount does not match the settlement budget")
            settlement.status = "locked"
            settlement.transaction_hash = body.transaction_hash
            settlement.confirmation_count = body.confirmations
            transition_order(session, order, actor, {"awaiting_funding"}, "funded", "order.funded_onchain")
            notify(session, order.provider_id, "order.funded", "Order funds were verified on-chain", order.title, "order", order.id)
        elif body.event_type == "release_submitted":
            if settlement.status != "release_pending" or decision is None:
                raise HTTPException(status_code=409, detail="Settlement has no pending payment decision")
            if body.ccip_message_id is None:
                raise HTTPException(status_code=422, detail="release_submitted requires ccipMessageId")
            if amount != Decimal(str(decision.payable_amount)):
                raise HTTPException(status_code=422, detail="Release amount does not match the payment decision")
            settlement.transaction_hash = body.transaction_hash
            settlement.ccip_message_id = body.ccip_message_id
            settlement.confirmation_count = body.confirmations
            release_task = session.scalar(
                select(RelayerTask).where(
                    RelayerTask.settlement_id == settlement.id,
                    RelayerTask.task_type == "release",
                )
            )
            if release_task is not None:
                release_task.status = "completed"
                release_task.transaction_hash = body.transaction_hash
            audit(session, actor.id, "settlement.release_submitted", "settlement", settlement.id, body.model_dump(by_alias=True))
        elif body.event_type == "settlement_received":
            if settlement.status != "release_pending" or decision is None:
                raise HTTPException(status_code=409, detail="Settlement is not awaiting destination delivery")
            if body.ccip_message_id is None or settlement.ccip_message_id != body.ccip_message_id:
                raise HTTPException(status_code=422, detail="CCIP message ID does not match the submitted release")
            if amount != Decimal(str(decision.payable_amount)):
                raise HTTPException(status_code=422, detail="Received amount does not match the payment decision")
            settlement.confirmation_count = body.confirmations
            settlement.failure_reason = None
            _ensure_relayer_task(
                session,
                settlement,
                "confirm_release",
                payable_amount=decision.payable_amount,
                ccip_message_id=body.ccip_message_id,
            )
            notify(session, order.requester_id, "settlement.received", "Destination payment was confirmed", order.title, "order", order.id)
            notify(session, order.provider_id, "settlement.received", "Cross-chain USDC payment was received", order.title, "order", order.id)
        elif body.event_type == "release_confirmed":
            if settlement.status != "release_pending" or decision is None:
                raise HTTPException(status_code=409, detail="Settlement is not awaiting source confirmation")
            if body.ccip_message_id is None or settlement.ccip_message_id != body.ccip_message_id:
                raise HTTPException(status_code=422, detail="Confirmed CCIP message ID does not match the release")
            settlement.status = "released"
            settlement.confirmation_count = body.confirmations
            transition_order(session, order, actor, {"accepted"}, "completed", "order.release_confirmed")
            provider = session.get(Agent, order.provider_id)
            if provider is not None:
                provider.completed_orders += 1
            confirm_task = session.scalar(
                select(RelayerTask).where(
                    RelayerTask.settlement_id == settlement.id,
                    RelayerTask.task_type == "confirm_release",
                )
            )
            if confirm_task is not None:
                confirm_task.status = "completed"
                confirm_task.transaction_hash = body.transaction_hash
            notify(session, order.requester_id, "settlement.completed", "Source escrow release was confirmed", order.title, "order", order.id)
        elif body.event_type == "settlement_failed":
            if settlement.status != "release_pending":
                raise HTTPException(status_code=409, detail="Only a pending release can fail")
            settlement.status = "failed"
            settlement.failure_reason = body.failure_reason or "CCIP delivery failed"
            if order.status == "accepted":
                transition_order(session, order, actor, {"accepted"}, "settlement_failed", "order.settlement_failed")
        elif body.event_type == "refund_confirmed":
            if settlement.status != "refund_pending":
                raise HTTPException(status_code=409, detail="Settlement is not waiting for a refund event")
            if amount != Decimal(str(settlement.amount)):
                raise HTTPException(status_code=422, detail="Refund amount does not match the locked budget")
            settlement.status = "refunded"
            settlement.transaction_hash = body.transaction_hash
            settlement.confirmation_count = body.confirmations
            transition_order(session, order, actor, {"refund_pending"}, "refunded", "order.refunded_onchain")

        event = SettlementChainEvent(
            event_id=body.event_id,
            event_type=body.event_type,
            settlement_id=settlement.id,
            chain_selector=body.chain_selector,
            transaction_hash=body.transaction_hash,
            ccip_message_id=body.ccip_message_id,
            token_address=body.token_address,
            amount=amount,
            block_number=body.block_number,
            block_hash=body.block_hash,
            payload_hash=body.payload_hash,
            confirmations=body.confirmations,
        )
        session.add(event)
        session.flush()
        audit(session, actor.id, "settlement.chain_event_reconciled", "settlement", settlement.id, {"eventId": body.event_id, "eventType": body.event_type})
        response = {"duplicate": False, "event": serialize(event), "order": _order_detail(session, order)}
        session.commit()
        return response

    @application.post("/api/v1/integration/settlement-events/orphaned", tags=["integration"])
    def mark_orphaned_settlement_event(
        body: OrphanedChainEvent,
        _listener_id: str = Depends(require_listener_auth),
        session: Session = Depends(get_session),
    ) -> dict[str, Any]:
        event = session.scalar(select(SettlementChainEvent).where(SettlementChainEvent.event_id == body.event_id))
        if event is None:
            raise _not_found("Settlement chain event")
        if event.canonical_status == "orphaned":
            return {"duplicate": True, "event": serialize(event)}
        if event.block_hash and event.block_hash.lower() != body.observed_block_hash.lower():
            raise HTTPException(status_code=409, detail="Observed block hash does not match stored event")
        settlement = session.get(Settlement, event.settlement_id)
        if settlement is None:
            raise _not_found("Settlement")
        order = session.get(Order, settlement.order_id)
        event.canonical_status = "orphaned"
        event.orphaned_at = utcnow()
        settlement.status = "reconciliation_required"
        settlement.failure_reason = "A previously confirmed chain event was removed by a reorganization"
        if order is not None:
            order.status = "reconciliation_required"
        actor = _integration_actor(session)
        audit(
            session,
            actor.id,
            "settlement.chain_event_orphaned",
            "settlement",
            settlement.id,
            body.model_dump(by_alias=True),
        )
        session.commit()
        return {"duplicate": False, "event": serialize(event), "order": serialize(order)}

    @application.post("/api/v1/integration/stalled-settlements/check", tags=["integration"])
    def check_stalled_settlements(
        body: StalledSettlementCheck,
        _authenticated: None = Depends(require_listener_auth),
        session: Session = Depends(get_session),
    ) -> dict[str, Any]:
        actor = _integration_actor(session)
        cutoff = utcnow() - timedelta(seconds=body.threshold_seconds)
        stalled: list[str] = []
        for settlement in session.scalars(
            select(Settlement).where(
                Settlement.status == "release_pending",
                Settlement.ccip_message_id.is_not(None),
            )
        ):
            release_event = session.scalar(
                select(SettlementChainEvent)
                .where(
                    SettlementChainEvent.settlement_id == settlement.id,
                    SettlementChainEvent.event_type == "release_submitted",
                )
                .order_by(SettlementChainEvent.created_at.desc())
            )
            received_event = session.scalar(
                select(SettlementChainEvent).where(
                    SettlementChainEvent.settlement_id == settlement.id,
                    SettlementChainEvent.event_type == "settlement_received",
                )
            )
            already_reported = session.scalar(
                select(AuditLog).where(
                    AuditLog.action == "settlement.delivery_stalled",
                    AuditLog.resource_type == "settlement",
                    AuditLog.resource_id == settlement.id,
                )
            )
            if (
                release_event is None
                or _aware(release_event.created_at) > cutoff
                or received_event is not None
                or already_reported is not None
            ):
                continue
            settlement.failure_reason = "CCIP delivery is stalled and requires operator review"
            order = session.get(Order, settlement.order_id)
            if order is not None:
                notify(
                    session,
                    order.requester_id,
                    "settlement.stalled",
                    "Cross-chain delivery is delayed",
                    order.title,
                    "order",
                    order.id,
                )
                notify(
                    session,
                    actor.id,
                    "settlement.stalled",
                    "Cross-chain settlement requires review",
                    order.title,
                    "order",
                    order.id,
                )
            audit(
                session,
                actor.id,
                "settlement.delivery_stalled",
                "settlement",
                settlement.id,
                {"ccipMessageId": settlement.ccip_message_id, "thresholdSeconds": body.threshold_seconds},
            )
            stalled.append(settlement.id)
        session.commit()
        return {"stalledSettlementIds": stalled, "count": len(stalled)}

    @application.post("/api/v1/webhooks", tags=["integration"], status_code=201)
    def create_webhook(
        body: WebhookCreate,
        actor: Agent = Depends(get_current_agent),
        idem: str | None = Depends(idempotency_header),
        session: Session = Depends(get_session),
    ) -> dict[str, Any]:
        payload = body.model_dump(by_alias=True)
        replay = get_idempotent_response(session, actor.id, "webhooks.create", idem, payload)
        if replay:
            return replay
        webhook = WebhookSubscription(agent_id=actor.id, **body.model_dump())
        session.add(webhook)
        session.flush()
        response = serialize(webhook)
        audit(session, actor.id, "webhook.created", "webhook", webhook.id)
        save_idempotent_response(session, actor.id, "webhooks.create", idem, payload, response)
        session.commit()
        return response

    @application.get("/api/v1/notifications", tags=["integration"])
    def list_notifications(
        unread_only: bool = Query(default=False, alias="unreadOnly"),
        actor: Agent = Depends(get_current_agent),
        session: Session = Depends(get_session),
    ) -> dict[str, Any]:
        statement = select(Notification).where(Notification.agent_id == actor.id)
        if unread_only:
            statement = statement.where(Notification.read_at.is_(None))
        items = list(session.scalars(statement.order_by(Notification.created_at.desc())))
        return _list_response(items, len(items))

    @application.post("/api/v1/notifications/{notification_id}/read", tags=["integration"])
    def read_notification(
        notification_id: str,
        actor: Agent = Depends(get_current_agent),
        idem: str | None = Depends(idempotency_header),
        session: Session = Depends(get_session),
    ) -> dict[str, Any]:
        replay = get_idempotent_response(session, actor.id, f"notifications.{notification_id}.read", idem, {})
        if replay:
            return replay
        notification = session.get(Notification, notification_id)
        if notification is None:
            raise _not_found("Notification")
        if notification.agent_id != actor.id:
            raise HTTPException(status_code=403, detail="Notification belongs to another agent")
        notification.read_at = notification.read_at or utcnow()
        response = serialize(notification)
        save_idempotent_response(session, actor.id, f"notifications.{notification_id}.read", idem, {}, response)
        session.commit()
        return response

    @application.get("/api/v1/audit-logs", tags=["admin"])
    def list_audit_logs(
        actor: Agent = Depends(require_role("admin")),
        session: Session = Depends(get_session),
    ) -> dict[str, Any]:
        items = list(session.scalars(select(AuditLog).order_by(AuditLog.created_at.desc()).limit(200)))
        return _list_response(items, len(items))

    return application


app = create_app()
