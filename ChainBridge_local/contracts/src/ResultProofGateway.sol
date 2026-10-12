// SPDX-License-Identifier: MIT
pragma solidity ^0.8.28;

import {ExperimentProtocol} from "./ExperimentProtocol.sol";

interface IExperimentResultWorkflow {
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

contract ResultProofGateway {
    struct TrustedRoute {
        uint64 sourceChainId;
        address sourceGateway;
        bool active;
    }

    struct ResultProof {
        bytes32 messageId;
        bytes32 workflowId;
        bytes32 nodeId;
        bytes32 attemptId;
        bytes32 resultHash;
        bytes32 proofHash;
        bytes32 routeKey;
        uint32 routeVersion;
        uint64 sourceChainId;
        bool delivered;
    }
    struct ResultProofRequest {
        bytes32 messageId;
        uint64 sourceChainId;
        address sourceGateway;
        bytes32 routeKey;
        uint32 routeVersion;
        bytes32 workflowId;
        bytes32 nodeId;
        bytes32 attemptId;
        bytes32 resultHash;
        bytes32 proofHash;
    }
    struct NodeSnapshot {
        bytes32 workflowId;
        bytes32 currentAttemptId;
        bytes32 routeKey;
        uint32 routeVersion;
        uint8 status;
    }

    address public immutable controller;
    address public immutable relay;
    IExperimentResultWorkflow public workflowEngine;
    mapping(bytes32 routeKey => mapping(uint32 routeVersion => TrustedRoute route)) public routes;
    mapping(bytes32 attemptId => ResultProof proof) public proofs;
    mapping(bytes32 messageId => bool processed) public processedMessages;

    event ResultRouteConfigured(bytes32 indexed routeKey, uint32 indexed routeVersion, uint64 sourceChainId, address sourceGateway, bool active);
    event WorkflowEngineConfigured(address indexed workflowEngine);
    event ResultProofDelivered(bytes32 indexed messageId, bytes32 indexed nodeId, bytes32 indexed attemptId, bytes32 resultHash, bytes32 proofHash, uint64 sourceChainId);

    error Unauthorized();
    error InvalidInput();
    error UnsupportedRoute();
    error MessageAlreadyProcessed(bytes32 messageId);
    error StaleAttempt(bytes32 expectedAttemptId, bytes32 providedAttemptId);

    constructor(address _controller, address _relay) {
        if (_controller == address(0) || _relay == address(0)) revert InvalidInput();
        controller = _controller;
        relay = _relay;
    }

    function configureWorkflowEngine(IExperimentResultWorkflow engine) external {
        if (msg.sender != controller) revert Unauthorized();
        if (address(engine) == address(0) || address(workflowEngine) != address(0)) revert InvalidInput();
        workflowEngine = engine;
        emit WorkflowEngineConfigured(address(engine));
    }

    function configureRoute(bytes32 routeKey, uint32 routeVersion, uint64 sourceChainId, address sourceGateway, bool active) external {
        if (msg.sender != controller) revert Unauthorized();
        if (routeKey == bytes32(0) || routeVersion == 0 || sourceChainId == 0 || sourceGateway == address(0)) revert InvalidInput();
        routes[routeKey][routeVersion] = TrustedRoute(sourceChainId, sourceGateway, active);
        emit ResultRouteConfigured(routeKey, routeVersion, sourceChainId, sourceGateway, active);
    }

    function receiveResultProof(
        bytes32 messageId,
        uint64 sourceChainId,
        address sourceGateway,
        bytes32 routeKey,
        uint32 routeVersion,
        bytes32 workflowId,
        bytes32 nodeId,
        bytes32 attemptId,
        bytes32 resultHash,
        bytes32 proofHash
    ) external {
        if (msg.sender != relay) revert Unauthorized();
        _storeProof(ResultProofRequest({
            messageId: messageId,
            sourceChainId: sourceChainId,
            sourceGateway: sourceGateway,
            routeKey: routeKey,
            routeVersion: routeVersion,
            workflowId: workflowId,
            nodeId: nodeId,
            attemptId: attemptId,
            resultHash: resultHash,
            proofHash: proofHash
        }));
    }

    function receiveResultProofV2(
        ExperimentProtocol.Envelope calldata envelope,
        bytes calldata payload
    ) external {
        if (msg.sender != relay) revert Unauthorized();
        if (
            envelope.messageType != ExperimentProtocol.RESULT ||
            envelope.targetChainId != block.chainid ||
            envelope.targetReceiver != address(this) ||
            !ExperimentProtocol.valid(envelope, payload) ||
            address(workflowEngine) == address(0)
        ) revert InvalidInput();
        NodeSnapshot memory node;
        (
            node.workflowId,
            ,
            node.currentAttemptId,
            node.routeKey,
            node.routeVersion,
            ,
            ,
            node.status
        ) = workflowEngine.nodes(envelope.nodeId);
        if (
            node.workflowId != envelope.workflowId || node.currentAttemptId != envelope.attemptId ||
            node.routeKey != envelope.routeKey || node.routeVersion != envelope.routeVersion || node.status != 3
        ) revert StaleAttempt(node.currentAttemptId, envelope.attemptId);
        (bytes32 resultHash, bytes32 proofHash) = abi.decode(payload, (bytes32, bytes32));
        _storeProof(ResultProofRequest({
            messageId: envelope.messageId,
            sourceChainId: envelope.sourceChainId,
            sourceGateway: envelope.sourceSender,
            routeKey: envelope.routeKey,
            routeVersion: envelope.routeVersion,
            workflowId: envelope.workflowId,
            nodeId: envelope.nodeId,
            attemptId: envelope.attemptId,
            resultHash: resultHash,
            proofHash: proofHash
        }));
    }

    function _storeProof(ResultProofRequest memory request) private {
        if (processedMessages[request.messageId]) revert MessageAlreadyProcessed(request.messageId);
        TrustedRoute memory route = routes[request.routeKey][request.routeVersion];
        if (
            !route.active || route.sourceChainId != request.sourceChainId || route.sourceGateway != request.sourceGateway
        ) revert UnsupportedRoute();
        if (
            request.messageId == bytes32(0) || request.workflowId == bytes32(0) || request.nodeId == bytes32(0) ||
            request.attemptId == bytes32(0) || request.resultHash == bytes32(0) || request.proofHash == bytes32(0)
        ) revert InvalidInput();
        if (proofs[request.attemptId].delivered) revert InvalidInput();
        processedMessages[request.messageId] = true;
        proofs[request.attemptId] = ResultProof(
            request.messageId,
            request.workflowId,
            request.nodeId,
            request.attemptId,
            request.resultHash,
            request.proofHash,
            request.routeKey,
            request.routeVersion,
            request.sourceChainId,
            true
        );
        emit ResultProofDelivered(
            request.messageId,
            request.nodeId,
            request.attemptId,
            request.resultHash,
            request.proofHash,
            request.sourceChainId
        );
    }

    function hasProof(bytes32 attemptId, bytes32 resultHash, bytes32 proofHash) external view returns (bool) {
        ResultProof storage proof = proofs[attemptId];
        return proof.delivered && proof.resultHash == resultHash && proof.proofHash == proofHash;
    }
}
