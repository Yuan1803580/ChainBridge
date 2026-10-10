// SPDX-License-Identifier: MIT
pragma solidity ^0.8.28;

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

    address public immutable controller;
    address public immutable relay;
    mapping(bytes32 routeKey => mapping(uint32 routeVersion => TrustedRoute route)) public routes;
    mapping(bytes32 attemptId => ResultProof proof) public proofs;
    mapping(bytes32 messageId => bool processed) public processedMessages;

    event ResultRouteConfigured(bytes32 indexed routeKey, uint32 indexed routeVersion, uint64 sourceChainId, address sourceGateway, bool active);
    event ResultProofDelivered(bytes32 indexed messageId, bytes32 indexed nodeId, bytes32 indexed attemptId, bytes32 resultHash, bytes32 proofHash, uint64 sourceChainId);

    error Unauthorized();
    error InvalidInput();
    error UnsupportedRoute();
    error MessageAlreadyProcessed(bytes32 messageId);

    constructor(address _controller, address _relay) {
        if (_controller == address(0) || _relay == address(0)) revert InvalidInput();
        controller = _controller;
        relay = _relay;
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
        if (processedMessages[messageId]) revert MessageAlreadyProcessed(messageId);
        TrustedRoute memory route = routes[routeKey][routeVersion];
        if (!route.active || route.sourceChainId != sourceChainId || route.sourceGateway != sourceGateway) revert UnsupportedRoute();
        if (messageId == bytes32(0) || workflowId == bytes32(0) || nodeId == bytes32(0) || attemptId == bytes32(0) || resultHash == bytes32(0) || proofHash == bytes32(0)) revert InvalidInput();
        if (proofs[attemptId].delivered) revert InvalidInput();
        processedMessages[messageId] = true;
        proofs[attemptId] = ResultProof(messageId, workflowId, nodeId, attemptId, resultHash, proofHash, routeKey, routeVersion, sourceChainId, true);
        emit ResultProofDelivered(messageId, nodeId, attemptId, resultHash, proofHash, sourceChainId);
    }

    function hasProof(bytes32 attemptId, bytes32 resultHash, bytes32 proofHash) external view returns (bool) {
        ResultProof storage proof = proofs[attemptId];
        return proof.delivered && proof.resultHash == resultHash && proof.proofHash == proofHash;
    }
}
