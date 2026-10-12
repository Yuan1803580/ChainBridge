// SPDX-License-Identifier: MIT
pragma solidity ^0.8.28;

import {IERC20} from "@openzeppelin/contracts/token/ERC20/IERC20.sol";
import {SafeERC20} from "@openzeppelin/contracts/token/ERC20/utils/SafeERC20.sol";

import {IRouterClient} from "@chainlink/contracts-ccip/contracts/interfaces/IRouterClient.sol";
import {Client} from "@chainlink/contracts-ccip/contracts/libraries/Client.sol";

contract RealCCIPSettlementSender {
    using SafeERC20 for IERC20;

    struct SettlementPayload {
        bytes32 settlementId;
        bytes32 orderId;
        address payer;
        address payee;
        uint256 amount;
    }

    struct SentTransfer {
        bytes32 messageId;
        bytes32 settlementId;
        bytes32 orderId;
        uint64 destinationChainSelector;
        address receiver;
        address token;
        uint256 amount;
        bytes32 payloadHash;
        address feeToken;
        uint256 fee;
    }

    IRouterClient public immutable ccipRouter;
    uint64 public immutable allowedDestinationChainSelector;
    address public immutable allowedReceiver;
    address public immutable allowedToken;
    address public immutable authorizedEscrow;
    uint256 public immutable gasLimit;

    mapping(bytes32 => bool) private sentMessages;
    mapping(bytes32 => SentTransfer) private sentTransfers;

    event RealCrossChainSettlementSent(
        bytes32 indexed messageId,
        bytes32 indexed settlementId,
        bytes32 indexed orderId,
        uint64 destinationChainSelector,
        address receiver,
        address token,
        uint256 amount,
        address feeToken,
        uint256 fee
    );

    error UnsupportedDestinationChain(uint64 chainSelector);
    error UnauthorizedReceiver(address receiver);
    error UnsupportedSettlementToken(address token);
    error UnauthorizedEscrow(address caller);
    error InvalidPayload();
    error InvalidConfig();
    error InsufficientNativeFee(uint256 requiredFee, uint256 providedFee);
    error NativeFeeRefundFailed();

    constructor(
        address _ccipRouter,
        uint64 _allowedDestinationChainSelector,
        address _allowedReceiver,
        address _allowedToken,
        address _authorizedEscrow,
        uint256 _gasLimit
    ) {
        if (
            _ccipRouter == address(0) ||
            _allowedReceiver == address(0) ||
            _allowedToken == address(0) ||
            _authorizedEscrow == address(0) ||
            _gasLimit == 0
        ) {
            revert InvalidConfig();
        }

        ccipRouter = IRouterClient(_ccipRouter);
        allowedDestinationChainSelector = _allowedDestinationChainSelector;
        allowedReceiver = _allowedReceiver;
        allowedToken = _allowedToken;
        authorizedEscrow = _authorizedEscrow;
        gasLimit = _gasLimit;
    }

    receive() external payable {}

    function quoteFee(
        uint64 destinationChainSelector,
        address receiver,
        address token,
        uint256 amount
    ) external view returns (uint256 fee) {
        _validateRoute(destinationChainSelector, receiver, token);
        if (amount == 0) {
            revert InvalidPayload();
        }

        SettlementPayload memory payload = SettlementPayload({
            settlementId: bytes32(0),
            orderId: bytes32(0),
            payer: address(this),
            payee: receiver,
            amount: amount
        });

        return ccipRouter.getFee(
            destinationChainSelector,
            _buildMessage(receiver, token, payload)
        );
    }

    function sendSettlementPayNative(
        uint64 destinationChainSelector,
        address receiver,
        address token,
        SettlementPayload calldata payload
    ) external payable returns (bytes32 messageId) {
        if (payload.payer != msg.sender) {
            revert InvalidPayload();
        }

        return _sendSettlementPayNative(
            destinationChainSelector,
            receiver,
            token,
            payload
        );
    }

    function sendSettlementFromEscrowPayNative(
        uint64 destinationChainSelector,
        address receiver,
        address token,
        SettlementPayload calldata payload
    ) external payable returns (bytes32 messageId) {
        if (msg.sender != authorizedEscrow) {
            revert UnauthorizedEscrow(msg.sender);
        }
        if (payload.payer == address(0)) {
            revert InvalidPayload();
        }

        return _sendSettlementPayNative(
            destinationChainSelector,
            receiver,
            token,
            payload
        );
    }

    function getSentTransfer(
        bytes32 messageId
    ) external view returns (SentTransfer memory transfer) {
        return sentTransfers[messageId];
    }

    function isMessageSent(bytes32 messageId) external view returns (bool) {
        return sentMessages[messageId];
    }

    function _sendSettlementPayNative(
        uint64 destinationChainSelector,
        address receiver,
        address token,
        SettlementPayload calldata payload
    ) private returns (bytes32 messageId) {
        _validateRoute(destinationChainSelector, receiver, token);
        if (payload.payee == address(0) || payload.amount == 0) {
            revert InvalidPayload();
        }

        Client.EVM2AnyMessage memory message = _buildMessage(
            receiver,
            token,
            payload
        );
        uint256 fee = ccipRouter.getFee(destinationChainSelector, message);
        if (msg.value < fee) {
            revert InsufficientNativeFee(fee, msg.value);
        }

        IERC20(token).safeTransferFrom(
            msg.sender,
            address(this),
            payload.amount
        );
        IERC20(token).forceApprove(address(ccipRouter), payload.amount);

        messageId = ccipRouter.ccipSend{value: fee}(
            destinationChainSelector,
            message
        );

        if (msg.value > fee) {
            (bool refunded, ) = msg.sender.call{value: msg.value - fee}("");
            if (!refunded) {
                revert NativeFeeRefundFailed();
            }
        }

        bytes32 payloadHash = keccak256(abi.encode(payload));
        sentMessages[messageId] = true;
        sentTransfers[messageId] = SentTransfer({
            messageId: messageId,
            settlementId: payload.settlementId,
            orderId: payload.orderId,
            destinationChainSelector: destinationChainSelector,
            receiver: receiver,
            token: token,
            amount: payload.amount,
            payloadHash: payloadHash,
            feeToken: address(0),
            fee: fee
        });

        emit RealCrossChainSettlementSent(
            messageId,
            payload.settlementId,
            payload.orderId,
            destinationChainSelector,
            receiver,
            token,
            payload.amount,
            address(0),
            fee
        );
    }

    function _buildMessage(
        address receiver,
        address token,
        SettlementPayload memory payload
    ) private view returns (Client.EVM2AnyMessage memory message) {
        Client.EVMTokenAmount[]
            memory tokenAmounts = new Client.EVMTokenAmount[](1);
        tokenAmounts[0] = Client.EVMTokenAmount({
            token: token,
            amount: payload.amount
        });

        return
            Client.EVM2AnyMessage({
                receiver: abi.encode(receiver),
                data: abi.encode(payload),
                tokenAmounts: tokenAmounts,
                feeToken: address(0),
                extraArgs: Client._argsToBytes(
                    Client.GenericExtraArgsV2({
                        gasLimit: gasLimit,
                        allowOutOfOrderExecution: false
                    })
                )
            });
    }

    function _validateRoute(
        uint64 destinationChainSelector,
        address receiver,
        address token
    ) private view {
        if (destinationChainSelector != allowedDestinationChainSelector) {
            revert UnsupportedDestinationChain(destinationChainSelector);
        }
        if (receiver != allowedReceiver) {
            revert UnauthorizedReceiver(receiver);
        }
        if (token != allowedToken) {
            revert UnsupportedSettlementToken(token);
        }
    }
}
