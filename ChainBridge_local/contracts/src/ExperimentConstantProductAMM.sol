// SPDX-License-Identifier: MIT
pragma solidity ^0.8.28;

import {IERC20} from "@openzeppelin/contracts/token/ERC20/IERC20.sol";
import {SafeERC20} from "@openzeppelin/contracts/token/ERC20/utils/SafeERC20.sol";
import {Math} from "@openzeppelin/contracts/utils/math/Math.sol";
import {ReentrancyGuard} from "@openzeppelin/contracts/utils/ReentrancyGuard.sol";

contract ExperimentConstantProductAMM is ReentrancyGuard {
    using SafeERC20 for IERC20;

    IERC20 public immutable token0;
    IERC20 public immutable token1;
    uint256 public reserve0;
    uint256 public reserve1;
    uint256 public totalLiquidity;
    mapping(address provider => uint256 shares) public liquidityOf;

    event LiquidityAdded(address indexed provider, uint256 amount0, uint256 amount1, uint256 shares);
    event LiquidityRemoved(address indexed provider, uint256 amount0, uint256 amount1, uint256 shares);
    event Swap(address indexed sender, address indexed tokenIn, uint256 amountIn, uint256 amountOut, address indexed recipient);

    error InvalidInput();
    error QuoteExpired(uint256 deadline, uint256 currentTimestamp);
    error InsufficientLiquidity();
    error SlippageExceeded(uint256 amountOut, uint256 minAmountOut);

    constructor(IERC20 _token0, IERC20 _token1) {
        if (address(_token0) == address(0) || address(_token1) == address(0) || _token0 == _token1) revert InvalidInput();
        token0 = _token0;
        token1 = _token1;
    }

    function addLiquidity(
        uint256 amount0,
        uint256 amount1,
        uint256 minShares,
        uint256 deadline
    ) external nonReentrant returns (uint256 shares) {
        if (block.timestamp > deadline) revert QuoteExpired(deadline, block.timestamp);
        if (amount0 == 0 || amount1 == 0) revert InvalidInput();
        if (totalLiquidity == 0) {
            shares = Math.sqrt(amount0 * amount1);
        } else {
            shares = Math.min(
                Math.mulDiv(amount0, totalLiquidity, reserve0),
                Math.mulDiv(amount1, totalLiquidity, reserve1)
            );
        }
        if (shares == 0 || shares < minShares) revert SlippageExceeded(shares, minShares);
        token0.safeTransferFrom(msg.sender, address(this), amount0);
        token1.safeTransferFrom(msg.sender, address(this), amount1);
        liquidityOf[msg.sender] += shares;
        totalLiquidity += shares;
        _sync();
        emit LiquidityAdded(msg.sender, amount0, amount1, shares);
    }

    function removeLiquidity(
        uint256 shares,
        uint256 minAmount0,
        uint256 minAmount1,
        uint256 deadline
    ) external nonReentrant returns (uint256 amount0, uint256 amount1) {
        if (block.timestamp > deadline) revert QuoteExpired(deadline, block.timestamp);
        if (shares == 0 || shares > liquidityOf[msg.sender]) revert InvalidInput();
        amount0 = Math.mulDiv(shares, reserve0, totalLiquidity);
        amount1 = Math.mulDiv(shares, reserve1, totalLiquidity);
        if (amount0 < minAmount0 || amount1 < minAmount1) revert SlippageExceeded(Math.min(amount0, amount1), Math.min(minAmount0, minAmount1));
        liquidityOf[msg.sender] -= shares;
        totalLiquidity -= shares;
        token0.safeTransfer(msg.sender, amount0);
        token1.safeTransfer(msg.sender, amount1);
        _sync();
        emit LiquidityRemoved(msg.sender, amount0, amount1, shares);
    }

    function quoteExactInput(address tokenIn, uint256 amountIn) public view returns (uint256 amountOut) {
        if (amountIn == 0) revert InvalidInput();
        (uint256 reserveIn, uint256 reserveOut) = _reserves(tokenIn);
        if (reserveIn == 0 || reserveOut == 0) revert InsufficientLiquidity();
        uint256 amountInWithFee = amountIn * 997;
        amountOut = Math.mulDiv(amountInWithFee, reserveOut, reserveIn * 1000 + amountInWithFee);
        if (amountOut == 0 || amountOut >= reserveOut) revert InsufficientLiquidity();
    }

    function swapExactInput(
        address tokenIn,
        uint256 amountIn,
        uint256 minAmountOut,
        uint256 deadline,
        address recipient
    ) external nonReentrant returns (uint256 amountOut) {
        if (block.timestamp > deadline) revert QuoteExpired(deadline, block.timestamp);
        if (recipient == address(0) || minAmountOut == 0) revert InvalidInput();
        amountOut = quoteExactInput(tokenIn, amountIn);
        if (amountOut < minAmountOut) revert SlippageExceeded(amountOut, minAmountOut);
        IERC20 input = tokenIn == address(token0) ? token0 : token1;
        IERC20 output = tokenIn == address(token0) ? token1 : token0;
        input.safeTransferFrom(msg.sender, address(this), amountIn);
        output.safeTransfer(recipient, amountOut);
        _sync();
        emit Swap(msg.sender, tokenIn, amountIn, amountOut, recipient);
    }

    function reservesFor(address tokenIn) external view returns (uint256 reserveIn, uint256 reserveOut) {
        return _reserves(tokenIn);
    }

    function _reserves(address tokenIn) private view returns (uint256 reserveIn, uint256 reserveOut) {
        if (tokenIn == address(token0)) return (reserve0, reserve1);
        if (tokenIn == address(token1)) return (reserve1, reserve0);
        revert InvalidInput();
    }

    function _sync() private {
        reserve0 = token0.balanceOf(address(this));
        reserve1 = token1.balanceOf(address(this));
    }
}

