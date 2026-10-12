// SPDX-License-Identifier: MIT
pragma solidity ^0.8.28;

import {IERC20} from "@openzeppelin/contracts/token/ERC20/IERC20.sol";
import {SafeERC20} from "@openzeppelin/contracts/token/ERC20/utils/SafeERC20.sol";
import {ExperimentProtocol} from "./ExperimentProtocol.sol";

contract ExperimentSourceVault {
    using SafeERC20 for IERC20;

    enum SettlementStatus { None, Locked, Confirmed }
    struct Route { uint64 targetChainId; address destinationVault; bool active; }
    struct Settlement {
        bytes32 workflowId;
        bytes32 nodeId;
        bytes32 attemptId;
        uint32 attemptNumber;
        bytes32 routeKey;
        uint32 routeVersion;
        uint64 targetChainId;
        address destinationVault;
        address payee;
        uint256 bridgeAmount;
        uint256 payoutAmount;
        SettlementStatus status;
    }

    address public immutable controller;
    address public immutable relay;
    IERC20 public immutable bridgeAsset;
    address public authorizedEscrow;
    uint256 public nonce;
    uint256 public lockedBacking;
    mapping(bytes32 routeKey => mapping(uint32 routeVersion => Route route)) public routes;
    mapping(bytes32 messageId => Settlement settlement) private _settlements;
    mapping(bytes32 messageId => bool processedConfirmations) public processedConfirmations;
    mapping(bytes32 attemptId => bool confirmed) public isAttemptConfirmed;

    event EscrowConfigured(address indexed escrow);
    event RouteConfigured(bytes32 indexed routeKey, uint32 indexed routeVersion, uint64 targetChainId, address destinationVault, bool active);
    event ExperimentMessageSent(
        uint8 indexed messageType,
        bytes32 indexed messageId,
        bytes32 indexed attemptId,
        bytes encodedEnvelope,
        bytes payload
    );
    event SourceSettlementConfirmed(bytes32 indexed messageId, bytes32 indexed attemptId, uint256 bridgeAmount, uint256 payoutAmount, bytes32 payoutEvidenceHash);

    error Unauthorized();
    error InvalidInput();
    error UnsupportedRoute();
    error MessageAlreadyProcessed(bytes32 messageId);

    constructor(address _controller, address _relay, IERC20 _bridgeAsset) {
        if (_controller == address(0) || _relay == address(0) || address(_bridgeAsset) == address(0)) revert InvalidInput();
        controller = _controller;
        relay = _relay;
        bridgeAsset = _bridgeAsset;
    }

    function configureEscrow(address escrow) external {
        if (msg.sender != controller) revert Unauthorized();
        if (escrow == address(0) || authorizedEscrow != address(0)) revert InvalidInput();
        authorizedEscrow = escrow;
        emit EscrowConfigured(escrow);
    }

    function configureRoute(bytes32 routeKey, uint32 routeVersion, uint64 targetChainId, address destinationVault, bool active) external {
        if (msg.sender != controller) revert Unauthorized();
        if (routeKey == bytes32(0) || routeVersion == 0 || targetChainId == 0 || destinationVault == address(0)) revert InvalidInput();
        routes[routeKey][routeVersion] = Route(targetChainId, destinationVault, active);
        emit RouteConfigured(routeKey, routeVersion, targetChainId, destinationVault, active);
    }

    function lockAndSend(ExperimentProtocol.SettlementRequest calldata request) external returns (bytes32 messageId) {
        if (msg.sender != authorizedEscrow) revert Unauthorized();
        Route memory route = routes[request.routeKey][request.routeVersion];
        if (!route.active) revert UnsupportedRoute();
        if (
            request.workflowId == bytes32(0) || request.nodeId == bytes32(0) || request.attemptId == bytes32(0) ||
            request.attemptNumber == 0 || request.bridgeAmount == 0 || request.payee == address(0) ||
            request.payoutToken == address(0) || request.minPayoutAmount == 0 || request.payoutDeadline <= block.timestamp
        ) revert InvalidInput();
        bridgeAsset.safeTransferFrom(msg.sender, address(this), request.bridgeAmount);
        lockedBacking += request.bridgeAmount;
        bytes memory payload = abi.encode(
            request.bridgeAmount, request.payee, request.payoutToken, request.minPayoutAmount, request.payoutDeadline
        );
        ExperimentProtocol.Envelope memory envelope;
        envelope.messageType = ExperimentProtocol.SETTLEMENT;
        envelope.sourceChainId = uint64(block.chainid);
        envelope.targetChainId = route.targetChainId;
        envelope.sourceSender = address(this);
        envelope.targetReceiver = route.destinationVault;
        envelope.workflowId = request.workflowId;
        envelope.nodeId = request.nodeId;
        envelope.attemptId = request.attemptId;
        envelope.attemptNumber = request.attemptNumber;
        envelope.routeKey = request.routeKey;
        envelope.routeVersion = request.routeVersion;
        envelope.nonce = ++nonce;
        envelope.payloadHash = keccak256(payload);
        envelope.messageId = ExperimentProtocol.messageId(envelope);
        messageId = envelope.messageId;
        if (_settlements[messageId].status != SettlementStatus.None) revert InvalidInput();
        _settlements[messageId] = Settlement({
            workflowId: request.workflowId,
            nodeId: request.nodeId,
            attemptId: request.attemptId,
            attemptNumber: request.attemptNumber,
            routeKey: request.routeKey,
            routeVersion: request.routeVersion,
            targetChainId: route.targetChainId,
            destinationVault: route.destinationVault,
            payee: request.payee,
            bridgeAmount: request.bridgeAmount,
            payoutAmount: 0,
            status: SettlementStatus.Locked
        });
        _emitMessage(envelope, payload);
    }

    function receiveConfirmationV2(ExperimentProtocol.Envelope calldata envelope, bytes calldata payload) external {
        if (msg.sender != relay) revert Unauthorized();
        if (processedConfirmations[envelope.messageId]) revert MessageAlreadyProcessed(envelope.messageId);
        if (
            envelope.messageType != ExperimentProtocol.SETTLEMENT_CONFIRMATION ||
            envelope.targetChainId != block.chainid || envelope.targetReceiver != address(this) ||
            !ExperimentProtocol.valid(envelope, payload)
        ) revert InvalidInput();
        (bytes32 settlementMessageId, uint256 payoutAmount, bytes32 payoutEvidenceHash) =
            abi.decode(payload, (bytes32, uint256, bytes32));
        Settlement storage settlement = _settlements[settlementMessageId];
        if (
            settlement.status != SettlementStatus.Locked ||
            envelope.sourceChainId != settlement.targetChainId ||
            envelope.sourceSender != settlement.destinationVault ||
            envelope.workflowId != settlement.workflowId || envelope.nodeId != settlement.nodeId ||
            envelope.attemptId != settlement.attemptId || envelope.attemptNumber != settlement.attemptNumber ||
            envelope.routeKey != settlement.routeKey || envelope.routeVersion != settlement.routeVersion ||
            payoutAmount == 0 || payoutEvidenceHash == bytes32(0)
        ) revert InvalidInput();
        processedConfirmations[envelope.messageId] = true;
        isAttemptConfirmed[settlement.attemptId] = true;
        settlement.payoutAmount = payoutAmount;
        settlement.status = SettlementStatus.Confirmed;
        emit SourceSettlementConfirmed(settlementMessageId, settlement.attemptId, settlement.bridgeAmount, payoutAmount, payoutEvidenceHash);
    }

    function settlementStatus(bytes32 messageId) external view returns (SettlementStatus) {
        return _settlements[messageId].status;
    }

    function settlementAmounts(bytes32 messageId) external view returns (
        uint256 bridgeAmount,
        uint256 payoutAmount,
        address payee
    ) {
        Settlement storage settlement = _settlements[messageId];
        return (settlement.bridgeAmount, settlement.payoutAmount, settlement.payee);
    }

    function _emitMessage(ExperimentProtocol.Envelope memory envelope, bytes memory payload) private {
        emit ExperimentMessageSent(
            envelope.messageType, envelope.messageId, envelope.attemptId, abi.encode(envelope), payload
        );
    }
}
