// SPDX-License-Identifier: MIT
pragma solidity ^0.8.28;

import {ReentrancyGuard} from "@openzeppelin/contracts/utils/ReentrancyGuard.sol";

interface IWorkflowGasLifecycle {
    function workflows(bytes32 workflowId) external view returns (
        address requester,
        bytes32 termsHash,
        uint32 nodeCount,
        uint32 settledNodeCount,
        bool active
    );

    function workflowFailureStatus(bytes32 workflowId) external view returns (uint8);
}

contract GasTreasuryA is ReentrancyGuard {
    struct SourceGasAccount {
        address payer;
        uint256 deposited;
        uint256 charged;
        uint256 refunded;
        bool closed;
    }

    address public immutable controller;
    IWorkflowGasLifecycle public workflowEngine;
    bool public workflowEngineConfigured;
    mapping(bytes32 workflowId => SourceGasAccount account) public accounts;
    mapping(bytes32 chargeId => bool charged) public processedCharges;

    event SourceGasDeposited(bytes32 indexed workflowId, address indexed payer, uint256 amount);
    event SourceGasCharged(bytes32 indexed workflowId, bytes32 indexed chargeId, address indexed recipient, uint256 amount);
    event SourceGasRefunded(bytes32 indexed workflowId, address indexed payer, uint256 amount);
    event WorkflowEngineConfigured(address indexed workflowEngine);

    error Unauthorized();
    error InvalidInput();
    error InsufficientGasBudget(uint256 available, uint256 requested);

    constructor(address _controller) {
        if (_controller == address(0)) revert InvalidInput();
        controller = _controller;
    }

    modifier onlyController() {
        if (msg.sender != controller) revert Unauthorized();
        _;
    }

    function configureWorkflowEngine(IWorkflowGasLifecycle _workflowEngine) external onlyController {
        if (workflowEngineConfigured || address(_workflowEngine) == address(0)) revert InvalidInput();
        workflowEngine = _workflowEngine;
        workflowEngineConfigured = true;
        emit WorkflowEngineConfigured(address(_workflowEngine));
    }

    function depositSourceGas(bytes32 workflowId) external payable {
        if (!workflowEngineConfigured || workflowId == bytes32(0) || msg.value == 0) revert InvalidInput();
        SourceGasAccount storage account = accounts[workflowId];
        if (account.closed || (account.payer != address(0) && account.payer != msg.sender)) revert InvalidInput();
        if (account.payer == address(0)) account.payer = msg.sender;
        account.deposited += msg.value;
        emit SourceGasDeposited(workflowId, msg.sender, msg.value);
    }

    function charge(
        bytes32 workflowId,
        bytes32 chargeId,
        address payable recipient,
        uint256 amount
    ) external onlyController nonReentrant {
        SourceGasAccount storage account = accounts[workflowId];
        if (chargeId == bytes32(0) || recipient == address(0) || amount == 0 || processedCharges[chargeId]) {
            revert InvalidInput();
        }
        uint256 available = account.deposited - account.charged - account.refunded;
        if (amount > available) revert InsufficientGasBudget(available, amount);
        processedCharges[chargeId] = true;
        account.charged += amount;
        (bool sent, ) = recipient.call{value: amount}("");
        if (!sent) revert InvalidInput();
        emit SourceGasCharged(workflowId, chargeId, recipient, amount);
    }

    function refund(bytes32 workflowId) external nonReentrant returns (uint256 amount) {
        SourceGasAccount storage account = accounts[workflowId];
        if (account.payer == address(0) || account.closed) revert InvalidInput();
        if (msg.sender != account.payer && msg.sender != controller) revert Unauthorized();
        (, , uint32 nodeCount, uint32 settledNodeCount, bool active) = workflowEngine.workflows(workflowId);
        uint8 failureStatus = workflowEngine.workflowFailureStatus(workflowId);
        bool completed = !active && nodeCount > 0 && nodeCount == settledNodeCount && failureStatus == 0;
        bool failed = !active && (failureStatus == 1 || failureStatus == 2);
        if (!completed && !failed) revert InvalidInput();
        amount = account.deposited - account.charged - account.refunded;
        account.refunded += amount;
        account.closed = true;
        if (amount > 0) {
            (bool sent, ) = payable(account.payer).call{value: amount}("");
            if (!sent) revert InvalidInput();
        }
        emit SourceGasRefunded(workflowId, account.payer, amount);
    }
}
