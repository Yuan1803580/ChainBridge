import { network } from "hardhat";
import { getAddress, parseAbi, type Address } from "viem";

import { optionalBigInt, requiredAddress, requiredBigInt, requiredEnv } from "./real-ccip-env.js";

const { viem } = await network.create();
const publicClient = await viem.getPublicClient();
const [provider] = await viem.getWalletClients();

const positionManager = requiredAddress("UNISWAP_V3_POSITION_MANAGER");
const usdc = requiredAddress("DESTINATION_SETTLEMENT_TOKEN");
const stc = requiredAddress("DESTINATION_STC_TOKEN");
const usdcAmount = requiredBigInt("POOL_USDC_AMOUNT");
const stcAmount = requiredBigInt("POOL_STC_AMOUNT");
const fee = Number(process.env.POOL_FEE ?? "3000");
const tickLower = Number(process.env.POOL_TICK_LOWER ?? "-887220");
const tickUpper = Number(process.env.POOL_TICK_UPPER ?? "887220");
const liquiditySlippageBps = Number(process.env.POOL_LIQUIDITY_SLIPPAGE_BPS ?? "100");

const erc20Abi = parseAbi([
  "function balanceOf(address owner) view returns (uint256)",
  "function approve(address spender, uint256 amount) returns (bool)",
]);
const positionManagerAbi = parseAbi([
  "function createAndInitializePoolIfNecessary(address token0,address token1,uint24 fee,uint160 sqrtPriceX96) payable returns (address pool)",
  "function mint((address token0,address token1,uint24 fee,int24 tickLower,int24 tickUpper,uint256 amount0Desired,uint256 amount1Desired,uint256 amount0Min,uint256 amount1Min,address recipient,uint256 deadline)) payable returns (uint256 tokenId,uint128 liquidity,uint256 amount0,uint256 amount1)",
]);
const poolAbi = parseAbi([
  "function token0() view returns (address)",
  "function token1() view returns (address)",
  "function liquidity() view returns (uint128)",
]);

const [token0, token1] = BigInt(usdc) < BigInt(stc) ? [usdc, stc] : [stc, usdc];
const amount0 = getAddress(token0) === getAddress(usdc) ? usdcAmount : stcAmount;
const amount1 = getAddress(token1) === getAddress(stc) ? stcAmount : usdcAmount;

function decimalFraction(value: string): [bigint, bigint] {
  if (!/^\d+(?:\.\d+)?$/.test(value) || Number(value) <= 0) throw new Error("POOL_STC_PER_USDC must be positive");
  const [whole, fraction = ""] = value.split(".");
  const denominator = 10n ** BigInt(fraction.length);
  return [BigInt(whole + fraction), denominator];
}

function sqrt(value: bigint): bigint {
  if (value < 0n) throw new Error("Cannot calculate a negative square root");
  if (value < 2n) return value;
  let left = 1n;
  let right = value;
  while (left + 1n < right) {
    const middle = (left + right) / 2n;
    if (middle * middle <= value) left = middle;
    else right = middle;
  }
  return left;
}

function calculatedSqrtPriceX96(): bigint {
  const [priceNumerator, priceDenominator] = decimalFraction(requiredEnv("POOL_STC_PER_USDC"));
  const q192 = 2n ** 192n;
  const numerator = getAddress(token0) === getAddress(usdc)
    ? priceNumerator * (10n ** 18n)
    : priceDenominator * (10n ** 6n);
  const denominator = getAddress(token0) === getAddress(usdc)
    ? priceDenominator * (10n ** 6n)
    : priceNumerator * (10n ** 18n);
  return sqrt((numerator * q192) / denominator);
}

const sqrtPriceX96 = optionalBigInt("POOL_SQRT_PRICE_X96", calculatedSqrtPriceX96());
const tickSpacings = new Map([[100, 1], [500, 10], [3000, 60], [10000, 200]]);
const tickSpacing = tickSpacings.get(fee);
if (!tickSpacing) throw new Error("POOL_FEE is not a supported Uniswap V3 fee tier");
if (tickLower >= tickUpper || tickLower % tickSpacing !== 0 || tickUpper % tickSpacing !== 0) {
  throw new Error(`Pool ticks must be ordered and divisible by tick spacing ${tickSpacing}`);
}
if (liquiditySlippageBps < 0 || liquiditySlippageBps > 1000) {
  throw new Error("POOL_LIQUIDITY_SLIPPAGE_BPS must be between 0 and 1000");
}
const minimum = (amount: bigint) => amount * BigInt(10000 - liquiditySlippageBps) / 10000n;

for (const [token, amount] of [[token0, amount0], [token1, amount1]] as Array<[Address, bigint]>) {
  const balance = await publicClient.readContract({
    address: token,
    abi: erc20Abi,
    functionName: "balanceOf",
    args: [provider.account.address],
  });
  if (balance < amount) throw new Error(`Insufficient liquidity token balance for ${token}`);
  const approval = await publicClient.simulateContract({
    account: provider.account,
    address: token,
    abi: erc20Abi,
    functionName: "approve",
    args: [positionManager, amount],
  });
  const hash = await provider.writeContract(approval.request);
  await publicClient.waitForTransactionReceipt({ hash });
}

const initialize = await publicClient.simulateContract({
  account: provider.account,
  address: positionManager,
  abi: positionManagerAbi,
  functionName: "createAndInitializePoolIfNecessary",
  args: [token0, token1, fee, sqrtPriceX96],
});
const pool = initialize.result;
const initializeHash = await provider.writeContract(initialize.request);
await publicClient.waitForTransactionReceipt({ hash: initializeHash });

const mint = await publicClient.simulateContract({
  account: provider.account,
  address: positionManager,
  abi: positionManagerAbi,
  functionName: "mint",
  args: [{
    token0,
    token1,
    fee,
    tickLower,
    tickUpper,
    amount0Desired: amount0,
    amount1Desired: amount1,
    amount0Min: minimum(amount0),
    amount1Min: minimum(amount1),
    recipient: provider.account.address,
    deadline: BigInt(Math.floor(Date.now() / 1000) + 1200),
  }],
});
const mintHash = await provider.writeContract(mint.request);
await publicClient.waitForTransactionReceipt({ hash: mintHash });

const [actualToken0, actualToken1, liquidity] = await Promise.all([
  publicClient.readContract({ address: pool, abi: poolAbi, functionName: "token0" }),
  publicClient.readContract({ address: pool, abi: poolAbi, functionName: "token1" }),
  publicClient.readContract({ address: pool, abi: poolAbi, functionName: "liquidity" }),
]);
if (getAddress(actualToken0) !== getAddress(token0) || getAddress(actualToken1) !== getAddress(token1)) {
  throw new Error("Created pool token ordering does not match configuration");
}
if (liquidity === 0n) throw new Error("Pool was created without active liquidity");

console.log(`pool                          ${pool}`);
console.log(`initialize tx                 ${initializeHash}`);
console.log(`liquidity tx                  ${mintHash}`);
console.log(`sqrtPriceX96                  ${sqrtPriceX96}`);
console.log(`active liquidity              ${liquidity}`);
console.log("0x indexing is asynchronous; keep payout conversion disabled until its quote endpoint returns this route.");
