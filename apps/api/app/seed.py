from __future__ import annotations

from decimal import Decimal

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from .models import Agent, Capability, Credential, Demand
from .security import demo_evm_wallet, derive_demo_private_key, public_key_hex


DEMO_AGENTS = [
    {
        "slug": "campus-requester",
        "display_name": "校园需求 Agent",
        "description": "发布校园创新项目的数据处理与内容服务需求。",
        "roles": ["requester"],
    },
    {
        "slug": "text-summary",
        "display_name": "文本摘要 Agent",
        "description": "提供可控长度的中英文文本摘要能力。",
        "roles": ["provider", "requester"],
    },
    {
        "slug": "data-statistics",
        "display_name": "数据统计 Agent",
        "description": "提供确定性的描述性统计计算。",
        "roles": ["provider"],
    },
    {
        "slug": "hash-audit",
        "display_name": "内容哈希审计 Agent",
        "description": "为文本和交付物生成可复核的内容哈希。",
        "roles": ["provider"],
    },
    {
        "slug": "code-review",
        "display_name": "代码审查 Agent",
        "description": "使用 Mock Provider 提供结构化代码审查演示。",
        "roles": ["provider", "reviewer"],
    },
    {
        "slug": "workflow-research-b",
        "display_name": "Workflow Research Agent B",
        "description": "Executes research nodes from its Chain B home wallet.",
        "roles": ["provider"],
    },
    {
        "slug": "workflow-verification-c",
        "display_name": "Workflow Verification Agent C",
        "description": "Controlled failed execution used to prove retry behavior.",
        "roles": ["provider"],
    },
    {
        "slug": "workflow-verification-d",
        "display_name": "Workflow Verification Agent D",
        "description": "Replacement Agent that completes verification on Chain D.",
        "roles": ["provider"],
    },
    {
        "slug": "platform-admin",
        "display_name": "平台仲裁 Agent",
        "description": "仅用于本地演示的争议仲裁身份。",
        "roles": ["admin", "reviewer"],
    },
    {
        "slug": "arbitrator-one",
        "display_name": "Arbitrator One",
        "description": "Independent local arbitration signer one.",
        "roles": ["arbitrator"],
    },
    {
        "slug": "arbitrator-two",
        "display_name": "Arbitrator Two",
        "description": "Independent local arbitration signer two.",
        "roles": ["arbitrator"],
    },
    {
        "slug": "arbitrator-three",
        "display_name": "Arbitrator Three",
        "description": "Independent local arbitration signer three.",
        "roles": ["arbitrator"],
    },
]


ORGANIZATIONS = {
    "campus-requester": "org:campus-requester",
    "code-review": "org:independent-review-lab",
    "platform-admin": "org:platform-operations",
    "arbitrator-one": "org:arbitration-a",
    "arbitrator-two": "org:arbitration-b",
    "arbitrator-three": "org:arbitration-c",
    "workflow-research-b": "org:provider-b",
    "workflow-verification-c": "org:provider-c",
    "workflow-verification-d": "org:provider-d",
}


CAPABILITY_DEFINITIONS = [
    {
        "provider": "text-summary",
        "slug": "text-summary-v1",
        "name": "文本摘要",
        "description": "将长文本压缩为指定长度和语言的摘要。",
        "input_schema": {
            "$schema": "https://json-schema.org/draft/2020-12/schema",
            "type": "object",
            "properties": {
                "text": {"type": "string", "minLength": 1},
                "maxLength": {"type": "integer", "minimum": 20, "maximum": 2000},
                "language": {"type": "string", "enum": ["zh-CN", "en"]},
            },
            "required": ["text", "maxLength", "language"],
            "additionalProperties": False,
        },
        "output_schema": {
            "type": "object",
            "properties": {"summary": {"type": "string"}, "originalLength": {"type": "integer"}},
            "required": ["summary", "originalLength"],
        },
        "example_input": {"text": "Agent 市场能够帮助服务发现与协商。", "maxLength": 50, "language": "zh-CN"},
        "base_price": Decimal("0.25"),
        "estimated_duration_seconds": 10,
    },
    {
        "provider": "data-statistics",
        "slug": "data-statistics-v1",
        "name": "数据统计",
        "description": "计算数列的计数、均值、中位数、最小值和最大值。",
        "input_schema": {
            "$schema": "https://json-schema.org/draft/2020-12/schema",
            "type": "object",
            "properties": {
                "numbers": {"type": "array", "items": {"type": "number"}, "minItems": 1, "maxItems": 10000},
                "metrics": {
                    "type": "array",
                    "items": {"type": "string", "enum": ["count", "mean", "median", "min", "max"]},
                    "minItems": 1,
                    "uniqueItems": True,
                },
            },
            "required": ["numbers", "metrics"],
            "additionalProperties": False,
        },
        "output_schema": {"type": "object", "additionalProperties": {"type": "number"}},
        "example_input": {"numbers": [1, 2, 3, 4, 5], "metrics": ["mean", "median", "max"]},
        "base_price": Decimal("0.15"),
        "estimated_duration_seconds": 5,
    },
    {
        "provider": "hash-audit",
        "slug": "hash-audit-v1",
        "name": "内容哈希审计",
        "description": "使用指定算法计算内容哈希，便于链上存证。",
        "input_schema": {
            "$schema": "https://json-schema.org/draft/2020-12/schema",
            "type": "object",
            "properties": {
                "content": {"type": "string"},
                "algorithm": {"type": "string", "enum": ["sha256", "sha512"]},
            },
            "required": ["content", "algorithm"],
            "additionalProperties": False,
        },
        "output_schema": {
            "type": "object",
            "properties": {"algorithm": {"type": "string"}, "digest": {"type": "string"}},
            "required": ["algorithm", "digest"],
        },
        "example_input": {"content": "delivery artifact", "algorithm": "sha256"},
        "base_price": Decimal("0.05"),
        "estimated_duration_seconds": 3,
    },
    {
        "provider": "code-review",
        "slug": "code-review-v1",
        "name": "代码审查",
        "description": "按指定关注点返回结构化代码审查意见，默认使用 Mock Provider。",
        "input_schema": {
            "$schema": "https://json-schema.org/draft/2020-12/schema",
            "type": "object",
            "properties": {
                "code": {"type": "string", "minLength": 1},
                "language": {"type": "string", "minLength": 1},
                "focus": {"type": "string", "enum": ["correctness", "security", "performance", "maintainability"]},
            },
            "required": ["code", "language", "focus"],
            "additionalProperties": False,
        },
        "output_schema": {
            "type": "object",
            "properties": {"provider": {"type": "string"}, "findings": {"type": "array"}},
            "required": ["provider", "findings"],
        },
        "example_input": {"code": "def add(a, b): return a + b", "language": "python", "focus": "correctness"},
        "base_price": Decimal("1.5"),
        "estimated_duration_seconds": 60,
    },
]


def _seed_capabilities_and_demands(session: Session, agents: dict[str, Agent]) -> None:
    """幂等地确保 4 个能力 + 2 个需求始终存在。

    无论 seed_database 走「更新已有 Agent」还是「全新创建」分支，都必须调用本函数，
    否则在库里已存在 Agent 的情况下能力/需求市场会永远是空的（原 bug）。
    """
    existing_cap_slugs = {c.slug for c in session.scalars(select(Capability))}
    for definition in CAPABILITY_DEFINITIONS:
        if definition["slug"] in existing_cap_slugs:
            continue
        capability = Capability(
            provider_id=agents[definition["provider"]].id,
            name=definition["name"],
            slug=definition["slug"],
            description=definition["description"],
            input_schema=definition["input_schema"],
            output_schema=definition["output_schema"],
            example_input=definition["example_input"],
            endpoint="pending",
            pricing_model="fixed",
            base_price=definition["base_price"],
            currency="USDC",
            estimated_duration_seconds=definition["estimated_duration_seconds"],
            availability="available",
            version="1.0.0",
        )
        session.add(capability)
        session.flush()
        capability.endpoint = f"/api/v1/capabilities/{capability.id}/invoke"

    # 仅当需求表为空时才补建 demo 需求，避免每次重启重复插入。
    if session.scalar(select(func.count()).select_from(Demand)):
        return
    requester = agents.get("campus-requester")
    if requester is None:
        return
    session.add_all(
        [
            Demand(
                requester_id=requester.id,
                title="课程资料批量摘要",
                description="为十份课程资料生成结构化中文摘要。",
                input_payload={"documentCount": 10, "language": "zh-CN"},
                acceptance_criteria="每份摘要不超过 300 字，并保留关键术语。",
                budget=Decimal("12.00"),
                currency="USDC",
                status="published",
            ),
            Demand(
                requester_id=requester.id,
                title="实验数据描述性统计",
                description="计算实验数据的均值、中位数与极值并输出 JSON。",
                input_payload={"rowCount": 500},
                acceptance_criteria="输出可被 JSON Schema 校验，并给出字段说明。",
                budget=Decimal("5.00"),
                currency="USDC",
                status="published",
            ),
        ]
    )


def seed_database(session: Session) -> None:
    existing = {agent.slug: agent for agent in session.scalars(select(Agent))}
    agents: dict[str, Agent] = {}

    if existing:
        # 库里已有 Agent：原地更新其字段，并补建新增到 DEMO_AGENTS 的 Agent。
        for definition in DEMO_AGENTS:
            slug = definition["slug"]
            agent = existing.get(slug)
            if agent is None:
                private_key = derive_demo_private_key(slug)
                agent = Agent(
                    did=f"did:local:{slug}",
                    slug=slug,
                    display_name=definition["display_name"],
                    description=definition["description"],
                    roles=definition["roles"],
                    status="active",
                    endpoint=f"http://localhost:8000/api/v1/demo-agents/{slug}",
                    operational_public_key=public_key_hex(private_key),
                )
                session.add(agent)
                session.flush()
                session.add(
                    Credential(
                        agent_id=agent.id,
                        credential_type="AgentCapabilityCredential",
                        issuer_did="did:local:platform-admin",
                        claims={"roles": definition["roles"], "development": True},
                    )
                )
            agent.organization_id = ORGANIZATIONS.get(slug, f"org:{slug}")
            agent.review_wallet = demo_evm_wallet(slug)
            agent.reviewer_active = bool({"reviewer", "arbitrator"} & set(definition["roles"]))
            agent.roles = definition["roles"]
            agents[slug] = agent
        _seed_capabilities_and_demands(session, agents)
        session.commit()
        return

    for definition in DEMO_AGENTS:
        slug = definition["slug"]
        private_key = derive_demo_private_key(slug)
        agent = Agent(
            did=f"did:local:{slug}",
            slug=slug,
            display_name=definition["display_name"],
            description=definition["description"],
            roles=definition["roles"],
            status="active",
            endpoint=f"http://localhost:8000/api/v1/demo-agents/{slug}",
            operational_public_key=public_key_hex(private_key),
            organization_id=ORGANIZATIONS.get(slug, f"org:{slug}"),
            review_wallet=demo_evm_wallet(slug),
            reviewer_active=bool({"reviewer", "arbitrator"} & set(definition["roles"])),
        )
        session.add(agent)
        session.flush()
        session.add(
            Credential(
                agent_id=agent.id,
                credential_type="AgentCapabilityCredential",
                issuer_did="did:local:platform-admin",
                claims={"roles": definition["roles"], "development": True},
            )
        )
        agents[slug] = agent

    _seed_capabilities_and_demands(session, agents)
    session.commit()
