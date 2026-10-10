from __future__ import annotations

from datetime import datetime, timedelta, timezone
from decimal import Decimal

from .conftest import auth_headers


def _agent_id(client, slug: str) -> str:
    identities = client.get("/api/v1/dev/identities").json()["items"]
    return next(item["id"] for item in identities if item["did"] == f"did:local:{slug}")


def _capability(client, slug: str) -> dict:
    capabilities = client.get("/api/v1/capabilities").json()["items"]
    return next(item for item in capabilities if item["slug"] == slug)


def _confirm_direct_order(client, order_id: str, capability: dict) -> dict:
    provider_slug = capability["provider"]["slug"]
    response = client.post(
        f"/api/v1/orders/{order_id}/confirm",
        headers=auth_headers(client, provider_slug, f"confirm-{order_id}"),
        json={"note": "Terms accepted"},
    )
    assert response.status_code == 200, response.text
    assert response.json()["status"] == "awaiting_funding"
    return response.json()


def _new_demand(client) -> dict:
    response = client.post(
        "/api/v1/demands",
        headers=auth_headers(client, "campus-requester", "demand-key-0001"),
        json={
            "title": "测试竞标需求",
            "description": "需要一个可验证的摘要服务完成测试任务。",
            "inputPayload": {"text": "long input"},
            "acceptanceCriteria": "输出摘要且提供内容哈希。",
            "budget": "8.50",
            "currency": "USDC",
            "status": "published",
        },
    )
    assert response.status_code == 201, response.text
    return response.json()


def test_multi_provider_bidding_winner_selection_and_idempotency(client):
    demand = _new_demand(client)
    first_headers = auth_headers(client, "text-summary", "bid-key-text-01")
    first_payload = {
        "amount": "6.25",
        "currency": "USDC",
        "proposal": "提供摘要和哈希。",
        "estimatedDurationSeconds": 120,
    }
    first = client.post(f"/api/v1/demands/{demand['id']}/bids", headers=first_headers, json=first_payload)
    assert first.status_code == 201
    replay = client.post(f"/api/v1/demands/{demand['id']}/bids", headers=first_headers, json=first_payload)
    assert replay.status_code == 201
    assert replay.json()["id"] == first.json()["id"]

    second = client.post(
        f"/api/v1/demands/{demand['id']}/bids",
        headers=auth_headers(client, "data-statistics", "bid-key-stats-1"),
        json={
            "amount": "7.00",
            "currency": "USDC",
            "proposal": "快速处理并交付。",
            "estimatedDurationSeconds": 90,
        },
    )
    assert second.status_code == 201

    accepted = client.post(
        f"/api/v1/bids/{first.json()['id']}/accept",
        headers=auth_headers(client, "campus-requester", "accept-bid-key-1"),
    )
    assert accepted.status_code == 200, accepted.text
    assert accepted.json()["status"] == "awaiting_funding"
    demand_detail = client.get(f"/api/v1/demands/{demand['id']}").json()
    statuses = {bid["id"]: bid["status"] for bid in demand_detail["bids"]}
    assert statuses[first.json()["id"]] == "accepted"
    assert statuses[second.json()["id"]] == "rejected"

    late_bid = client.post(
        f"/api/v1/demands/{demand['id']}/bids",
        headers=auth_headers(client, "hash-audit", "late-bid-key-01"),
        json={
            "amount": "5.00",
            "currency": "USDC",
            "proposal": "已经太迟的报价。",
            "estimatedDurationSeconds": 60,
        },
    )
    assert late_bid.status_code == 409


def test_direct_order_schema_validation_and_complete_flow(client):
    capability = _capability(client, "text-summary-v1")
    requester_headers = auth_headers(client, "campus-requester", "direct-order-key-1")
    invalid = client.post(
        f"/api/v1/capabilities/{capability['id']}/orders",
        headers=requester_headers,
        json={"inputPayload": {"text": "missing required fields"}},
    )
    assert invalid.status_code == 422

    direct = client.post(
        f"/api/v1/capabilities/{capability['id']}/orders",
        headers=requester_headers,
        json={
            "inputPayload": {"text": "Agent 市场测试文本", "maxLength": 30, "language": "zh-CN"},
            "payerAccount": "eip155:421614:0xRequester",
            "payeeAccount": "eip155:421614:0xProvider",
            "assetId": "eip155:421614/erc20:0xUSDC",
            "sourceChain": "eip155:421614",
            "destinationChain": "eip155:421614",
        },
    )
    assert direct.status_code == 201, direct.text
    order = direct.json()
    assert order["amount"] == "0.25"
    assert order["status"] == "awaiting_confirmation"
    assert order["settlement"]["status"] == "created"

    order_id = order["id"]
    _confirm_direct_order(client, order_id, capability)
    quote = client.post(
        f"/api/v1/orders/{order_id}/settlement-quote",
        headers=auth_headers(client, "campus-requester", "quote-order-key01"),
        json={
            "sourceAsset": "ETH-like",
            "settlementToken": "USDC",
            "sourceAmount": "0.000125",
            "maxSlippageBps": 100,
            "sourceDecimals": 18,
            "settlementDecimals": 6,
        },
    )
    assert quote.status_code == 200, quote.text
    quote_body = quote.json()
    assert quote_body["quotedSettlementAmount"] == "0.25"
    assert quote_body["minSettlementAmount"] == "0.2475"
    assert quote_body["maxSlippageBps"] == 100
    assert quote_body["quoteHash"].startswith("0x")
    assert quote_body["termsHash"].startswith("0x")

    funded = client.post(
        f"/api/v1/orders/{order_id}/fund",
        headers=auth_headers(client, "campus-requester", "fund-order-key01"),
        json={
            "payerAccount": "eip155:11155111:0xRequester",
            "payeeAccount": "eip155:421614:0xProvider",
            "assetId": "eip155:11155111/erc20:0xMockUSDC",
            "sourceChain": "eip155:11155111",
            "destinationChain": "eip155:421614",
            "quotedSettlementAmount": quote_body["quotedSettlementAmount"],
            "minSettlementAmount": quote_body["minSettlementAmount"],
            "maxSlippageBps": quote_body["maxSlippageBps"],
            "quoteDeadline": quote_body["quoteDeadline"],
            "quoteHash": quote_body["quoteHash"],
        },
    )
    assert funded.status_code == 200
    assert funded.json()["status"] == "funded"
    assert funded.json()["settlement"]["status"] == "locked"
    assert funded.json()["settlement"]["transactionHash"].startswith("0x")
    assert funded.json()["termsSnapshot"]["settlementQuote"]["quoteHash"] == quote_body["quoteHash"]

    started = client.post(
        f"/api/v1/orders/{order_id}/start",
        headers=auth_headers(client, "text-summary", "start-order-key1"),
    )
    assert started.status_code == 200
    assert started.json()["status"] == "in_progress"

    delivery = client.post(
        f"/api/v1/orders/{order_id}/deliver",
        headers=auth_headers(client, "text-summary", "deliver-key-0001"),
        json={
            "artifactUri": "s3://deliveries/demo-result.json",
            "contentHash": "sha256:0123456789abcdef0123456789abcdef",
            "summary": "摘要已完成。",
            "outputPayload": {"summary": "Agent 市场测试文本"},
        },
    )
    assert delivery.status_code == 200
    assert delivery.json()["status"] == "delivered"

    accepted = client.post(
        f"/api/v1/orders/{order_id}/accept",
        headers=auth_headers(client, "campus-requester", "accept-order-key"),
    )
    assert accepted.status_code == 200
    assert accepted.json()["status"] == "completed"
    assert accepted.json()["settlement"]["status"] == "released"
    assert accepted.json()["settlement"]["ccipMessageId"].startswith("0x")
    assert [event["toStatus"] for event in accepted.json()["events"]][-2:] == ["accepted", "completed"]

    review = client.post(
        f"/api/v1/orders/{order_id}/reviews",
        headers=auth_headers(client, "campus-requester", "review-key-0001"),
        json={"rating": 5, "comment": "交付符合要求。"},
    )
    assert review.status_code == 201
    assert review.json()["rating"] == 5


def test_quote_protected_funding_rejects_expired_and_underfunded_quotes(client):
    capability = _capability(client, "data-statistics-v1")
    created = client.post(
        f"/api/v1/capabilities/{capability['id']}/orders",
        headers=auth_headers(client, "campus-requester", "quote-fail-order"),
        json={"inputPayload": {"numbers": [1, 2, 3], "metrics": ["count"]}},
    )
    assert created.status_code == 201, created.text
    order_id = created.json()["id"]
    _confirm_direct_order(client, order_id, capability)

    quote = client.post(
        f"/api/v1/orders/{order_id}/settlement-quote",
        headers=auth_headers(client, "campus-requester", "quote-fail-quote"),
        json={
            "sourceAsset": "ETH-like",
            "settlementToken": "USDC",
            "sourceAmount": "0.000125",
            "maxSlippageBps": 100,
            "sourceDecimals": 18,
            "settlementDecimals": 6,
        },
    )
    assert quote.status_code == 200, quote.text
    quote_body = quote.json()

    expired = client.post(
        f"/api/v1/orders/{order_id}/fund",
        headers=auth_headers(client, "campus-requester", "quote-expired-fund"),
        json={
            "quotedSettlementAmount": quote_body["quotedSettlementAmount"],
            "minSettlementAmount": quote_body["minSettlementAmount"],
            "maxSlippageBps": quote_body["maxSlippageBps"],
            "quoteDeadline": (datetime.now(timezone.utc) - timedelta(seconds=1)).isoformat(),
            "quoteHash": quote_body["quoteHash"],
        },
    )
    assert expired.status_code == 409

    high_quote = client.post(
        f"/api/v1/orders/{order_id}/settlement-quote",
        headers=auth_headers(client, "campus-requester", "quote-high-quote"),
        json={
            "sourceAsset": "ETH-like",
            "settlementToken": "USDC",
            "sourceAmount": "0.0002",
            "maxSlippageBps": 100,
            "sourceDecimals": 18,
            "settlementDecimals": 6,
        },
    )
    assert high_quote.status_code == 200, high_quote.text
    high_quote_body = high_quote.json()
    assert high_quote_body["minSettlementAmount"] == "0.396"

    underfunded = client.post(
        f"/api/v1/orders/{order_id}/fund",
        headers=auth_headers(client, "campus-requester", "quote-underfunded-fund"),
        json={
            "quotedSettlementAmount": high_quote_body["quotedSettlementAmount"],
            "minSettlementAmount": high_quote_body["minSettlementAmount"],
            "maxSlippageBps": high_quote_body["maxSlippageBps"],
            "quoteDeadline": high_quote_body["quoteDeadline"],
            "quoteHash": high_quote_body["quoteHash"],
        },
    )
    assert underfunded.status_code == 409


def test_authorization_illegal_transitions_and_refund(client):
    capability = _capability(client, "hash-audit-v1")
    created = client.post(
        f"/api/v1/capabilities/{capability['id']}/orders",
        headers=auth_headers(client, "campus-requester", "refund-direct-01"),
        json={"inputPayload": {"content": "artifact", "algorithm": "sha256"}},
    ).json()
    order_id = created["id"]
    _confirm_direct_order(client, order_id, capability)

    unauthorized = client.post(
        f"/api/v1/orders/{order_id}/fund",
        headers=auth_headers(client, "text-summary", "bad-fund-key-001"),
        json={},
    )
    assert unauthorized.status_code == 403

    early_start = client.post(
        f"/api/v1/orders/{order_id}/start",
        headers=auth_headers(client, "hash-audit", "early-start-key1"),
    )
    assert early_start.status_code == 409

    assert client.post(
        f"/api/v1/orders/{order_id}/fund",
        headers=auth_headers(client, "campus-requester", "refund-fund-key1"),
        json={},
    ).status_code == 200
    disputed = client.post(
        f"/api/v1/orders/{order_id}/dispute",
        headers=auth_headers(client, "campus-requester", "dispute-key-001"),
        json={"reason": "交付环境无法满足要求。"},
    )
    assert disputed.status_code == 200
    assert disputed.json()["status"] == "disputed"

    provider_refund = client.post(
        f"/api/v1/orders/{order_id}/refund",
        headers=auth_headers(client, "hash-audit", "bad-refund-key1"),
        json={"reason": "provider cannot arbitrate"},
    )
    assert provider_refund.status_code == 403

    refunded = client.post(
        f"/api/v1/orders/{order_id}/refund",
        headers=auth_headers(client, "platform-admin", "admin-refund-key"),
        json={"reason": "仲裁确认退款。"},
    )
    assert refunded.status_code == 200
    assert refunded.json()["status"] == "refunded"
    assert refunded.json()["settlement"]["status"] == "refunded"


def test_platform_arbitration_can_release_part_of_budget(client):
    capability = _capability(client, "hash-audit-v1")
    created = client.post(
        f"/api/v1/capabilities/{capability['id']}/orders",
        headers=auth_headers(client, "campus-requester", "arbitration-create"),
        json={"inputPayload": {"content": "artifact", "algorithm": "sha256"}},
    ).json()
    order_id = created["id"]
    _confirm_direct_order(client, order_id, capability)
    assert client.post(
        f"/api/v1/orders/{order_id}/fund",
        headers=auth_headers(client, "campus-requester", "arbitration-fund"),
        json={},
    ).status_code == 200
    assert client.post(
        f"/api/v1/orders/{order_id}/dispute",
        headers=auth_headers(client, "hash-audit", "arbitration-dispute"),
        json={"reason": "Provider requests payment for partial work"},
    ).status_code == 200

    budget = Decimal(created["amount"])
    payable = budget / Decimal("2")
    resolved = client.post(
        f"/api/v1/orders/{order_id}/resolve",
        headers=auth_headers(client, "platform-admin", "arbitration-resolve"),
        json={
            "outcome": "release",
            "payableAmount": format(payable, "f"),
            "rationale": "Evidence supports a half-budget award",
            "evidenceHashes": ["sha256:arbitration-evidence"],
        },
    )
    assert resolved.status_code == 200, resolved.text
    body = resolved.json()
    assert body["status"] == "completed"
    assert body["paymentDecision"]["decisionType"] == "arbitration_release"
    assert Decimal(body["paymentDecision"]["payableAmount"]) == payable
    assert Decimal(body["paymentDecision"]["refundAmount"]) == budget - payable


def test_demo_capability_invocation_and_money_types(client):
    capability = _capability(client, "data-statistics-v1")
    assert isinstance(capability["basePrice"], str)
    response = client.post(
        f"/api/v1/capabilities/{capability['id']}/invoke",
        headers=auth_headers(client, "campus-requester", "invoke-key-0001"),
        json={"inputPayload": {"numbers": [1, 2, 3, 4], "metrics": ["count", "mean", "median", "max"]}},
    )
    assert response.status_code == 200
    assert response.json()["output"] == {"count": 4, "mean": 2.5, "median": 2.5, "max": 4}
