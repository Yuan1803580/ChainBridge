from __future__ import annotations

import asyncio
import json
import os
import secrets
import threading
import uuid
from datetime import datetime, timezone
from decimal import Decimal, InvalidOperation
from pathlib import Path
from typing import Any, Literal

import httpx
from fastapi import Depends, FastAPI, Header, HTTPException, Request, status
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from .identity import canonical_hash, load_coordinator_identity


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


class ApiModel(BaseModel):
    model_config = ConfigDict(alias_generator=lambda value: "".join(
        [value.split("_")[0], *[part.capitalize() for part in value.split("_")[1:]]]
    ), populate_by_name=True, extra="forbid")


class ChatRequest(ApiModel):
    message: str = Field(min_length=10, max_length=10000)
    budget: str = Field(default="20.00", pattern=r"^(0|[1-9][0-9]*)(\.[0-9]{1,8})?$")
    platform_fee: str = Field(default="1.10", pattern=r"^(0|[1-9][0-9]*)(\.[0-9]{1,8})?$")
    currency: str = Field(default="USDC", min_length=2, max_length=32)
    input_payload: dict[str, Any] = Field(default_factory=dict)


class CreateWorkflowRequest(ApiModel):
    funding_mode: Literal["RESERVED", "ONCHAIN"] = "RESERVED"
    payer_address: str | None = Field(default=None, pattern=r"^0x[a-fA-F0-9]{40}$")

    @model_validator(mode="after")
    def require_onchain_payer(self) -> "CreateWorkflowRequest":
        if self.funding_mode == "ONCHAIN" and self.payer_address is None:
            raise ValueError("payerAddress is required for ONCHAIN funding")
        return self


class CoordinateRequest(ApiModel):
    max_actions: int = Field(default=1, ge=1, le=20)


class FailureAdviceRequest(ApiModel):
    node_key: str | None = Field(default=None, pattern=r"^[A-Z][A-Z0-9_-]{0,15}$")


class RefineRequest(ApiModel):
    message: str = Field(min_length=2, max_length=10000)
    budget: str | None = Field(default=None, pattern=r"^(0|[1-9][0-9]*)(\.[0-9]{1,8})?$")
    platform_fee: str | None = Field(default=None, pattern=r"^(0|[1-9][0-9]*)(\.[0-9]{1,8})?$")
    currency: str | None = Field(default=None, min_length=2, max_length=32)
    input_payload: dict[str, Any] | None = None


class IdentitySignRequest(ApiModel):
    message: str = Field(min_length=16, max_length=10000)


class PlatformRegistrationRequest(ApiModel):
    platform_url: str = Field(pattern=r"^https?://[^\s]+$", max_length=500)
    profile_id: str = Field(min_length=36, max_length=36)
    status: Literal["ONLINE", "BUSY", "OFFLINE"] = "ONLINE"
    capacity_available: bool = True
    endpoint_latency_ms: int = Field(default=500, ge=0, le=60000)
    heartbeat_interval_seconds: int = Field(default=30, ge=10, le=50)


class CoordinatorNodeProposal(ApiModel):
    node_key: str = Field(pattern=r"^[A-Z][A-Z0-9_-]{0,15}$")
    title: str = Field(min_length=2, max_length=200)
    description: str = Field(default="", max_length=5000)
    capability: str = Field(min_length=2, max_length=100)
    depends_on: list[str] = Field(default_factory=list, max_length=20)
    acceptance_criteria: dict[str, Any] = Field(default_factory=dict)
    input_payload: dict[str, Any] = Field(default_factory=dict)
    suggested_reward: str = Field(pattern=r"^(0|[1-9][0-9]*)(\.[0-9]{1,8})?$")
    max_attempts: int = Field(default=3, ge=1, le=10)
    rationale: str = Field(default="", max_length=2000)


class CoordinatorTaskProposal(ApiModel):
    title: str = Field(min_length=3, max_length=200)
    objective: str = Field(min_length=10, max_length=10000)
    input_payload: dict[str, Any] = Field(default_factory=dict)
    nodes: list[CoordinatorNodeProposal] = Field(min_length=1, max_length=20)
    explanation: str = Field(default="", max_length=5000)

    @model_validator(mode="after")
    def validate_graph_references(self) -> "CoordinatorTaskProposal":
        keys = [node.node_key for node in self.nodes]
        if len(keys) != len(set(keys)):
            raise ValueError("Coordinator proposal has duplicate nodeKey values")
        known = set(keys)
        for node in self.nodes:
            unknown = set(node.depends_on) - known
            if unknown:
                raise ValueError(f"node {node.node_key} has unknown dependencies: {sorted(unknown)}")
            if node.node_key in node.depends_on:
                raise ValueError(f"node {node.node_key} cannot depend on itself")
        return self


def _validated_task_proposal(
    result: dict[str, Any],
    fallback_input_payload: dict[str, Any],
) -> dict[str, Any]:
    raw_nodes = result.get("nodes")
    if not isinstance(raw_nodes, list):
        raise ValueError("Coordinator proposal is missing a node list")
    node_fields = {
        "nodeKey",
        "title",
        "description",
        "capability",
        "dependsOn",
        "acceptanceCriteria",
        "inputPayload",
        "suggestedReward",
        "maxAttempts",
        "rationale",
    }
    sanitized_nodes = [
        {key: value for key, value in node.items() if key in node_fields}
        for node in raw_nodes
        if isinstance(node, dict)
    ]
    proposal = CoordinatorTaskProposal.model_validate(
        {
            "title": str(result.get("title", "")).strip()[:200],
            "objective": str(result.get("objective", "")).strip()[:10000],
            "inputPayload": result.get("inputPayload", fallback_input_payload),
            "nodes": sanitized_nodes,
            "explanation": str(result.get("explanation", "")).strip()[:5000],
        }
    )
    return proposal.model_dump(by_alias=True)


def _validate_proposal_market_contract(
    proposal: dict[str, Any],
    market_context: dict[str, Any] | None,
) -> dict[str, Any]:
    if not isinstance(market_context, dict):
        return proposal
    raw_capabilities = market_context.get("availableCapabilities")
    if not isinstance(raw_capabilities, list):
        return proposal
    capabilities = {
        str(item.get("capability", "")): item
        for item in raw_capabilities
        if isinstance(item, dict) and str(item.get("capability", ""))
    }
    for node in proposal.get("nodes", []):
        if not isinstance(node, dict):
            continue
        capability = str(node.get("capability", ""))
        contract = capabilities.get(capability)
        if contract is None:
            raise ValueError(f"Capability {capability!r} is not available in the current market")
        known_contracts = contract.get("outputContractsKnown")
        # A capability with no published output schema cannot be validated here.
        # Coordinator Core still revalidates the attempt output at dispatch time,
        # so an unknown contract degrades to a warning instead of a hard failure.
        if isinstance(known_contracts, int) and known_contracts == 0:
            continue
        common_fields = contract.get("commonOutputFields")
        if not isinstance(common_fields, list):
            continue
        criteria = node.get("acceptanceCriteria", {})
        criteria = criteria if isinstance(criteria, dict) else {}
        required_fields = criteria.get("requiredFields", [])
        if not isinstance(required_fields, list) or any(
            not isinstance(field, str) or not field.strip() for field in required_fields
        ):
            raise ValueError(
                f"Node {node.get('nodeKey')} acceptanceCriteria.requiredFields must be a string list"
            )
        unsupported = sorted(set(required_fields) - set(common_fields))
        if unsupported:
            available = ", ".join(common_fields) if common_fields else "(none published)"
            raise ValueError(
                f"Node {node.get('nodeKey')} requires output fields not shared by current "
                f"{capability} providers: {', '.join(unsupported)}. Available shared fields: {available}. "
                f"Move task-specific wording into acceptanceCriteria.rules instead."
            )
    return proposal


class TaskInterpreter:
    provider_name = "mock"
    model = "mock-deterministic-dag-v1"

    async def interpret(
        self,
        message: str,
        input_payload: dict[str, Any],
        *,
        budget: str = "20.00",
        platform_fee: str = "1.10",
        currency: str = "USDC",
        current_draft: dict[str, Any] | None = None,
        market_context: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        normalized = " ".join(message.split())
        if current_draft is not None:
            objective = f"{current_draft['objective']} Revision requested: {normalized}"
            title = str(current_draft["title"])
        else:
            objective = normalized
            title = normalized[:80]
        return _validated_task_proposal(
            {
                "title": title,
                "objective": objective,
                "inputPayload": input_payload,
                "explanation": "The proposal separates evidence production from independent verification.",
                "nodes": [
                    {
                        "nodeKey": "N1",
                        "title": "Research and structure source facts",
                        "description": "Produce a structured evidence summary for downstream verification.",
                        "capability": "research",
                        "dependsOn": [],
                        "acceptanceCriteria": {"requiredFields": ["summary"], "minScore": 80},
                        "inputPayload": {"source": "workflow.input"},
                        "suggestedReward": "4.20",
                        "maxAttempts": 2,
                        "rationale": "A separate research node creates auditable evidence for later checks.",
                    },
                    {
                        "nodeKey": "N2",
                        "title": "Verify evidence and publish report",
                        "description": "Verify the settled N1 evidence against workflow criteria.",
                        "capability": "verification",
                        "dependsOn": ["N1"],
                        "acceptanceCriteria": {
                            "requiredFields": ["verified", "report"],
                            "mustEqual": {"verified": True},
                        },
                        "inputPayload": {"evidence": "N1.output"},
                        "suggestedReward": "6.80",
                        "maxAttempts": 3,
                        "rationale": "Independent verification prevents an unreviewed result from unlocking settlement.",
                    },
                ],
            },
            input_payload,
        )


class DeepSeekTaskInterpreter(TaskInterpreter):
    provider_name = "deepseek"

    def __init__(self) -> None:
        self.api_key = os.getenv("DEEPSEEK_API_KEY", "").strip()
        if not self.api_key:
            raise ValueError("DEEPSEEK_API_KEY is required when COORDINATOR_LLM_PROVIDER=deepseek")
        self.base_url = os.getenv("DEEPSEEK_BASE_URL", "https://api.deepseek.com").rstrip("/")
        self.model = os.getenv("DEEPSEEK_MODEL", "deepseek-chat")
        self.timeout = float(os.getenv("DEEPSEEK_TIMEOUT_SECONDS", "60"))
        self.max_retries = int(os.getenv("DEEPSEEK_MAX_RETRIES", "2"))

    async def interpret(
        self,
        message: str,
        input_payload: dict[str, Any],
        *,
        budget: str = "20.00",
        platform_fee: str = "1.10",
        currency: str = "USDC",
        current_draft: dict[str, Any] | None = None,
        market_context: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        prompt = {
            "message": message,
            "currentDraft": current_draft,
            "budget": budget,
            "platformFee": platform_fee,
            "currency": currency,
            "capabilityMarket": market_context or {},
            "inputPayload": input_payload,
            "requiredOutput": {
                "title": "short task title",
                "objective": "precise executable objective preserving all user constraints",
                "inputPayload": "object containing only task inputs stated by the user",
                "explanation": "brief explanation of the proposed workflow",
                "nodes": [
                    {
                        "nodeKey": "N1",
                        "title": "node title",
                        "description": "executable node objective",
                        "capability": "one required capability",
                        "dependsOn": [],
                        "acceptanceCriteria": {
                            "requiredFields": ["summary", "score", "sources"],
                            "rules": ["task-specific semantic requirement"],
                        },
                        "inputPayload": {},
                        "suggestedReward": "positive decimal string",
                        "maxAttempts": 3,
                        "rationale": "why this node and reward are needed",
                    }
                ],
            },
        }
        request_body = {
            "model": self.model,
            "temperature": float(os.getenv("DEEPSEEK_TEMPERATURE", "0")),
            "max_tokens": int(os.getenv("DEEPSEEK_MAX_TOKENS", "2048")),
            "response_format": {"type": "json_object"},
            "messages": [
                {
                    "role": "system",
                    "content": (
                        "You are a Coordinator Agent. Return one JSON object only. Propose an acyclic DAG "
                        "with no more than 20 business nodes. Choose the node count that actually fits "
                        "the task: decompose it as far as the goal requires and do not fall back on a "
                        "fixed two-node shape. Every capability you use must exist in capabilityMarket, "
                        "so plan around the capabilities that are available rather than inventing work "
                        "no registered Agent can perform. "
                        "Node rewards plus the platform fee must not "
                        "exceed the total budget. Use only exact capability values listed in capabilityMarket "
                        "and keep suggestedReward at or above its lowestQuote when the budget permits. "
                        "Every acceptanceCriteria.requiredFields value must be listed in that capability's "
                        "commonOutputFields. Put task-specific content requirements in acceptanceCriteria.rules "
                        "instead of inventing new top-level output field names. "
                        "You may propose task fields only. Never choose risk level, "
                        "review policy, Agent identity, wallet, chain, route, settlement status, or payment. "
                        "Those decisions belong exclusively to Coordinator Core."
                    ),
                },
                {"role": "user", "content": json.dumps(prompt, ensure_ascii=False)},
            ],
        }
        last_error: Exception | None = None
        for attempt in range(self.max_retries + 1):
            try:
                async with httpx.AsyncClient(timeout=self.timeout, follow_redirects=False) as client:
                    response = await client.post(
                        f"{self.base_url}/chat/completions",
                        headers={"Authorization": f"Bearer {self.api_key}"},
                        json=request_body,
                    )
                    response.raise_for_status()
                content = response.json()["choices"][0]["message"]["content"]
                result = json.loads(content)
                if not isinstance(result, dict):
                    raise ValueError("DeepSeek task interpretation is not an object")
                return _validated_task_proposal(result, input_payload)
            except (httpx.HTTPError, KeyError, IndexError, TypeError, ValueError, json.JSONDecodeError) as exc:
                last_error = exc
                if attempt < self.max_retries:
                    await asyncio.sleep(0.25 * (2 ** attempt))
        raise ValueError(f"DeepSeek task interpretation failed: {last_error}")


class OpenAICompatibleTaskInterpreter(TaskInterpreter):
    provider_name = "openai_compatible"

    def __init__(self, transport: httpx.AsyncBaseTransport | None = None) -> None:
        self.api_key = os.getenv("COORDINATOR_OPENAI_API_KEY", "").strip()
        self.base_url = os.getenv("COORDINATOR_OPENAI_BASE_URL", "").strip().rstrip("/")
        self.model = os.getenv("COORDINATOR_OPENAI_MODEL", "").strip()
        self.api_mode = os.getenv("COORDINATOR_OPENAI_API_MODE", "responses").strip().lower()
        self.timeout = float(os.getenv("COORDINATOR_OPENAI_TIMEOUT_SECONDS", "120"))
        self.max_retries = int(os.getenv("COORDINATOR_OPENAI_MAX_RETRIES", "1"))
        self.max_output_tokens = int(os.getenv("COORDINATOR_OPENAI_MAX_OUTPUT_TOKENS", "2048"))
        self.transport = transport
        if not self.api_key:
            raise ValueError(
                "COORDINATOR_OPENAI_API_KEY is required when "
                "COORDINATOR_LLM_PROVIDER=openai_compatible"
            )
        if not self.base_url:
            raise ValueError(
                "COORDINATOR_OPENAI_BASE_URL is required when "
                "COORDINATOR_LLM_PROVIDER=openai_compatible"
            )
        if not self.model:
            raise ValueError(
                "COORDINATOR_OPENAI_MODEL is required when "
                "COORDINATOR_LLM_PROVIDER=openai_compatible"
            )
        if self.api_mode not in {"responses", "chat_completions"}:
            raise ValueError(
                "COORDINATOR_OPENAI_API_MODE must be responses or chat_completions"
            )

    @staticmethod
    def _system_prompt() -> str:
        return (
            "You are a Coordinator Agent responsible for user interaction and workflow planning. "
            "Return one JSON object only. Propose an acyclic DAG with no more than 20 business nodes. "
            "Choose the node count that actually fits the task: decompose it as far as the goal "
            "requires and do not fall back on a fixed two-node shape. Every capability you use must "
            "exist in capabilityMarket.availableCapabilities, so plan around the capabilities that "
            "are available rather than inventing work no registered Agent can perform. "
            "For every node provide nodeKey, title, description, capability, dependsOn, "
            "acceptanceCriteria, inputPayload, suggestedReward, maxAttempts, and rationale. "
            "Use only exact capability values listed in capabilityMarket.availableCapabilities and keep "
            "suggestedReward at or above the listed lowestQuote when the budget permits. "
            "Every acceptanceCriteria.requiredFields value must be listed in that capability's "
            "commonOutputFields. Put task-specific content requirements in acceptanceCriteria.rules "
            "instead of inventing new top-level output field names. "
            "Node rewards plus the platform fee must not exceed the total budget. "
            "Never choose or return riskLevel, review policy, Agent identity, wallet, chain, route, "
            "settlement eligibility, or payment state. Coordinator Core owns those decisions."
        )

    @staticmethod
    def _prompt(
        message: str,
        input_payload: dict[str, Any],
        budget: str,
        platform_fee: str,
        currency: str,
        current_draft: dict[str, Any] | None,
        market_context: dict[str, Any] | None,
    ) -> dict[str, Any]:
        return {
            "message": message,
            "currentDraft": current_draft,
            "budget": budget,
            "platformFee": platform_fee,
            "currency": currency,
            "capabilityMarket": market_context or {},
            "inputPayload": input_payload,
            "requiredOutput": {
                "title": "short task title",
                "objective": "precise executable objective preserving all user constraints",
                "inputPayload": "object containing only task inputs stated by the user",
                "explanation": "brief workflow and cost explanation",
                "nodes": [
                    {
                        "nodeKey": "N1",
                        "title": "node title",
                        "description": "executable node objective",
                        "capability": "required capability",
                        "dependsOn": [],
                        "acceptanceCriteria": {
                            "requiredFields": ["summary", "score", "sources"],
                            "rules": ["task-specific semantic requirement"],
                        },
                        "inputPayload": {},
                        "suggestedReward": "positive decimal string",
                        "maxAttempts": 3,
                        "rationale": "why this node and reward are needed",
                    }
                ],
            },
        }

    @staticmethod
    def _json_object(content: str) -> dict[str, Any]:
        normalized = content.strip()
        if normalized.startswith("```") and normalized.endswith("```"):
            lines = normalized.splitlines()
            if len(lines) >= 3:
                normalized = "\n".join(lines[1:-1]).strip()
        parsed = json.loads(normalized)
        if not isinstance(parsed, dict):
            raise ValueError("OpenAI-compatible task interpretation is not an object")
        return parsed

    @staticmethod
    def _responses_text(payload: dict[str, Any]) -> str:
        direct = payload.get("output_text")
        if isinstance(direct, str) and direct.strip():
            return direct
        output = payload.get("output")
        if not isinstance(output, list):
            raise ValueError("OpenAI-compatible Responses payload has no output")
        parts: list[str] = []
        for item in output:
            if not isinstance(item, dict):
                continue
            content = item.get("content")
            if not isinstance(content, list):
                continue
            for block in content:
                if not isinstance(block, dict):
                    continue
                text = block.get("text")
                if isinstance(text, str) and text:
                    parts.append(text)
        if not parts:
            raise ValueError("OpenAI-compatible Responses payload has no output text")
        return "\n".join(parts)

    @staticmethod
    def _chat_text(payload: dict[str, Any]) -> str:
        choices = payload.get("choices")
        if not isinstance(choices, list) or not choices or not isinstance(choices[0], dict):
            raise ValueError("OpenAI-compatible Chat Completions payload has no choices")
        message = choices[0].get("message")
        if not isinstance(message, dict):
            raise ValueError("OpenAI-compatible Chat Completions payload has no message")
        content = message.get("content")
        if not isinstance(content, str) or not content.strip():
            raise ValueError("OpenAI-compatible Chat Completions payload has no content")
        return content

    @staticmethod
    def _validated_result(
        result: dict[str, Any],
        input_payload: dict[str, Any],
    ) -> dict[str, Any]:
        return _validated_task_proposal(result, input_payload)

    async def interpret(
        self,
        message: str,
        input_payload: dict[str, Any],
        *,
        budget: str = "20.00",
        platform_fee: str = "1.10",
        currency: str = "USDC",
        current_draft: dict[str, Any] | None = None,
        market_context: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        prompt = json.dumps(
            self._prompt(
                message,
                input_payload,
                budget,
                platform_fee,
                currency,
                current_draft,
                market_context,
            ),
            ensure_ascii=False,
        )
        if self.api_mode == "responses":
            path = "/responses"
            request_body: dict[str, Any] = {
                "model": self.model,
                "input": [
                    {"role": "system", "content": self._system_prompt()},
                    {"role": "user", "content": prompt},
                ],
                "max_output_tokens": self.max_output_tokens,
            }
        else:
            path = "/chat/completions"
            request_body = {
                "model": self.model,
                "messages": [
                    {"role": "system", "content": self._system_prompt()},
                    {"role": "user", "content": prompt},
                ],
                "max_tokens": self.max_output_tokens,
                "response_format": {"type": "json_object"},
            }

        last_error: Exception | None = None
        for attempt in range(self.max_retries + 1):
            try:
                async with httpx.AsyncClient(
                    timeout=self.timeout,
                    follow_redirects=False,
                    transport=self.transport,
                ) as client:
                    response = await client.post(
                        f"{self.base_url}{path}",
                        headers={"Authorization": f"Bearer {self.api_key}"},
                        json=request_body,
                    )
                    response.raise_for_status()
                payload = response.json()
                if not isinstance(payload, dict):
                    raise ValueError("OpenAI-compatible provider returned a non-object response")
                content = (
                    self._responses_text(payload)
                    if self.api_mode == "responses"
                    else self._chat_text(payload)
                )
                return self._validated_result(self._json_object(content), input_payload)
            except (httpx.HTTPError, KeyError, IndexError, TypeError, ValueError, json.JSONDecodeError) as exc:
                last_error = exc
                if attempt < self.max_retries:
                    await asyncio.sleep(0.25 * (2 ** attempt))
        raise ValueError(f"OpenAI-compatible task interpretation failed: {last_error}")


class PlatformClient:
    def __init__(self) -> None:
        self.base_url = os.getenv("COORDINATOR_PLATFORM_URL", "http://127.0.0.1:8000").rstrip("/")
        self.requester_did = os.getenv("COORDINATOR_REQUESTER_DID", "did:local:campus-requester")
        self.timeout = float(os.getenv("COORDINATOR_PLATFORM_TIMEOUT_SECONDS", "30"))
        self.execution_timeout = float(os.getenv("COORDINATOR_EXECUTION_TIMEOUT_SECONDS", "2200"))
        self._token: str | None = None

    async def _login(self) -> str:
        if os.getenv("APP_ENV", "development").lower() not in {"development", "dev", "test"}:
            raise ValueError("Production Coordinator requires delegated user authentication")
        async with httpx.AsyncClient(timeout=self.timeout, follow_redirects=False) as client:
            response = await client.post(
                f"{self.base_url}/api/v1/auth/dev-login",
                headers={"Idempotency-Key": str(uuid.uuid4())},
                json={"did": self.requester_did},
            )
            response.raise_for_status()
        token = response.json().get("accessToken")
        if not isinstance(token, str) or not token:
            raise ValueError("Platform dev login did not return an access token")
        self._token = token
        return token

    async def _request(
        self,
        method: str,
        path: str,
        body: dict[str, Any] | None = None,
        *,
        timeout_seconds: float | None = None,
    ) -> dict[str, Any]:
        token = self._token or await self._login()
        for auth_attempt in range(2):
            headers = {"Authorization": f"Bearer {token}"}
            if method != "GET":
                headers["Idempotency-Key"] = str(uuid.uuid4())
            async with httpx.AsyncClient(
                timeout=timeout_seconds or self.timeout,
                follow_redirects=False,
            ) as client:
                response = await client.request(method, f"{self.base_url}{path}", headers=headers, json=body)
            if response.status_code == 401 and auth_attempt == 0:
                token = await self._login()
                continue
            try:
                payload = response.json()
            except ValueError:
                payload = {"detail": response.text}
            if not response.is_success:
                raise ValueError(f"Platform {method} {path} failed ({response.status_code}): {payload}")
            if not isinstance(payload, dict):
                raise ValueError(f"Platform {method} {path} returned a non-object response")
            return payload
        raise ValueError("Platform authentication failed")

    async def health(self) -> dict[str, Any]:
        async with httpx.AsyncClient(timeout=3, follow_redirects=False) as client:
            response = await client.get(f"{self.base_url}/api/v1/health")
            response.raise_for_status()
            payload = response.json()
        return payload if isinstance(payload, dict) else {"status": "unknown"}

    async def plan(self, payload: dict[str, Any]) -> dict[str, Any]:
        return await self._request("POST", "/api/v1/workflow-plans", payload)

    async def planning_context(self) -> dict[str, Any]:
        payload = await self._request("GET", "/api/v1/workflow-agents")
        raw_items = payload.get("items", [])
        items = raw_items if isinstance(raw_items, list) else []
        capabilities: dict[str, dict[str, Any]] = {}
        for profile in items:
            if not isinstance(profile, dict):
                continue
            if str(profile.get("agentType") or "EXECUTOR").upper() != "EXECUTOR":
                continue
            eligible = bool(
                profile.get("active")
                and profile.get("admissionStatus") == "APPROVED"
                and profile.get("chainStatus") == "READY"
                and profile.get("availability") == "ONLINE"
                and profile.get("capacityAvailable")
                and profile.get("gasReady")
                and profile.get("credentialStatus") == "ACTIVE"
            )
            if not eligible:
                continue
            try:
                quote = Decimal(str(profile.get("priceFloor")))
            except (InvalidOperation, TypeError, ValueError):
                continue
            profile_capabilities = profile.get("capabilities", [])
            if not isinstance(profile_capabilities, list):
                continue
            metadata = profile.get("metadata", {})
            verification = metadata.get("verification", {}) if isinstance(metadata, dict) else {}
            manifest = verification.get("manifest", {}) if isinstance(verification, dict) else {}
            manifest_capabilities = manifest.get("capabilities", []) if isinstance(manifest, dict) else []
            output_schemas = {
                str(item.get("capabilityId")): item.get("outputSchema")
                for item in manifest_capabilities
                if isinstance(item, dict) and item.get("capabilityId")
            } if isinstance(manifest_capabilities, list) else {}
            for capability in profile_capabilities:
                slug = str(capability).strip()
                if not slug:
                    continue
                entry = capabilities.setdefault(
                    slug,
                    {
                        "capability": slug,
                        "lowestQuote": str(quote),
                        "eligibleProviders": 0,
                        "liveProviders": 0,
                        "_outputContracts": [],
                    },
                )
                if quote < Decimal(str(entry["lowestQuote"])):
                    entry["lowestQuote"] = str(quote)
                entry["eligibleProviders"] += 1
                if profile.get("matchable"):
                    entry["liveProviders"] += 1
                    output_schema = output_schemas.get(slug)
                    if isinstance(output_schema, dict):
                        schema_required = output_schema.get("required", [])
                        fields = (
                            set(schema_required)
                            if isinstance(schema_required, list)
                            and all(isinstance(field, str) and field.strip() for field in schema_required)
                            else set()
                        )
                        additional = output_schema.get("additionalProperties", True)
                        entry["_outputContracts"].append({
                            "known": True,
                            "fields": fields,
                            "allowsAdditional": additional is not False,
                        })
                    else:
                        entry["_outputContracts"].append({
                            "known": False,
                            "fields": set(),
                            "allowsAdditional": False,
                        })
        for entry in capabilities.values():
            contracts = entry.pop("_outputContracts")
            known = [item for item in contracts if item["known"]]
            common_fields = set(known[0]["fields"]) if known else set()
            for contract in known[1:]:
                common_fields.intersection_update(contract["fields"])
            entry["commonOutputFields"] = sorted(common_fields)
            entry["outputContractsKnown"] = len(known)
            entry["outputContractsUnknown"] = len(contracts) - len(known)
            entry["allowsAdditionalOutputFields"] = bool(known) and all(
                item["allowsAdditional"] for item in known
            )
        return {
            "currency": "USDC",
            "availableCapabilities": sorted(capabilities.values(), key=lambda item: item["capability"]),
            "rules": [
                "Use an exact capability value from availableCapabilities.",
                "Set suggestedReward at or above lowestQuote when possible.",
                "Use only commonOutputFields as acceptanceCriteria.requiredFields.",
                "Express task-specific content requirements in acceptanceCriteria.rules.",
                "Core revalidates price, availability, route and capacity at confirmation time.",
            ],
        }

    async def create_workflow(self, payload: dict[str, Any]) -> dict[str, Any]:
        return await self._request("POST", "/api/v1/workflows", payload)

    async def coordinate(self, workflow_id: str, max_actions: int) -> dict[str, Any]:
        return await self._request(
            "POST",
            f"/api/v1/workflows/{workflow_id}/coordinate",
            {"maxActions": max_actions},
            timeout_seconds=self.execution_timeout,
        )

    async def workflow(self, workflow_id: str) -> dict[str, Any]:
        return await self._request("GET", f"/api/v1/workflows/{workflow_id}")


class ConversationStore:
    def __init__(self, path: Path | None) -> None:
        self.path = path
        self.lock = threading.Lock()
        self.items: dict[str, dict[str, Any]] = {}
        if path is not None and path.exists():
            loaded = json.loads(path.read_text(encoding="utf-8-sig"))
            if isinstance(loaded, dict):
                self.items = {str(key): value for key, value in loaded.items() if isinstance(value, dict)}

    def save(self, value: dict[str, Any]) -> None:
        with self.lock:
            self.items[str(value["conversationId"])] = value
            if self.path is None:
                return
            self.path.parent.mkdir(parents=True, exist_ok=True)
            temporary = self.path.with_suffix(self.path.suffix + ".tmp")
            temporary.write_text(
                json.dumps(self.items, ensure_ascii=False, sort_keys=True, separators=(",", ":")),
                encoding="utf-8",
            )
            temporary.replace(self.path)

    def get(self, conversation_id: str) -> dict[str, Any] | None:
        with self.lock:
            value = self.items.get(conversation_id)
            return json.loads(json.dumps(value)) if value is not None else None

    def find_by_workflow(self, workflow_id: str) -> dict[str, Any] | None:
        with self.lock:
            value = next(
                (item for item in self.items.values() if str(item.get("workflowId")) == workflow_id),
                None,
            )
            return json.loads(json.dumps(value)) if value is not None else None


def _workflow_nodes(plan: dict[str, Any]) -> list[dict[str, Any]]:
    allowed = {
        "nodeKey", "title", "description", "capability", "dependsOn", "acceptanceCriteria",
        "inputPayload", "reward", "riskLevel", "reviewPolicyVersion", "requiredApprovals",
        "challengePeriodSeconds", "samplingRateBps", "reviewBudget", "maxAttempts",
    }
    nodes = plan.get("nodes", [])
    if not isinstance(nodes, list):
        raise ValueError("Platform workflow plan has no node list")
    prepared: list[dict[str, Any]] = []
    for node in nodes:
        if not isinstance(node, dict):
            continue
        value = {key: child for key, child in node.items() if key in allowed}
        assessment = node.get("riskAssessment")
        if isinstance(assessment, dict) and assessment.get("assignmentMode") == "AUTO":
            for generated_key in ("riskLevel", "requiredApprovals", "challengePeriodSeconds", "samplingRateBps"):
                value.pop(generated_key, None)
        prepared.append(value)
    return prepared


def _plan_message(interpreted: dict[str, Any], plan: dict[str, Any]) -> str:
    validation = plan.get("validation", {})
    warnings = validation.get("warnings", []) if isinstance(validation, dict) else []
    explanation = str(interpreted.get("explanation", "")).strip()
    status_text = (
        f"Coordinator Core validated {len(plan.get('nodes', []))} workflow nodes"
        f" with {len(warnings)} warning(s)."
    )
    return f"{explanation} {status_text}".strip()


def _failure_advice(workflow: dict[str, Any], requested_node_key: str | None) -> dict[str, Any]:
    raw_nodes = workflow.get("nodes", [])
    nodes = [node for node in raw_nodes if isinstance(node, dict)] if isinstance(raw_nodes, list) else []
    selected = next(
        (node for node in nodes if node.get("nodeKey") == requested_node_key),
        None,
    ) if requested_node_key else next(
        (
            node for node in nodes
            if node.get("status") in {
                "FAILED", "RETRYING", "WAITING_FOR_AGENT", "WAITING_FOR_ROUTE_DEPLOYMENT",
                "REVIEW_PENDING", "SETTLEMENT_PENDING",
            }
        ),
        None,
    )
    if requested_node_key and selected is None:
        raise ValueError(f"Workflow has no node {requested_node_key}")
    if selected is None:
        return {
            "workflowId": workflow.get("id"),
            "recommendation": "NO_RECOVERY_REQUIRED",
            "reason": "No recoverable failed or waiting node is currently present.",
            "allowedActions": [],
            "advisoryOnly": True,
        }
    status_value = str(selected.get("status", "UNKNOWN"))
    incidents = selected.get("failureIncidents", [])
    last_incident = incidents[-1] if isinstance(incidents, list) and incidents else {}
    if status_value in {"RETRYING", "FAILED"}:
        recommendation = "CREATE_NEW_ATTEMPT_WITH_NEXT_MATCHED_AGENT"
        actions = ["RELEASE_FAILED_RESERVATION", "REMATCH_AGENT", "CREATE_NEW_ATTEMPT"]
    elif status_value == "WAITING_FOR_AGENT":
        recommendation = "WAIT_FOR_OR_ADMIT_A_MATCHABLE_AGENT"
        actions = ["REFRESH_MARKET", "ADMIT_AGENT", "RETRY_MATCHING"]
    elif status_value == "WAITING_FOR_ROUTE_DEPLOYMENT":
        recommendation = "COMPLETE_ROUTE_DEPLOYMENT_BEFORE_RETRY"
        actions = ["VERIFY_ROUTE", "COMPLETE_DEPLOYMENT", "RETRY_DISPATCH"]
    elif status_value == "SETTLEMENT_PENDING":
        recommendation = "RETRY_SETTLEMENT_WITH_CORE_VALIDATED_PARAMETERS"
        actions = ["CHECK_DESTINATION_VAULT", "UPDATE_SWAP_LIMITS", "RETRY_SETTLEMENT"]
    else:
        recommendation = "WAIT_FOR_REQUIRED_REVIEW_OR_CORE_TRANSITION"
        actions = ["CHECK_REVIEW_POLICY", "COLLECT_REQUIRED_APPROVALS"]
    return {
        "workflowId": workflow.get("id"),
        "nodeKey": selected.get("nodeKey"),
        "nodeStatus": status_value,
        "reasonCode": last_incident.get("reasonCode") if isinstance(last_incident, dict) else None,
        "recommendation": recommendation,
        "allowedActions": actions,
        "advisoryOnly": True,
    }


def _workflow_summary(workflow: dict[str, Any]) -> dict[str, Any]:
    raw_nodes = workflow.get("nodes", [])
    nodes = [node for node in raw_nodes if isinstance(node, dict)] if isinstance(raw_nodes, list) else []
    summaries = []
    final_output: Any = None
    for node in nodes:
        current_attempt = node.get("currentAttempt")
        if not isinstance(current_attempt, dict):
            attempts = node.get("attempts", [])
            current_attempt = attempts[-1] if isinstance(attempts, list) and attempts else {}
        output = (
            current_attempt.get("resultPayload", current_attempt.get("output"))
            if isinstance(current_attempt, dict)
            else None
        )
        if output is not None:
            final_output = output
        summaries.append(
            {
                "nodeKey": node.get("nodeKey"),
                "title": node.get("title"),
                "status": node.get("status"),
                "attemptId": current_attempt.get("id") if isinstance(current_attempt, dict) else None,
                "agentKey": current_attempt.get("agentKey") if isinstance(current_attempt, dict) else None,
                "output": output,
            }
        )
    return {
        "workflowId": workflow.get("id"),
        "referenceCode": workflow.get("referenceCode"),
        "status": workflow.get("status"),
        "completed": workflow.get("status") == "COMPLETED",
        "nodes": summaries,
        "finalOutput": final_output,
        "funds": {
            "budget": workflow.get("budget"),
            "paidAmount": workflow.get("paidAmount"),
            "platformFee": workflow.get("platformFee"),
            "refundedAmount": workflow.get("refundedAmount"),
        },
    }


def build_app(
    *,
    platform_client: PlatformClient | None = None,
    interpreter: TaskInterpreter | None = None,
    state_path: Path | None = None,
) -> FastAPI:
    provider_name = os.getenv("COORDINATOR_LLM_PROVIDER", os.getenv("LLM_PROVIDER", "mock")).lower()
    if interpreter is not None:
        selected_interpreter = interpreter
    elif provider_name == "mock":
        selected_interpreter = TaskInterpreter()
    elif provider_name == "deepseek":
        selected_interpreter = DeepSeekTaskInterpreter()
    elif provider_name in {"openai", "openai-compatible", "openai_compatible"}:
        selected_interpreter = OpenAICompatibleTaskInterpreter()
    else:
        raise ValueError(f"Unsupported COORDINATOR_LLM_PROVIDER: {provider_name}")
    selected_platform = platform_client or PlatformClient()
    configured_state_path = os.getenv("COORDINATOR_STATE_PATH", "").strip()
    store = ConversationStore(state_path if state_path is not None else (Path(configured_state_path) if configured_state_path else None))
    identity = load_coordinator_identity()
    agent_name = os.getenv("COORDINATOR_AGENT_NAME", "Workflow Coordinator Agent").strip()
    api_token = os.getenv("COORDINATOR_API_TOKEN", "")
    allow_insecure_local = os.getenv("COORDINATOR_ALLOW_INSECURE_LOCAL", "false").lower() in {"1", "true", "yes"}

    app = FastAPI(
        title="User-facing Coordinator Agent",
        version="1.0.0",
        description="Constrained task interpretation and workflow orchestration over the platform API.",
    )
    origins = [
        value.strip()
        for value in os.getenv(
            "COORDINATOR_CORS_ORIGINS", "http://127.0.0.1:3000,http://localhost:3000"
        ).split(",") if value.strip()
    ]
    app.add_middleware(
        CORSMiddleware,
        allow_origins=origins,
        allow_credentials=False,
        allow_methods=["GET", "POST"],
        allow_headers=["Authorization", "Content-Type"],
    )
    app.state.platform = selected_platform
    app.state.interpreter = selected_interpreter
    app.state.store = store
    app.state.identity = identity
    app.state.platform_registration = None
    app.state.heartbeat_task = None

    registration_path_value = os.getenv("COORDINATOR_PLATFORM_REGISTRATION_PATH", "").strip()
    registration_path = Path(registration_path_value) if registration_path_value else None

    def persist_platform_registration(value: dict[str, Any]) -> None:
        if registration_path is None:
            return
        registration_path.parent.mkdir(parents=True, exist_ok=True)
        temporary = registration_path.with_suffix(registration_path.suffix + ".tmp")
        temporary.write_text(json.dumps(value, indent=2), encoding="utf-8")
        temporary.replace(registration_path)

    async def send_platform_heartbeat(registration: dict[str, Any]) -> dict[str, Any]:
        platform_url = str(registration["platformUrl"]).rstrip("/")
        profile_id = str(registration["profileId"])
        heartbeat_state = {
            "status": registration["status"],
            "capacityAvailable": registration["capacityAvailable"],
            "endpointLatencyMs": registration["endpointLatencyMs"],
        }
        timeout = float(os.getenv("COORDINATOR_PLATFORM_TIMEOUT_SECONDS", "30"))
        async with httpx.AsyncClient(timeout=timeout, follow_redirects=False) as client:
            challenge_response = await client.post(
                f"{platform_url}/api/v1/agent-onboarding/registrations/{profile_id}/heartbeat-challenge",
                json=heartbeat_state,
            )
            challenge_response.raise_for_status()
            challenge = challenge_response.json()
            response = await client.post(
                f"{platform_url}/api/v1/agent-onboarding/registrations/{profile_id}/heartbeat",
                json={
                    "challengeId": challenge["challengeId"],
                    **heartbeat_state,
                    "signature": identity.sign_message(str(challenge["message"])),
                },
            )
            response.raise_for_status()
            payload = response.json()
        if not isinstance(payload, dict):
            raise ValueError("Platform heartbeat returned a non-object response")
        return payload

    async def heartbeat_loop() -> None:
        while app.state.platform_registration is not None:
            registration = app.state.platform_registration
            await asyncio.sleep(int(registration["heartbeatIntervalSeconds"]))
            registration = app.state.platform_registration
            if registration is None:
                return
            try:
                result = await send_platform_heartbeat(registration)
                registration["lastHeartbeatAt"] = utc_now()
                registration["lastHeartbeatResult"] = result
                registration["lastError"] = None
            except (httpx.HTTPError, KeyError, TypeError, ValueError) as exc:
                registration["lastError"] = str(exc)

    def start_heartbeat(registration: dict[str, Any]) -> None:
        previous = app.state.heartbeat_task
        if previous is not None:
            previous.cancel()
        app.state.platform_registration = registration
        persist_platform_registration({
            key: registration[key]
            for key in (
                "platformUrl", "profileId", "status", "capacityAvailable",
                "endpointLatencyMs", "heartbeatIntervalSeconds",
            )
        })
        app.state.heartbeat_task = asyncio.create_task(heartbeat_loop())

    def require_access(authorization: str | None = Header(default=None)) -> None:
        if not api_token:
            if allow_insecure_local and os.getenv("APP_ENV", "development").lower() in {"development", "dev", "test"}:
                return
            raise HTTPException(status_code=503, detail="COORDINATOR_API_TOKEN is required")
        expected = f"Bearer {api_token}"
        if authorization is None or not secrets.compare_digest(authorization, expected):
            raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Invalid Coordinator token")

    def conversation(conversation_id: str) -> dict[str, Any]:
        item = store.get(conversation_id)
        if item is None:
            raise HTTPException(status_code=404, detail="Conversation not found")
        return item

    @app.on_event("startup")
    async def restore_platform_registration() -> None:
        if registration_path is None or not registration_path.exists():
            return
        saved = json.loads(registration_path.read_text(encoding="utf-8-sig"))
        body = PlatformRegistrationRequest.model_validate(saved)
        registration = body.model_dump(by_alias=True)
        try:
            registration["lastHeartbeatResult"] = await send_platform_heartbeat(registration)
            registration["lastHeartbeatAt"] = utc_now()
            registration["lastError"] = None
        except (httpx.HTTPError, KeyError, TypeError, ValueError) as exc:
            registration["lastError"] = str(exc)
        start_heartbeat(registration)

    @app.on_event("shutdown")
    async def stop_platform_heartbeat() -> None:
        task = app.state.heartbeat_task
        if task is None:
            return
        task.cancel()
        await asyncio.gather(task, return_exceptions=True)

    async def prepare_draft(
        *,
        message: str,
        budget: str,
        platform_fee: str,
        currency: str,
        input_payload: dict[str, Any],
        current_draft: dict[str, Any] | None = None,
    ) -> tuple[dict[str, Any], dict[str, Any], dict[str, Any], dict[str, Any]]:
        market_context = await selected_platform.planning_context()
        interpreted = await selected_interpreter.interpret(
            message,
            input_payload,
            budget=budget,
            platform_fee=platform_fee,
            currency=currency,
            current_draft=current_draft,
            market_context=market_context,
        )
        interpreted = _validate_proposal_market_contract(interpreted, market_context)
        registration = app.state.platform_registration or {}
        coordinator_identity = {
            "agentType": "COORDINATOR",
            "agentKey": identity.agent_key,
            "did": identity.did,
            "profileId": registration.get("profileId"),
        }
        authoritative_input = {
            **interpreted["inputPayload"],
            "_coordinator": coordinator_identity,
        }
        plan = await selected_platform.plan(
            {
                "objective": interpreted["objective"],
                "budget": budget,
                "platformFee": platform_fee,
                "currency": currency,
                "inputPayload": authoritative_input,
                "proposedNodes": interpreted["nodes"],
                "proposalVersion": "LLM_DAG_PROPOSAL_V1",
            }
        )
        draft = {
            "title": interpreted["title"],
            "objective": interpreted["objective"],
            "budget": budget,
            "platformFee": platform_fee,
            "currency": currency,
            "sourceChainId": int(os.getenv("DYNAMIC_SOURCE_CHAIN_ID", "31337")),
            "inputPayload": authoritative_input,
            "nodes": _workflow_nodes(plan),
        }
        return interpreted, plan, draft, {**market_context, "coordinator": coordinator_identity}

    @app.get("/manifest")
    async def manifest(request: Request) -> dict[str, Any]:
        base_url = str(request.base_url).rstrip("/")
        return {
            "agentId": identity.agent_key,
            "agentType": "COORDINATOR",
            "did": identity.did,
            "name": agent_name,
            "description": "User-facing workflow planning Agent backed by Coordinator Core validation.",
            "version": "1.0.0",
            "status": "online",
            "serviceEndpoint": base_url,
            "verificationMethod": {
                "id": f"{identity.did}#operational-1",
                "type": "EcdsaSecp256k1VerificationKey2019",
                "controller": identity.did,
                "publicKeyHex": identity.public_key_hex,
            },
            "llm": {
                "provider": selected_interpreter.provider_name,
                "model": str(
                    getattr(selected_interpreter, "model", None)
                    or f"{selected_interpreter.provider_name}-default"
                ),
            },
            "capabilities": [
                {
                    "capabilityId": "workflow_coordination",
                    "name": "Workflow coordination",
                    "description": "Conversational task clarification and DAG proposal generation.",
                    "endpoint": f"{base_url}/chat",
                    "method": "POST",
                    "pricingModel": "fixed",
                    "basePrice": "0",
                    "currency": "USDC",
                    "estimatedDurationSeconds": 60,
                    "inputSchema": ChatRequest.model_json_schema(by_alias=True),
                    "outputSchema": {
                        "type": "object",
                        "required": ["conversationId", "status", "plan", "draft"],
                        "properties": {
                            "conversationId": {"type": "string"},
                            "status": {"type": "string"},
                            "plan": {"type": "object"},
                            "draft": {"type": "object"},
                        },
                        "additionalProperties": True,
                    },
                    "exampleRequest": {
                        "input": {
                            "message": "Create a research workflow with one verifiable output.",
                            "budget": "20.00",
                            "platformFee": "1.10",
                            "currency": "USDC",
                            "inputPayload": {"source": "platform-technical-check"},
                        }
                    },
                }
            ],
            "coordination": {
                "chatEndpoint": f"{base_url}/chat",
                "testPlanEndpoint": f"{base_url}/test-plan",
                "conversationEndpoint": f"{base_url}/conversations/{{conversationId}}",
                "coreValidated": True,
            },
        }

    @app.post("/identity-signatures", dependencies=[Depends(require_access)])
    async def sign_identity_message(body: IdentitySignRequest) -> dict[str, Any]:
        allowed_prefixes = (
            "Agent onboarding ownership challenge\n",
            "Agent heartbeat challenge\n",
        )
        if not body.message.startswith(allowed_prefixes):
            raise HTTPException(status_code=422, detail="Unsupported identity signing purpose")
        return {
            "agentId": identity.agent_key,
            "did": identity.did,
            "algorithm": "ES256K",
            "publicKeyHex": identity.public_key_hex,
            "signature": identity.sign_message(body.message),
        }

    @app.post("/platform-registration", dependencies=[Depends(require_access)])
    async def register_with_platform(body: PlatformRegistrationRequest) -> dict[str, Any]:
        registration = body.model_dump(by_alias=True)
        registration["platformUrl"] = body.platform_url.rstrip("/")
        try:
            first_heartbeat = await send_platform_heartbeat(registration)
        except (httpx.HTTPError, KeyError, TypeError, ValueError) as exc:
            raise HTTPException(status_code=422, detail=f"Platform heartbeat registration failed: {exc}") from exc
        registration["lastHeartbeatAt"] = utc_now()
        registration["lastHeartbeatResult"] = first_heartbeat
        registration["lastError"] = None
        start_heartbeat(registration)
        return {
            "registered": True,
            "agentId": identity.agent_key,
            "profileId": body.profile_id,
            "heartbeat": first_heartbeat,
        }

    @app.get("/platform-registration", dependencies=[Depends(require_access)])
    async def platform_registration_status() -> dict[str, Any]:
        registration = app.state.platform_registration
        if registration is None:
            raise HTTPException(status_code=404, detail="Coordinator Agent has no platform registration")
        return dict(registration)

    @app.post("/test-plan", dependencies=[Depends(require_access)])
    async def test_plan(body: ChatRequest) -> dict[str, Any]:
        try:
            _, plan, draft, _ = await prepare_draft(
                message=body.message,
                budget=body.budget,
                platform_fee=body.platform_fee,
                currency=body.currency,
                input_payload=body.input_payload,
            )
        except (httpx.HTTPError, ValueError, KeyError, TypeError) as exc:
            raise HTTPException(status_code=502, detail=str(exc)) from exc
        result = {
            "agentId": identity.agent_key,
            "agentType": "COORDINATOR",
            "did": identity.did,
            "proposalValid": bool(
                isinstance(plan.get("nodes"), list)
                and plan.get("nodes")
                and isinstance(draft.get("nodes"), list)
                and draft.get("nodes")
            ),
            "plan": plan,
            "draft": draft,
        }
        return {**result, "outputHash": canonical_hash(result)}

    @app.get("/health")
    async def health() -> dict[str, Any]:
        platform_status = "unreachable"
        try:
            result = await selected_platform.health()
            platform_status = str(result.get("status", "unknown"))
        except (httpx.HTTPError, ValueError):
            pass
        return {
            "status": "ok",
            "service": "agent-crosschain-platform-coordinator-agent",
            "agentId": identity.agent_key,
            "agentType": "COORDINATOR",
            "did": identity.did,
            "runtimeId": os.getenv("LOCAL_PLATFORM_RUNTIME_ID"),
            "provider": selected_interpreter.provider_name,
            "model": getattr(selected_interpreter, "model", None),
            "platformStatus": platform_status,
            "timestamp": utc_now(),
        }

    @app.post("/chat", dependencies=[Depends(require_access)])
    async def chat(body: ChatRequest) -> dict[str, Any]:
        try:
            interpreted, plan, draft, market_context = await prepare_draft(
                message=body.message,
                budget=body.budget,
                platform_fee=body.platform_fee,
                currency=body.currency,
                input_payload=body.input_payload,
            )
        except (httpx.HTTPError, ValueError, KeyError, TypeError) as exc:
            raise HTTPException(status_code=502, detail=str(exc)) from exc
        conversation_id = str(uuid.uuid4())
        assistant_message = _plan_message(interpreted, plan)
        item = {
            "conversationId": conversation_id,
            "status": "DRAFT",
            "provider": selected_interpreter.provider_name,
            "coordinator": market_context.get("coordinator"),
            "userMessage": body.message,
            "assistantMessage": assistant_message,
            "messages": [
                {"role": "user", "content": body.message, "createdAt": utc_now()},
                {"role": "assistant", "content": assistant_message, "createdAt": utc_now()},
            ],
            "interpretedTask": interpreted,
            "plan": plan,
            "marketContext": market_context,
            "draft": draft,
            "workflowId": None,
            "createdAt": utc_now(),
            "updatedAt": utc_now(),
        }
        store.save(item)
        return item

    @app.get("/conversations/{conversation_id}", dependencies=[Depends(require_access)])
    async def get_conversation(conversation_id: str) -> dict[str, Any]:
        return conversation(conversation_id)

    @app.post("/conversations/{conversation_id}/refine", dependencies=[Depends(require_access)])
    async def refine_conversation(conversation_id: str, body: RefineRequest) -> dict[str, Any]:
        item = conversation(conversation_id)
        if item.get("workflowId"):
            raise HTTPException(status_code=409, detail="A confirmed workflow cannot be replanned")
        existing_draft = item["draft"]
        budget = body.budget or str(existing_draft["budget"])
        platform_fee = body.platform_fee or str(existing_draft["platformFee"])
        currency = body.currency or str(existing_draft["currency"])
        input_payload = body.input_payload if body.input_payload is not None else existing_draft["inputPayload"]
        try:
            interpreted, plan, draft, market_context = await prepare_draft(
                message=body.message,
                budget=budget,
                platform_fee=platform_fee,
                currency=currency,
                input_payload=input_payload,
                current_draft=item["interpretedTask"],
            )
        except (httpx.HTTPError, ValueError, KeyError, TypeError) as exc:
            raise HTTPException(status_code=502, detail=str(exc)) from exc
        assistant_message = _plan_message(interpreted, plan)
        messages = item.get("messages", [])
        if not isinstance(messages, list):
            messages = []
        messages.extend(
            [
                {"role": "user", "content": body.message, "createdAt": utc_now()},
                {"role": "assistant", "content": assistant_message, "createdAt": utc_now()},
            ]
        )
        item.update(
            {
                "status": "DRAFT",
                "userMessage": body.message,
                "assistantMessage": assistant_message,
                "messages": messages,
                "interpretedTask": interpreted,
                "plan": plan,
                "marketContext": market_context,
                "coordinator": market_context.get("coordinator"),
                "draft": draft,
                "updatedAt": utc_now(),
            }
        )
        store.save(item)
        return item

    @app.post("/conversations/{conversation_id}/workflows", dependencies=[Depends(require_access)])
    async def create_workflow(conversation_id: str, body: CreateWorkflowRequest) -> dict[str, Any]:
        item = conversation(conversation_id)
        if item.get("workflowId"):
            return {"conversation": item, "workflow": await selected_platform.workflow(str(item["workflowId"]))}
        payload = {
            **item["draft"],
            "fundingMode": body.funding_mode,
            "payerAddress": body.payer_address,
        }
        try:
            workflow = await selected_platform.create_workflow(payload)
        except (httpx.HTTPError, ValueError) as exc:
            raise HTTPException(status_code=502, detail=str(exc)) from exc
        item["status"] = "WORKFLOW_CREATED"
        item["workflowId"] = workflow.get("id")
        item["updatedAt"] = utc_now()
        store.save(item)
        return {"conversation": item, "workflow": workflow}

    @app.post("/conversations/{conversation_id}/coordinate", dependencies=[Depends(require_access)])
    async def coordinate(conversation_id: str, body: CoordinateRequest) -> dict[str, Any]:
        item = conversation(conversation_id)
        workflow_id = item.get("workflowId")
        if not workflow_id:
            raise HTTPException(status_code=409, detail="Conversation has no confirmed workflow")
        try:
            return await selected_platform.coordinate(str(workflow_id), body.max_actions)
        except (httpx.HTTPError, ValueError) as exc:
            raise HTTPException(status_code=502, detail=str(exc)) from exc

    @app.post("/conversations/{conversation_id}/failure-advice", dependencies=[Depends(require_access)])
    async def failure_advice(conversation_id: str, body: FailureAdviceRequest) -> dict[str, Any]:
        item = conversation(conversation_id)
        workflow_id = item.get("workflowId")
        if not workflow_id:
            raise HTTPException(status_code=409, detail="Conversation has no confirmed workflow")
        try:
            workflow = await selected_platform.workflow(str(workflow_id))
            return _failure_advice(workflow, body.node_key)
        except (httpx.HTTPError, ValueError) as exc:
            raise HTTPException(status_code=502, detail=str(exc)) from exc

    @app.get("/conversations/{conversation_id}/summary", dependencies=[Depends(require_access)])
    async def workflow_summary(conversation_id: str) -> dict[str, Any]:
        item = conversation(conversation_id)
        workflow_id = item.get("workflowId")
        if not workflow_id:
            raise HTTPException(status_code=409, detail="Conversation has no confirmed workflow")
        try:
            workflow = await selected_platform.workflow(str(workflow_id))
            return _workflow_summary(workflow)
        except (httpx.HTTPError, ValueError) as exc:
            raise HTTPException(status_code=502, detail=str(exc)) from exc

    @app.get("/workflows/{workflow_id}/summary", dependencies=[Depends(require_access)])
    async def workflow_summary_by_id(workflow_id: str) -> dict[str, Any]:
        item = store.find_by_workflow(workflow_id)
        if item is None:
            raise HTTPException(status_code=404, detail="Coordinator conversation for workflow was not found")
        try:
            workflow = await selected_platform.workflow(workflow_id)
            return _workflow_summary(workflow)
        except (httpx.HTTPError, ValueError) as exc:
            raise HTTPException(status_code=502, detail=str(exc)) from exc

    return app


app = build_app()
