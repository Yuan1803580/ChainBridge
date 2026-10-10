import { parseAbi, parseUnits, type Address, type Hex } from "viem";

import { apiRequest, env, sendQuotedTransaction, verifyQuoteExecution, walletContext } from "./wallet-swap-utils.ts";

const erc20Abi = parseAbi([
  "function balanceOf(address owner) view returns (uint256)",
  "function approve(address spender, uint256 amount) returns (bool)",
]);
const escrowAbi = parseAbi([
  "function createAndLockWithQuote(bytes32 settlementId, bytes32 orderId, bytes32 baseTermsHash, bytes32 quoteHash, bytes32 termsHash, address payee, address token, uint256 budgetAmount, uint256 minSettlementAmount, uint256 quoteDeadline)",
]);

const orderId = env("ORDER_ID");
const apiToken = env("AGENT_API_TOKEN");
const escrowAddress = env("REAL_MARKETPLACE_ESCROW_ADDRESS") as Address;
const usdcAddress = env("SOURCE_SETTLEMENT_TOKEN") as Address;
const context = walletContext("SOURCE_RPC_URL", "A_PRIVATE_KEY");

const order = await apiRequest<any>(`/api/v1/orders/${orderId}`, apiToken);
const settlement = order.settlement;
if (!settlement?.payeeAccount) throw new Error("Order settlement payeeAccount is required");
const budget = parseUnits(String(order.amount), 6);

const quote = await apiRequest<any>(`/api/v1/orders/${orderId}/dex-quotes`, apiToken, {
  method: "POST",
  body: JSON.stringify({
    side: "funding",
    walletAddress: context.account.address,
    maxSlippageBps: Number(process.env.MAX_SLIPPAGE_BPS ?? "100"),
  }),
});
if (Date.parse(quote.expiresAt) <= Date.now()) throw new Error("0x quote expired before signing");
verifyQuoteExecution(quote);

console.log(`0x quote                      ${quote.quoteId}`);
console.log(`ETH sell amount               ${quote.sellAmount}`);
console.log(`USDC minimum                  ${quote.minBuyAmount}`);
const swapHash = await sendQuotedTransaction(context, quote.transaction);
console.log(`swap tx                       ${swapHash}`);

const balance = await context.publicClient.readContract({
  address: usdcAddress,
  abi: erc20Abi,
  functionName: "balanceOf",
  args: [context.account.address],
});
if (balance < budget) throw new Error("Confirmed USDC balance is below the order budget");

const funding = await apiRequest<any>(`/api/v1/orders/${orderId}/fund`, apiToken, {
  method: "POST",
  headers: { "Idempotency-Key": `wallet-fund-${orderId}` },
  body: JSON.stringify({
    payerAccount: context.account.address,
    payeeAccount: settlement.payeeAccount,
    assetId: usdcAddress,
    sourceChain: settlement.sourceChain,
    destinationChain: settlement.destinationChain,
    quoteId: quote.quoteId,
    swapTransactionHash: swapHash,
  }),
});
const protectedTerms = funding.termsSnapshot?.settlementQuote;
if (!protectedTerms) throw new Error("API did not return quote-protected settlement terms");

const approveRequest = await context.publicClient.simulateContract({
  account: context.account,
  address: usdcAddress,
  abi: erc20Abi,
  functionName: "approve",
  args: [escrowAddress, budget],
});
const approveHash = await context.walletClient.writeContract(approveRequest.request);
await context.publicClient.waitForTransactionReceipt({ hash: approveHash });
console.log(`USDC approve tx               ${approveHash}`);

const lockRequest = await context.publicClient.simulateContract({
  account: context.account,
  address: escrowAddress,
  abi: escrowAbi,
  functionName: "createAndLockWithQuote",
  args: [
    settlement.onchainSettlementId as Hex,
    settlement.onchainOrderId as Hex,
    protectedTerms.baseTermsHash as Hex,
    protectedTerms.quoteHash as Hex,
    protectedTerms.termsHash as Hex,
    settlement.payeeAccount as Address,
    usdcAddress,
    budget,
    BigInt(protectedTerms.budgetAmountBaseUnits),
    BigInt(Math.floor(Date.parse(protectedTerms.quoteDeadline) / 1000)),
  ],
});
const lockHash = await context.walletClient.writeContract(lockRequest.request);
await context.publicClient.waitForTransactionReceipt({ hash: lockHash });
console.log(`escrow lock tx                ${lockHash}`);
console.log("The chain-worker will mark the order funded after confirmation depth is reached.");
