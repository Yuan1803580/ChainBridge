// SPDX-License-Identifier: MIT
pragma solidity ^0.8.28;

import {IERC20} from "@openzeppelin/contracts/token/ERC20/IERC20.sol";
import {Ownable} from "@openzeppelin/contracts/access/Ownable.sol";
import {SafeERC20} from "@openzeppelin/contracts/token/ERC20/utils/SafeERC20.sol";

import {CCIPReceiver} from "@chainlink/contracts-ccip/contracts/applications/CCIPReceiver.sol";
import {Client} from "@chainlink/contracts-ccip/contracts/libraries/Client.sol";

contract RealCCIPSettlementReceiver is CCIPReceiver, Ownable {
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

    uint64 public immutable allowedSourceChainSelector;
    address public immutable allowedToken;
    address public allowedRemoteSender;

    mapping(bytes32 => bool) private processedMessages;
    mapping(bytes32 => ReceivedTransfer) private receivedTransfers;

    event RealCrossChainSettlementReceived(
        bytes32 indexed messageId,
        bytes32 indexed settlementId,
        bytes32 indexed orderId,
        uint64 sourceChainSelector,
        address sender,
        address token,
        uint256 amount
    );

    event AllowedRemoteSenderUpdated(
        address indexed oldSender,
        address indexed newSender
    );

    error UnsupportedSourceChain(uint64 sourceChainSelector);
    error UnauthorizedRemoteSender(uint64 sourceChainSelector, address sender);
    error UnsupportedSettlementToken(address token);
    error MessageAlreadyProcessed(bytes32 messageId);
    error InvalidPayload();

    constructor(
        address _ccipRouter,
        uint64 _allowedSourceChainSelector,
        address _allowedToken
    ) CCIPReceiver(_ccipRouter) Ownable(msg.sender) {
        if (_allowedToken == address(0)) {
            revert InvalidPayload();
        }

        allowedSourceChainSelector = _allowedSourceChainSelector;
        allowedToken = _allowedToken;
    }

    function setAllowedRemoteSender(address newSender) external onlyOwner {
        if (newSender == address(0)) {
            revert InvalidPayload();
        }

        address oldSender = allowedRemoteSender;
        allowedRemoteSender = newSender;

        emit AllowedRemoteSenderUpdated(oldSender, newSender);
    }

    function getReceivedTransfer(
        bytes32 messageId
    ) external view returns (ReceivedTransfer memory transfer) {
        return receivedTransfers[messageId];
    }

    function isMessageProcessed(bytes32 messageId) external view returns (bool) {
        return processedMessages[messageId];
    }

    function _ccipReceive(
        Client.Any2EVMMessage memory message
    ) internal override {
        if (message.sourceChainSelector != allowedSourceChainSelector) {
            revert UnsupportedSourceChain(message.sourceChainSelector);
        }

        address remoteSender = abi.decode(message.sender, (address));
        if (remoteSender != allowedRemoteSender) {
            revert UnauthorizedRemoteSender(
                message.sourceChainSelector,
                remoteSender
            );
        }
        if (processedMessages[message.messageId]) {
            revert MessageAlreadyProcessed(message.messageId);
        }
        if (message.destTokenAmounts.length != 1) {
            revert InvalidPayload();
        }

        Client.EVMTokenAmount memory receivedToken = message.destTokenAmounts[0];
        if (receivedToken.token != allowedToken) {
            revert UnsupportedSettlementToken(receivedToken.token);
        }
        if (receivedToken.amount == 0) {
            revert InvalidPayload();
        }

        SettlementPayload memory payload = abi.decode(
            message.data,
            (SettlementPayload)
        );
        if (
            payload.payee == address(0) ||
            payload.amount == 0 ||
            payload.amount != receivedToken.amount
        ) {
            revert InvalidPayload();
        }

        processedMessages[message.messageId] = true;
        bytes32 payloadHash = keccak256(message.data);

        receivedTransfers[message.messageId] = ReceivedTransfer({
            messageId: message.messageId,
            settlementId: payload.settlementId,
            orderId: payload.orderId,
            sourceChainSelector: message.sourceChainSelector,
            sender: remoteSender,
            receiver: payload.payee,
            token: receivedToken.token,
            amount: receivedToken.amount,
            payloadHash: payloadHash
        });

        IERC20(receivedToken.token).safeTransfer(
            payload.payee,
            receivedToken.amount
        );

        emit RealCrossChainSettlementReceived(
            message.messageId,
            payload.settlementId,
            payload.orderId,
            message.sourceChainSelector,
            remoteSender,
            receivedToken.token,
            receivedToken.amount
        );
    }
}
