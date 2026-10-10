from __future__ import annotations

from decimal import Decimal

import pytest
from fastapi import HTTPException

from app import workflow_api
from app.workflow_models import Workflow
from app.workflow_schemas import WorkflowFundingConfirmation


ESCROW = "0x" + "11" * 20
TOKEN = "0x" + "22" * 20
PAYER = "0x" + "33" * 20
LOCK_TX = "0x" + "aa" * 32
WORKFLOW_ID = "0x" + "44" * 32
TERMS_HASH = "0x" + "55" * 32
LOCK_SELECTOR = "0x12345678"
LOCK_TOPIC = "0x" + "66" * 32


def _word(value: int | str) -> str:
    if isinstance(value, int):
        return f"{value:064x}"
    return value.lower().removeprefix("0x").rjust(64, "0")


def _fixture_transaction() -> tuple[Workflow, WorkflowFundingConfirmation, dict, dict]:
    budget = 10_000_000
    fee = 1_100_000
    workflow = Workflow(
        id="workflow-test",
        reference_code="WF-TEST",
        requester_id="requester-test",
        title="Funding verification",
        objective="Verify the exact on-chain funding transaction.",
        budget=Decimal("10"),
        platform_fee=Decimal("1.1"),
        terms_hash=TERMS_HASH,
        onchain_workflow_id=WORKFLOW_ID,
    )
    body = WorkflowFundingConfirmation(
        walletAddress=PAYER,
        lockTxHash=LOCK_TX,
        sourceGasAmount="0",
    )
    transaction = {
        "from": PAYER,
        "to": ESCROW,
        "value": "0x0",
        "input": LOCK_SELECTOR
        + _word(WORKFLOW_ID)
        + _word(TERMS_HASH)
        + _word(TOKEN)
        + _word(budget)
        + _word(fee),
    }
    receipt = {
        "status": "0x1",
        "logs": [{
            "address": ESCROW,
            "topics": [LOCK_TOPIC, WORKFLOW_ID, "0x" + _word(PAYER), "0x" + _word(TOKEN)],
            "data": "0x" + _word(budget) + _word(fee),
        }],
    }
    return workflow, body, transaction, receipt


def _install_rpc(monkeypatch, transaction: dict, receipt: dict) -> None:
    def rpc_call(_rpc_url: str, method: str, _params: list):
        if method == "eth_getTransactionReceipt":
            return receipt
        if method == "eth_getTransactionByHash":
            return transaction
        if method == "eth_call":
            return hex(6)
        raise AssertionError(f"unexpected RPC method {method}")

    monkeypatch.setattr(workflow_api, "_rpc_call", rpc_call)
    monkeypatch.setattr(
        workflow_api,
        "_local_protocol_contracts",
        lambda: {"taskEscrow": ESCROW, "sourceToken": TOKEN, "gasTreasury": "0x" + "77" * 20},
    )
    monkeypatch.setattr(workflow_api, "_function_selector", lambda *_args: LOCK_SELECTOR)
    monkeypatch.setattr(workflow_api, "_event_topic", lambda *_args: LOCK_TOPIC)


def test_accepts_only_the_exact_lock_budget_transaction(monkeypatch):
    workflow, body, transaction, receipt = _fixture_transaction()
    _install_rpc(monkeypatch, transaction, receipt)
    workflow_api._verify_lock_transaction(workflow, body)


def test_workflow_funding_config_exposes_only_public_chain_metadata(client, monkeypatch):
    monkeypatch.setattr(
        workflow_api,
        "_local_protocol_manifest",
        lambda: {
            "chainId": 31337,
            "contracts": {
                "taskEscrow": ESCROW,
                "sourceToken": TOKEN,
                "gasTreasury": "0x" + "77" * 20,
                "coordinatorReview": "0x" + "88" * 20,
            },
        },
    )

    response = client.get("/api/v1/workflow-funding-config")

    assert response.status_code == 200, response.text
    assert response.json() == {
        "profile": "local-demo",
        "chainId": 31337,
        "chainName": "Chain A (local)",
        "assetLabel": "MockUSDC",
        "localTestnet": True,
        "defaultSourceGasAmount": "0.01",
        "contracts": {
            "sourceToken": TOKEN,
            "taskEscrow": ESCROW,
            "gasTreasury": "0x" + "77" * 20,
        },
    }


@pytest.mark.parametrize("tampering", ["target", "selector", "amount", "token", "event"])
def test_rejects_successful_but_unrelated_or_tampered_transactions(monkeypatch, tampering):
    workflow, body, transaction, receipt = _fixture_transaction()
    if tampering == "target":
        transaction["to"] = "0x" + "88" * 20
    elif tampering == "selector":
        transaction["input"] = "0xdeadbeef" + transaction["input"][10:]
    elif tampering == "amount":
        transaction["input"] = transaction["input"][:-64] + _word(1_099_999)
    elif tampering == "token":
        calldata = transaction["input"]
        transaction["input"] = calldata[: 10 + 2 * 64] + _word("0x" + "99" * 20) + calldata[10 + 3 * 64 :]
    elif tampering == "event":
        receipt["logs"][0]["address"] = "0x" + "aa" * 20
    _install_rpc(monkeypatch, transaction, receipt)

    with pytest.raises(HTTPException) as error:
        workflow_api._verify_lock_transaction(workflow, body)
    assert error.value.status_code == 422
