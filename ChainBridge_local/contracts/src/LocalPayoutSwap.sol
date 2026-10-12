// SPDX-License-Identifier: MIT
pragma solidity ^0.8.28;

import {IERC20} from "@openzeppelin/contracts/token/ERC20/IERC20.sol";
import {SafeERC20} from "@openzeppelin/contracts/token/ERC20/utils/SafeERC20.sol";
import {Math} from "@openzeppelin/contracts/utils/math/Math.sol";
import {ReentrancyGuard} from "@openzeppelin/contracts/utils/ReentrancyGuard.sol";

contract LocalPayoutSwap is ReentrancyGuard {
    using SafeERC20 for IERC20;

    uint256 public constant WAD = 1e18;

    address public immutable controller;
    IERC20 public immutable settlementToken;
    IERC20 public immutable payoutToken;
    uint8 public immutable settlementDecimals;
    uint8 public immutable payoutDecimals;
    uint256 public immutable payoutPerSettlementWad;
    address public authorizedReceiver;

    event ReceiverConfigured(address indexed receiver);
    event PayoutSwapped(
        address indexed receiver,
        address indexed payee,
        uint256 settlementAmount,
        uint256 payoutAmount
    );

    error Unauthorized();
    error InvalidInput();
    error QuoteExpired(uint256 deadline, uint256 currentTimestamp);
    error SlippageExceeded(uint256 amountOut, uint256 minAmountOut);

    constructor(
        address _controller,
        IERC20 _settlementToken,
        IERC20 _payoutToken,
        uint8 _settlementDecimals,
        uint8 _payoutDecimals,
        uint256 _payoutPerSettlementWad
    ) {
        if (
            _controller == address(0) ||
            address(_settlementToken) == address(0) ||
            address(_payoutToken) == address(0) ||
            _settlementDecimals > 18 ||
            _payoutDecimals > 18 ||
            _payoutPerSettlementWad == 0
        ) revert InvalidInput();
        controller = _controller;
        settlementToken = _settlementToken;
        payoutToken = _payoutToken;
        settlementDecimals = _settlementDecimals;
        payoutDecimals = _payoutDecimals;
        payoutPerSettlementWad = _payoutPerSettlementWad;
    }

    function configureReceiver(address receiver) external {
        if (msg.sender != controller) revert Unauthorized();
        if (receiver == address(0)) revert InvalidInput();
        authorizedReceiver = receiver;
        emit ReceiverConfigured(receiver);
    }

    function quoteExactInput(uint256 settlementAmount) public view returns (uint256 payoutAmount) {
        if (settlementAmount == 0) revert InvalidInput();
        payoutAmount = Math.mulDiv(settlementAmount, payoutPerSettlementWad, WAD);
        if (payoutDecimals > settlementDecimals) {
            payoutAmount *= 10 ** (payoutDecimals - settlementDecimals);
        } else if (settlementDecimals > payoutDecimals) {
            payoutAmount /= 10 ** (settlementDecimals - payoutDecimals);
        }
    }

    function swapExactInput(
        uint256 settlementAmount,
        uint256 minPayoutAmount,
        uint256 deadline,
        address payee
    ) external nonReentrant returns (uint256 payoutAmount) {
        if (msg.sender != authorizedReceiver) revert Unauthorized();
        if (payee == address(0) || minPayoutAmount == 0) revert InvalidInput();
        if (block.timestamp > deadline) revert QuoteExpired(deadline, block.timestamp);
        payoutAmount = quoteExactInput(settlementAmount);
        if (payoutAmount < minPayoutAmount) revert SlippageExceeded(payoutAmount, minPayoutAmount);
        settlementToken.safeTransferFrom(msg.sender, address(this), settlementAmount);
        payoutToken.safeTransfer(payee, payoutAmount);
        emit PayoutSwapped(msg.sender, payee, settlementAmount, payoutAmount);
    }
}
