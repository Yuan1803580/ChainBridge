from __future__ import annotations

import asyncio
import json

import pytest

from app.providers import KimiProvider


class FakeResponse:
    def __init__(self, message: dict[str, object]) -> None:
        self.message = message

    def raise_for_status(self) -> None:
        return None

    def json(self) -> dict[str, object]:
        return {
            "model": "kimi-k2.6",
            "choices": [{"message": self.message}],
            "usage": {"prompt_tokens": 10, "completion_tokens": 5, "total_tokens": 15},
        }


class FakeAsyncClient:
    requests: list[dict[str, object]] = []
    response_message: dict[str, object] = {"role": "assistant", "content": "ok"}

    def __init__(self, *args, **kwargs) -> None:
        self.timeout = kwargs.get("timeout")

    async def __aenter__(self):
        return self

    async def __aexit__(self, exc_type, exc, traceback):
        return False

    async def post(self, url: str, *, json: dict[str, object], headers: dict[str, str]):
        self.__class__.requests.append({"url": url, "json": json, "headers": headers})
        return FakeResponse(self.__class__.response_message)


def test_kimi_provider_uses_required_model_settings(monkeypatch: pytest.MonkeyPatch) -> None:
    FakeAsyncClient.requests = []
    FakeAsyncClient.response_message = {"role": "assistant", "content": "project answer"}
    monkeypatch.setattr("app.providers.httpx.AsyncClient", FakeAsyncClient)
    provider = KimiProvider(api_key="test-secret", max_retries=0)

    result = asyncio.run(
        provider.complete_chat(
            messages=[{"role": "user", "content": "Explain the workflow"}]
        )
    )

    assert result["content"] == "project answer"
    request = FakeAsyncClient.requests[0]
    assert request["url"] == "https://api.moonshot.cn/v1/chat/completions"
    assert request["json"]["model"] == "kimi-k2.6"
    assert request["json"]["temperature"] == 1
    assert request["headers"]["Authorization"] == "Bearer test-secret"


def test_kimi_provider_returns_structured_task(monkeypatch: pytest.MonkeyPatch) -> None:
    FakeAsyncClient.requests = []
    FakeAsyncClient.response_message = {
        "role": "assistant",
        "content": json.dumps(
            {"summary": "Auditable result", "score": 90, "sources": ["docs/runbook.md"]}
        ),
    }
    monkeypatch.setattr("app.providers.httpx.AsyncClient", FakeAsyncClient)
    provider = KimiProvider(api_key="test-secret", max_retries=0)

    result = asyncio.run(
        provider.complete_json(
            system_prompt="Return JSON only.",
            task={"objective": "Review settlement"},
            output_schema={"type": "object"},
        )
    )

    assert result["score"] == 90
    assert FakeAsyncClient.requests[0]["json"]["response_format"] == {
        "type": "json_object"
    }


def test_kimi_provider_accepts_tool_call_only_response(monkeypatch: pytest.MonkeyPatch) -> None:
    FakeAsyncClient.requests = []
    FakeAsyncClient.response_message = {
        "role": "assistant",
        "content": "",
        "tool_calls": [
            {
                "id": "call-1",
                "type": "function",
                "function": {"name": "read_project_context", "arguments": '{"query":"settlement"}'},
            }
        ],
    }
    monkeypatch.setattr("app.providers.httpx.AsyncClient", FakeAsyncClient)
    provider = KimiProvider(api_key="test-secret", max_retries=0)
    tools = [
        {
            "type": "function",
            "function": {
                "name": "read_project_context",
                "description": "Search docs",
                "parameters": {"type": "object", "properties": {}},
            },
        }
    ]

    result = asyncio.run(
        provider.complete_chat(
            messages=[{"role": "user", "content": "Check settlement"}],
            tools=tools,
        )
    )

    assert result["content"] == ""
    assert result["toolCalls"][0]["function"]["name"] == "read_project_context"
    assert FakeAsyncClient.requests[0]["json"]["tools"] == tools
    assert FakeAsyncClient.requests[0]["json"]["tool_choice"] == "auto"
