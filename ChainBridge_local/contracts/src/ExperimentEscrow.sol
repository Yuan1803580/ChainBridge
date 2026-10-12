// SPDX-License-Identifier: MIT
pragma solidity ^0.8.28;

import {IERC20} from "@openzeppelin/contracts/token/ERC20/IERC20.sol";
import {SafeERC20} from "@openzeppelin/contracts/token/ERC20/utils/SafeERC20.sol";
import {ReentrancyGuard} from "@openzeppelin/contracts/utils/ReentrancyGuard.sol";
import {ExperimentProtocol} from "./ExperimentProtocol.sol";

interface IExperimentEscrowWorkflow {
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
    function workflows(bytes32 workflowId) external view returns (
        address requester,
        bytes32 termsHash,
        uint32 nodeCount,
        uint32 settledNodeCount,
        bool active
    );
}

interface IExperimentEscrowReview {
    function reviews(bytes32 attemptId) external view returns (
        bytes32 criteriaHash,
        bytes32 resultHash,
        bytes32 proofHash,
        bytes32 reasonCode,
        bytes32 reviewHash,
        uint8 decision,
        uint64 recordedAt
    );
}

interface IExperimentSourceAMM {
    function swapExactInput(address tokenIn, uint256 amountIn, uint256 minAmountOut, uint256 deadline, address recipient)
        external returns (uint256 amountOut);
}

interface IExperimentEscrowSourceVault {
    function lockAndSend(ExperimentProtocol.SettlementRequest calldata request) external returns (bytes32 messageId);
}

contract ExperimentEscrow is ReentrancyGuard {
    using SafeERC20 for IERC20;

    enum ReservationStatus { None, Reserved, Released, SettlementSent }
    struct Account {
        address payer;
        uint256 budget;
        uint256 reserved;
        uint256 spent;
        uint256 platformFee;
        uint256 refunded;
        uint256 platformFeeCap;
        bool locked;
    }
    struct Reservation {
        bytes32 workflowId;
        bytes32 nodeId;
        uint256 sourceAmount;
        ReservationStatus status;
    }
    struct PrepareSettlementRequest {
        bytes32 attemptId;
        uint32 attemptNumber;
        uint256 minBridgeAmount;
        uint256 sourceSwapDeadline;
        address payee;
        address payoutToken;
        uint256 minPayoutAmount;
        uint256 payoutDeadline;
    }
    struct NodeSnapshot {
        bytes32 workflowId;
        bytes32 currentAttemptId;
        bytes32 routeKey;
        uint32 routeVersion;
        address agent;
        uint8 status;
    }

    address public immutable coordinator;
    address public immutable platformTreasury;
    IERC20 public immutable sourceToken;
    IERC20 public immutable bridgeAsset;
    IExperimentEscrowWorkflow public immutable workflowEngine;
    IExperimentEscrowReview public immutable coordinatorReview;
    IExperimentSourceAMM public immutable sourceAMM;
    IExperimentEscrowSourceVault public immutable sourceVault;
    mapping(bytes32 workflowId => Account account) public accounts;
    mapping(bytes32 attemptId => Reservation reservation) public reservations;
    mapping(bytes32 attemptId => bytes32 settlementMessageId) public settlementMessageIds;

    event BudgetLocked(bytes32 indexed workflowId, address indexed payer, uint256 amount);
    event BudgetReserved(bytes32 indexed workflowId, bytes32 indexed nodeId, bytes32 indexed attemptId, uint256 sourceAmount);
    event ReservationReleased(bytes32 indexed workflowId, bytes32 indexed attemptId, uint256 sourceAmount);
    event SettlementPrepared(bytes32 indexed workflowId, bytes32 indexed attemptId, bytes32 indexed messageId, uint256 sourceAmount, uint256 bridgeAmount);
    event PlatformFeePaid(bytes32 indexed workflowId, uint256 amount);
    event RemainderRefunded(bytes32 indexed workflowId, address indexed payer, uint256 amount);

    error Unauthorized();
    error InvalidInput();
    error InvalidStatus();
    error InsufficientAvailable(uint256 available, uint256 requested);
    error PlatformFeeCapExceeded(uint256 cap, uint256 requestedTotal);

    constructor(
        address _coordinator,
        address _platformTreasury,
        IERC20 _sourceToken,
        IERC20 _bridgeAsset,
        IExperimentEscrowWorkflow _workflowEngine,
        IExperimentEscrowReview _coordinatorReview,
        IExperimentSourceAMM _sourceAMM,
        IExperimentEscrowSourceVault _sourceVault
    ) {
        if (
            _coordinator == address(0) || _platformTreasury == address(0) ||
            address(_sourceToken) == address(0) || address(_bridgeAsset) == address(0) ||
            address(_workflowEngine) == address(0) || address(_coordinatorReview) == address(0) ||
            address(_sourceAMM) == address(0) || address(_sourceVault) == address(0)
        ) revert InvalidInput();
        coordinator = _coordinator;
        platformTreasury = _platformTreasury;
        sourceToken = _sourceToken;
        bridgeAsset = _bridgeAsset;
        workflowEngine = _workflowEngine;
        coordinatorReview = _coordinatorReview;
        sourceAMM = _sourceAMM;
        sourceVault = _sourceVault;
    }

    modifier onlyCoordinator() {
        if (msg.sender != coordinator) revert Unauthorized();
        _;
    }

    function lockBudget(bytes32 workflowId, uint256 amount, uint256 platformFeeCap) external nonReentrant {
        if (workflowId == bytes32(0) || amount == 0 || platformFeeCap > amount || accounts[workflowId].locked) revert InvalidInput();
        (address requester, , , , bool active) = workflowEngine.workflows(workflowId);
        if (!active || requester != msg.sender) revert InvalidStatus();
        accounts[workflowId] = Account({
            payer: msg.sender,
            budget: amount,
            reserved: 0,
            spent: 0,
            platformFee: 0,
            refunded: 0,
            platformFeeCap: platformFeeCap,
            locked: true
        });
        sourceToken.safeTransferFrom(msg.sender, address(this), amount);
        emit BudgetLocked(workflowId, msg.sender, amount);
    }

    function reserve(bytes32 workflowId, bytes32 nodeId, bytes32 attemptId, uint256 sourceAmount) external onlyCoordinator {
        Account storage account = accounts[workflowId];
        if (!account.locked || attemptId == bytes32(0) || sourceAmount == 0 || reservations[attemptId].status != ReservationStatus.None) revert InvalidInput();
        (bytes32 nodeWorkflowId, , bytes32 currentAttemptId, , , , , uint8 status) = workflowEngine.nodes(nodeId);
        if (nodeWorkflowId != workflowId || currentAttemptId != attemptId || status != 3) revert InvalidStatus();
        uint256 available = availableBalance(workflowId);
        if (sourceAmount > available) revert InsufficientAvailable(available, sourceAmount);
        reservations[attemptId] = Reservation(workflowId, nodeId, sourceAmount, ReservationStatus.Reserved);
        account.reserved += sourceAmount;
        emit BudgetReserved(workflowId, nodeId, attemptId, sourceAmount);
    }

    function releaseReservation(bytes32 attemptId) external onlyCoordinator {
        Reservation storage reservation = reservations[attemptId];
        if (reservation.status != ReservationStatus.Reserved) revert InvalidStatus();
        reservation.status = ReservationStatus.Released;
        accounts[reservation.workflowId].reserved -= reservation.sourceAmount;
        emit ReservationReleased(reservation.workflowId, attemptId, reservation.sourceAmount);
    }

    function prepareSettlement(PrepareSettlementRequest calldata request)
        external onlyCoordinator nonReentrant returns (bytes32 messageId, uint256 bridgeAmount)
    {
        Reservation storage reservation = reservations[request.attemptId];
        if (reservation.status != ReservationStatus.Reserved || request.attemptNumber == 0) revert InvalidStatus();
        NodeSnapshot memory node;
        (
            node.workflowId,
            ,
            node.currentAttemptId,
            node.routeKey,
            node.routeVersion,
            ,
            node.agent,
            node.status
        ) = workflowEngine.nodes(reservation.nodeId);
        if (
            node.workflowId != reservation.workflowId || node.currentAttemptId != request.attemptId ||
            node.status != 4 || node.agent != request.payee
        ) revert InvalidStatus();
        (, , , , , uint8 decision, ) = coordinatorReview.reviews(request.attemptId);
        if (decision != 1) revert InvalidStatus();
        sourceToken.forceApprove(address(sourceAMM), reservation.sourceAmount);
        bridgeAmount = sourceAMM.swapExactInput(
            address(sourceToken), reservation.sourceAmount, request.minBridgeAmount, request.sourceSwapDeadline, address(this)
        );
        bridgeAsset.forceApprove(address(sourceVault), bridgeAmount);
        messageId = sourceVault.lockAndSend(ExperimentProtocol.SettlementRequest({
            workflowId: node.workflowId,
            nodeId: reservation.nodeId,
            attemptId: request.attemptId,
            attemptNumber: request.attemptNumber,
            routeKey: node.routeKey,
            routeVersion: node.routeVersion,
            bridgeAmount: bridgeAmount,
            payee: request.payee,
            payoutToken: request.payoutToken,
            minPayoutAmount: request.minPayoutAmount,
            payoutDeadline: request.payoutDeadline
        }));
        reservation.status = ReservationStatus.SettlementSent;
        Account storage account = accounts[node.workflowId];
        account.reserved -= reservation.sourceAmount;
        account.spent += reservation.sourceAmount;
        settlementMessageIds[request.attemptId] = messageId;
        emit SettlementPrepared(node.workflowId, request.attemptId, messageId, reservation.sourceAmount, bridgeAmount);
    }

    function payPlatformFee(bytes32 workflowId, uint256 amount) external onlyCoordinator nonReentrant {
        if (amount == 0) revert InvalidInput();
        Account storage account = accounts[workflowId];
        uint256 requestedTotal = account.platformFee + amount;
        if (requestedTotal > account.platformFeeCap) {
            revert PlatformFeeCapExceeded(account.platformFeeCap, requestedTotal);
        }
        uint256 available = availableBalance(workflowId);
        if (amount > available) revert InsufficientAvailable(available, amount);
        account.platformFee = requestedTotal;
        sourceToken.safeTransfer(platformTreasury, amount);
        emit PlatformFeePaid(workflowId, amount);
    }

    function refundRemainder(bytes32 workflowId) external nonReentrant returns (uint256 amount) {
        Account storage account = accounts[workflowId];
        if (msg.sender != account.payer && msg.sender != coordinator) revert Unauthorized();
        (, , , , bool active) = workflowEngine.workflows(workflowId);
        if (active || account.reserved != 0 || !account.locked) revert InvalidStatus();
        amount = availableBalance(workflowId);
        account.refunded += amount;
        account.locked = false;
        sourceToken.safeTransfer(account.payer, amount);
        emit RemainderRefunded(workflowId, account.payer, amount);
    }

    function availableBalance(bytes32 workflowId) public view returns (uint256) {
        Account storage account = accounts[workflowId];
        return account.budget - account.reserved - account.spent - account.platformFee - account.refunded;
    }
}
