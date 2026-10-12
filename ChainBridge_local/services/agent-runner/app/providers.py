from __future__ import annotations

import json
import os
import re
import asyncio
from dataclasses import dataclass
from typing import Any, Protocol

import httpx


class CodeReviewProvider(Protocol):
    @property
    def name(self) -> str: ...

    async def review(
        self, *, code: str, language: str, focus: str
    ) -> dict[str, object]: ...


class StructuredTaskProvider(Protocol):
    async def complete_json(
        self,
        *,
        system_prompt: str,
        task: dict[str, object],
        output_schema: dict[str, object],
    ) -> dict[str, object]: ...


class ConversationProvider(Protocol):
    @property
    def name(self) -> str: ...

    @property
    def model_name(self) -> str: ...

    async def complete_chat(
        self,
        *,
        messages: list[dict[str, Any]],
        tools: list[dict[str, object]] | None = None,
    ) -> dict[str, object]: ...


@dataclass(frozen=True)
class MockConversationProvider:
    """Deterministic conversation provider for local tests without API spend."""

    name: str = "mock"
    model_name: str = "mock-conversation"

    async def complete_chat(
        self,
        *,
        messages: list[dict[str, Any]],
        tools: list[dict[str, object]] | None = None,
    ) -> dict[str, object]:
        user_messages = [
            str(message.get("content", ""))
            for message in messages
            if message.get("role") == "user"
        ]
        latest = user_messages[-1] if user_messages else ""
        return {
            "content": f"Local mock response: {latest}",
            "model": self.model_name,
            "provider": self.name,
            "usage": {
                "prompt_tokens": 0,
                "completion_tokens": 0,
                "total_tokens": 0,
            },
        }


@dataclass(frozen=True)
class MockCodeReviewProvider:
    """Deterministic reviewer used in local development and automated tests."""

    name: str = "mock"

    async def review(
        self, *, code: str, language: str, focus: str
    ) -> dict[str, object]:
        findings: list[dict[str, str]] = []
        checks = (
            (r"\beval\s*\(", "high", "Avoid eval on untrusted input."),
            (r"\bexec\s*\(", "high", "Avoid dynamic code execution."),
            (r"except\s*:", "medium", "Catch a specific exception type."),
            (
                r"\b(TODO|FIXME)\b",
                "low",
                "Resolve the outstanding implementation marker.",
            ),
            (r"console\.log\s*\(", "low", "Remove debug logging before production."),
        )
        for pattern, severity, message in checks:
            if re.search(pattern, code, flags=re.IGNORECASE):
                findings.append(
                    {
                        "severity": severity,
                        "message": message,
                        "focus": focus,
                    }
                )

        if not code.strip():
            findings.append(
                {
                    "severity": "medium",
                    "message": "The submitted source is empty.",
                    "focus": focus,
                }
            )

        score = max(
            0,
            100
            - sum(
                {"high": 25, "medium": 12, "low": 4}[finding["severity"]]
                for finding in findings
            ),
        )
        summary = (
            "No deterministic issues found."
            if not findings
            else f"Found {len(findings)} issue(s) in the submitted {language} code."
        )
        return {
            "summary": summary,
            "score": score,
            "findings": findings,
            "provider": self.name,
        }


class ExternalCodeReviewProvider:
    """Integration boundary for an OpenAI-compatible or organization-specific provider."""

    name = "external"

    async def review(
        self, *, code: str, language: str, focus: str
    ) -> dict[str, object]:
        raise NotImplementedError(
            "Configure an application-specific ExternalCodeReviewProvider implementation"
        )


@dataclass(frozen=True)
class DeepSeekCodeReviewProvider:
    name: str = "deepseek"
    api_key: str = ""
    model: str = "deepseek-chat"
    base_url: str = "https://api.deepseek.com"
    timeout_seconds: float = 30.0
    max_retries: int = 2

    @classmethod
    def from_env(cls) -> "DeepSeekCodeReviewProvider":
        api_key = os.getenv("DEEPSEEK_API_KEY", "")
        if not api_key:
            raise ValueError("DEEPSEEK_API_KEY is required when LLM_PROVIDER=deepseek")
        return cls(
            api_key=api_key,
            model=os.getenv("DEEPSEEK_MODEL", os.getenv("LLM_MODEL", "deepseek-chat")),
            base_url=os.getenv("DEEPSEEK_BASE_URL", "https://api.deepseek.com").rstrip("/"),
            timeout_seconds=float(os.getenv("DEEPSEEK_TIMEOUT_SECONDS", "30")),
            max_retries=int(os.getenv("DEEPSEEK_MAX_RETRIES", "2")),
        )

    async def review(
        self, *, code: str, language: str, focus: str
    ) -> dict[str, object]:
        system = (
            "You are a code review Agent. Return strict JSON with keys "
            "summary, score, findings. findings must be an array of objects "
            "with severity low|medium|high, message, and focus."
        )
        user = {
            "language": language,
            "focus": focus,
            "code": code,
        }
        payload = {
            "model": self.model,
            "messages": [
                {"role": "system", "content": system},
                {"role": "user", "content": json.dumps(user, ensure_ascii=False)},
            ],
            "response_format": {"type": "json_object"},
            "temperature": 0,
        }
        headers = {"Authorization": f"Bearer {self.api_key}"}
        last_error: Exception | None = None
        async with httpx.AsyncClient(timeout=self.timeout_seconds) as client:
            for attempt in range(self.max_retries + 1):
                try:
                    response = await client.post(
                        f"{self.base_url}/chat/completions",
                        json=payload,
                        headers=headers,
                    )
                    response.raise_for_status()
                    raw = response.json()
                    content = raw["choices"][0]["message"]["content"]
                    parsed = json.loads(content)
                    findings = parsed.get("findings", [])
                    if not isinstance(findings, list):
                        findings = []
                    return {
                        "summary": str(parsed.get("summary", "DeepSeek review completed.")),
                        "score": int(parsed.get("score", 0)),
                        "findings": findings,
                        "provider": self.name,
                        "usage": raw.get("usage", {}),
                        "model": self.model,
                    }
                except (httpx.HTTPError, KeyError, ValueError, TypeError) as exc:
                    last_error = exc
                    if attempt >= self.max_retries:
                        break
                    await asyncio.sleep(0.5 * (attempt + 1))
        raise RuntimeError(f"DeepSeek review failed: {last_error}")


@dataclass(frozen=True)
class DeepSeekStructuredTaskProvider:
    api_key: str
    model: str = "deepseek-chat"
    base_url: str = "https://api.deepseek.com"
    timeout_seconds: float = 60.0
    max_retries: int = 2
    temperature: float = 0.0
    max_tokens: int = 4096

    @classmethod
    def from_env(cls) -> "DeepSeekStructuredTaskProvider":
        api_key = os.getenv("DEEPSEEK_API_KEY", "")
        if not api_key:
            raise ValueError("DEEPSEEK_API_KEY is required when LLM_PROVIDER=deepseek")
        return cls(
            api_key=api_key,
            model=os.getenv("DEEPSEEK_MODEL", os.getenv("LLM_MODEL", "deepseek-chat")),
            base_url=os.getenv("DEEPSEEK_BASE_URL", "https://api.deepseek.com").rstrip("/"),
            timeout_seconds=float(os.getenv("DEEPSEEK_TIMEOUT_SECONDS", "60")),
            max_retries=int(os.getenv("DEEPSEEK_MAX_RETRIES", "2")),
            temperature=float(os.getenv("DEEPSEEK_TEMPERATURE", "0")),
            max_tokens=int(os.getenv("DEEPSEEK_MAX_TOKENS", "4096")),
        )

    async def complete_json(
        self,
        *,
        system_prompt: str,
        task: dict[str, object],
        output_schema: dict[str, object],
    ) -> dict[str, object]:
        payload = {
            "model": self.model,
            "messages": [
                {"role": "system", "content": system_prompt},
                {
                    "role": "user",
                    "content": json.dumps(
                        {"task": task, "requiredOutputSchema": output_schema},
                        ensure_ascii=False,
                    ),
                },
            ],
            "response_format": {"type": "json_object"},
            "temperature": self.temperature,
            "max_tokens": self.max_tokens,
        }
        headers = {"Authorization": f"Bearer {self.api_key}"}
        last_error: Exception | None = None
        async with httpx.AsyncClient(timeout=self.timeout_seconds) as client:
            for attempt in range(self.max_retries + 1):
                try:
                    response = await client.post(
                        f"{self.base_url}/chat/completions",
                        json=payload,
                        headers=headers,
                    )
                    response.raise_for_status()
                    content = response.json()["choices"][0]["message"]["content"]
                    result = json.loads(content)
                    if not isinstance(result, dict):
                        raise ValueError("DeepSeek structured result must be an object")
                    return result
                except (httpx.HTTPError, KeyError, ValueError, TypeError) as exc:
                    last_error = exc
                    if attempt >= self.max_retries:
                        break
                    await asyncio.sleep(0.5 * (attempt + 1))
        raise RuntimeError(f"DeepSeek structured task failed: {last_error}")


@dataclass(frozen=True)
class KimiProvider:
    """Moonshot Kimi provider for structured workflow tasks and conversations."""

    api_key: str
    model: str = "kimi-k2.6"
    base_url: str = "https://api.moonshot.cn/v1"
    timeout_seconds: float = 300.0
    max_retries: int = 2
    max_tokens: int = 4096
    name: str = "kimi"

    @property
    def model_name(self) -> str:
        return self.model

    @classmethod
    def from_env(cls) -> "KimiProvider":
        api_key = os.getenv("KIMI_API_KEY", "").strip()
        if not api_key:
            raise ValueError("KIMI_API_KEY is required when LLM_PROVIDER=kimi")
        return cls(
            api_key=api_key,
            model=os.getenv("KIMI_MODEL", os.getenv("LLM_MODEL", "kimi-k2.6")),
            base_url=os.getenv("KIMI_BASE_URL", "https://api.moonshot.cn/v1").rstrip("/"),
            timeout_seconds=float(os.getenv("KIMI_TIMEOUT_SECONDS", "300")),
            max_retries=int(os.getenv("KIMI_MAX_RETRIES", "2")),
            max_tokens=int(os.getenv("KIMI_MAX_TOKENS", "4096")),
        )

    async def _complete(self, payload: dict[str, object]) -> dict[str, object]:
        headers = {
            "Authorization": f"Bearer {self.api_key}",
            "Content-Type": "application/json",
        }
        last_error: Exception | None = None
        async with httpx.AsyncClient(timeout=self.timeout_seconds) as client:
            for attempt in range(self.max_retries + 1):
                try:
                    response = await client.post(
                        f"{self.base_url}/chat/completions",
                        json=payload,
                        headers=headers,
                    )
                    response.raise_for_status()
                    raw = response.json()
                    message = raw["choices"][0]["message"]
                    if not isinstance(message, dict):
                        raise ValueError("Kimi returned an invalid assistant message")
                    content = message.get("content")
                    tool_calls = message.get("tool_calls")
                    if (not isinstance(content, str) or not content.strip()) and not tool_calls:
                        raise ValueError("Kimi returned an empty response")
                    return {
                        "content": content if isinstance(content, str) else "",
                        "message": message,
                        "toolCalls": tool_calls if isinstance(tool_calls, list) else [],
                        "usage": raw.get("usage", {}),
                        "model": str(raw.get("model", self.model)),
                        "provider": self.name,
                    }
                except (httpx.HTTPError, KeyError, ValueError, TypeError) as exc:
                    last_error = exc
                    if attempt >= self.max_retries:
                        break
                    await asyncio.sleep(0.5 * (attempt + 1))
        raise RuntimeError(f"Kimi request failed: {last_error}")

    async def complete_chat(
        self,
        *,
        messages: list[dict[str, Any]],
        tools: list[dict[str, object]] | None = None,
    ) -> dict[str, object]:
        payload: dict[str, object] = {
            "model": self.model,
            "messages": messages,
            # kimi-k2.6 currently requires temperature=1.
            "temperature": 1,
            "max_tokens": self.max_tokens,
        }
        if tools:
            payload["tools"] = tools
            payload["tool_choice"] = "auto"
        return await self._complete(payload)

    async def complete_json(
        self,
        *,
        system_prompt: str,
        task: dict[str, object],
        output_schema: dict[str, object],
    ) -> dict[str, object]:
        result = await self._complete(
            {
                "model": self.model,
                "messages": [
                    {
                        "role": "system",
                        "content": (
                            f"{system_prompt}\nReturn one JSON object matching the supplied "
                            "JSON Schema. Do not use Markdown fences."
                        ),
                    },
                    {
                        "role": "user",
                        "content": json.dumps(
                            {"task": task, "requiredOutputSchema": output_schema},
                            ensure_ascii=False,
                        ),
                    },
                ],
                "response_format": {"type": "json_object"},
                "temperature": 1,
                "max_tokens": self.max_tokens,
            }
        )
        content = str(result["content"]).strip()
        if content.startswith("```"):
            content = re.sub(r"^```(?:json)?\s*|\s*```$", "", content, flags=re.IGNORECASE)
        try:
            parsed = json.loads(content)
        except json.JSONDecodeError as exc:
            raise RuntimeError("Kimi structured task did not return valid JSON") from exc
        if not isinstance(parsed, dict):
            raise RuntimeError("Kimi structured result must be an object")
        return parsed
