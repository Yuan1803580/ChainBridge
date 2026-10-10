// SPDX-License-Identifier: MIT
pragma solidity ^0.8.28;

import {IERC20} from "@openzeppelin/contracts/token/ERC20/IERC20.sol";
import {SafeERC20} from "@openzeppelin/contracts/token/ERC20/utils/SafeERC20.sol";

contract DynamicSettlementSender {
    using SafeERC20 for IERC20;

    enum SettlementStatus { None, Pending, Committed, Confirmed, Cancelled }

    struct Route { address receiver; address destinationToken; bool active; }
    struct Settlement {
        bytes32 attemptId;
        bytes32 workflowId;
        uint64 destinationChainId;
        address receiver;
        address destinationToken;
        address payee;
        uint256 amount;
        SettlementStatus status;
    }

    address public immutable controller;
    address public immutable relay;
    address public immutable authorizedEscrow;
    IERC20 public immutable sourceToken;
    uint256 public nonce;
    mapping(uint64 destinationChainId => Route route) public routes;
    mapping(bytes32 messageId => Settlement settlement) public settlements;

    event SettlementRouteConfigured(uint64 indexed destinationChainId, address indexed receiver, address indexed destinationToken, bool active);
    event DynamicSettlementSent(bytes32 indexed messageId, bytes32 indexed workflowId, bytes32 indexed attemptId, uint64 destinationChainId, address receiver, address destinationToken, address payee, uint256 amount);
    event SourceSettlementCommitted(bytes32 indexed messageId, bytes32 indexed attemptId);
    event SourceSettlementConfirmed(bytes32 indexed messageId, bytes32 indexed attemptId, bytes32 destinationTxHash);
    event SettlementCancelled(bytes32 indexed messageId, bytes32 indexed attemptId, address refundRecipient);

    error Unauthorized();
    error InvalidInput();
    error InvalidStatus();

    constructor(address _controller, address _relay, address _authorizedEscrow, IERC20 _sourceToken) {
        if (_controller == address(0) || _relay == address(0) || _authorizedEscrow == address(0) || address(_sourceToken) == address(0)) revert InvalidInput();
        controller = _controller;
        relay = _relay;
        authorizedEscrow = _authorizedEscrow;
        sourceToken = _sourceToken;
    }

    function configureRoute(uint64 destinationChainId, address receiver, address destinationToken, bool active) external {
        if (msg.sender != controller) revert Unauthorized();
        if (destinationChainId == 0 || receiver == address(0) || destinationToken == address(0)) revert InvalidInput();
        routes[destinationChainId] = Route(receiver, destinationToken, active);
        emit SettlementRouteConfigured(destinationChainId, receiver, destinationToken, active);
    }

    function sendSettlement(
        bytes32 workflowId,
        bytes32 attemptId,
        uint64 destinationChainId,
        address payee,
        uint256 amount
    ) external returns (bytes32 messageId) {
        if (msg.sender != authorizedEscrow) revert Unauthorized();
        messageId = keccak256(abi.encode(address(this), workflowId, attemptId, destinationChainId, ++nonce));
        _sendSettlement(messageId, workflowId, attemptId, destinationChainId, payee, amount);
    }

    function sendSettlementWithMessageId(
        bytes32 messageId,
        bytes32 workflowId,
        bytes32 attemptId,
        uint64 destinationChainId,
        address payee,
        uint256 amount
    ) external {
        if (msg.sender != authorizedEscrow) revert Unauthorized();
        _sendSettlement(messageId, workflowId, attemptId, destinationChainId, payee, amount);
    }

    function _sendSettlement(
        bytes32 messageId,
        bytes32 workflowId,
        bytes32 attemptId,
        uint64 destinationChainId,
        address payee,
        uint256 amount
    ) private {
        Route memory route = routes[destinationChainId];
        if (messageId == bytes32(0) || settlements[messageId].status != SettlementStatus.None || !route.active || workflowId == bytes32(0) || attemptId == bytes32(0) || payee == address(0) || amount == 0) revert InvalidInput();
        settlements[messageId] = Settlement(attemptId, workflowId, destinationChainId, route.receiver, route.destinationToken, payee, amount, SettlementStatus.Pending);
        emit DynamicSettlementSent(messageId, workflowId, attemptId, destinationChainId, route.receiver, route.destinationToken, payee, amount);
    }

    function confirmSettlement(bytes32 messageId, bytes32 destinationTxHash) external {
        if (msg.sender != relay) revert Unauthorized();
        Settlement storage settlement = settlements[messageId];
        if (settlement.status != SettlementStatus.Committed || destinationTxHash == bytes32(0)) revert InvalidStatus();
        settlement.status = SettlementStatus.Confirmed;
        emit SourceSettlementConfirmed(messageId, settlement.attemptId, destinationTxHash);
    }

    function commitSettlement(bytes32 messageId) external {
        if (msg.sender != relay) revert Unauthorized();
        Settlement storage settlement = settlements[messageId];
        if (settlement.status != SettlementStatus.Pending) revert InvalidStatus();
        settlement.status = SettlementStatus.Committed;
        emit SourceSettlementCommitted(messageId, settlement.attemptId);
    }

    function cancelSettlement(bytes32 messageId, address refundRecipient) external {
        if (msg.sender != authorizedEscrow) revert Unauthorized();
        Settlement storage settlement = settlements[messageId];
        if (settlement.status != SettlementStatus.Pending || refundRecipient == address(0)) revert InvalidStatus();
        settlement.status = SettlementStatus.Cancelled;
        sourceToken.safeTransfer(refundRecipient, settlement.amount);
        emit SettlementCancelled(messageId, settlement.attemptId, refundRecipient);
    }

    function isConfirmed(bytes32 messageId) external view returns (bool) {
        return settlements[messageId].status == SettlementStatus.Confirmed;
    }

    function isCommitted(bytes32 messageId) external view returns (bool) {
        SettlementStatus status = settlements[messageId].status;
        return status == SettlementStatus.Committed || status == SettlementStatus.Confirmed;
    }
}
