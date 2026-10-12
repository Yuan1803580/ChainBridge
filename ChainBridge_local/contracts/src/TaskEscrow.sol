// SPDX-License-Identifier: MIT
pragma solidity ^0.8.28;

import {IERC20} from "@openzeppelin/contracts/token/ERC20/IERC20.sol";
import {SafeERC20} from "@openzeppelin/contracts/token/ERC20/utils/SafeERC20.sol";
import {ReentrancyGuard} from "@openzeppelin/contracts/utils/ReentrancyGuard.sol";

interface IDynamicSettlementSender {
    function sourceToken() external view returns (IERC20);
    function sendSettlement(bytes32 workflowId, bytes32 attemptId, uint64 destinationChainId, address payee, uint256 amount) external returns (bytes32 messageId);
    function sendSettlementWithMessageId(bytes32 messageId, bytes32 workflowId, bytes32 attemptId, uint64 destinationChainId, address payee, uint256 amount) external;
    function cancelSettlement(bytes32 messageId, address refundRecipient) external;
    function isConfirmed(bytes32 messageId) external view returns (bool);
}

interface IWorkflowEscrowLifecycle {
    function workflows(bytes32 workflowId) external view returns (
        address requester,
        bytes32 termsHash,
        uint32 nodeCount,
        uint32 settledNodeCount,
        bool active
    );

    function workflowFailureStatus(bytes32 workflowId) external view returns (uint8);

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

interface ICoordinatorReviewStatus {
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

interface IReviewManagerV2 {
    function isSettlementAllowed(bytes32 attemptId) external view returns (bool);
}

contract TaskEscrow is ReentrancyGuard {
    using SafeERC20 for IERC20;

    enum WorkflowEscrowStatus {
        None,
        Locked,
        Completed,
        Refunded
    }

    enum ReservationStatus {
        None,
        Reserved,
        Released,
        Paid,
        SettlementPending
    }

    struct WorkflowAccount {
        address payer;
        IERC20 token;
        bytes32 termsHash;
        uint256 budget;
        uint256 reserved;
        uint256 paid;
        uint256 platformFeeLimit;
        uint256 platformFeePaid;
        uint256 refunded;
        WorkflowEscrowStatus status;
    }

    struct Reservation {
        bytes32 workflowId;
        bytes32 nodeId;
        uint256 amount;
        ReservationStatus status;
    }

    address public immutable coordinator;
    address public immutable platformTreasury;
    IWorkflowEscrowLifecycle public workflowEngine;
    ICoordinatorReviewStatus public coordinatorReview;
    IERC20 public allowedToken;
    IReviewManagerV2 public reviewManagerV2;
    bool public protocolConfigured;
    mapping(bytes32 workflowId => WorkflowAccount account) public workflowAccounts;
    mapping(bytes32 attemptId => Reservation reservation) public reservations;
    mapping(bytes32 workflowId => uint256 amount) public pendingSettlements;
    mapping(bytes32 attemptId => bytes32 messageId) public settlementMessageIds;
    mapping(bytes32 attemptId => address sender) public settlementSenders;

    event WorkflowBudgetLocked(
        bytes32 indexed workflowId,
        address indexed payer,
        address indexed token,
        uint256 budget,
        uint256 platformFeeLimit
    );
    event BudgetReserved(bytes32 indexed workflowId, bytes32 indexed nodeId, bytes32 indexed attemptId, uint256 amount);
    event ReservationReleased(bytes32 indexed workflowId, bytes32 indexed attemptId, uint256 amount);
    event ReservationPaid(
        bytes32 indexed workflowId,
        bytes32 indexed attemptId,
        address indexed recipient,
        uint256 amount
    );
    event ReservationSettlementPending(bytes32 indexed workflowId, bytes32 indexed attemptId, bytes32 indexed messageId, uint64 destinationChainId, address recipient, uint256 amount);
    event ReservationSettlementCancelled(bytes32 indexed workflowId, bytes32 indexed attemptId, bytes32 indexed messageId);
    event PlatformFeePaid(bytes32 indexed workflowId, uint256 amount);
    event WorkflowRefunded(bytes32 indexed workflowId, address indexed payer, uint256 amount);
    event FailedWorkflowRefunded(bytes32 indexed workflowId, address indexed payer, uint256 amount);
    event ProtocolConfigured(address indexed workflowEngine, address indexed coordinatorReview, address indexed allowedToken);
    event ReviewManagerV2Configured(address indexed reviewManager);

    error Unauthorized();
    error InvalidInput();
    error AlreadyExists(bytes32 id);
    error NotFound(bytes32 id);
    error InvalidStatus();
    error InsufficientAvailable(uint256 available, uint256 requested);

    constructor(address _coordinator, address _platformTreasury) {
        if (_coordinator == address(0) || _platformTreasury == address(0)) revert InvalidInput();
        coordinator = _coordinator;
        platformTreasury = _platformTreasury;
    }

    modifier onlyCoordinator() {
        if (msg.sender != coordinator) revert Unauthorized();
        _;
    }

    function configureProtocol(
        IWorkflowEscrowLifecycle _workflowEngine,
        ICoordinatorReviewStatus _coordinatorReview,
        IERC20 _allowedToken
    ) external onlyCoordinator {
        if (
            protocolConfigured ||
            address(_workflowEngine) == address(0) ||
            address(_coordinatorReview) == address(0) ||
            address(_allowedToken) == address(0)
        ) revert InvalidInput();
        workflowEngine = _workflowEngine;
        coordinatorReview = _coordinatorReview;
        allowedToken = _allowedToken;
        protocolConfigured = true;
        emit ProtocolConfigured(address(_workflowEngine), address(_coordinatorReview), address(_allowedToken));
    }

    function configureReviewManagerV2(IReviewManagerV2 _reviewManagerV2) external onlyCoordinator {
        if (address(_reviewManagerV2) == address(0) || address(reviewManagerV2) != address(0)) revert InvalidInput();
        reviewManagerV2 = _reviewManagerV2;
        emit ReviewManagerV2Configured(address(_reviewManagerV2));
    }

    function lockBudget(
        bytes32 workflowId,
        bytes32 termsHash,
        address token,
        uint256 budget,
        uint256 platformFeeLimit
    ) external nonReentrant {
        if (
            !protocolConfigured ||
            workflowId == bytes32(0) ||
            termsHash == bytes32(0) ||
            token == address(0) ||
            budget == 0 ||
            platformFeeLimit > budget
        ) revert InvalidInput();
        if (token != address(allowedToken)) revert InvalidInput();
        if (workflowAccounts[workflowId].status != WorkflowEscrowStatus.None) revert AlreadyExists(workflowId);
        workflowAccounts[workflowId] = WorkflowAccount({
            payer: msg.sender,
            token: IERC20(token),
            termsHash: termsHash,
            budget: budget,
            reserved: 0,
            paid: 0,
            platformFeeLimit: platformFeeLimit,
            platformFeePaid: 0,
            refunded: 0,
            status: WorkflowEscrowStatus.Locked
        });
        IERC20(token).safeTransferFrom(msg.sender, address(this), budget);
        emit WorkflowBudgetLocked(workflowId, msg.sender, token, budget, platformFeeLimit);
    }

    function reserve(
        bytes32 workflowId,
        bytes32 nodeId,
        bytes32 attemptId,
        uint256 amount
    ) external onlyCoordinator {
        WorkflowAccount storage account = _locked(workflowId);
        if (nodeId == bytes32(0) || attemptId == bytes32(0) || amount == 0) revert InvalidInput();
        if (reservations[attemptId].status != ReservationStatus.None) revert AlreadyExists(attemptId);
        _requireCurrentAttempt(workflowId, nodeId, attemptId, 3);
        uint256 available = availableBalance(workflowId);
        if (amount > available) revert InsufficientAvailable(available, amount);
        reservations[attemptId] = Reservation({
            workflowId: workflowId,
            nodeId: nodeId,
            amount: amount,
            status: ReservationStatus.Reserved
        });
        account.reserved += amount;
        emit BudgetReserved(workflowId, nodeId, attemptId, amount);
    }

    function releaseReservation(bytes32 attemptId) external onlyCoordinator {
        Reservation storage reservation = reservations[attemptId];
        if (reservation.status == ReservationStatus.None) revert NotFound(attemptId);
        if (reservation.status != ReservationStatus.Reserved) revert InvalidStatus();
        WorkflowAccount storage account = _locked(reservation.workflowId);
        reservation.status = ReservationStatus.Released;
        account.reserved -= reservation.amount;
        emit ReservationReleased(reservation.workflowId, attemptId, reservation.amount);
    }

    function payReservation(bytes32 attemptId, address recipient) external onlyCoordinator nonReentrant {
        Reservation storage reservation = reservations[attemptId];
        if (reservation.status == ReservationStatus.None) revert NotFound(attemptId);
        if (reservation.status != ReservationStatus.Reserved || recipient == address(0)) revert InvalidStatus();
        _requirePayableAttempt(reservation, attemptId);
        WorkflowAccount storage account = _locked(reservation.workflowId);
        reservation.status = ReservationStatus.Paid;
        account.reserved -= reservation.amount;
        account.paid += reservation.amount;
        account.token.safeTransfer(recipient, reservation.amount);
        emit ReservationPaid(reservation.workflowId, attemptId, recipient, reservation.amount);
    }

    function initiateCrossChainPayment(
        bytes32 attemptId,
        IDynamicSettlementSender sender,
        uint64 destinationChainId,
        address recipient
    ) external onlyCoordinator nonReentrant returns (bytes32 messageId) {
        Reservation storage reservation = reservations[attemptId];
        if (reservation.status == ReservationStatus.None) revert NotFound(attemptId);
        if (reservation.status != ReservationStatus.Reserved || address(sender) == address(0) || recipient == address(0)) revert InvalidStatus();
        _requirePayableAttempt(reservation, attemptId);
        WorkflowAccount storage account = _locked(reservation.workflowId);
        if (address(sender.sourceToken()) != address(account.token)) revert InvalidInput();
        reservation.status = ReservationStatus.SettlementPending;
        account.reserved -= reservation.amount;
        pendingSettlements[reservation.workflowId] += reservation.amount;
        account.token.safeTransfer(address(sender), reservation.amount);
        messageId = sender.sendSettlement(reservation.workflowId, attemptId, destinationChainId, recipient, reservation.amount);
        settlementMessageIds[attemptId] = messageId;
        settlementSenders[attemptId] = address(sender);
        emit ReservationSettlementPending(reservation.workflowId, attemptId, messageId, destinationChainId, recipient, reservation.amount);
    }

    function initiateCrossChainPaymentWithMessageId(
        bytes32 attemptId,
        IDynamicSettlementSender sender,
        uint64 destinationChainId,
        address recipient,
        bytes32 messageId
    ) external onlyCoordinator nonReentrant {
        Reservation storage reservation = reservations[attemptId];
        if (reservation.status == ReservationStatus.None) revert NotFound(attemptId);
        if (reservation.status != ReservationStatus.Reserved || address(sender) == address(0) || recipient == address(0) || messageId == bytes32(0)) revert InvalidStatus();
        _requirePayableAttempt(reservation, attemptId);
        WorkflowAccount storage account = _locked(reservation.workflowId);
        if (address(sender.sourceToken()) != address(account.token)) revert InvalidInput();
        reservation.status = ReservationStatus.SettlementPending;
        account.reserved -= reservation.amount;
        pendingSettlements[reservation.workflowId] += reservation.amount;
        account.token.safeTransfer(address(sender), reservation.amount);
        sender.sendSettlementWithMessageId(messageId, reservation.workflowId, attemptId, destinationChainId, recipient, reservation.amount);
        settlementMessageIds[attemptId] = messageId;
        settlementSenders[attemptId] = address(sender);
        emit ReservationSettlementPending(reservation.workflowId, attemptId, messageId, destinationChainId, recipient, reservation.amount);
    }

    function confirmCrossChainPayment(bytes32 attemptId, address recipient) external onlyCoordinator {
        Reservation storage reservation = reservations[attemptId];
        bytes32 messageId = settlementMessageIds[attemptId];
        IDynamicSettlementSender sender = IDynamicSettlementSender(settlementSenders[attemptId]);
        if (reservation.status != ReservationStatus.SettlementPending || messageId == bytes32(0) || !sender.isConfirmed(messageId)) revert InvalidStatus();
        WorkflowAccount storage account = _locked(reservation.workflowId);
        reservation.status = ReservationStatus.Paid;
        pendingSettlements[reservation.workflowId] -= reservation.amount;
        account.paid += reservation.amount;
        emit ReservationPaid(reservation.workflowId, attemptId, recipient, reservation.amount);
    }

    function cancelCrossChainPayment(bytes32 attemptId) external onlyCoordinator nonReentrant {
        Reservation storage reservation = reservations[attemptId];
        bytes32 messageId = settlementMessageIds[attemptId];
        IDynamicSettlementSender sender = IDynamicSettlementSender(settlementSenders[attemptId]);
        if (reservation.status != ReservationStatus.SettlementPending || messageId == bytes32(0)) revert InvalidStatus();
        sender.cancelSettlement(messageId, address(this));
        reservation.status = ReservationStatus.Released;
        pendingSettlements[reservation.workflowId] -= reservation.amount;
        emit ReservationSettlementCancelled(reservation.workflowId, attemptId, messageId);
    }

    function payPlatformFee(bytes32 workflowId, uint256 amount) external onlyCoordinator nonReentrant {
        WorkflowAccount storage account = _locked(workflowId);
        if (amount == 0 || account.platformFeePaid + amount > account.platformFeeLimit) revert InvalidInput();
        uint256 available = availableBalance(workflowId);
        if (amount > available) revert InsufficientAvailable(available, amount);
        account.platformFeePaid += amount;
        account.token.safeTransfer(platformTreasury, amount);
        emit PlatformFeePaid(workflowId, amount);
    }

    function refundRemainder(bytes32 workflowId) external nonReentrant returns (uint256 refundAmount) {
        WorkflowAccount storage account = _locked(workflowId);
        if (msg.sender != account.payer && msg.sender != coordinator) revert Unauthorized();
        if (account.reserved != 0 || pendingSettlements[workflowId] != 0) revert InvalidStatus();
        (, , uint32 nodeCount, uint32 settledNodeCount, bool active) = workflowEngine.workflows(workflowId);
        if (active || nodeCount == 0 || settledNodeCount != nodeCount || workflowEngine.workflowFailureStatus(workflowId) != 0) {
            revert InvalidStatus();
        }
        refundAmount = availableBalance(workflowId);
        account.refunded = refundAmount;
        account.status = WorkflowEscrowStatus.Completed;
        if (refundAmount > 0) account.token.safeTransfer(account.payer, refundAmount);
        emit WorkflowRefunded(workflowId, account.payer, refundAmount);
    }

    function refundFailedWorkflow(bytes32 workflowId) external onlyCoordinator nonReentrant returns (uint256 refundAmount) {
        WorkflowAccount storage account = _locked(workflowId);
        if (account.reserved != 0 || pendingSettlements[workflowId] != 0) revert InvalidStatus();
        if (workflowEngine.workflowFailureStatus(workflowId) != 1) revert InvalidStatus();
        refundAmount = availableBalance(workflowId);
        account.refunded = refundAmount;
        account.status = WorkflowEscrowStatus.Refunded;
        if (refundAmount > 0) account.token.safeTransfer(account.payer, refundAmount);
        emit FailedWorkflowRefunded(workflowId, account.payer, refundAmount);
    }

    function availableBalance(bytes32 workflowId) public view returns (uint256) {
        WorkflowAccount storage account = workflowAccounts[workflowId];
        if (account.status == WorkflowEscrowStatus.None) revert NotFound(workflowId);
        return account.budget - account.reserved - pendingSettlements[workflowId] - account.paid - account.platformFeePaid - account.refunded;
    }

    function _locked(bytes32 workflowId) private view returns (WorkflowAccount storage account) {
        account = workflowAccounts[workflowId];
        if (account.status == WorkflowEscrowStatus.None) revert NotFound(workflowId);
        if (account.status != WorkflowEscrowStatus.Locked) revert InvalidStatus();
    }

    function _requireCurrentAttempt(
        bytes32 workflowId,
        bytes32 nodeId,
        bytes32 attemptId,
        uint8 requiredStatus
    ) private view {
        (bytes32 nodeWorkflowId, , bytes32 currentAttemptId, , , , , uint8 status) = workflowEngine.nodes(nodeId);
        if (nodeWorkflowId != workflowId || currentAttemptId != attemptId || status != requiredStatus) revert InvalidStatus();
        (, , , , bool active) = workflowEngine.workflows(workflowId);
        if (!active) revert InvalidStatus();
    }

    function _requirePayableAttempt(Reservation storage reservation, bytes32 attemptId) private view {
        _requireCurrentAttempt(reservation.workflowId, reservation.nodeId, attemptId, 4);
        (, , , , , uint8 decision, ) = coordinatorReview.reviews(attemptId);
        if (decision != 1) revert InvalidStatus();
        if (address(reviewManagerV2) != address(0) && !reviewManagerV2.isSettlementAllowed(attemptId)) {
            revert InvalidStatus();
        }
    }
}
