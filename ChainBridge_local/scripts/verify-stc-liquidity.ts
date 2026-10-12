import { createPublicClient, getAddress, http, parseAbi } from "viem";

import { requiredAddress, requiredBigInt, requiredEnv } from "./real-ccip-env.js";

const rpcUrl = requiredEnv("DESTINATION_RPC_URL");
const chainId = Number(requiredEnv("DESTINATION_CHAIN_ID"));
const pool = requiredAddress("USDC_STC_POOL_ADDRESS");
const usdc = requiredAddress("DESTINATION_SETTLEMENT_TOKEN");
const stc = requiredAddress("DESTINATION_STC_TOKEN");
const taker = requiredAddress("PAYOUT_TAKER_ADDRESS");
const sellAmount = requiredBigInt("PAYOUT_TEST_SELL_AMOUNT");
const apiKey = requiredEnv("ZERO_EX_API_KEY");
const apiUrl = (process.env.ZERO_EX_API_URL ?? "https://api.0x.org").replace(/\/$/, "");
const client = createPublicClient({ transport: http(rpcUrl) });
const poolAbi = parseAbi([
  "function token0() view returns (address)",
  "function token1() view returns (address)",
  "function liquidity() view returns (uint128)",
]);

const [token0, token1, liquidity] = await Promise.all([
  client.readContract({ address: pool, abi: poolAbi, functionName: "token0" }),
  client.readContract({ address: pool, abi: poolAbi, functionName: "token1" }),
  client.readContract({ address: pool, abi: poolAbi, functionName: "liquidity" }),
]);
const tokens = new Set([getAddress(token0), getAddress(token1)]);
if (!tokens.has(getAddress(usdc)) || !tokens.has(getAddress(stc)) || liquidity === 0n) {
  throw new Error("USDC/STC pool configuration or liquidity is invalid");
}

const url = new URL(`${apiUrl}/swap/allowance-holder/quote`);
for (const [key, value] of Object.entries({
  chainId: String(chainId),
  taker,
  recipient: taker,
  sellToken: usdc,
  buyToken: stc,
  sellAmount: sellAmount.toString(),
  slippageBps: "100",
})) url.searchParams.set(key, value);
const response = await fetch(url, { headers: { "0x-api-key": apiKey, "0x-version": "v2" } });
const quote = await response.json() as Record<string, any>;
if (!response.ok || quote.liquidityAvailable === false || !quote.transaction?.to) {
  throw new Error(`no_liquidity: ${JSON.stringify(quote).slice(0, 500)}`);
}
console.log(`pool                          ${pool}`);
console.log(`active liquidity              ${liquidity}`);
console.log(`0x buy amount                 ${quote.buyAmount}`);
console.log(`0x transaction target         ${quote.transaction.to}`);
