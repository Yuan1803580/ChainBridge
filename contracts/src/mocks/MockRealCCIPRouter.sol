// SPDX-License-Identifier: MIT
pragma solidity ^0.8.28;

import {IERC20} from "@openzeppelin/contracts/token/ERC20/IERC20.sol";
import {SafeERC20} from "@openzeppelin/contracts/token/ERC20/utils/SafeERC20.sol";

import {IAny2EVMMessageReceiver} from "@chainlink/contracts-ccip/contracts/interfaces/IAny2EVMMessageReceiver.sol";
import {IRouterClient} from "@chainlink/contracts-ccip/contracts/interfaces/IRouterClient.sol";
import {Client} from "@chainlink/contracts-ccip/contracts/libraries/Client.sol";

contract MockRealCCIPRouter is IRouterClient {
    using SafeERC20 for IERC20;

    uint64 public sourceChainSelector;
    uint256 public fee;
    uint256 public nonce;

    event MockCCIPMessageSent(
        bytes32 indexed messageId,
        uint64 indexed destinationChainSelector,
        address indexed receiver
    );

    error InvalidReceiver();

    constructor(uint64 _sourceChainSelector, uint256 _fee) {
        sourceChainSelector = _sourceChainSelector;
        fee = _fee;
    }

    function setSourceChainSelector(uint64 _sourceChainSelector) external {
        sourceChainSelector = _sourceChainSelector;
    }

    function setFee(uint256 _fee) external {
        fee = _fee;
    }

    function isChainSupported(
        uint64
    ) external pure returns (bool supported) {
        return true;
    }

    function getFee(
        uint64,
        Client.EVM2AnyMessage memory
    ) public view returns (uint256) {
        return fee;
    }

    function ccipSend(
        uint64 destinationChainSelector,
        Client.EVM2AnyMessage calldata message
    ) external payable returns (bytes32 messageId) {
        if (message.feeToken == address(0)) {
            if (msg.value < fee) {
                revert InsufficientFeeTokenAmount();
            }
        } else {
            if (msg.value > 0) {
                revert InvalidMsgValue();
            }
            IERC20(message.feeToken).safeTransferFrom(
                msg.sender,
                address(this),
                fee
            );
        }

        address receiver = abi.decode(message.receiver, (address));
        if (receiver == address(0)) {
            revert InvalidReceiver();
        }

        messageId = keccak256(
            abi.encode(msg.sender, destinationChainSelector, message, nonce++)
        );

        Client.EVMTokenAmount[]
            memory destTokenAmounts = new Client.EVMTokenAmount[](
                message.tokenAmounts.length
            );
        for (uint256 i = 0; i < message.tokenAmounts.length; i++) {
            destTokenAmounts[i] = message.tokenAmounts[i];
            IERC20(message.tokenAmounts[i].token).safeTransferFrom(
                msg.sender,
                receiver,
                message.tokenAmounts[i].amount
            );
        }

        IAny2EVMMessageReceiver(receiver).ccipReceive(
            Client.Any2EVMMessage({
                messageId: messageId,
                sourceChainSelector: sourceChainSelector,
                sender: abi.encode(msg.sender),
                data: message.data,
                destTokenAmounts: destTokenAmounts
            })
        );

        emit MockCCIPMessageSent(
            messageId,
            destinationChainSelector,
            receiver
        );
    }

    function deliverMessage(
        address receiver,
        Client.Any2EVMMessage calldata message
    ) external {
        IAny2EVMMessageReceiver(receiver).ccipReceive(message);
    }
}
