// SPDX-License-Identifier: MIT
pragma solidity ^0.8.28;

contract WorkflowEngine {
    enum NodeStatus {
        None,
        Blocked,
        Ready,
        Assigned,
        Succeeded,
        Settled,
        Retrying,
        Failed,
        WaitingForAgent,
        SystemRetry,
        SettlementPending,
        Disputed
    }

    enum FailureStatus { None, FailedRefunding, FailedRefunded }

    struct Workflow {
        address requester;
        bytes32 termsHash;
        uint32 nodeCount;
        uint32 settledNodeCount;
        bool active;
    }

    struct Node {
        bytes32 workflowId;
        bytes32 criteriaHash;
        bytes32 currentAttemptId;
        bytes32 routeKey;
        uint32 routeVersion;
        uint32 unsettledDependencies;
        address agent;
        NodeStatus status;
    }

    address public immutable coordinator;
    mapping(bytes32 workflowId => Workflow workflow) public workflows;
    mapping(bytes32 workflowId => FailureStatus status) public workflowFailureStatus;
    mapping(bytes32 nodeId => Node node) public nodes;
    mapping(bytes32 nodeId => bytes32[] dependencyNodeIds) private dependencies;
    mapping(bytes32 nodeId => bytes32[] dependentNodeIds) private dependents;

    event WorkflowCreated(bytes32 indexed workflowId, address indexed requester, bytes32 indexed termsHash);
    event NodeRegistered(
        bytes32 indexed workflowId,
        bytes32 indexed nodeId,
        bytes32 indexed criteriaHash,
        uint32 dependencyCount
    );
    event NodeAssigned(
        bytes32 indexed workflowId,
        bytes32 indexed nodeId,
        bytes32 indexed attemptId,
        bytes32 routeKey,
        uint32 routeVersion,
        address agent
    );
    event NodeSucceeded(bytes32 indexed workflowId, bytes32 indexed nodeId, bytes32 indexed attemptId);
    event NodeSettled(bytes32 indexed workflowId, bytes32 indexed nodeId, bytes32 indexed attemptId);
    event NodeRetrying(bytes32 indexed workflowId, bytes32 indexed nodeId, bytes32 indexed attemptId);
    event NodeUnlocked(bytes32 indexed workflowId, bytes32 indexed nodeId);
    event WorkflowCompleted(bytes32 indexed workflowId);
    event NodeWaitingForAgent(bytes32 indexed workflowId, bytes32 indexed nodeId);
    event NodeSystemRetry(bytes32 indexed workflowId, bytes32 indexed nodeId, bytes32 indexed attemptId, bytes32 reasonCode);
    event NodeSettlementPending(bytes32 indexed workflowId, bytes32 indexed nodeId, bytes32 indexed attemptId, bytes32 messageId);
    event WorkflowFailedRefunding(bytes32 indexed workflowId, bytes32 reasonCode);
    event WorkflowFailedRefunded(bytes32 indexed workflowId);

    error Unauthorized();
    error InvalidInput();
    error AlreadyExists(bytes32 id);
    error NotFound(bytes32 id);
    error InvalidNodeStatus(bytes32 nodeId, NodeStatus status);
    error StaleAttempt(bytes32 expectedAttemptId, bytes32 providedAttemptId);

    constructor(address _coordinator) {
        if (_coordinator == address(0)) revert InvalidInput();
        coordinator = _coordinator;
    }

    modifier onlyCoordinator() {
        if (msg.sender != coordinator) revert Unauthorized();
        _;
    }

    function createWorkflow(bytes32 workflowId, address requester, bytes32 termsHash) external onlyCoordinator {
        if (workflowId == bytes32(0) || requester == address(0) || termsHash == bytes32(0)) revert InvalidInput();
        if (workflows[workflowId].requester != address(0)) revert AlreadyExists(workflowId);
        workflows[workflowId] = Workflow({
            requester: requester,
            termsHash: termsHash,
            nodeCount: 0,
            settledNodeCount: 0,
            active: true
        });
        emit WorkflowCreated(workflowId, requester, termsHash);
    }

    function registerNode(
        bytes32 workflowId,
        bytes32 nodeId,
        bytes32 criteriaHash,
        bytes32[] calldata dependencyNodeIds
    ) external onlyCoordinator {
        Workflow storage workflow = workflows[workflowId];
        if (!workflow.active || nodeId == bytes32(0) || criteriaHash == bytes32(0)) revert InvalidInput();
        if (nodes[nodeId].workflowId != bytes32(0)) revert AlreadyExists(nodeId);

        for (uint256 index = 0; index < dependencyNodeIds.length; index++) {
            bytes32 dependencyId = dependencyNodeIds[index];
            if (nodes[dependencyId].workflowId != workflowId || dependencyId == nodeId) revert InvalidInput();
            dependencies[nodeId].push(dependencyId);
            dependents[dependencyId].push(nodeId);
        }
        nodes[nodeId] = Node({
            workflowId: workflowId,
            criteriaHash: criteriaHash,
            currentAttemptId: bytes32(0),
            routeKey: bytes32(0),
            routeVersion: 0,
            unsettledDependencies: uint32(dependencyNodeIds.length),
            agent: address(0),
            status: dependencyNodeIds.length == 0 ? NodeStatus.Ready : NodeStatus.Blocked
        });
        workflow.nodeCount += 1;
        emit NodeRegistered(workflowId, nodeId, criteriaHash, uint32(dependencyNodeIds.length));
    }

    function assignNode(
        bytes32 nodeId,
        bytes32 attemptId,
        bytes32 routeKey,
        uint32 routeVersion,
        address agent
    ) external onlyCoordinator {
        Node storage node = _node(nodeId);
        if (node.status != NodeStatus.Ready && node.status != NodeStatus.Retrying && node.status != NodeStatus.WaitingForAgent && node.status != NodeStatus.SystemRetry) {
            revert InvalidNodeStatus(nodeId, node.status);
        }
        if (attemptId == bytes32(0) || routeKey == bytes32(0) || routeVersion == 0 || agent == address(0)) {
            revert InvalidInput();
        }
        node.currentAttemptId = attemptId;
        node.routeKey = routeKey;
        node.routeVersion = routeVersion;
        node.agent = agent;
        node.status = NodeStatus.Assigned;
        emit NodeAssigned(node.workflowId, nodeId, attemptId, routeKey, routeVersion, agent);
    }

    function markSucceeded(bytes32 nodeId, bytes32 attemptId) external onlyCoordinator {
        Node storage node = _currentAssignedAttempt(nodeId, attemptId);
        node.status = NodeStatus.Succeeded;
        emit NodeSucceeded(node.workflowId, nodeId, attemptId);
    }

    function markAttemptFailed(bytes32 nodeId, bytes32 attemptId) external onlyCoordinator {
        Node storage node = _currentAssignedAttempt(nodeId, attemptId);
        node.status = NodeStatus.Retrying;
        emit NodeRetrying(node.workflowId, nodeId, attemptId);
    }

    function markWaitingForAgent(bytes32 nodeId) external onlyCoordinator {
        Node storage node = _node(nodeId);
        if (node.status != NodeStatus.Ready && node.status != NodeStatus.Retrying && node.status != NodeStatus.SystemRetry) {
            revert InvalidNodeStatus(nodeId, node.status);
        }
        node.status = NodeStatus.WaitingForAgent;
        emit NodeWaitingForAgent(node.workflowId, nodeId);
    }

    function markSystemRetry(bytes32 nodeId, bytes32 attemptId, bytes32 reasonCode) external onlyCoordinator {
        Node storage node = _currentAssignedAttempt(nodeId, attemptId);
        if (reasonCode == bytes32(0)) revert InvalidInput();
        node.status = NodeStatus.SystemRetry;
        emit NodeSystemRetry(node.workflowId, nodeId, attemptId, reasonCode);
    }

    function markSettlementPending(bytes32 nodeId, bytes32 attemptId, bytes32 messageId) external onlyCoordinator {
        Node storage node = _node(nodeId);
        if (node.currentAttemptId != attemptId) revert StaleAttempt(node.currentAttemptId, attemptId);
        if (node.status != NodeStatus.Succeeded || messageId == bytes32(0)) revert InvalidNodeStatus(nodeId, node.status);
        node.status = NodeStatus.SettlementPending;
        emit NodeSettlementPending(node.workflowId, nodeId, attemptId, messageId);
    }

    function markSettled(bytes32 nodeId, bytes32 attemptId) external onlyCoordinator {
        Node storage node = _node(nodeId);
        if (node.currentAttemptId != attemptId) revert StaleAttempt(node.currentAttemptId, attemptId);
        if (node.status != NodeStatus.Succeeded && node.status != NodeStatus.SettlementPending) revert InvalidNodeStatus(nodeId, node.status);
        node.status = NodeStatus.Settled;

        Workflow storage workflow = workflows[node.workflowId];
        workflow.settledNodeCount += 1;
        emit NodeSettled(node.workflowId, nodeId, attemptId);

        bytes32[] storage nodeDependents = dependents[nodeId];
        for (uint256 index = 0; index < nodeDependents.length; index++) {
            Node storage dependent = nodes[nodeDependents[index]];
            if (dependent.status == NodeStatus.Blocked && dependent.unsettledDependencies > 0) {
                dependent.unsettledDependencies -= 1;
                if (dependent.unsettledDependencies == 0) {
                    dependent.status = NodeStatus.Ready;
                    emit NodeUnlocked(dependent.workflowId, nodeDependents[index]);
                }
            }
        }
        if (workflow.nodeCount > 0 && workflow.settledNodeCount == workflow.nodeCount) {
            workflow.active = false;
            emit WorkflowCompleted(node.workflowId);
        }
    }

    function getDependencies(bytes32 nodeId) external view returns (bytes32[] memory) {
        if (nodes[nodeId].workflowId == bytes32(0)) revert NotFound(nodeId);
        return dependencies[nodeId];
    }

    function startFailedRefund(bytes32 workflowId, bytes32 reasonCode) external onlyCoordinator {
        Workflow storage workflow = workflows[workflowId];
        if (!workflow.active || reasonCode == bytes32(0) || workflowFailureStatus[workflowId] != FailureStatus.None) revert InvalidInput();
        workflow.active = false;
        workflowFailureStatus[workflowId] = FailureStatus.FailedRefunding;
        emit WorkflowFailedRefunding(workflowId, reasonCode);
    }

    function confirmFailedRefund(bytes32 workflowId) external onlyCoordinator {
        if (workflowFailureStatus[workflowId] != FailureStatus.FailedRefunding) revert InvalidInput();
        workflowFailureStatus[workflowId] = FailureStatus.FailedRefunded;
        emit WorkflowFailedRefunded(workflowId);
    }

    function getDependents(bytes32 nodeId) external view returns (bytes32[] memory) {
        if (nodes[nodeId].workflowId == bytes32(0)) revert NotFound(nodeId);
        return dependents[nodeId];
    }

    function _node(bytes32 nodeId) private view returns (Node storage node) {
        node = nodes[nodeId];
        if (node.workflowId == bytes32(0)) revert NotFound(nodeId);
    }

    function _currentAssignedAttempt(
        bytes32 nodeId,
        bytes32 attemptId
    ) private view returns (Node storage node) {
        node = _node(nodeId);
        if (node.currentAttemptId != attemptId) revert StaleAttempt(node.currentAttemptId, attemptId);
        if (node.status != NodeStatus.Assigned) revert InvalidNodeStatus(nodeId, node.status);
    }
}
