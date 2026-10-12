// SPDX-License-Identifier: MIT
pragma solidity ^0.8.24;

/// @title ICCIPSettlementAdapter
/// @notice Future Chainlink CCIP integration boundary for marketplace settlement.
/// @dev An implementation must authenticate the CCIP router, allowlist source chains
///      and senders, prevent message replay, and reconcile messages with the escrow.
interface ICCIPSettlementAdapter {
    struct TransferRequest {
        bytes32 settlementId;
        bytes32 orderId;
        uint64 destinationChainSelector;
        address receiver;
        address token;
        uint256 amount;
        bytes data;
        bytes extraArgs;
    }

    struct ReceivedTransfer {
        bytes32 messageId;
        bytes32 settlementId;
        bytes32 orderId;
        uint64 sourceChainSelector;
        address sender;
        address receiver;
        address token;
        uint256 amount;
        bytes32 payloadHash;
    }

    error UnsupportedDestinationChain(uint64 chainSelector);
    error UnsupportedSourceChain(uint64 chainSelector);
    error UnauthorizedRemoteSender(uint64 chainSelector, address sender);
    error UnsupportedSettlementToken(address token);
    error MessageAlreadyProcessed(bytes32 messageId);
    error InsufficientCCIPFee(uint256 supplied, uint256 required);

    event CrossChainSettlementSent(
        bytes32 indexed messageId,
        bytes32 indexed settlementId,
        bytes32 indexed orderId,
        uint64 destinationChainSelector,
        address receiver,
        address token,
        uint256 amount,
        address feeToken,
        uint256 fee
    );
    event CrossChainSettlementReceived(
        bytes32 indexed messageId,
        bytes32 indexed settlementId,
        bytes32 indexed orderId,
        uint64 sourceChainSelector,
        address sender,
        address token,
        uint256 amount
    );
    event CrossChainSettlementFailed(
        bytes32 indexed messageId,
        bytes32 indexed settlementId,
        bytes32 indexed reasonHash
    );

    function quoteFee(
        TransferRequest calldata request,
        address feeToken
    ) external view returns (uint256 fee);

    function sendSettlement(
        TransferRequest calldata request,
        address feeToken
    ) external payable returns (bytes32 messageId);

    function getReceivedTransfer(
        bytes32 messageId
    ) external view returns (ReceivedTransfer memory transfer);

    function isMessageProcessed(bytes32 messageId) external view returns (bool);
}

