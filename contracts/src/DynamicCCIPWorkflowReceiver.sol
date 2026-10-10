// SPDX-License-Identifier: MIT
pragma solidity ^0.8.28;

import {IERC20} from "@openzeppelin/contracts/token/ERC20/IERC20.sol";
import {SafeERC20} from "@openzeppelin/contracts/token/ERC20/utils/SafeERC20.sol";
import {Ownable} from "@openzeppelin/contracts/access/Ownable.sol";
import {CCIPReceiver} from "@chainlink/contracts-ccip/contracts/applications/CCIPReceiver.sol";
import {Client} from "@chainlink/contracts-ccip/contracts/libraries/Client.sol";

contract DynamicCCIPWorkflowReceiver is CCIPReceiver, Ownable {
    using SafeERC20 for IERC20;

    struct WorkflowPayload {
        bytes32 workflowId;
        bytes32 attemptId;
        address payee;
        uint256 amount;
        address destinationToken;
    }

    struct SourceRoute {
        address sender;
        bool active;
    }

    mapping(uint64 sourceChainSelector => SourceRoute route) public sourceRoutes;
    mapping(bytes32 messageId => bool processed) public processedMessages;
    mapping(bytes32 attemptId => bytes32 messageId) public settlementMessageByAttempt;

    event CCIPWorkflowSourceConfigured(uint64 indexed sourceChainSelector, address indexed sender, bool active);
    event CCIPWorkflowSettlementReceived(
        bytes32 indexed messageId,
        bytes32 indexed workflowId,
        bytes32 indexed attemptId,
        uint64 sourceChainSelector,
        address payee,
        address token,
        uint256 amount
    );

    error InvalidInput();
    error UnsupportedSource(uint64 sourceChainSelector, address sender);
    error MessageAlreadyProcessed(bytes32 messageId);

    constructor(address router) CCIPReceiver(router) Ownable(msg.sender) {}

    function configureSource(uint64 sourceChainSelector, address sender, bool active) external onlyOwner {
        if (sourceChainSelector == 0 || sender == address(0)) revert InvalidInput();
        sourceRoutes[sourceChainSelector] = SourceRoute(sender, active);
        emit CCIPWorkflowSourceConfigured(sourceChainSelector, sender, active);
    }

    function _ccipReceive(Client.Any2EVMMessage memory message) internal override {
        address sender = abi.decode(message.sender, (address));
        SourceRoute memory route = sourceRoutes[message.sourceChainSelector];
        if (!route.active || route.sender != sender) revert UnsupportedSource(message.sourceChainSelector, sender);
        if (processedMessages[message.messageId]) revert MessageAlreadyProcessed(message.messageId);
        if (message.destTokenAmounts.length != 1) revert InvalidInput();

        WorkflowPayload memory payload = abi.decode(message.data, (WorkflowPayload));
        Client.EVMTokenAmount memory received = message.destTokenAmounts[0];
        if (
            payload.workflowId == bytes32(0) ||
            payload.attemptId == bytes32(0) ||
            payload.payee == address(0) ||
            payload.amount == 0 ||
            payload.amount != received.amount ||
            payload.destinationToken != received.token ||
            settlementMessageByAttempt[payload.attemptId] != bytes32(0)
        ) revert InvalidInput();

        processedMessages[message.messageId] = true;
        settlementMessageByAttempt[payload.attemptId] = message.messageId;
        IERC20(received.token).safeTransfer(payload.payee, received.amount);
        emit CCIPWorkflowSettlementReceived(
            message.messageId,
            payload.workflowId,
            payload.attemptId,
            message.sourceChainSelector,
            payload.payee,
            received.token,
            received.amount
        );
    }
}
