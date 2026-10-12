import "dotenv/config";

import { readFileSync } from "node:fs";
import { resolve } from "node:path";
import {
  createPublicClient,
  getAddress,
  http,
  isHash,
  parseAbi,
  type Address,
  type Hex,
} from "viem";

import { requiredEnv } from "./real-ccip-env.ts";

type ChainName = "source" | "destination";
type TransactionEvidence = {
  chain: ChainName;
  hash: Hex;
  expectedFrom?: Address;
  expectedTo?: Address;
};
type BalanceCheck = {
  name: string;
  chain: ChainName;
  token: Address;
  account: Address;
  expectedRaw?: string;
  minimumRaw?: string;
  maximumRaw?: string;
};
type AcceptanceRecord = {
  orderApiId: string;
  onchainSettlementId: Hex;
  ccipMessageId: Hex;
  transactions: Record<string, TransactionEvidence>;
  balanceChecks: BalanceCheck[];
};

const requiredTransactions: Record<string, ChainName> = {
  fundingSwap: "source",
  sourceUsdcApprove: "source",
  escrowLock: "source",
  ccipRelease: "source",
  destinationReceive: "destination",
  sourceConfirmation: "source",
  payoutUsdcApprove: "destination",
  payoutSwap: "destination",
};
const recordPath = resolve(process.env.ACCEPTANCE_RECORD ?? "docs/testnet-acceptance.json");
const record = JSON.parse(readFileSync(recordPath, "utf8")) as AcceptanceRecord;
if (!record.orderApiId || !isHash(record.onchainSettlementId) || !isHash(record.ccipMessageId)) {
  throw new Error("Acceptance record is missing orderApiId, onchainSettlementId, or ccipMessageId");
}

const sourceClient = createPublicClient({ transport: http(requiredEnv("SOURCE_RPC_URL")) });
const destinationClient = createPublicClient({ transport: http(requiredEnv("DESTINATION_RPC_URL")) });
const expectedChainIds: Record<ChainName, number> = {
  source: Number(requiredEnv("SOURCE_CHAIN_ID")),
  destination: Number(requiredEnv("DESTINATION_CHAIN_ID")),
};
const clients = { source: sourceClient, destination: destinationClient };
const actualChainIds = {
  source: await sourceClient.getChainId(),
  destination: await destinationClient.getChainId(),
};
for (const chain of ["source", "destination"] as const) {
  if (actualChainIds[chain] !== expectedChainIds[chain]) {
    throw new Error(`${chain} RPC chain ID ${actualChainIds[chain]} does not match ${expectedChainIds[chain]}`);
  }
}

const observedHashes = new Set<string>();
for (const [name, expectedChain] of Object.entries(requiredTransactions)) {
  const evidence = record.transactions?.[name];
  if (!evidence || evidence.chain !== expectedChain || !isHash(evidence.hash)) {
    throw new Error(`${name} must contain a valid ${expectedChain} transaction hash`);
  }
  if (observedHashes.has(evidence.hash.toLowerCase())) throw new Error(`${name} reuses another transaction hash`);
  observedHashes.add(evidence.hash.toLowerCase());
  const client = clients[evidence.chain];
  const [transaction, receipt] = await Promise.all([
    client.getTransaction({ hash: evidence.hash }),
    client.getTransactionReceipt({ hash: evidence.hash }),
  ]);
  if (receipt.status !== "success") throw new Error(`${name} reverted: ${evidence.hash}`);
  if (evidence.expectedFrom && getAddress(transaction.from) !== getAddress(evidence.expectedFrom)) {
    throw new Error(`${name} sender does not match the acceptance record`);
  }
  if (evidence.expectedTo) {
    if (!transaction.to || getAddress(transaction.to) !== getAddress(evidence.expectedTo)) {
      throw new Error(`${name} target does not match the acceptance record`);
    }
  }
  console.log(`${name.padEnd(24)} ${evidence.hash} block=${receipt.blockNumber}`);
}

if (!Array.isArray(record.balanceChecks) || record.balanceChecks.length < 4) {
  throw new Error("At least four balanceChecks are required for payer refund, payee USDC/STC, and Escrow");
}
const erc20Abi = parseAbi(["function balanceOf(address) view returns (uint256)"]);
for (const check of record.balanceChecks) {
  if (check.expectedRaw == null && check.minimumRaw == null && check.maximumRaw == null) {
    throw new Error(`Balance check ${check.name} has no expected bound`);
  }
  const balance = await (clients[check.chain] as any).readContract({
    address: getAddress(check.token),
    abi: erc20Abi,
    functionName: "balanceOf",
    args: [getAddress(check.account)],
  }) as bigint;
  if (check.expectedRaw != null && balance !== BigInt(check.expectedRaw)) {
    throw new Error(`${check.name} balance ${balance} does not equal ${check.expectedRaw}`);
  }
  if (check.minimumRaw != null && balance < BigInt(check.minimumRaw)) {
    throw new Error(`${check.name} balance ${balance} is below ${check.minimumRaw}`);
  }
  if (check.maximumRaw != null && balance > BigInt(check.maximumRaw)) {
    throw new Error(`${check.name} balance ${balance} exceeds ${check.maximumRaw}`);
  }
  console.log(`${check.name.padEnd(24)} ${balance}`);
}

const apiUrl = requiredEnv("API_URL").replace(/\/$/, "");
const token = requiredEnv("AGENT_API_TOKEN");
const response = await fetch(`${apiUrl}/api/v1/orders/${record.orderApiId}`, {
  headers: { Authorization: `Bearer ${token}` },
});
if (!response.ok) throw new Error(`Order API verification failed with HTTP ${response.status}`);
const order = await response.json() as Record<string, any>;
if (order.status !== "completed" || order.settlement?.status !== "released") {
  throw new Error(`Order is not final: order=${order.status}, settlement=${order.settlement?.status}`);
}
console.log(`order status             ${order.status}/${order.settlement.status}`);
console.log(`CCIP message ID          ${record.ccipMessageId}`);
console.log("testnet acceptance verified");
