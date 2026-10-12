// SPDX-License-Identifier: MIT
pragma solidity ^0.8.28;

import {IERC20} from "@openzeppelin/contracts/token/ERC20/IERC20.sol";
import {SafeERC20} from "@openzeppelin/contracts/token/ERC20/utils/SafeERC20.sol";
import {IRouterClient} from "@chainlink/contracts-ccip/contracts/interfaces/IRouterClient.sol";
import {Client} from "@chainlink/contracts-ccip/contracts/libraries/Client.sol";

contract DynamicCCIPWorkflowSender {
    using SafeERC20 for IERC20;

    struct Route {
        uint64 destinationChainSelector;
        address receiver;
        address destinationToken;
        bool active;
    }

    struct WorkflowPayload {
        bytes32 workflowId;
        bytes32 attemptId;
        address payee;
        uint256 amount;
        address destinationToken;
    }

    struct Settlement {
        bytes32 workflowId;
        bytes32 attemptId;
        uint64 destinationChainId;
        address payee;
        uint256 amount;
        uint256 fee;
        bytes32 destinationTxHash;
        bool confirmed;
    }

    address public immutable controller;
    address public immutable authorizedEscrow;
    IRouterClient public immutable ccipRouter;
    IERC20 public immutable sourceToken;
    uint256 public immutable gasLimit;
    mapping(uint64 destinationChainId => Route route) public routes;
    mapping(bytes32 messageId => Settlement settlement) public settlements;

    event CCIPWorkflowRouteConfigured(
        uint64 indexed destinationChainId,
        uint64 indexed destinationChainSelector,
        address indexed receiver,
        address destinationToken,
        bool active
    );
    event CCIPWorkflowSettlementSent(
        bytes32 indexed messageId,
        bytes32 indexed workflowId,
        bytes32 indexed attemptId,
        uint64 destinationChainId,
        address payee,
        uint256 amount,
        uint256 fee
    );
    event CCIPWorkflowSettlementConfirmed(bytes32 indexed messageId, bytes32 indexed destinationTxHash);

    error Unauthorized();
    error InvalidInput();
    error UnsupportedRoute(uint64 destinationChainId);
    error InsufficientFeeBalance(uint256 available, uint256 required);
    error ClientSuppliedMessageIdUnsupported();

    constructor(
        address _controller,
        address _authorizedEscrow,
        IRouterClient _ccipRouter,
        IERC20 _sourceToken,
        uint256 _gasLimit
    ) {
        if (
            _controller == address(0) ||
            _authorizedEscrow == address(0) ||
            address(_ccipRouter) == address(0) ||
            address(_sourceToken) == address(0) ||
            _gasLimit == 0
        ) revert InvalidInput();
        controller = _controller;
        authorizedEscrow = _authorizedEscrow;
        ccipRouter = _ccipRouter;
        sourceToken = _sourceToken;
        gasLimit = _gasLimit;
    }

    receive() external payable {}

    function configureRoute(
        uint64 destinationChainId,
        uint64 destinationChainSelector,
        address receiver,
        address destinationToken,
        bool active
    ) external {
        if (msg.sender != controller) revert Unauthorized();
        if (
            destinationChainId == 0 ||
            destinationChainSelector == 0 ||
            receiver == address(0) ||
            destinationToken == address(0)
        ) revert InvalidInput();
        routes[destinationChainId] = Route(destinationChainSelector, receiver, destinationToken, active);
        emit CCIPWorkflowRouteConfigured(destinationChainId, destinationChainSelector, receiver, destinationToken, active);
    }

    function quoteFee(
        bytes32 workflowId,
        bytes32 attemptId,
        uint64 destinationChainId,
        address payee,
        uint256 amount
    ) external view returns (uint256) {
        Route memory route = _route(destinationChainId);
        WorkflowPayload memory payload = _payload(workflowId, attemptId, payee, amount, route.destinationToken);
        return ccipRouter.getFee(route.destinationChainSelector, _message(route, payload));
    }

    function sendSettlement(
        bytes32 workflowId,
        bytes32 attemptId,
        uint64 destinationChainId,
        address payee,
        uint256 amount
    ) external returns (bytes32 messageId) {
        if (msg.sender != authorizedEscrow) revert Unauthorized();
        Route memory route = _route(destinationChainId);
        WorkflowPayload memory payload = _payload(workflowId, attemptId, payee, amount, route.destinationToken);
        Client.EVM2AnyMessage memory message = _message(route, payload);
        uint256 fee = ccipRouter.getFee(route.destinationChainSelector, message);
        if (address(this).balance < fee) revert InsufficientFeeBalance(address(this).balance, fee);
        sourceToken.forceApprove(address(ccipRouter), amount);
        messageId = ccipRouter.ccipSend{value: fee}(route.destinationChainSelector, message);
        settlements[messageId] = Settlement(workflowId, attemptId, destinationChainId, payee, amount, fee, bytes32(0), false);
        emit CCIPWorkflowSettlementSent(messageId, workflowId, attemptId, destinationChainId, payee, amount, fee);
    }

    function sendSettlementWithMessageId(
        bytes32,
        bytes32,
        bytes32,
        uint64,
        address,
        uint256
    ) external pure {
        revert ClientSuppliedMessageIdUnsupported();
    }

    function confirmSettlement(bytes32 messageId, bytes32 destinationTxHash) external {
        if (msg.sender != controller) revert Unauthorized();
        Settlement storage settlement = settlements[messageId];
        if (settlement.attemptId == bytes32(0) || settlement.confirmed || destinationTxHash == bytes32(0)) {
            revert InvalidInput();
        }
        settlement.confirmed = true;
        settlement.destinationTxHash = destinationTxHash;
        emit CCIPWorkflowSettlementConfirmed(messageId, destinationTxHash);
    }

    function cancelSettlement(bytes32, address) external pure {
        revert ClientSuppliedMessageIdUnsupported();
    }

    function isConfirmed(bytes32 messageId) external view returns (bool) {
        return settlements[messageId].confirmed;
    }

    function _route(uint64 destinationChainId) private view returns (Route memory route) {
        route = routes[destinationChainId];
        if (!route.active) revert UnsupportedRoute(destinationChainId);
    }

    function _payload(
        bytes32 workflowId,
        bytes32 attemptId,
        address payee,
        uint256 amount,
        address destinationToken
    ) private pure returns (WorkflowPayload memory payload) {
        if (workflowId == bytes32(0) || attemptId == bytes32(0) || payee == address(0) || amount == 0) {
            revert InvalidInput();
        }
        return WorkflowPayload(workflowId, attemptId, payee, amount, destinationToken);
    }

    function _message(Route memory route, WorkflowPayload memory payload)
        private
        view
        returns (Client.EVM2AnyMessage memory message)
    {
        Client.EVMTokenAmount[] memory tokenAmounts = new Client.EVMTokenAmount[](1);
        tokenAmounts[0] = Client.EVMTokenAmount(address(sourceToken), payload.amount);
        return Client.EVM2AnyMessage({
            receiver: abi.encode(route.receiver),
            data: abi.encode(payload),
            tokenAmounts: tokenAmounts,
            feeToken: address(0),
            extraArgs: Client._argsToBytes(Client.GenericExtraArgsV2({gasLimit: gasLimit, allowOutOfOrderExecution: false}))
        });
    }
}
