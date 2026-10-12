// SPDX-License-Identifier: MIT
pragma solidity ^0.8.28;

import {IERC20} from "@openzeppelin/contracts/token/ERC20/IERC20.sol";
import {SafeERC20} from "@openzeppelin/contracts/token/ERC20/utils/SafeERC20.sol";

import {CCIPSettlementReceiver} from "./CCIPSettlementReceiver.sol";

contract CCIPSettlementSender {
    using SafeERC20 for IERC20;

    struct SettlementPayload {
        bytes32 settlementId;
        bytes32 orderId;
        address payer;
        address payee;
        uint256 amount;
    }

    struct SentTransfer {
        bytes32 messageId;
        bytes32 settlementId;
        bytes32 orderId;
        uint64 destinationChainSelector;
        address receiver;
        address token;
        uint256 amount;
        bytes32 payloadHash;
    }

    uint64 public immutable sourceChainSelector;
    uint64 public immutable allowedDestinationChainSelector;
    address public immutable allowedReceiver;
    address public immutable allowedToken;
    address public immutable authorizedEscrow;

    uint256 public messageNonce;

    mapping(bytes32 => bool) private sentMessages;
    mapping(bytes32 => SentTransfer) private sentTransfers;

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

    error UnsupportedDestinationChain(uint64 chainSelector);
    error UnauthorizedReceiver(address receiver);
    error UnsupportedSettlementToken(address token);
    error UnauthorizedEscrow(address caller);
    error InvalidPayload();

    constructor(
        uint64 _sourceChainSelector,
        uint64 _allowedDestinationChainSelector,
        address _allowedReceiver,
        address _allowedToken,
        address _authorizedEscrow
    ) {
        if (
            _allowedReceiver == address(0) ||
            _allowedToken == address(0) ||
            _authorizedEscrow == address(0)
        ) {
            revert InvalidPayload();
        }

        sourceChainSelector = _sourceChainSelector;
        allowedDestinationChainSelector = _allowedDestinationChainSelector;
        allowedReceiver = _allowedReceiver;
        allowedToken = _allowedToken;
        authorizedEscrow = _authorizedEscrow;
    }

    function quoteFee(
        uint64 destinationChainSelector,
        address receiver,
        address token,
        uint256 amount
    ) external view returns (uint256 fee) {
        _validateRoute(destinationChainSelector, receiver, token);
        if (amount == 0) {
            revert InvalidPayload();
        }

        return 0;
    }

    function sendSettlement(
        uint64 destinationChainSelector,
        address receiver,
        address token,
        SettlementPayload calldata payload
    ) external returns (bytes32 messageId) {
        if (payload.payer != msg.sender) {
            revert InvalidPayload();
        }

        return _sendSettlement(destinationChainSelector, receiver, token, payload);
    }

    function sendSettlementFromEscrow(
        uint64 destinationChainSelector,
        address receiver,
        address token,
        SettlementPayload calldata payload
    ) external returns (bytes32 messageId) {
        if (msg.sender != authorizedEscrow) {
            revert UnauthorizedEscrow(msg.sender);
        }
        if (payload.payer == address(0)) {
            revert InvalidPayload();
        }

        return _sendSettlement(destinationChainSelector, receiver, token, payload);
    }

    function getSentTransfer(
        bytes32 messageId
    ) external view returns (SentTransfer memory transfer) {
        return sentTransfers[messageId];
    }

    function isMessageSent(bytes32 messageId) external view returns (bool) {
        return sentMessages[messageId];
    }

    function _sendSettlement(
        uint64 destinationChainSelector,
        address receiver,
        address token,
        SettlementPayload calldata payload
    ) private returns (bytes32 messageId) {
        _validateRoute(destinationChainSelector, receiver, token);
        if (payload.payee == address(0) || payload.amount == 0) {
            revert InvalidPayload();
        }

        uint256 nonce = ++messageNonce;
        messageId = keccak256(
            abi.encode(
                address(this),
                sourceChainSelector,
                destinationChainSelector,
                payload.settlementId,
                payload.orderId,
                nonce
            )
        );
        bytes32 payloadHash = keccak256(abi.encode(payload));

        IERC20(token).safeTransferFrom(msg.sender, receiver, payload.amount);

        CCIPSettlementReceiver(receiver).mockReceive(
            messageId,
            sourceChainSelector,
            address(this),
            token,
            CCIPSettlementReceiver.SettlementPayload({
                settlementId: payload.settlementId,
                orderId: payload.orderId,
                payer: payload.payer,
                payee: payload.payee,
                amount: payload.amount
            })
        );

        sentMessages[messageId] = true;
        sentTransfers[messageId] = SentTransfer({
            messageId: messageId,
            settlementId: payload.settlementId,
            orderId: payload.orderId,
            destinationChainSelector: destinationChainSelector,
            receiver: receiver,
            token: token,
            amount: payload.amount,
            payloadHash: payloadHash
        });

        emit CrossChainSettlementSent(
            messageId,
            payload.settlementId,
            payload.orderId,
            destinationChainSelector,
            receiver,
            token,
            payload.amount,
            address(0),
            0
        );
    }

    function _validateRoute(
        uint64 destinationChainSelector,
        address receiver,
        address token
    ) private view {
        if (destinationChainSelector != allowedDestinationChainSelector) {
            revert UnsupportedDestinationChain(destinationChainSelector);
        }
        if (receiver != allowedReceiver) {
            revert UnauthorizedReceiver(receiver);
        }
        if (token != allowedToken) {
            revert UnsupportedSettlementToken(token);
        }
    }
}
