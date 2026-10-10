from __future__ import annotations

import json
import time
import uuid
from datetime import timedelta

from fastapi.testclient import TestClient
from sqlalchemy import select

from app.config import Settings
from app.integration_auth import listener_signature
from app.main import create_app
from app.models import DexQuote, SettlementChainEvent, utcnow

from .conftest import auth_headers

USDC = "0x1111111111111111111111111111111111111111"
PAYER = "0x2222222222222222222222222222222222222222"
PAYEE = "0x3333333333333333333333333333333333333333"
SOURCE_SELECTOR = "16015286601757825753"
DESTINATION_SELECTOR = "3478487238524512106"


def _external_client() -> TestClient:
    return TestClient(
        create_app(
            Settings(
                app_env="test",
                app_secret="external-ccip-test-secret",
                database_url="sqlite://",
                cors_origins=("http://localhost:3000",),
                settlement_mode="external_ccip",
                source_usdc_address=USDC,
                destination_usdc_address=USDC,
                source_chain_selector=SOURCE_SELECTOR,
                destination_chain_selector=DESTINATION_SELECTOR,
                listener_hmac_secret="external-listener-test-secret",
                listener_hmac_keys=(
                    ("test-worker", "external-listener-test-secret"),
                    ("second-worker", "second-listener-test-secret"),
                ),
            )
        )
    )


def _hash(character: str) -> str:
    return "0x" + character * 64


def _signed_post(
    client: TestClient,
    path: str,
    payload: dict,
    listener_id: str = "test-worker",
):
    body = json.dumps(payload, separators=(",", ":")).encode()
    timestamp = str(int(time.time()))
    nonce = str(uuid.uuid4())
    configured = dict(client.app.state.settings.listener_hmac_keys)
    secret = configured.get(listener_id, client.app.state.settings.listener_hmac_secret)
    return client.post(
        path,
        content=body,
        headers={
            "Content-Type": "application/json",
            "X-Listener-Id": listener_id,
            "X-Listener-Timestamp": timestamp,
            "X-Listener-Nonce": nonce,
            "X-Listener-Signature": listener_signature(
                secret, "POST", timestamp, nonce, path, body
            ),
        },
    )


def test_external_ccip_flow_waits_for_source_confirmation_and_refunds_remainder():
    with _external_client() as client:
        capability = next(
            item for item in client.get("/api/v1/capabilities").json()["items"]
            if item["slug"] == "text-summary-v1"
        )
        created = client.post(
            f"/api/v1/capabilities/{capability['id']}/orders",
            headers=auth_headers(client, "campus-requester", "ccip-create"),
            json={
                "inputPayload": {"text": "cross-chain task", "maxLength": 20, "language": "en"},
                "acceptanceCriteria": ["summary is at most 20 characters"],
                "budgetMax": "0.25",
                "sourceChain": "eip155:11155111",
                "destinationChain": "eip155:421614",
                "sourceAsset": "ETH",
                "settlementToken": "USDC",
                "destinationLocalAsset": "STC",
                "payerAccount": PAYER,
                "payeeAccount": PAYEE,
                "assetId": USDC,
            },
        ).json()
        order_id = created["id"]
        onchain_settlement_id = created["settlement"]["onchainSettlementId"]

        assert client.post(
            f"/api/v1/orders/{order_id}/confirm",
            headers=auth_headers(client, "text-summary", "ccip-confirm"),
            json={"note": "accepted"},
        ).status_code == 200
        with client.app.state.session_factory() as session:
            quote = DexQuote(
                order_id=order_id,
                side="funding",
                chain_id=11155111,
                taker=PAYER,
                sell_token="0xeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeee",
                buy_token=USDC,
                sell_amount="100000000000000",
                max_sell_amount="100000000000000",
                buy_amount="250000",
                min_buy_amount="250000",
                max_slippage_bps=100,
                transaction={"to": PAYEE, "data": "0x12345678", "value": "100000000000000", "gas": "100000"},
                quote_hash=_hash("7"),
                execution_hash=_hash("8"),
                expires_at=utcnow() + timedelta(minutes=5),
            )
            session.add(quote)
            session.commit()
            quote_id = quote.id
        client.app.state.source_swap_verifier = type(
            "Verifier", (), {"verify_swap": lambda self, quote, transaction_hash: None}
        )()
        funding = client.post(
            f"/api/v1/orders/{order_id}/fund",
            headers=auth_headers(client, "campus-requester", "ccip-fund"),
            json={
                "payerAccount": PAYER,
                "payeeAccount": PAYEE,
                "quoteId": quote_id,
                "swapTransactionHash": _hash("9"),
            },
        )
        assert funding.status_code == 200
        assert funding.json()["settlement"]["status"] == "lock_pending"

        locked = _signed_post(
            client,
            "/api/v1/integration/settlement-events",
            {
                "eventId": "11155111:" + _hash("1") + ":0",
                "eventType": "funds_locked",
                "onchainSettlementId": onchain_settlement_id,
                "transactionHash": _hash("1"),
                "amount": "0.25",
                "tokenAddress": USDC,
                "chainSelector": SOURCE_SELECTOR,
                "blockNumber": 10,
                "blockHash": _hash("b"),
                "confirmations": 3,
            },
        )
        assert locked.status_code == 200, locked.text
        assert locked.json()["order"]["status"] == "funded"

        assert client.post(
            f"/api/v1/orders/{order_id}/start",
            headers=auth_headers(client, "text-summary", "ccip-start"),
        ).status_code == 200
        assert client.post(
            f"/api/v1/orders/{order_id}/deliver",
            headers=auth_headers(client, "text-summary", "ccip-deliver"),
            json={
                "artifactUri": "s3://deliveries/cross-chain.json",
                "contentHash": "sha256:0123456789abcdef0123456789abcdef",
                "summary": "delivered off-chain",
            },
        ).status_code == 200
        accepted = client.post(
            f"/api/v1/orders/{order_id}/accept",
            headers=auth_headers(client, "campus-requester", "ccip-accept"),
            json={
                "payableAmount": "0.20",
                "verificationScore": 92,
                "verificationSummary": "meets the signed acceptance criteria",
            },
        )
        assert accepted.status_code == 200, accepted.text
        assert accepted.json()["settlement"]["status"] == "release_pending"

        release_job = _signed_post(
            client,
            "/api/v1/integration/relayer-tasks/lease",
            {"workerId": "test-worker", "leaseSeconds": 300},
        ).json()["task"]
        assert release_job["taskType"] == "release"
        assert release_job["payableAmount"] == "0.2"
        competing_lease = _signed_post(
            client,
            "/api/v1/integration/relayer-tasks/lease",
            {"workerId": "second-worker", "leaseSeconds": 300},
            listener_id="second-worker",
        )
        assert competing_lease.status_code == 200
        assert competing_lease.json()["task"] is None
        heartbeat_path = f"/api/v1/integration/relayer-tasks/{release_job['id']}/heartbeat"
        heartbeat = _signed_post(
            client,
            heartbeat_path,
            {
                "workerId": "test-worker",
                "leaseToken": release_job["leaseToken"],
                "leaseSeconds": 300,
            },
        )
        assert heartbeat.status_code == 200, heartbeat.text
        stale_heartbeat = _signed_post(
            client,
            heartbeat_path,
            {
                "workerId": "test-worker",
                "leaseToken": str(uuid.uuid4()),
                "leaseSeconds": 300,
            },
        )
        assert stale_heartbeat.status_code == 409
        wrong_owner_heartbeat = _signed_post(
            client,
            heartbeat_path,
            {
                "workerId": "second-worker",
                "leaseToken": release_job["leaseToken"],
                "leaseSeconds": 300,
            },
            listener_id="second-worker",
        )
        assert wrong_owner_heartbeat.status_code == 409

        message_id = _hash("a")
        submitted = _signed_post(
            client,
            "/api/v1/integration/settlement-events",
            {
                "eventId": "11155111:" + _hash("2") + ":0",
                "eventType": "release_submitted",
                "onchainSettlementId": onchain_settlement_id,
                "transactionHash": _hash("2"),
                "ccipMessageId": message_id,
                "amount": "0.20",
                "chainSelector": SOURCE_SELECTOR,
            },
        )
        assert submitted.status_code == 200, submitted.text

        with client.app.state.session_factory() as session:
            release_event = session.scalar(
                select(SettlementChainEvent).where(SettlementChainEvent.event_type == "release_submitted")
            )
            assert release_event is not None
            release_event.created_at = utcnow() - timedelta(seconds=120)
            session.commit()
        stalled = _signed_post(
            client,
            "/api/v1/integration/stalled-settlements/check",
            {"thresholdSeconds": 60},
        )
        assert stalled.status_code == 200, stalled.text
        assert stalled.json()["count"] == 1

        wrong_amount = _signed_post(
            client,
            "/api/v1/integration/settlement-events",
            {
                "eventId": "421614:" + _hash("3") + ":0",
                "eventType": "settlement_received",
                "onchainSettlementId": onchain_settlement_id,
                "transactionHash": _hash("3"),
                "ccipMessageId": message_id,
                "amount": "0.19",
            },
        )
        assert wrong_amount.status_code == 422

        received_payload = {
            "eventId": "421614:" + _hash("4") + ":0",
            "eventType": "settlement_received",
            "onchainSettlementId": onchain_settlement_id,
            "transactionHash": _hash("4"),
            "ccipMessageId": message_id,
            "amount": "0.20",
            "tokenAddress": USDC,
            "chainSelector": DESTINATION_SELECTOR,
            "blockNumber": 20,
            "blockHash": _hash("c"),
            "confirmations": 5,
        }
        received = _signed_post(client, "/api/v1/integration/settlement-events", received_payload)
        assert received.status_code == 200, received.text
        assert received.json()["order"]["status"] == "accepted"
        assert received.json()["order"]["settlement"]["status"] == "release_pending"

        confirm_job = _signed_post(
            client,
            "/api/v1/integration/relayer-tasks/lease",
            {"workerId": "test-worker", "leaseSeconds": 300},
        ).json()["task"]
        assert confirm_job["taskType"] == "confirm_release"
        assert confirm_job["ccipMessageId"] == message_id

        confirmed_release = _signed_post(
            client,
            "/api/v1/integration/settlement-events",
            {
                "eventId": "11155111:" + _hash("6") + ":0",
                "eventType": "release_confirmed",
                "onchainSettlementId": onchain_settlement_id,
                "transactionHash": _hash("6"),
                "ccipMessageId": message_id,
                "chainSelector": SOURCE_SELECTOR,
                "blockNumber": 30,
                "blockHash": _hash("d"),
                "confirmations": 3,
            },
        )
        assert confirmed_release.status_code == 200, confirmed_release.text
        assert confirmed_release.json()["order"]["status"] == "completed"
        assert confirmed_release.json()["order"]["settlement"]["status"] == "released"

        replay = _signed_post(
            client,
            "/api/v1/integration/settlement-events",
            {**received_payload, "confirmations": 10},
        )
        assert replay.status_code == 200
        assert replay.json()["duplicate"] is True
        assert replay.json()["event"]["confirmations"] == 10
        conflicting_replay = _signed_post(
            client,
            "/api/v1/integration/settlement-events",
            {**received_payload, "transactionHash": _hash("5")},
        )
        assert conflicting_replay.status_code == 409


def test_listener_rejects_missing_or_expired_signature():
    with _external_client() as client:
        path = "/api/v1/integration/relayer-tasks/lease"
        assert client.post(path, json={"workerId": "test-worker"}).status_code == 401
        body = b'{"workerId":"test-worker"}'
        timestamp = str(int(time.time()) - 1000)
        nonce = str(uuid.uuid4())
        response = client.post(
            path,
            content=body,
            headers={
                "Content-Type": "application/json",
                "X-Listener-Id": "test-worker",
                "X-Listener-Timestamp": timestamp,
                "X-Listener-Nonce": nonce,
                "X-Listener-Signature": listener_signature(
                    client.app.state.settings.listener_hmac_secret, "POST", timestamp, nonce, path, body
                ),
            },
        )
        assert response.status_code == 401

        valid_body = b'{"workerId":"test-worker","leaseSeconds":300}'
        valid_timestamp = str(int(time.time()))
        replay_nonce = str(uuid.uuid4())
        headers = {
            "Content-Type": "application/json",
            "X-Listener-Id": "test-worker",
            "X-Listener-Timestamp": valid_timestamp,
            "X-Listener-Nonce": replay_nonce,
            "X-Listener-Signature": listener_signature(
                client.app.state.settings.listener_hmac_secret,
                "POST",
                valid_timestamp,
                replay_nonce,
                path,
                valid_body,
            ),
        }
        assert client.post(path, content=valid_body, headers=headers).status_code == 200
        assert client.post(path, content=valid_body, headers=headers).status_code == 409

        unknown_nonce = str(uuid.uuid4())
        unknown_headers = {
            "Content-Type": "application/json",
            "X-Listener-Id": "unknown-worker",
            "X-Listener-Timestamp": valid_timestamp,
            "X-Listener-Nonce": unknown_nonce,
            "X-Listener-Signature": listener_signature(
                client.app.state.settings.listener_hmac_secret,
                "POST",
                valid_timestamp,
                unknown_nonce,
                path,
                valid_body,
            ),
        }
        assert client.post(path, content=valid_body, headers=unknown_headers).status_code == 401
