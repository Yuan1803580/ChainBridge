from __future__ import annotations

import pytest
from pydantic import ValidationError

from app.workflow_schemas import (
    AttemptGasClosureConfirmation,
    AttemptGasDepositConfirmation,
    WorkflowFundingConfirmation,
)


TX_HASH = "0x" + "a" * 64
WALLET = "0x" + "b" * 40


def test_native_gas_confirmations_accept_evm_wei_precision() -> None:
    deposit = AttemptGasDepositConfirmation(transactionHash=TX_HASH, amount="0.001000000000000001")
    closure = AttemptGasClosureConfirmation(
        chargedAmount="0.000123456789012345",
        refundedAmount="0.000876543210987656",
        refundTransactionHash=TX_HASH,
    )
    funding = WorkflowFundingConfirmation(
        walletAddress=WALLET,
        lockTxHash=TX_HASH,
        sourceGasTxHash=TX_HASH,
        sourceGasAmount="0.010000000000000001",
    )

    assert deposit.amount == "0.001000000000000001"
    assert closure.charged_amount == "0.000123456789012345"
    assert closure.refunded_amount == "0.000876543210987656"
    assert funding.source_gas_amount == "0.010000000000000001"


def test_native_gas_confirmations_reject_more_than_evm_precision() -> None:
    with pytest.raises(ValidationError):
        AttemptGasClosureConfirmation(
            chargedAmount="0.0000000000000000001",
            refundedAmount="0",
            refundTransactionHash=TX_HASH,
        )
