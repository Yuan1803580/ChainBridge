// SPDX-License-Identifier: MIT
pragma solidity ^0.8.28;

import {IERC20} from "@openzeppelin/contracts/token/ERC20/IERC20.sol";
import {SafeERC20} from "@openzeppelin/contracts/token/ERC20/utils/SafeERC20.sol";

contract CCIPSettlementReceiver {
    using SafeERC20 for IERC20;

    struct SettlementPayload {
        bytes32 settlementId;
        bytes32 orderId;
        address payer;
        address payee;
        uint256 amount;
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

    address public immutable ccipRouter;
    uint64 public immutable allowedSourceChainSelector;
    address public immutable allowedRemoteSender;
    address public immutable allowedToken;

    mapping(bytes32 => bool) private processedMessages;
    mapping(bytes32 => ReceivedTransfer) private receivedTransfers;

    event CrossChainSettlementReceived(
        bytes32 indexed messageId,
        bytes32 indexed settlementId,
        bytes32 indexed orderId,
        uint64 sourceChainSelector,
        address sender,
        address token,
        uint256 amount
    );

    error UnauthorizedRouter(address caller);
    error UnsupportedSourceChain(uint64 sourceChainSelector);
    error UnauthorizedRemoteSender(uint64 sourceChainSelector, address sender);
    error UnsupportedSettlementToken(address token);
    error MessageAlreadyProcessed(bytes32 messageId);
    error InvalidPayload();

    constructor(
        address _ccipRouter,
        uint64 _allowedSourceChainSelector,
        address _allowedRemoteSender,
        address _allowedToken
    ) {
        if (
            _ccipRouter == address(0) ||
            _allowedRemoteSender == address(0) ||
            _allowedToken == address(0)
        ) {
            revert InvalidPayload();
        }

        ccipRouter = _ccipRouter;
        allowedSourceChainSelector = _allowedSourceChainSelector;
        allowedRemoteSender = _allowedRemoteSender;
        allowedToken = _allowedToken;
    }

    function mockReceive(
        bytes32 messageId,
        uint64 sourceChainSelector,
        address remoteSender,
        address token,
        SettlementPayload calldata payload
    ) external {
        if (msg.sender != ccipRouter) {
            revert UnauthorizedRouter(msg.sender);
        }
        if (sourceChainSelector != allowedSourceChainSelector) {
            revert UnsupportedSourceChain(sourceChainSelector);
        }
        if (remoteSender != allowedRemoteSender) {
            revert UnauthorizedRemoteSender(sourceChainSelector, remoteSender);
        }
        if (token != allowedToken) {
            revert UnsupportedSettlementToken(token);
        }
        if (processedMessages[messageId]) {
            revert MessageAlreadyProcessed(messageId);
        }
        if (payload.payee == address(0) || payload.amount == 0) {
            revert InvalidPayload();
        }

        processedMessages[messageId] = true;
        bytes32 payloadHash = keccak256(abi.encode(payload));

        receivedTransfers[messageId] = ReceivedTransfer({
            messageId: messageId,
            settlementId: payload.settlementId,
            orderId: payload.orderId,
            sourceChainSelector: sourceChainSelector,
            sender: remoteSender,
            receiver: payload.payee,
            token: token,
            amount: payload.amount,
            payloadHash: payloadHash
        });

        IERC20(token).safeTransfer(payload.payee, payload.amount);

        emit CrossChainSettlementReceived(
            messageId,
            payload.settlementId,
            payload.orderId,
            sourceChainSelector,
            remoteSender,
            token,
            payload.amount
        );
    }

    function getReceivedTransfer(
        bytes32 messageId
    ) external view returns (ReceivedTransfer memory transfer) {
        return receivedTransfers[messageId];
    }

    function isMessageProcessed(bytes32 messageId) external view returns (bool) {
        return processedMessages[messageId];
    }
}
