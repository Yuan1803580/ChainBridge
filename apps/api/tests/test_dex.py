from __future__ import annotations

import json
import httpx
import pytest
from fastapi.testclient import TestClient
from sqlalchemy import select
from types import SimpleNamespace

from app.config import Settings
from app.dex import DexQuoteError, EvmRpcVerifier, SwapVerificationError, ZeroExClient
from app.main import create_app
from app.models import Order, PaymentDecision, Settlement

from .conftest import auth_headers


USDC = "0x1111111111111111111111111111111111111111"
STC = "0x2222222222222222222222222222222222222222"
TARGET = "0x3333333333333333333333333333333333333333"
WALLET = "0x4444444444444444444444444444444444444444"
PAYEE = "0x5555555555555555555555555555555555555555"
SWAP_HASH = "0x" + "a" * 64


class AcceptingSwapVerifier:
    def verify_swap(self, quote, transaction_hash: str) -> None:
        assert quote.taker.lower() == WALLET.lower()
        assert transaction_hash == SWAP_HASH


class AcceptingPayoutVerifier:
    def verify_swap(self, quote, transaction_hash: str) -> None:
        assert quote.taker.lower() == PAYEE.lower()
        assert quote.sell_token.lower() == USDC.lower()
        assert quote.buy_token.lower() == STC.lower()
        assert transaction_hash == SWAP_HASH


def _client(handler) -> TestClient:
    settings = Settings(
        app_env="test",
        app_secret="dex-test-secret-that-is-long-enough",
        database_url="sqlite://",
        cors_origins=("http://localhost:3000",),
        settlement_mode="external_ccip",
        source_usdc_address=USDC,
        destination_usdc_address=USDC,
        destination_stc_address=STC,
        zero_ex_api_key="test-0x-key",
        zero_ex_allowed_targets=(TARGET,),
        zero_ex_allowed_allowance_targets=(TARGET,),
        zero_ex_allowed_selectors=("0xabcdef01",),
    )
    application = create_app(settings)
    application.state.dex_client = ZeroExClient(settings, transport=httpx.MockTransport(handler))
    application.state.source_swap_verifier = AcceptingSwapVerifier()
    return TestClient(application)


def _create_confirmed_order(client: TestClient) -> str:
    capability = next(
        item for item in client.get("/api/v1/capabilities").json()["items"]
        if item["slug"] == "text-summary-v1"
    )
    order = client.post(
        f"/api/v1/capabilities/{capability['id']}/orders",
        headers=auth_headers(client, "campus-requester", "dex-create"),
        json={
            "inputPayload": {"text": "quote task", "maxLength": 20, "language": "en"},
            "budgetMax": "0.25",
            "sourceChain": "eip155:11155111",
            "destinationChain": "eip155:421614",
            "payerAccount": WALLET,
            "payeeAccount": PAYEE,
            "assetId": USDC,
        },
    ).json()
    assert client.post(
        f"/api/v1/orders/{order['id']}/confirm",
        headers=auth_headers(client, "text-summary", "dex-confirm"),
        json={},
    ).status_code == 200
    return order["id"]


def test_funding_quote_uses_exact_usdc_budget_and_returns_unsigned_transaction():
    def handler(request: httpx.Request) -> httpx.Response:
        assert request.url.params["chainId"] == "11155111"
        assert request.url.params["buyAmount"] == "250000"
        assert request.url.params["recipient"].lower() == WALLET.lower()
        return httpx.Response(
            200,
            json={
                "liquidityAvailable": True,
                "sellAmount": "125000000000000",
                "buyAmount": "250000",
                "minBuyAmount": "250000",
                "transaction": {"to": TARGET, "data": "0xabcdef01", "value": "125000000000000", "gas": "150000"},
            },
        )

    with _client(handler) as client:
        order_id = _create_confirmed_order(client)
        response = client.post(
            f"/api/v1/orders/{order_id}/dex-quotes",
            headers=auth_headers(client, "campus-requester"),
            json={"side": "funding", "walletAddress": WALLET, "maxSlippageBps": 100},
        )
        assert response.status_code == 201, response.text
        quote = response.json()
        assert quote["buyAmount"] == "250000"
        assert quote["minBuyAmount"] == "250000"
        assert quote["transaction"]["to"] == TARGET
        assert quote["allowanceTarget"] is None
        assert quote["quoteHash"].startswith("0x")
        assert quote["executionHash"].startswith("0x")
        assert quote["maxSellAmount"] == "125000000000000"

        funded = client.post(
            f"/api/v1/orders/{order_id}/fund",
            headers=auth_headers(client, "campus-requester", "verified-funding"),
            json={
                "payerAccount": WALLET,
                "payeeAccount": PAYEE,
                "assetId": USDC,
                "sourceChain": "eip155:11155111",
                "destinationChain": "eip155:421614",
                "quoteId": quote["quoteId"],
                "swapTransactionHash": SWAP_HASH,
            },
        )
        assert funded.status_code == 200, funded.text
        assert funded.json()["settlement"]["status"] == "lock_pending"
        protected = funded.json()["termsSnapshot"]["settlementQuote"]
        assert protected["quoteHash"] == quote["quoteHash"]
        assert protected["swapTransactionHash"] == SWAP_HASH


def test_dex_quote_fails_closed_for_no_liquidity_and_mock_quote_is_disabled():
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(400, json={"reason": "NO_OPTIMAL_PATH"})

    with _client(handler) as client:
        order_id = _create_confirmed_order(client)
        quote = client.post(
            f"/api/v1/orders/{order_id}/dex-quotes",
            headers=auth_headers(client, "campus-requester"),
            json={"side": "funding", "walletAddress": WALLET},
        )
        assert quote.status_code == 409
        assert "no_liquidity" in quote.json()["error"]["message"]

        legacy = client.post(
            f"/api/v1/orders/{order_id}/settlement-quote",
            headers=auth_headers(client, "campus-requester"),
            json={
                "sourceAsset": "ETH",
                "settlementToken": "USDC",
                "sourceAmount": "1",
                "maxSlippageBps": 100,
                "sourceDecimals": 18,
                "settlementDecimals": 6,
            },
        )
        assert legacy.status_code == 409


def test_zero_ex_rejects_unapproved_target():
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200,
            json={
                "sellAmount": "1",
                "buyAmount": "250000",
                "minBuyAmount": "250000",
                "transaction": {
                    "to": "0x9999999999999999999999999999999999999999",
                    "data": "0x12345678",
                    "value": "1",
                    "gas": "1",
                },
            },
        )

    with _client(handler) as client:
        order_id = _create_confirmed_order(client)
        response = client.post(
            f"/api/v1/orders/{order_id}/dex-quotes",
            headers=auth_headers(client, "campus-requester"),
            json={"side": "funding", "walletAddress": WALLET},
        )
        assert response.status_code == 502
        assert "unapproved" in response.json()["error"]["message"]


def test_zero_ex_rejects_unapproved_allowance_target_and_route_metadata():
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200,
            json={
                "chainId": 1,
                "taker": WALLET,
                "recipient": WALLET,
                "sellToken": USDC,
                "buyToken": STC,
                "sellAmount": "200000",
                "buyAmount": "2000000000000000000",
                "minBuyAmount": "1980000000000000000",
                "allowanceTarget": "0x9999999999999999999999999999999999999999",
                "transaction": {"to": TARGET, "data": "0xabcdef01", "value": "0", "gas": "1"},
            },
        )

    settings = Settings(
        app_env="test",
        app_secret="dex-route-test-secret-that-is-long-enough",
        database_url="sqlite://",
        settlement_mode="external_ccip",
        zero_ex_api_key="test-0x-key",
        zero_ex_allowed_targets=(TARGET,),
        zero_ex_allowed_allowance_targets=(TARGET,),
        zero_ex_allowed_selectors=("0xabcdef01",),
    )
    client = ZeroExClient(settings, transport=httpx.MockTransport(handler))
    with pytest.raises(DexQuoteError, match="allowance target|chainId"):
        client.quote(
            chain_id=421614,
            taker=WALLET,
            sell_token=USDC,
            buy_token=STC,
            slippage_bps=100,
            sell_amount="200000",
        )


def test_rpc_verifier_rejects_tampered_calldata():
    def handler(request: httpx.Request) -> httpx.Response:
        method = request.read().decode()
        if "eth_chainId" in method:
            return httpx.Response(200, json={"jsonrpc": "2.0", "id": 1, "result": "0xaa36a7"})
        if "eth_getTransactionByHash" in method:
            return httpx.Response(200, json={
                "jsonrpc": "2.0",
                "id": 1,
                "result": {
                    "from": WALLET,
                    "to": TARGET,
                    "input": "0x99999999",
                    "value": "0x1",
                },
            })
        return httpx.Response(200, json={"jsonrpc": "2.0", "id": 1, "result": {"status": "0x1"}})

    quote = SimpleNamespace(
        taker=WALLET,
        buy_token=USDC,
        min_buy_amount="250000",
        transaction={"to": TARGET, "data": "0x12345678", "value": "1"},
        max_sell_amount="1",
    )
    verifier = EvmRpcVerifier("https://rpc.invalid", 11155111, httpx.MockTransport(handler))
    with pytest.raises(SwapVerificationError, match="calldata"):
        verifier.verify_swap(quote, SWAP_HASH)


def test_rpc_verifier_requires_transfer_log_proving_minimum_output():
    recipient_topic = "0x" + WALLET.removeprefix("0x").rjust(64, "0")

    def transport(output: int):
        def handler(request: httpx.Request) -> httpx.Response:
            method = json.loads(request.read())["method"]
            if method == "eth_chainId":
                result = "0xaa36a7"
            elif method == "eth_getTransactionByHash":
                result = {"from": WALLET, "to": TARGET, "input": "0x12345678", "value": "0x1"}
            else:
                result = {
                    "status": "0x1",
                    "logs": [{
                        "address": USDC,
                        "topics": [
                            "0xddf252ad1be2c89b69c2b068fc378daa952ba7f163c4a11628f55a4df523b3ef",
                            "0x" + "0" * 64,
                            recipient_topic,
                        ],
                        "data": hex(output),
                    }],
                }
            return httpx.Response(200, json={"jsonrpc": "2.0", "id": 1, "result": result})

        return httpx.MockTransport(handler)

    quote = SimpleNamespace(
        taker=WALLET,
        buy_token=USDC,
        min_buy_amount="250000",
        transaction={"to": TARGET, "data": "0x12345678", "value": "1"},
        max_sell_amount="1",
    )
    EvmRpcVerifier("https://rpc.invalid", 11155111, transport(250000)).verify_swap(quote, SWAP_HASH)
    with pytest.raises(SwapVerificationError, match="minimum token output"):
        EvmRpcVerifier("https://rpc.invalid", 11155111, transport(249999)).verify_swap(quote, SWAP_HASH)


def test_payout_quote_is_consumed_once_after_verified_destination_swap():
    def handler(request: httpx.Request) -> httpx.Response:
        assert request.url.params["chainId"] == "421614"
        assert request.url.params["sellToken"].lower() == USDC.lower()
        assert request.url.params["buyToken"].lower() == STC.lower()
        assert request.url.params["recipient"].lower() == PAYEE.lower()
        return httpx.Response(
            200,
            json={
                "liquidityAvailable": True,
                "sellAmount": "200000",
                "buyAmount": "2000000000000000000",
                "minBuyAmount": "1980000000000000000",
                "allowanceTarget": TARGET,
                "transaction": {"to": TARGET, "data": "0xabcdef01", "value": "0", "gas": "180000"},
            },
        )

    with _client(handler) as client:
        order_id = _create_confirmed_order(client)
        with client.app.state.session_factory() as session:
            order = session.get(Order, order_id)
            assert order is not None and order.settlement_id is not None and order.provider_id is not None
            settlement = session.get(Settlement, order.settlement_id)
            assert settlement is not None
            order.status = "completed"
            settlement.status = "released"
            session.add(PaymentDecision(
                order_id=order.id,
                settlement_id=settlement.id,
                decided_by_id=order.requester_id,
                decision_type="accept",
                payable_amount="0.20",
                refund_amount="0.05",
                decision_hash="0x" + "d" * 64,
            ))
            session.commit()
        client.app.state.destination_swap_verifier = AcceptingPayoutVerifier()

        quoted = client.post(
            f"/api/v1/orders/{order_id}/dex-quotes",
            headers=auth_headers(client, "text-summary"),
            json={"side": "payout", "walletAddress": PAYEE, "sellAmount": "0.20"},
        )
        assert quoted.status_code == 201, quoted.text
        quote_id = quoted.json()["quoteId"]
        path = f"/api/v1/orders/{order_id}/dex-quotes/{quote_id}/executions"
        first = client.post(
            path,
            headers=auth_headers(client, "text-summary"),
            json={"transactionHash": SWAP_HASH},
        )
        assert first.status_code == 200, first.text
        assert first.json()["status"] == "consumed"
        assert first.json()["swapTransactionHash"] == SWAP_HASH
        assert client.post(
            path,
            headers=auth_headers(client, "text-summary"),
            json={"transactionHash": SWAP_HASH},
        ).status_code == 409
