// SPDX-License-Identifier: MIT
pragma solidity ^0.8.28;

import {ExperimentProtocol} from "./ExperimentProtocol.sol";

interface IExperimentWorkflowEngine {
    function nodes(bytes32 nodeId) external view returns (
        bytes32 workflowId,
        bytes32 criteriaHash,
        bytes32 currentAttemptId,
        bytes32 routeKey,
        uint32 routeVersion,
        uint32 unsettledDependencies,
        address agent,
        uint8 status
    );
}

contract ExperimentDispatchSender {
    struct Route { uint64 targetChainId; address receiver; bool active; }
    struct NodeSnapshot {
        bytes32 workflowId;
        bytes32 criteriaHash;
        bytes32 currentAttemptId;
        bytes32 routeKey;
        uint32 routeVersion;
        address agent;
        uint8 status;
    }

    address public immutable controller;
    IExperimentWorkflowEngine public immutable workflowEngine;
    uint256 public nonce;
    mapping(bytes32 routeKey => mapping(uint32 routeVersion => Route route)) public routes;

    event RouteConfigured(bytes32 indexed routeKey, uint32 indexed routeVersion, uint64 targetChainId, address receiver, bool active);
    event ExperimentMessageSent(
        uint8 indexed messageType,
        bytes32 indexed messageId,
        bytes32 indexed attemptId,
        bytes encodedEnvelope,
        bytes payload
    );

    error Unauthorized();
    error InvalidInput();
    error UnsupportedRoute();
    error StaleAttempt();

    constructor(address _controller, IExperimentWorkflowEngine _workflowEngine) {
        if (_controller == address(0) || address(_workflowEngine) == address(0)) revert InvalidInput();
        controller = _controller;
        workflowEngine = _workflowEngine;
    }

    function configureRoute(bytes32 routeKey, uint32 routeVersion, uint64 targetChainId, address receiver, bool active) external {
        if (msg.sender != controller) revert Unauthorized();
        if (routeKey == bytes32(0) || routeVersion == 0 || targetChainId == 0 || receiver == address(0)) revert InvalidInput();
        routes[routeKey][routeVersion] = Route(targetChainId, receiver, active);
        emit RouteConfigured(routeKey, routeVersion, targetChainId, receiver, active);
    }

    function sendAssignment(
        bytes32 nodeId,
        bytes32 attemptId,
        uint32 attemptNumber,
        bytes32 agentDidHash,
        uint256 reward
    ) external returns (bytes32 messageId) {
        if (msg.sender != controller) revert Unauthorized();
        NodeSnapshot memory node;
        (
            node.workflowId,
            node.criteriaHash,
            node.currentAttemptId,
            node.routeKey,
            node.routeVersion,
            ,
            node.agent,
            node.status
        ) = workflowEngine.nodes(nodeId);
        if (
            node.workflowId == bytes32(0) || node.currentAttemptId != attemptId || node.status != 3 ||
            attemptNumber == 0 || agentDidHash == bytes32(0) || reward == 0
        ) revert StaleAttempt();
        Route memory route = routes[node.routeKey][node.routeVersion];
        if (!route.active) revert UnsupportedRoute();
        bytes memory payload = abi.encode(agentDidHash, node.criteriaHash, node.agent, reward);
        ExperimentProtocol.Envelope memory envelope;
        envelope.messageType = ExperimentProtocol.ASSIGNMENT;
        envelope.sourceChainId = uint64(block.chainid);
        envelope.targetChainId = route.targetChainId;
        envelope.sourceSender = address(this);
        envelope.targetReceiver = route.receiver;
        envelope.workflowId = node.workflowId;
        envelope.nodeId = nodeId;
        envelope.attemptId = attemptId;
        envelope.attemptNumber = attemptNumber;
        envelope.routeKey = node.routeKey;
        envelope.routeVersion = node.routeVersion;
        envelope.nonce = ++nonce;
        envelope.payloadHash = keccak256(payload);
        envelope.messageId = ExperimentProtocol.messageId(envelope);
        messageId = envelope.messageId;
        _emitMessage(envelope, payload);
    }

    function _emitMessage(ExperimentProtocol.Envelope memory envelope, bytes memory payload) private {
        emit ExperimentMessageSent(
            envelope.messageType, envelope.messageId, envelope.attemptId, abi.encode(envelope), payload
        );
    }
}
