from __future__ import annotations

import hashlib
import math
import re
import statistics
from dataclasses import dataclass
from typing import Awaitable, Callable

from app.providers import CodeReviewProvider, MockCodeReviewProvider, StructuredTaskProvider


Handler = Callable[[dict[str, object]], Awaitable[dict[str, object]]]


@dataclass(frozen=True)
class AgentDefinition:
    slug: str
    name: str
    description: str
    capability_id: str
    version: str
    base_price: str
    currency: str
    estimated_duration_seconds: int
    input_schema: dict[str, object]
    output_schema: dict[str, object]
    example_input: dict[str, object]
    handler: Handler


async def summarize(payload: dict[str, object]) -> dict[str, object]:
    text = str(payload["text"])
    max_length = int(payload["maxLength"])
    language = str(payload["language"])
    normalized = re.sub(r"\s+", " ", text).strip()
    if len(normalized) <= max_length:
        summary = normalized
    else:
        candidates = re.split(r"(?<=[.!?\u3002\uff01\uff1f])\s*", normalized)
        selected = ""
        for sentence in candidates:
            if not sentence:
                continue
            candidate = f"{selected} {sentence}".strip()
            if len(candidate) > max_length:
                break
            selected = candidate
        if selected:
            summary = selected
        elif max_length == 1:
            summary = normalized[:1]
        else:
            summary = normalized[: max_length - 1].rstrip() + "..."[:1]
    return {
        "summary": summary,
        "language": language,
        "originalLength": len(text),
        "summaryLength": len(summary),
    }


async def statistics_handler(payload: dict[str, object]) -> dict[str, object]:
    numbers = [float(item) for item in payload["numbers"]]  # type: ignore[arg-type]
    if not all(math.isfinite(number) for number in numbers):
        raise ValueError("numbers must contain only finite values")
    requested = payload["metrics"]
    functions: dict[str, Callable[[], float]] = {
        "count": lambda: float(len(numbers)),
        "sum": lambda: math.fsum(numbers),
        "mean": lambda: statistics.fmean(numbers),
        "median": lambda: statistics.median(numbers),
        "min": lambda: min(numbers),
        "max": lambda: max(numbers),
        "stdev": lambda: statistics.pstdev(numbers),
    }
    result = {metric: functions[metric]() for metric in requested}  # type: ignore[index]
    return {"metrics": result, "sampleSize": len(numbers)}


async def hash_handler(payload: dict[str, object]) -> dict[str, object]:
    content = str(payload["content"]).encode("utf-8")
    algorithm = str(payload["algorithm"])
    digest = hashlib.new(algorithm, content).hexdigest()
    return {"algorithm": algorithm, "digest": digest, "byteLength": len(content)}


def build_agents(
    review_provider: CodeReviewProvider | None = None,
    task_provider: StructuredTaskProvider | None = None,
) -> dict[str, AgentDefinition]:
    provider = review_provider or MockCodeReviewProvider()

    async def review_handler(payload: dict[str, object]) -> dict[str, object]:
        return await provider.review(
            code=str(payload["code"]),
            language=str(payload["language"]),
            focus=str(payload["focus"]),
        )

    schema_uri = "https://json-schema.org/draft/2020-12/schema"
    research_output_schema = {
        "$schema": schema_uri,
        "type": "object",
        "required": ["summary", "score"],
        "properties": {
            "summary": {"type": "string", "minLength": 1},
            "score": {"type": "integer", "minimum": 0, "maximum": 100},
            "sources": {"type": "array", "items": {"type": "string"}},
        },
        "additionalProperties": False,
    }
    verification_output_schema = {
        "$schema": schema_uri,
        "type": "object",
        "required": ["verified", "report"],
        "properties": {
            "verified": {"type": "boolean"},
            "report": {"type": "string", "minLength": 1},
        },
        "additionalProperties": False,
    }

    async def workflow_research(payload: dict[str, object]) -> dict[str, object]:
        if task_provider is not None:
            return await task_provider.complete_json(
                system_prompt="Produce a concise, auditable research summary. Return JSON only.",
                task=payload,
                output_schema=research_output_schema,
            )
        objective = str(payload["objective"])
        return {
            "summary": f"Evidence summary for: {objective}",
            "score": 92,
            "sources": ["local-demo-evidence"],
        }

    async def workflow_verification_success(payload: dict[str, object]) -> dict[str, object]:
        if task_provider is not None:
            result = await task_provider.complete_json(
                system_prompt="Verify the evidence and return JSON only. Set verified to true only when supported.",
                task=payload,
                output_schema=verification_output_schema,
            )
            if result.get("verified") is not True:
                raise ValueError("DeepSeek did not verify the supplied evidence")
            return result
        return {"verified": True, "report": "Replacement Agent verified the settled evidence."}

    async def workflow_verification_failure(payload: dict[str, object]) -> dict[str, object]:
        return {"verified": False, "report": "Controlled local rejection for retry acceptance."}

    agents = [
        AgentDefinition(
            slug="workflow-research-b",
            name="Workflow Research Agent B",
            description="Completes the first workflow node on home Chain B.",
            capability_id="research",
            version="1.0.0",
            base_price="4.20",
            currency="USDC",
            estimated_duration_seconds=10,
            input_schema={
                "$schema": schema_uri,
                "type": "object",
                "required": ["objective"],
                "properties": {
                    "objective": {"type": "string", "minLength": 1},
                    "input": {"type": "object"},
                    "dependencies": {"type": "object"},
                },
                "additionalProperties": False,
            },
            output_schema=research_output_schema,
            example_input={"objective": "Summarize cross-chain security evidence", "input": {}, "dependencies": {}},
            handler=workflow_research,
        ),
        AgentDefinition(
            slug="workflow-verification-c",
            name="Workflow Verification Agent C",
            description="Produces a controlled failed review for retry testing.",
            capability_id="verification",
            version="1.0.0",
            base_price="6.80",
            currency="USDC",
            estimated_duration_seconds=5,
            input_schema={
                "$schema": schema_uri,
                "type": "object",
                "required": ["objective", "dependencies"],
                "properties": {
                    "objective": {"type": "string", "minLength": 1},
                    "input": {"type": "object"},
                    "dependencies": {"type": "object"},
                },
                "additionalProperties": False,
            },
            output_schema=verification_output_schema,
            example_input={"objective": "Verify evidence", "input": {}, "dependencies": {"N1": {"summary": "example"}}},
            handler=workflow_verification_failure,
        ),
        AgentDefinition(
            slug="workflow-verification-d",
            name="Workflow Verification Agent D",
            description="Completes a replacement workflow attempt on home Chain D.",
            capability_id="verification",
            version="1.0.0",
            base_price="6.80",
            currency="USDC",
            estimated_duration_seconds=10,
            input_schema={
                "$schema": schema_uri,
                "type": "object",
                "required": ["objective", "dependencies"],
                "properties": {
                    "objective": {"type": "string", "minLength": 1},
                    "input": {"type": "object"},
                    "dependencies": {"type": "object"},
                },
                "additionalProperties": False,
            },
            output_schema=verification_output_schema,
            example_input={"objective": "Verify evidence", "input": {}, "dependencies": {"N1": {"summary": "example"}}},
            handler=workflow_verification_success,
        ),
        AgentDefinition(
            slug="text-summary",
            name="Text Summary Agent",
            description="Produces a deterministic extractive summary for text input.",
            capability_id="cap-text-summary-v1",
            version="1.0.0",
            base_price="0.10",
            currency="USDC",
            estimated_duration_seconds=2,
            input_schema={
                "$schema": schema_uri,
                "type": "object",
                "required": ["text", "maxLength", "language"],
                "properties": {
                    "text": {"type": "string", "minLength": 1, "maxLength": 50000},
                    "maxLength": {"type": "integer", "minimum": 1, "maximum": 5000},
                    "language": {"type": "string", "enum": ["auto", "zh", "en"]},
                },
                "additionalProperties": False,
            },
            output_schema={
                "$schema": schema_uri,
                "type": "object",
                "required": ["summary", "language", "originalLength", "summaryLength"],
                "properties": {
                    "summary": {"type": "string"},
                    "language": {"type": "string"},
                    "originalLength": {"type": "integer"},
                    "summaryLength": {"type": "integer"},
                },
                "additionalProperties": False,
            },
            example_input={
                "text": "Agent markets connect requesters with providers. Signed terms make changes auditable.",
                "maxLength": 60,
                "language": "en",
            },
            handler=summarize,
        ),
        AgentDefinition(
            slug="data-statistics",
            name="Data Statistics Agent",
            description="Computes selected deterministic descriptive statistics.",
            capability_id="cap-data-statistics-v1",
            version="1.0.0",
            base_price="0.05",
            currency="USDC",
            estimated_duration_seconds=1,
            input_schema={
                "$schema": schema_uri,
                "type": "object",
                "required": ["numbers", "metrics"],
                "properties": {
                    "numbers": {
                        "type": "array",
                        "items": {"type": "number"},
                        "minItems": 1,
                        "maxItems": 10000,
                    },
                    "metrics": {
                        "type": "array",
                        "items": {
                            "type": "string",
                            "enum": [
                                "count",
                                "sum",
                                "mean",
                                "median",
                                "min",
                                "max",
                                "stdev",
                            ],
                        },
                        "minItems": 1,
                        "uniqueItems": True,
                    },
                },
                "additionalProperties": False,
            },
            output_schema={
                "$schema": schema_uri,
                "type": "object",
                "required": ["metrics", "sampleSize"],
                "properties": {
                    "metrics": {
                        "type": "object",
                        "additionalProperties": {"type": "number"},
                    },
                    "sampleSize": {"type": "integer"},
                },
                "additionalProperties": False,
            },
            example_input={
                "numbers": [1, 2, 3, 4],
                "metrics": ["mean", "median", "stdev"],
            },
            handler=statistics_handler,
        ),
        AgentDefinition(
            slug="hash-audit",
            name="Hash Audit Agent",
            description="Creates a reproducible content digest for delivery auditing.",
            capability_id="cap-hash-audit-v1",
            version="1.0.0",
            base_price="0.01",
            currency="USDC",
            estimated_duration_seconds=1,
            input_schema={
                "$schema": schema_uri,
                "type": "object",
                "required": ["content", "algorithm"],
                "properties": {
                    "content": {"type": "string", "maxLength": 1000000},
                    "algorithm": {
                        "type": "string",
                        "enum": ["sha256", "sha512", "blake2b"],
                    },
                },
                "additionalProperties": False,
            },
            output_schema={
                "$schema": schema_uri,
                "type": "object",
                "required": ["algorithm", "digest", "byteLength"],
                "properties": {
                    "algorithm": {"type": "string"},
                    "digest": {"type": "string"},
                    "byteLength": {"type": "integer"},
                },
                "additionalProperties": False,
            },
            example_input={"content": "delivery payload", "algorithm": "sha256"},
            handler=hash_handler,
        ),
        AgentDefinition(
            slug="code-review",
            name="Code Review Agent",
            description="Reviews source code through a replaceable provider interface.",
            capability_id="cap-code-review-v1",
            version="1.0.0",
            base_price="0.50",
            currency="USDC",
            estimated_duration_seconds=10,
            input_schema={
                "$schema": schema_uri,
                "type": "object",
                "required": ["code", "language", "focus"],
                "properties": {
                    "code": {"type": "string", "maxLength": 200000},
                    "language": {"type": "string", "minLength": 1, "maxLength": 40},
                    "focus": {
                        "type": "string",
                        "enum": ["general", "security", "correctness", "performance"],
                    },
                },
                "additionalProperties": False,
            },
            output_schema={
                "$schema": schema_uri,
                "type": "object",
                "required": ["summary", "score", "findings", "provider"],
                "properties": {
                    "summary": {"type": "string"},
                    "score": {"type": "integer", "minimum": 0, "maximum": 100},
                    "findings": {
                        "type": "array",
                        "items": {
                            "type": "object",
                            "required": ["severity", "message", "focus"],
                            "properties": {
                                "severity": {
                                    "type": "string",
                                    "enum": ["low", "medium", "high"],
                                },
                                "message": {"type": "string"},
                                "focus": {"type": "string"},
                            },
                            "additionalProperties": False,
                        },
                    },
                    "provider": {"type": "string"},
                    "usage": {"type": "object"},
                    "model": {"type": "string"},
                },
                "additionalProperties": False,
            },
            example_input={
                "code": "def parse(value):\n    return eval(value)",
                "language": "python",
                "focus": "security",
            },
            handler=review_handler,
        ),
    ]
    return {agent.slug: agent for agent in agents}
