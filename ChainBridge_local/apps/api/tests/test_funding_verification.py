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
APPROVE_TX = "0x" + "99" * 32
LOCK_TX = "0x" + "aa" * 32
WORKFLOW_ID = "0x" + "44" * 32
TERMS_HASH = "0x" + "55" * 32
DEPLOYMENT_ID = "0x" + "77" * 32
APPROVE_SELECTOR = "0x095ea7b3"
LOCK_SELECTOR = "0x12345678"
APPROVAL_TOPIC = "0x" + "88" * 32
LOCK_TOPIC = "0x" + "66" * 32


def _word(value: int | str) -> str:
    if isinstance(value, int):
        return f"{value:064x}"
    return value.lower().removeprefix("0x").rjust(64, "0")


def _fixture_transaction() -> tuple[Workflow, WorkflowFundingConfirmation, dict, dict, dict, dict]:
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
        termsHash=TERMS_HASH,
        approveTxHash=APPROVE_TX,
        lockTxHash=LOCK_TX,
        sourceGasAmount="0",
        deploymentId=DEPLOYMENT_ID,
    )
    approval = {
        "from": PAYER,
        "to": TOKEN,
        "value": "0x0",
        "input": APPROVE_SELECTOR + _word(ESCROW) + _word(budget),
    }
    approval_receipt = {
        "status": "0x1",
        "blockNumber": "0x10",
        "logs": [{
            "address": TOKEN,
            "topics": [APPROVAL_TOPIC, "0x" + _word(PAYER), "0x" + _word(ESCROW)],
            "data": "0x" + _word(budget),
        }],
    }
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
        "blockNumber": "0x10",
        "logs": [{
            "address": ESCROW,
            "topics": [LOCK_TOPIC, WORKFLOW_ID, "0x" + _word(PAYER), "0x" + _word(TOKEN)],
            "data": "0x" + _word(budget) + _word(fee),
        }],
    }
    return workflow, body, transaction, receipt, approval, approval_receipt


def _install_rpc(
    monkeypatch,
    transaction: dict,
    receipt: dict,
    approval: dict,
    approval_receipt: dict,
) -> None:
    def rpc_call(_rpc_url: str, method: str, params: list):
        if method == "eth_getTransactionReceipt":
            return approval_receipt if params[0] == APPROVE_TX else receipt
        if method == "eth_getTransactionByHash":
            return approval if params[0] == APPROVE_TX else transaction
        if method == "eth_call":
            return hex(6)
        if method == "eth_blockNumber":
            return "0x10"
        raise AssertionError(f"unexpected RPC method {method}")

    monkeypatch.setattr(workflow_api, "_rpc_call", rpc_call)
    monkeypatch.setattr(
        workflow_api,
        "_local_protocol_manifest",
        lambda: {
            "deploymentId": DEPLOYMENT_ID,
            "contracts": {"taskEscrow": ESCROW, "sourceToken": TOKEN, "gasTreasury": "0x" + "77" * 20},
        },
    )
    monkeypatch.setattr(
        workflow_api,
        "_function_selector",
        lambda _rpc, signature: APPROVE_SELECTOR if signature.startswith("approve") else LOCK_SELECTOR,
    )
    monkeypatch.setattr(
        workflow_api,
        "_event_topic",
        lambda _rpc, signature: APPROVAL_TOPIC if signature.startswith("Approval") else LOCK_TOPIC,
    )


def test_accepts_only_the_exact_three_part_funding_evidence(monkeypatch):
    workflow, body, transaction, receipt, approval, approval_receipt = _fixture_transaction()
    _install_rpc(monkeypatch, transaction, receipt, approval, approval_receipt)
    workflow_api._verify_lock_transaction(workflow, body)


@pytest.mark.parametrize("tampering", ["target", "selector", "amount", "token", "event", "approval"])
def test_rejects_successful_but_unrelated_or_tampered_transactions(monkeypatch, tampering):
    workflow, body, transaction, receipt, approval, approval_receipt = _fixture_transaction()
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
    elif tampering == "approval":
        approval["input"] = APPROVE_SELECTOR + _word(ESCROW) + _word(10_000_001)
    _install_rpc(monkeypatch, transaction, receipt, approval, approval_receipt)

    with pytest.raises(HTTPException) as error:
        workflow_api._verify_lock_transaction(workflow, body)
    assert error.value.status_code == 422


def test_rejects_stale_deployment(monkeypatch):
    workflow, body, transaction, receipt, approval, approval_receipt = _fixture_transaction()
    body.deployment_id = "old-deployment"
    _install_rpc(monkeypatch, transaction, receipt, approval, approval_receipt)

    with pytest.raises(HTTPException) as error:
        workflow_api._verify_lock_transaction(workflow, body)
    assert error.value.status_code == 409
