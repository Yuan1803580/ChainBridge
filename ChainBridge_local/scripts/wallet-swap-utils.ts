import {
  createPublicClient,
  createWalletClient,
  http,
  type Address,
  type Hex,
} from "viem";
import { privateKeyToAccount } from "viem/accounts";
import { createHash } from "node:crypto";

export function env(name: string): string {
  const value = process.env[name]?.trim();
  if (!value) throw new Error(`${name} is required`);
  return value;
}

export function walletContext(rpcEnv: string, keyEnv: string) {
  const rpcUrl = env(rpcEnv);
  const account = privateKeyToAccount(env(keyEnv) as Hex);
  return {
    account,
    publicClient: createPublicClient({ transport: http(rpcUrl) }),
    walletClient: createWalletClient({ account, transport: http(rpcUrl) }),
  };
}

export async function apiRequest<T>(
  path: string,
  token: string,
  init: RequestInit = {},
): Promise<T> {
  const baseUrl = (process.env.API_URL ?? "http://127.0.0.1:8000").replace(/\/$/, "");
  const response = await fetch(`${baseUrl}${path}`, {
    ...init,
    headers: {
      Authorization: `Bearer ${token}`,
      "Content-Type": "application/json",
      ...(init.headers ?? {}),
    },
  });
  const payload = await response.json() as any;
  if (!response.ok) throw new Error(payload?.error?.message ?? `API returned HTTP ${response.status}`);
  return payload as T;
}

export async function sendQuotedTransaction(
  context: ReturnType<typeof walletContext>,
  transaction: { to: Address; data: Hex; value: string; gas: string },
): Promise<Hex> {
  const hash = await context.walletClient.sendTransaction({
    account: context.account,
    to: transaction.to,
    data: transaction.data,
    value: BigInt(transaction.value),
    gas: transaction.gas && transaction.gas !== "0" ? BigInt(transaction.gas) : undefined,
  });
  const receipt = await context.publicClient.waitForTransactionReceipt({ hash });
  if (receipt.status !== "success") throw new Error(`Transaction ${hash} reverted`);
  return hash;
}

function canonicalJson(value: unknown): string {
  if (Array.isArray(value)) return `[${value.map(canonicalJson).join(",")}]`;
  if (value !== null && typeof value === "object") {
    const entries = Object.entries(value as Record<string, unknown>).sort(([left], [right]) => left.localeCompare(right));
    return `{${entries.map(([key, item]) => `${JSON.stringify(key)}:${canonicalJson(item)}`).join(",")}}`;
  }
  return JSON.stringify(value);
}

export function canonicalHash(value: unknown): Hex {
  return `0x${createHash("sha256").update(canonicalJson(value)).digest("hex")}`;
}

export function verifyQuoteExecution(quote: Record<string, any>): void {
  const material = {
    chainId: quote.chainId,
    taker: quote.taker,
    recipient: quote.taker,
    sellToken: quote.sellToken,
    buyToken: quote.buyToken,
    sellAmount: quote.sellAmount,
    minBuyAmount: quote.minBuyAmount,
    transaction: quote.transaction,
    expiresAt: quote.expiresAt,
  };
  if (canonicalHash(material).toLowerCase() !== String(quote.executionHash).toLowerCase()) {
    throw new Error("DEX quote transaction parameters do not match executionHash");
  }
  if (BigInt(quote.transaction.value) > BigInt(quote.maxSellAmount)) {
    throw new Error("DEX quote transaction value exceeds maxSellAmount");
  }
}
