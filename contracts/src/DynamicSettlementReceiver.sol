// SPDX-License-Identifier: MIT
pragma solidity ^0.8.28;

import {IERC20} from "@openzeppelin/contracts/token/ERC20/IERC20.sol";
import {SafeERC20} from "@openzeppelin/contracts/token/ERC20/utils/SafeERC20.sol";
import {ReentrancyGuard} from "@openzeppelin/contracts/utils/ReentrancyGuard.sol";

interface IMintableSettlementToken {
    function mint(address to, uint256 amount) external;
}

interface ILocalPayoutSwap {
    function payoutToken() external view returns (IERC20);
    function swapExactInput(uint256 settlementAmount, uint256 minPayoutAmount, uint256 deadline, address payee)
        external
        returns (uint256 payoutAmount);
}

contract DynamicSettlementReceiver is ReentrancyGuard {
    using SafeERC20 for IERC20;

    struct SourceRoute { address sender; bool active; }
    struct PayoutRoute { ILocalPayoutSwap adapter; IERC20 token; bool active; }
    struct PayoutRecord { address payee; address payoutToken; uint256 settlementAmount; uint256 payoutAmount; }

    address public immutable controller;
    address public immutable relay;
    IMintableSettlementToken public immutable token;
    mapping(uint64 sourceChainId => SourceRoute route) public routes;
    mapping(bytes32 messageId => bool processed) public processedMessages;
    PayoutRoute public payoutRoute;
    mapping(bytes32 messageId => PayoutRecord record) public payoutRecords;

    event SourceRouteConfigured(uint64 indexed sourceChainId, address indexed sender, bool active);
    event PayoutRouteConfigured(address indexed adapter, address indexed payoutToken, bool active);
    event DynamicSettlementReceived(bytes32 indexed messageId, bytes32 indexed workflowId, bytes32 indexed attemptId, uint64 sourceChainId, address payee, uint256 amount);
    event DynamicSettlementSwapped(bytes32 indexed messageId, bytes32 indexed workflowId, bytes32 indexed attemptId, address payee, address payoutToken, uint256 settlementAmount, uint256 payoutAmount);

    error Unauthorized();
    error InvalidInput();
    error UnsupportedRoute();
    error MessageAlreadyProcessed(bytes32 messageId);

    constructor(address _controller, address _relay, IMintableSettlementToken _token) {
        if (_controller == address(0) || _relay == address(0) || address(_token) == address(0)) revert InvalidInput();
        controller = _controller;
        relay = _relay;
        token = _token;
    }

    function configureSource(uint64 sourceChainId, address sender, bool active) external {
        if (msg.sender != controller) revert Unauthorized();
        if (sourceChainId == 0 || sender == address(0)) revert InvalidInput();
        routes[sourceChainId] = SourceRoute(sender, active);
        emit SourceRouteConfigured(sourceChainId, sender, active);
    }

    function configurePayout(address adapter, address payoutToken, bool active) external {
        if (msg.sender != controller) revert Unauthorized();
        if (adapter == address(0) || payoutToken == address(0)) revert InvalidInput();
        if (address(ILocalPayoutSwap(adapter).payoutToken()) != payoutToken) revert InvalidInput();
        payoutRoute = PayoutRoute(ILocalPayoutSwap(adapter), IERC20(payoutToken), active);
        emit PayoutRouteConfigured(adapter, payoutToken, active);
    }

    function receiveSettlement(
        bytes32 messageId,
        uint64 sourceChainId,
        address sourceSender,
        bytes32 workflowId,
        bytes32 attemptId,
        address destinationToken,
        address payee,
        uint256 amount
    ) external nonReentrant {
        _validateSettlement(messageId, sourceChainId, sourceSender, workflowId, attemptId, destinationToken, payee, amount);
        processedMessages[messageId] = true;
        token.mint(payee, amount);
        emit DynamicSettlementReceived(messageId, workflowId, attemptId, sourceChainId, payee, amount);
    }

    function receiveSettlementAndSwap(
        bytes32 messageId,
        uint64 sourceChainId,
        address sourceSender,
        bytes32 workflowId,
        bytes32 attemptId,
        address destinationToken,
        address payee,
        uint256 amount,
        address expectedPayoutToken,
        uint256 minPayoutAmount,
        uint256 quoteDeadline
    ) external nonReentrant returns (uint256 payoutAmount) {
        _validateSettlement(messageId, sourceChainId, sourceSender, workflowId, attemptId, destinationToken, payee, amount);
        PayoutRoute memory route = payoutRoute;
        if (!route.active || address(route.token) != expectedPayoutToken) revert UnsupportedRoute();
        processedMessages[messageId] = true;
        token.mint(address(this), amount);
        IERC20(address(token)).forceApprove(address(route.adapter), amount);
        payoutAmount = route.adapter.swapExactInput(amount, minPayoutAmount, quoteDeadline, payee);
        payoutRecords[messageId] = PayoutRecord(payee, expectedPayoutToken, amount, payoutAmount);
        emit DynamicSettlementSwapped(messageId, workflowId, attemptId, payee, expectedPayoutToken, amount, payoutAmount);
    }

    function _validateSettlement(
        bytes32 messageId,
        uint64 sourceChainId,
        address sourceSender,
        bytes32 workflowId,
        bytes32 attemptId,
        address destinationToken,
        address payee,
        uint256 amount
    ) private view {
        if (msg.sender != relay) revert Unauthorized();
        if (processedMessages[messageId]) revert MessageAlreadyProcessed(messageId);
        SourceRoute memory route = routes[sourceChainId];
        if (!route.active || route.sender != sourceSender) revert UnsupportedRoute();
        if (messageId == bytes32(0) || workflowId == bytes32(0) || attemptId == bytes32(0) || destinationToken != address(token) || payee == address(0) || amount == 0) revert InvalidInput();
    }
}
