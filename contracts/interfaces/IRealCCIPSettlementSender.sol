// SPDX-License-Identifier: MIT
pragma solidity ^0.8.28;

interface IRealCCIPSettlementSender {
    struct SettlementPayload {
        bytes32 settlementId;
        bytes32 orderId;
        address payer;
        address payee;
        uint256 amount;
    }

    function quoteFee(
        uint64 destinationChainSelector,
        address receiver,
        address token,
        uint256 amount
    ) external view returns (uint256 fee);

    function sendSettlementFromEscrowPayNative(
        uint64 destinationChainSelector,
        address receiver,
        address token,
        SettlementPayload calldata payload
    ) external payable returns (bytes32 messageId);
}

