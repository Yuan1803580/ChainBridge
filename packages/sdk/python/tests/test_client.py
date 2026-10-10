from __future__ import annotations

import json

import httpx
import pytest

from agent_market_sdk import MarketplaceAPIError, MarketplaceClient


def test_login_signs_challenge_and_stores_token() -> None:
    requests: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        assert request.headers.get("Idempotency-Key")
        if request.url.path.endswith("/auth/challenges"):
            return httpx.Response(
                200, json={"challengeId": "ch-1", "message": "sign me"}
            )
        assert json.loads(request.content)["signature"] == "signed:sign me"
        return httpx.Response(200, json={"accessToken": "access-1", "expiresIn": 900})

    with MarketplaceClient(
        "http://market.test", transport=httpx.MockTransport(handler)
    ) as client:
        result = client.login("did:agent:test", lambda message: f"signed:{message}")
        assert result["accessToken"] == "access-1"
        assert client.token == "access-1"
    assert [request.url.path for request in requests] == [
        "/api/v1/auth/challenges",
        "/api/v1/auth/verify",
    ]


def test_invoke_uses_camel_case_and_caller_idempotency_key() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        assert request.headers["Authorization"] == "Bearer token"
        assert request.headers["Idempotency-Key"] == "stable-retry-key"
        assert json.loads(request.content) == {
            "input": {"text": "hello"},
            "requestId": "req-1",
        }
        return httpx.Response(
            200, json={"requestId": "req-1", "output": {"summary": "hello"}}
        )

    with MarketplaceClient(
        "http://market.test",
        token="token",
        transport=httpx.MockTransport(handler),
    ) as client:
        client.invoke_capability(
            "cap-1",
            {"text": "hello"},
            request_id="req-1",
            idempotency_key="stable-retry-key",
        )


@pytest.mark.parametrize(
    ("call", "path"),
    [
        (
            lambda client: client.create_demand({"title": "Need review"}),
            "/api/v1/demands",
        ),
        (
            lambda client: client.create_bid("d-1", {"amount": "1.25"}),
            "/api/v1/demands/d-1/bids",
        ),
        (lambda client: client.accept_bid("b-1"), "/api/v1/bids/b-1/accept"),
        (lambda client: client.fund_order("o-1"), "/api/v1/orders/o-1/fund"),
        (
            lambda client: client.deliver_order("o-1", {"contentHash": "sha256:1"}),
            "/api/v1/orders/o-1/deliver",
        ),
        (
            lambda client: client.review_order("o-1", {"score": 5}),
            "/api/v1/orders/o-1/reviews",
        ),
    ],
)
def test_resource_methods_match_contract(call, path) -> None:
    captured: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        captured.append(request)
        return httpx.Response(200, json={"id": "result-1"})

    with MarketplaceClient(
        "http://market.test", transport=httpx.MockTransport(handler)
    ) as client:
        call(client)
    assert captured[0].url.path == path
    assert captured[0].headers.get("Idempotency-Key")


def test_api_errors_have_machine_readable_context() -> None:
    transport = httpx.MockTransport(
        lambda request: httpx.Response(
            409,
            json={
                "detail": {
                    "code": "illegal_transition",
                    "message": "Cannot start order",
                }
            },
        )
    )
    with MarketplaceClient("http://market.test", transport=transport) as client:
        with pytest.raises(MarketplaceAPIError) as error:
            client.start_order("o-1")
    assert error.value.status_code == 409
    assert error.value.code == "illegal_transition"
