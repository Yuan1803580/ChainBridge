// SPDX-License-Identifier: MIT
pragma solidity ^0.8.28;

import {IERC20} from "@openzeppelin/contracts/token/ERC20/IERC20.sol";
import {SafeERC20} from "@openzeppelin/contracts/token/ERC20/utils/SafeERC20.sol";
import {ReentrancyGuard} from "@openzeppelin/contracts/utils/ReentrancyGuard.sol";
import {ExperimentProtocol} from "./ExperimentProtocol.sol";

interface IExperimentPayoutAMM {
    function swapExactInput(address tokenIn, uint256 amountIn, uint256 minAmountOut, uint256 deadline, address recipient)
        external returns (uint256 amountOut);
}

interface IExperimentAttemptOracle {
    function currentAttemptByNode(bytes32 nodeId) external view returns (bytes32 attemptId);
    function latestAttemptNumberByNode(bytes32 nodeId) external view returns (uint32 attemptNumber);
}

contract ExperimentDestinationVault is ReentrancyGuard {
    using SafeERC20 for IERC20;

    enum PayoutStatus { None, Reserved, Paid }
    struct SourceRoute { uint64 sourceChainId; address sourceVault; bool active; }
    struct Payout {
        bytes32 workflowId;
        bytes32 nodeId;
        bytes32 attemptId;
        uint32 attemptNumber;
        bytes32 routeKey;
        uint32 routeVersion;
        uint64 sourceChainId;
        address sourceVault;
        address payee;
        uint256 bridgeAmount;
        uint256 minPayoutAmount;
        uint256 deadline;
        uint256 payoutAmount;
        PayoutStatus status;
    }

    address public immutable controller;
    address public immutable relay;
    uint64 public immutable chainId;
    IERC20 public immutable bridgeAsset;
    IERC20 public immutable payoutToken;
    IExperimentPayoutAMM public immutable payoutAMM;
    IExperimentAttemptOracle public immutable attemptOracle;
    uint256 public nonce;
    uint256 public reservedBridge;
    mapping(bytes32 routeKey => mapping(uint32 routeVersion => SourceRoute route)) public routes;
    mapping(bytes32 messageId => bool processedMessages) public processedMessages;
    mapping(bytes32 settlementMessageId => Payout payout) private _payouts;

    event SourceRouteConfigured(bytes32 indexed routeKey, uint32 indexed routeVersion, uint64 sourceChainId, address sourceVault, bool active);
    event DestinationLiquidityReserved(bytes32 indexed settlementMessageId, bytes32 indexed attemptId, uint256 bridgeAmount, address payee);
    event PayoutTermsUpdated(bytes32 indexed settlementMessageId, uint256 minPayoutAmount, uint256 deadline);
    event DestinationPayoutCompleted(bytes32 indexed settlementMessageId, bytes32 indexed attemptId, uint256 bridgeAmount, uint256 payoutAmount, address payee);
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
    error MessageAlreadyProcessed(bytes32 messageId);
    error StaleAttempt(bytes32 expectedAttemptId, bytes32 providedAttemptId);
    error InsufficientVaultLiquidity(uint256 available, uint256 required);

    constructor(
        address _controller,
        address _relay,
        uint64 _chainId,
        IERC20 _bridgeAsset,
        IERC20 _payoutToken,
        IExperimentPayoutAMM _payoutAMM,
        IExperimentAttemptOracle _attemptOracle
    ) {
        if (
            _controller == address(0) || _relay == address(0) || _chainId == 0 ||
            address(_bridgeAsset) == address(0) || address(_payoutToken) == address(0) ||
            address(_payoutAMM) == address(0) || address(_attemptOracle) == address(0)
        ) revert InvalidInput();
        controller = _controller;
        relay = _relay;
        chainId = _chainId;
        bridgeAsset = _bridgeAsset;
        payoutToken = _payoutToken;
        payoutAMM = _payoutAMM;
        attemptOracle = _attemptOracle;
    }

    function configureSource(bytes32 routeKey, uint32 routeVersion, uint64 sourceChainId, address sourceVault, bool active) external {
        if (msg.sender != controller) revert Unauthorized();
        if (routeKey == bytes32(0) || routeVersion == 0 || sourceChainId == 0 || sourceVault == address(0)) revert InvalidInput();
        routes[routeKey][routeVersion] = SourceRoute(sourceChainId, sourceVault, active);
        emit SourceRouteConfigured(routeKey, routeVersion, sourceChainId, sourceVault, active);
    }

    function availableLiquidity() public view returns (uint256) {
        return bridgeAsset.balanceOf(address(this)) - reservedBridge;
    }

    function receiveSettlementV2(ExperimentProtocol.Envelope calldata envelope, bytes calldata payload) external nonReentrant {
        if (msg.sender != relay) revert Unauthorized();
        if (processedMessages[envelope.messageId]) revert MessageAlreadyProcessed(envelope.messageId);
        if (
            envelope.messageType != ExperimentProtocol.SETTLEMENT || envelope.targetChainId != chainId ||
            envelope.targetReceiver != address(this) || !ExperimentProtocol.valid(envelope, payload)
        ) revert InvalidInput();
        SourceRoute memory route = routes[envelope.routeKey][envelope.routeVersion];
        if (!route.active || route.sourceChainId != envelope.sourceChainId || route.sourceVault != envelope.sourceSender) revert UnsupportedRoute();
        bytes32 currentAttempt = attemptOracle.currentAttemptByNode(envelope.nodeId);
        if (currentAttempt != envelope.attemptId || attemptOracle.latestAttemptNumberByNode(envelope.nodeId) != envelope.attemptNumber) {
            revert StaleAttempt(currentAttempt, envelope.attemptId);
        }
        (uint256 bridgeAmount, address payee, address expectedPayoutToken, uint256 minPayoutAmount, uint256 deadline) =
            abi.decode(payload, (uint256, address, address, uint256, uint256));
        if (bridgeAmount == 0 || payee == address(0) || expectedPayoutToken != address(payoutToken) || minPayoutAmount == 0) revert InvalidInput();
        uint256 available = availableLiquidity();
        if (bridgeAmount > available) revert InsufficientVaultLiquidity(available, bridgeAmount);
        processedMessages[envelope.messageId] = true;
        reservedBridge += bridgeAmount;
        _payouts[envelope.messageId] = Payout({
            workflowId: envelope.workflowId,
            nodeId: envelope.nodeId,
            attemptId: envelope.attemptId,
            attemptNumber: envelope.attemptNumber,
            routeKey: envelope.routeKey,
            routeVersion: envelope.routeVersion,
            sourceChainId: envelope.sourceChainId,
            sourceVault: envelope.sourceSender,
            payee: payee,
            bridgeAmount: bridgeAmount,
            minPayoutAmount: minPayoutAmount,
            deadline: deadline,
            payoutAmount: 0,
            status: PayoutStatus.Reserved
        });
        emit DestinationLiquidityReserved(envelope.messageId, envelope.attemptId, bridgeAmount, payee);
    }

    function executePayout(bytes32 settlementMessageId) external nonReentrant returns (uint256 payoutAmount, bytes32 confirmationMessageId) {
        if (msg.sender != relay && msg.sender != controller) revert Unauthorized();
        Payout storage payout = _payouts[settlementMessageId];
        if (payout.status != PayoutStatus.Reserved) revert InvalidInput();
        bridgeAsset.forceApprove(address(payoutAMM), payout.bridgeAmount);
        payoutAmount = payoutAMM.swapExactInput(
            address(bridgeAsset), payout.bridgeAmount, payout.minPayoutAmount, payout.deadline, payout.payee
        );
        payout.status = PayoutStatus.Paid;
        payout.payoutAmount = payoutAmount;
        reservedBridge -= payout.bridgeAmount;
        bytes32 evidenceHash = keccak256(abi.encode(settlementMessageId, payout.payee, payout.bridgeAmount, payoutAmount, block.number));
        bytes memory payload = abi.encode(settlementMessageId, payoutAmount, evidenceHash);
        ExperimentProtocol.Envelope memory envelope = ExperimentProtocol.Envelope({
            messageType: ExperimentProtocol.SETTLEMENT_CONFIRMATION,
            messageId: bytes32(0),
            sourceChainId: chainId,
            targetChainId: payout.sourceChainId,
            sourceSender: address(this),
            targetReceiver: payout.sourceVault,
            workflowId: payout.workflowId,
            nodeId: payout.nodeId,
            attemptId: payout.attemptId,
            attemptNumber: payout.attemptNumber,
            routeKey: payout.routeKey,
            routeVersion: payout.routeVersion,
            nonce: ++nonce,
            payloadHash: keccak256(payload)
        });
        envelope.messageId = ExperimentProtocol.messageId(envelope);
        confirmationMessageId = envelope.messageId;
        emit DestinationPayoutCompleted(settlementMessageId, payout.attemptId, payout.bridgeAmount, payoutAmount, payout.payee);
        _emitMessage(envelope, payload);
    }

    function updatePayoutTerms(bytes32 settlementMessageId, uint256 minPayoutAmount, uint256 deadline) external {
        if (msg.sender != controller) revert Unauthorized();
        Payout storage payout = _payouts[settlementMessageId];
        if (payout.status != PayoutStatus.Reserved || minPayoutAmount == 0 || deadline <= block.timestamp) {
            revert InvalidInput();
        }
        payout.minPayoutAmount = minPayoutAmount;
        payout.deadline = deadline;
        emit PayoutTermsUpdated(settlementMessageId, minPayoutAmount, deadline);
    }

    function payoutStatus(bytes32 settlementMessageId) external view returns (PayoutStatus) {
        return _payouts[settlementMessageId].status;
    }

    function payoutAmounts(bytes32 settlementMessageId) external view returns (
        uint256 bridgeAmount,
        uint256 minPayoutAmount,
        uint256 payoutAmount,
        address payee
    ) {
        Payout storage payout = _payouts[settlementMessageId];
        return (payout.bridgeAmount, payout.minPayoutAmount, payout.payoutAmount, payout.payee);
    }

    function _emitMessage(ExperimentProtocol.Envelope memory envelope, bytes memory payload) private {
        emit ExperimentMessageSent(
            envelope.messageType, envelope.messageId, envelope.attemptId, abi.encode(envelope), payload
        );
    }
}
