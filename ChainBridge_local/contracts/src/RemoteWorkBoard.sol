// SPDX-License-Identifier: MIT
pragma solidity ^0.8.28;

import {ExperimentProtocol} from "./ExperimentProtocol.sol";

interface IAgentIdentityRegistry {
    function isActiveAgent(bytes32 didHash, address wallet, uint64 homeChainId) external view returns (bool);
}

contract RemoteWorkBoard {
    enum AttemptStatus {
        None,
        Assigned,
        Executing,
        ResultSubmitted,
        Cancelled
    }

    struct RouteVersion {
        address sourceGateway;
        address resultReceiver;
        bool active;
    }

    struct Assignment {
        bytes32 workflowId;
        bytes32 nodeId;
        bytes32 attemptId;
        bytes32 routeKey;
        uint32 routeVersion;
        bytes32 criteriaHash;
        address agent;
        uint256 reward;
        AttemptStatus status;
        uint32 attemptNumber;
        bytes32 agentDidHash;
    }

    struct AssignmentRequest {
        bytes32 messageId;
        uint64 sourceChainId;
        address sourceGateway;
        bytes32 workflowId;
        bytes32 nodeId;
        bytes32 attemptId;
        uint32 attemptNumber;
        bytes32 agentDidHash;
        bytes32 routeKey;
        uint32 routeVersion;
        bytes32 criteriaHash;
        address agent;
        uint256 reward;
    }

    uint64 public immutable chainId;
    uint64 public immutable allowedSourceChainId;
    address public immutable relay;
    IAgentIdentityRegistry public identityRegistry;
    bool public identityRegistryConfigured;
    uint256 public resultNonce;
    mapping(bytes32 routeKey => mapping(uint32 routeVersion => RouteVersion route)) public routes;
    mapping(bytes32 attemptId => Assignment assignment) public assignments;
    mapping(bytes32 nodeId => bytes32 attemptId) public currentAttemptByNode;
    mapping(bytes32 nodeId => uint32 attemptNumber) public latestAttemptNumberByNode;
    mapping(bytes32 messageId => bool processed) public processedMessages;

    event RouteConfigured(bytes32 indexed routeKey, uint32 indexed routeVersion, address indexed sourceGateway, bool active);
    event ResultReceiverConfigured(bytes32 indexed routeKey, uint32 indexed routeVersion, address indexed resultReceiver);
    event IdentityRegistryConfigured(address indexed identityRegistry);
    event WorkAssigned(
        bytes32 indexed messageId,
        bytes32 indexed nodeId,
        bytes32 indexed attemptId,
        bytes32 workflowId,
        bytes32 routeKey,
        uint32 routeVersion,
        uint32 attemptNumber,
        bytes32 agentDidHash,
        address agent,
        uint256 reward
    );
    event WorkStarted(bytes32 indexed nodeId, bytes32 indexed attemptId, address indexed agent);
    event ResultSubmitted(
        bytes32 indexed resultMessageId,
        bytes32 indexed nodeId,
        bytes32 indexed attemptId,
        bytes32 resultHash,
        bytes32 proofHash
    );
    event AttemptCancelled(bytes32 indexed nodeId, bytes32 indexed attemptId);
    event ExperimentMessageSent(
        uint8 indexed messageType,
        bytes32 indexed messageId,
        bytes32 indexed attemptId,
        bytes encodedEnvelope,
        bytes payload
    );

    error Unauthorized();
    error InvalidInput();
    error UnsupportedRoute(bytes32 routeKey, uint32 routeVersion);
    error MessageAlreadyProcessed(bytes32 messageId);
    error AttemptAlreadyExists(bytes32 attemptId);
    error InvalidStatus(bytes32 attemptId, AttemptStatus status);
    error StaleAttempt(bytes32 expectedAttemptId, bytes32 providedAttemptId);
    error StaleAttemptNumber(uint32 latestAttemptNumber, uint32 providedAttemptNumber);
    error UnregisteredAgent(bytes32 didHash, address wallet);

    constructor(uint64 _chainId, uint64 _allowedSourceChainId, address _relay) {
        if (_chainId == 0 || _allowedSourceChainId == 0 || _chainId == _allowedSourceChainId || _relay == address(0)) {
            revert InvalidInput();
        }
        chainId = _chainId;
        allowedSourceChainId = _allowedSourceChainId;
        relay = _relay;
    }

    modifier onlyRelay() {
        if (msg.sender != relay) revert Unauthorized();
        _;
    }

    function configureIdentityRegistry(IAgentIdentityRegistry registry) external onlyRelay {
        if (identityRegistryConfigured || address(registry) == address(0)) revert InvalidInput();
        identityRegistry = registry;
        identityRegistryConfigured = true;
        emit IdentityRegistryConfigured(address(registry));
    }

    function configureRoute(
        bytes32 routeKey,
        uint32 routeVersion,
        address sourceGateway,
        bool active
    ) external onlyRelay {
        if (routeKey == bytes32(0) || routeVersion == 0 || sourceGateway == address(0)) revert InvalidInput();
        RouteVersion storage route = routes[routeKey][routeVersion];
        route.sourceGateway = sourceGateway;
        route.active = active;
        emit RouteConfigured(routeKey, routeVersion, sourceGateway, active);
    }

    function configureResultReceiver(bytes32 routeKey, uint32 routeVersion, address resultReceiver) external onlyRelay {
        RouteVersion storage route = routes[routeKey][routeVersion];
        if (routeKey == bytes32(0) || routeVersion == 0 || resultReceiver == address(0) || route.sourceGateway == address(0)) {
            revert InvalidInput();
        }
        route.resultReceiver = resultReceiver;
        emit ResultReceiverConfigured(routeKey, routeVersion, resultReceiver);
    }

    function receiveAssignment(AssignmentRequest calldata request) external onlyRelay {
        _receiveAssignment(request);
    }

    function receiveAssignmentV2(
        ExperimentProtocol.Envelope calldata envelope,
        bytes calldata payload
    ) external onlyRelay {
        if (
            envelope.messageType != ExperimentProtocol.ASSIGNMENT ||
            envelope.sourceChainId != allowedSourceChainId ||
            envelope.targetChainId != chainId ||
            envelope.targetReceiver != address(this) ||
            !ExperimentProtocol.valid(envelope, payload)
        ) revert InvalidInput();
        RouteVersion memory route = routes[envelope.routeKey][envelope.routeVersion];
        if (!route.active || route.sourceGateway != envelope.sourceSender) {
            revert UnsupportedRoute(envelope.routeKey, envelope.routeVersion);
        }
        (bytes32 agentDidHash, bytes32 criteriaHash, address agent, uint256 reward) =
            abi.decode(payload, (bytes32, bytes32, address, uint256));
        _receiveAssignment(AssignmentRequest({
            messageId: envelope.messageId,
            sourceChainId: envelope.sourceChainId,
            sourceGateway: envelope.sourceSender,
            workflowId: envelope.workflowId,
            nodeId: envelope.nodeId,
            attemptId: envelope.attemptId,
            attemptNumber: envelope.attemptNumber,
            agentDidHash: agentDidHash,
            routeKey: envelope.routeKey,
            routeVersion: envelope.routeVersion,
            criteriaHash: criteriaHash,
            agent: agent,
            reward: reward
        }));
    }

    function _receiveAssignment(AssignmentRequest memory request) private {
        if (processedMessages[request.messageId]) revert MessageAlreadyProcessed(request.messageId);
        RouteVersion memory route = routes[request.routeKey][request.routeVersion];
        if (!route.active || request.sourceChainId != allowedSourceChainId || route.sourceGateway != request.sourceGateway) {
            revert UnsupportedRoute(request.routeKey, request.routeVersion);
        }
        if (
            request.messageId == bytes32(0) ||
            request.workflowId == bytes32(0) ||
            request.nodeId == bytes32(0) ||
            request.attemptId == bytes32(0) ||
            request.attemptNumber == 0 ||
            request.agentDidHash == bytes32(0) ||
            request.criteriaHash == bytes32(0) ||
            request.agent == address(0) ||
            request.reward == 0
        ) revert InvalidInput();
        if (assignments[request.attemptId].status != AttemptStatus.None) revert AttemptAlreadyExists(request.attemptId);
        uint32 latestAttemptNumber = latestAttemptNumberByNode[request.nodeId];
        if (request.attemptNumber <= latestAttemptNumber) revert StaleAttemptNumber(latestAttemptNumber, request.attemptNumber);
        if (!identityRegistryConfigured || !identityRegistry.isActiveAgent(request.agentDidHash, request.agent, chainId)) {
            revert UnregisteredAgent(request.agentDidHash, request.agent);
        }

        processedMessages[request.messageId] = true;
        currentAttemptByNode[request.nodeId] = request.attemptId;
        latestAttemptNumberByNode[request.nodeId] = request.attemptNumber;
        assignments[request.attemptId] = Assignment({
            workflowId: request.workflowId,
            nodeId: request.nodeId,
            attemptId: request.attemptId,
            routeKey: request.routeKey,
            routeVersion: request.routeVersion,
            criteriaHash: request.criteriaHash,
            agent: request.agent,
            reward: request.reward,
            status: AttemptStatus.Assigned,
            attemptNumber: request.attemptNumber,
            agentDidHash: request.agentDidHash
        });
        emit WorkAssigned(
            request.messageId,
            request.nodeId,
            request.attemptId,
            request.workflowId,
            request.routeKey,
            request.routeVersion,
            request.attemptNumber,
            request.agentDidHash,
            request.agent,
            request.reward
        );
    }

    function startWork(bytes32 attemptId) external {
        Assignment storage assignment = assignments[attemptId];
        if (assignment.agent != msg.sender) revert Unauthorized();
        if (assignment.status != AttemptStatus.Assigned) revert InvalidStatus(attemptId, assignment.status);
        if (currentAttemptByNode[assignment.nodeId] != attemptId) {
            revert StaleAttempt(currentAttemptByNode[assignment.nodeId], attemptId);
        }
        assignment.status = AttemptStatus.Executing;
        emit WorkStarted(assignment.nodeId, attemptId, msg.sender);
    }

    function submitResult(
        bytes32 resultMessageId,
        bytes32 attemptId,
        bytes32 resultHash,
        bytes32 proofHash
    ) external {
        _submitResult(resultMessageId, attemptId, resultHash, proofHash);
    }

    function submitResultV2(
        bytes32 attemptId,
        bytes32 resultHash,
        bytes32 proofHash
    ) external returns (bytes32 resultMessageId) {
        Assignment storage assignment = assignments[attemptId];
        RouteVersion memory route = routes[assignment.routeKey][assignment.routeVersion];
        if (!route.active || route.resultReceiver == address(0)) {
            revert UnsupportedRoute(assignment.routeKey, assignment.routeVersion);
        }
        bytes memory payload = abi.encode(resultHash, proofHash);
        ExperimentProtocol.Envelope memory envelope = ExperimentProtocol.Envelope({
            messageType: ExperimentProtocol.RESULT,
            messageId: bytes32(0),
            sourceChainId: chainId,
            targetChainId: allowedSourceChainId,
            sourceSender: address(this),
            targetReceiver: route.resultReceiver,
            workflowId: assignment.workflowId,
            nodeId: assignment.nodeId,
            attemptId: attemptId,
            attemptNumber: assignment.attemptNumber,
            routeKey: assignment.routeKey,
            routeVersion: assignment.routeVersion,
            nonce: ++resultNonce,
            payloadHash: keccak256(payload)
        });
        envelope.messageId = ExperimentProtocol.messageId(envelope);
        resultMessageId = envelope.messageId;
        _submitResult(resultMessageId, attemptId, resultHash, proofHash);
        _emitMessage(envelope, payload);
    }

    function _submitResult(
        bytes32 resultMessageId,
        bytes32 attemptId,
        bytes32 resultHash,
        bytes32 proofHash
    ) private {
        Assignment storage assignment = assignments[attemptId];
        if (assignment.agent != msg.sender) revert Unauthorized();
        if (assignment.status != AttemptStatus.Assigned && assignment.status != AttemptStatus.Executing) {
            revert InvalidStatus(attemptId, assignment.status);
        }
        if (currentAttemptByNode[assignment.nodeId] != attemptId) {
            revert StaleAttempt(currentAttemptByNode[assignment.nodeId], attemptId);
        }
        if (processedMessages[resultMessageId]) revert MessageAlreadyProcessed(resultMessageId);
        if (resultMessageId == bytes32(0) || resultHash == bytes32(0) || proofHash == bytes32(0)) revert InvalidInput();
        processedMessages[resultMessageId] = true;
        assignment.status = AttemptStatus.ResultSubmitted;
        emit ResultSubmitted(resultMessageId, assignment.nodeId, attemptId, resultHash, proofHash);
    }

    function _emitMessage(ExperimentProtocol.Envelope memory envelope, bytes memory payload) private {
        emit ExperimentMessageSent(
            envelope.messageType, envelope.messageId, envelope.attemptId, abi.encode(envelope), payload
        );
    }

    function cancelAttempt(bytes32 attemptId) external onlyRelay {
        Assignment storage assignment = assignments[attemptId];
        if (assignment.status == AttemptStatus.None || assignment.status == AttemptStatus.ResultSubmitted) {
            revert InvalidStatus(attemptId, assignment.status);
        }
        assignment.status = AttemptStatus.Cancelled;
        emit AttemptCancelled(assignment.nodeId, attemptId);
    }
}
