// SPDX-License-Identifier: MIT
pragma solidity ^0.8.28;

import {ReentrancyGuard} from "@openzeppelin/contracts/utils/ReentrancyGuard.sol";
import {FeePolicy} from "./FeePolicy.sol";

contract ResultRelayGasVault is ReentrancyGuard {
    enum DepositStatus { None, Active, Refunded }

    struct Deposit {
        address depositor;
        bytes32 routeKey;
        uint32 routeVersion;
        uint256 deposited;
        uint256 charged;
        uint256 refunded;
        DepositStatus status;
    }

    address public immutable controller;
    FeePolicy public immutable feePolicy;
    mapping(bytes32 attemptId => Deposit deposit) public deposits;
    mapping(bytes32 feeEntryId => bool processed) public processedFees;
    mapping(bytes32 attemptId => bool terminal) public terminalAttempts;
    uint256 public platformPool;

    event AttemptGasDeposited(bytes32 indexed attemptId, address indexed depositor, bytes32 indexed routeKey, uint32 routeVersion, uint256 amount);
    event AttemptGasCharged(bytes32 indexed attemptId, bytes32 indexed feeEntryId, FeePolicy.FeeCategory category, address recipient, uint256 amount);
    event AttemptGasRefunded(bytes32 indexed attemptId, address indexed depositor, uint256 amount);
    event PlatformGasToppedUp(address indexed payer, uint256 amount);
    event PlatformFailureCompensated(bytes32 indexed attemptId, address indexed recipient, uint256 amount);
    event AttemptMarkedTerminal(bytes32 indexed attemptId);

    error Unauthorized();
    error InvalidInput();
    error InsufficientBalance(uint256 available, uint256 requested);

    constructor(address _controller, FeePolicy _feePolicy) {
        if (_controller == address(0) || address(_feePolicy) == address(0)) revert InvalidInput();
        controller = _controller;
        feePolicy = _feePolicy;
    }

    modifier onlyController() {
        if (msg.sender != controller) revert Unauthorized();
        _;
    }

    function topUpPlatformPool() external payable {
        if (msg.value == 0) revert InvalidInput();
        platformPool += msg.value;
        emit PlatformGasToppedUp(msg.sender, msg.value);
    }

    function depositForAttempt(bytes32 attemptId, bytes32 routeKey, uint32 routeVersion) external payable {
        if (attemptId == bytes32(0) || routeKey == bytes32(0) || routeVersion == 0 || msg.value == 0) revert InvalidInput();
        Deposit storage deposit = deposits[attemptId];
        if (deposit.status == DepositStatus.Refunded || (deposit.depositor != address(0) && deposit.depositor != msg.sender)) {
            revert InvalidInput();
        }
        if (deposit.depositor == address(0)) {
            deposit.depositor = msg.sender;
            deposit.routeKey = routeKey;
            deposit.routeVersion = routeVersion;
            deposit.status = DepositStatus.Active;
        } else if (deposit.routeKey != routeKey || deposit.routeVersion != routeVersion) {
            revert InvalidInput();
        }
        deposit.deposited += msg.value;
        emit AttemptGasDeposited(attemptId, msg.sender, routeKey, routeVersion, msg.value);
    }

    function chargeAttempt(
        bytes32 attemptId,
        bytes32 feeEntryId,
        FeePolicy.FeeCategory category,
        address payable recipient,
        uint256 amount
    ) external onlyController nonReentrant {
        Deposit storage deposit = deposits[attemptId];
        if (deposit.status != DepositStatus.Active || feeEntryId == bytes32(0) || processedFees[feeEntryId] || recipient == address(0) || amount == 0) {
            revert InvalidInput();
        }
        feePolicy.validateFee(deposit.routeKey, deposit.routeVersion, category, amount);
        uint256 available = deposit.deposited - deposit.charged - deposit.refunded;
        if (amount > available) revert InsufficientBalance(available, amount);
        processedFees[feeEntryId] = true;
        deposit.charged += amount;
        (bool sent, ) = recipient.call{value: amount}("");
        if (!sent) revert InvalidInput();
        emit AttemptGasCharged(attemptId, feeEntryId, category, recipient, amount);
    }

    function compensatePlatformFailure(bytes32 attemptId, address payable recipient, uint256 amount) external onlyController nonReentrant {
        if (recipient == address(0) || amount == 0 || amount > platformPool) revert InvalidInput();
        platformPool -= amount;
        (bool sent, ) = recipient.call{value: amount}("");
        if (!sent) revert InvalidInput();
        emit PlatformFailureCompensated(attemptId, recipient, amount);
    }

    function markAttemptTerminal(bytes32 attemptId) external onlyController {
        if (attemptId == bytes32(0) || deposits[attemptId].status != DepositStatus.Active) revert InvalidInput();
        terminalAttempts[attemptId] = true;
        emit AttemptMarkedTerminal(attemptId);
    }

    function refundAttempt(bytes32 attemptId) external nonReentrant returns (uint256 amount) {
        Deposit storage deposit = deposits[attemptId];
        if (deposit.status != DepositStatus.Active) revert InvalidInput();
        if (msg.sender != deposit.depositor && msg.sender != controller) revert Unauthorized();
        if (!terminalAttempts[attemptId]) revert InvalidInput();
        amount = deposit.deposited - deposit.charged - deposit.refunded;
        deposit.refunded += amount;
        deposit.status = DepositStatus.Refunded;
        if (amount > 0) {
            (bool sent, ) = payable(deposit.depositor).call{value: amount}("");
            if (!sent) revert InvalidInput();
        }
        emit AttemptGasRefunded(attemptId, deposit.depositor, amount);
    }
}
