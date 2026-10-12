import { parseAbi, parseUnits, type Address } from "viem";

import { apiRequest, env, sendQuotedTransaction, verifyQuoteExecution, walletContext } from "./wallet-swap-utils.ts";

const erc20Abi = parseAbi(["function approve(address spender, uint256 amount) returns (bool)"]);
const orderId = env("ORDER_ID");
const apiToken = env("AGENT_API_TOKEN");
const usdcAddress = env("DESTINATION_SETTLEMENT_TOKEN") as Address;
const context = walletContext("DESTINATION_RPC_URL", "B_PRIVATE_KEY");
const sellAmount = process.env.PAYOUT_SELL_AMOUNT;

const quote = await apiRequest<any>(`/api/v1/orders/${orderId}/dex-quotes`, apiToken, {
  method: "POST",
  body: JSON.stringify({
    side: "payout",
    walletAddress: context.account.address,
    maxSlippageBps: Number(process.env.MAX_SLIPPAGE_BPS ?? "100"),
    ...(sellAmount ? { sellAmount } : {}),
  }),
});
if (!quote.allowanceTarget) throw new Error("0x quote did not provide an allowance target for USDC");
if (Date.parse(quote.expiresAt) <= Date.now()) throw new Error("0x quote expired before signing");
verifyQuoteExecution(quote);

const amount = sellAmount ? parseUnits(sellAmount, 6) : BigInt(quote.sellAmount);
const approveRequest = await context.publicClient.simulateContract({
  account: context.account,
  address: usdcAddress,
  abi: erc20Abi,
  functionName: "approve",
  args: [quote.allowanceTarget as Address, amount],
});
const approveHash = await context.walletClient.writeContract(approveRequest.request);
await context.publicClient.waitForTransactionReceipt({ hash: approveHash });
console.log(`USDC approve tx               ${approveHash}`);

const swapHash = await sendQuotedTransaction(context, quote.transaction);
console.log(`USDC -> STC swap tx           ${swapHash}`);
await apiRequest(`/api/v1/orders/${orderId}/dex-quotes/${quote.quoteId}/executions`, apiToken, {
  method: "POST",
  body: JSON.stringify({ transactionHash: swapHash }),
});
console.log("payout swap recorded by API");
console.log(`minimum STC output            ${quote.minBuyAmount}`);
