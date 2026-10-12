import {
  createPublicClient,
  createWalletClient,
  custom,
  defineChain,
  encodeAbiParameters,
  formatUnits,
  getAddress,
  keccak256,
  parseUnits,
  toBytes,
  type Address,
  type Hex,
} from "viem";

import type { Order } from "./types";

type EthereumProvider = {
  request: (args: { method: string; params?: unknown[] }) => Promise<unknown>;
};

declare global {
  interface Window {
    ethereum?: EthereumProvider;
  }
}

const localChainId = Number(process.env.NEXT_PUBLIC_LOCAL_CHAIN_ID || "31337");
const mockUsdcAddress = process.env.NEXT_PUBLIC_MOCK_USDC_ADDRESS as Address | undefined;
const escrowAddress = process.env.NEXT_PUBLIC_MARKETPLACE_ESCROW_ADDRESS as Address | undefined;
const quoteProviderAddress = process.env.NEXT_PUBLIC_MOCK_QUOTE_PROVIDER_ADDRESS as Address | undefined;
const localPayeeAddress = process.env.NEXT_PUBLIC_LOCAL_PAYEE_ADDRESS as Address | undefined;

const localHardhat = defineChain({
  id: localChainId,
  name: "Hardhat Local",
  nativeCurrency: { name: "Ether", symbol: "ETH", decimals: 18 },
  rpcUrls: { default: { http: ["http://127.0.0.1:8545"] } },
});

const erc20Abi = [
  {
    type: "function",
    name: "approve",
    stateMutability: "nonpayable",
    inputs: [
      { name: "spender", type: "address" },
      { name: "amount", type: "uint256" },
    ],
    outputs: [{ name: "", type: "bool" }],
  },
] as const;

const quoteProviderAbi = [
  {
    type: "function",
    name: "quote",
    stateMutability: "view",
    inputs: [
      { name: "sourceAsset", type: "address" },
      { name: "settlementToken", type: "address" },
      { name: "sourceAmount", type: "uint256" },
      { name: "maxSlippageBps", type: "uint16" },
      { name: "quoteDeadline", type: "uint256" },
    ],
    outputs: [
      {
        name: "result",
        type: "tuple",
        components: [
          { name: "sourceAsset", type: "address" },
          { name: "settlementToken", type: "address" },
          { name: "sourceAmount", type: "uint256" },
          { name: "quotedSettlementAmount", type: "uint256" },
          { name: "minSettlementAmount", type: "uint256" },
          { name: "maxSlippageBps", type: "uint16" },
          { name: "quoteDeadline", type: "uint256" },
          { name: "quoteHash", type: "bytes32" },
        ],
      },
    ],
  },
] as const;

const escrowAbi = [
  {
    type: "function",
    name: "buildQuoteTermsHash",
    stateMutability: "pure",
    inputs: [
      { name: "orderId", type: "bytes32" },
      { name: "baseTermsHash", type: "bytes32" },
      { name: "quoteHash", type: "bytes32" },
    ],
    outputs: [{ name: "", type: "bytes32" }],
  },
  {
    type: "function",
    name: "createAndLockWithQuote",
    stateMutability: "nonpayable",
    inputs: [
      { name: "settlementId", type: "bytes32" },
      { name: "orderId", type: "bytes32" },
      { name: "baseTermsHash", type: "bytes32" },
      { name: "quoteHash", type: "bytes32" },
      { name: "termsHash", type: "bytes32" },
      { name: "payee", type: "address" },
      { name: "token", type: "address" },
      { name: "budgetAmount", type: "uint256" },
      { name: "minSettlementAmount", type: "uint256" },
      { name: "quoteDeadline", type: "uint256" },
    ],
    outputs: [],
  },
  {
    type: "function",
    name: "releaseCrossChain",
    stateMutability: "nonpayable",
    inputs: [
      { name: "settlementId", type: "bytes32" },
      { name: "paidAmount", type: "uint256" },
    ],
    outputs: [{ name: "messageId", type: "bytes32" }],
  },
  {
    type: "function",
    name: "releaseMessageIds",
    stateMutability: "view",
    inputs: [{ name: "settlementId", type: "bytes32" }],
    outputs: [{ name: "", type: "bytes32" }],
  },
] as const;

export type WalletState = {
  account?: Address;
  chainId?: number;
  ready: boolean;
  message: string;
};

export type LocalSettlementQuote = {
  sourceAmount: bigint;
  quotedSettlementAmount: bigint;
  minSettlementAmount: bigint;
  maxSlippageBps: number;
  quoteDeadline: bigint;
  quoteHash: Hex;
  termsHash: Hex;
  sourceAmountDisplay: string;
  quotedSettlementAmountDisplay: string;
  minSettlementAmountDisplay: string;
};

export type LocalTxResult = {
  hash: Hex;
};

export type LocalReleaseResult = LocalTxResult & {
  messageId: Hex;
};

function requireBrowserProvider(): EthereumProvider {
  if (typeof window === "undefined" || !window.ethereum) {
    throw new Error("MetaMask is not available in this browser.");
  }
  return window.ethereum;
}

function requireAddress(value: string | undefined, name: string): Address {
  if (!value) {
    throw new Error(`${name} is not configured in apps/web/.env.local.`);
  }
  return getAddress(value);
}

function clients() {
  const provider = requireBrowserProvider();
  return {
    publicClient: createPublicClient({ chain: localHardhat, transport: custom(provider) }),
    walletClient: createWalletClient({ chain: localHardhat, transport: custom(provider) }),
  };
}

export async function connectWallet(): Promise<WalletState> {
  const provider = requireBrowserProvider();
  const accounts = (await provider.request({ method: "eth_requestAccounts" })) as Address[];
  const chainIdHex = (await provider.request({ method: "eth_chainId" })) as Hex;
  const chainId = Number(BigInt(chainIdHex));
  const account = accounts[0] ? getAddress(accounts[0]) : undefined;
  return {
    account,
    chainId,
    ready: Boolean(account && chainId === localChainId),
    message: chainId === localChainId ? "Connected to local Hardhat." : `Switch MetaMask to chain ${localChainId}.`,
  };
}

export async function getWalletState(): Promise<WalletState> {
  if (typeof window === "undefined" || !window.ethereum) {
    return { ready: false, message: "MetaMask is not available." };
  }
  const accounts = (await window.ethereum.request({ method: "eth_accounts" })) as Address[];
  const chainIdHex = (await window.ethereum.request({ method: "eth_chainId" })) as Hex;
  const chainId = Number(BigInt(chainIdHex));
  const account = accounts[0] ? getAddress(accounts[0]) : undefined;
  return {
    account,
    chainId,
    ready: Boolean(account && chainId === localChainId),
    message: account
      ? chainId === localChainId
        ? "Connected to local Hardhat."
        : `Switch MetaMask to chain ${localChainId}.`
      : "Wallet is not connected.",
  };
}

export function orderBytes32(value: string): Hex {
  return keccak256(toBytes(value));
}

export function orderAmountToUsdcUnits(order: Order): bigint {
  return parseUnits(order.amount, 6);
}

export function orderAmountToSourceUnits(order: Order): bigint {
  return parseUnits((Number(order.amount) / 2000).toFixed(18), 18);
}

export async function readLocalQuote(order: Order, account: Address): Promise<LocalSettlementQuote> {
  const quoteProvider = requireAddress(quoteProviderAddress, "NEXT_PUBLIC_MOCK_QUOTE_PROVIDER_ADDRESS");
  const token = requireAddress(mockUsdcAddress, "NEXT_PUBLIC_MOCK_USDC_ADDRESS");
  const escrow = requireAddress(escrowAddress, "NEXT_PUBLIC_MARKETPLACE_ESCROW_ADDRESS");
  const { publicClient } = clients();
  const quoteDeadline = BigInt(Math.floor(Date.now() / 1000) + 15 * 60);
  const sourceAmount = orderAmountToSourceUnits(order);
  const result = await publicClient.readContract({
    address: quoteProvider,
    abi: quoteProviderAbi,
    functionName: "quote",
    args: [account, token, sourceAmount, 100, quoteDeadline],
  });
  const orderId = orderBytes32(order.id);
  const baseTermsHash = order.termsHash as Hex;
  const termsHash = await publicClient.readContract({
    address: escrow,
    abi: escrowAbi,
    functionName: "buildQuoteTermsHash",
    args: [orderId, baseTermsHash, result.quoteHash],
  });
  return {
    sourceAmount: result.sourceAmount,
    quotedSettlementAmount: result.quotedSettlementAmount,
    minSettlementAmount: result.minSettlementAmount,
    maxSlippageBps: result.maxSlippageBps,
    quoteDeadline: result.quoteDeadline,
    quoteHash: result.quoteHash,
    termsHash,
    sourceAmountDisplay: formatUnits(result.sourceAmount, 18),
    quotedSettlementAmountDisplay: formatUnits(result.quotedSettlementAmount, 6),
    minSettlementAmountDisplay: formatUnits(result.minSettlementAmount, 6),
  };
}

export async function approveSettlementToken(order: Order, account: Address): Promise<LocalTxResult> {
  const token = requireAddress(mockUsdcAddress, "NEXT_PUBLIC_MOCK_USDC_ADDRESS");
  const escrow = requireAddress(escrowAddress, "NEXT_PUBLIC_MARKETPLACE_ESCROW_ADDRESS");
  const { publicClient, walletClient } = clients();
  const hash = await walletClient.writeContract({
    account,
    address: token,
    abi: erc20Abi,
    functionName: "approve",
    args: [escrow, orderAmountToUsdcUnits(order)],
  });
  await publicClient.waitForTransactionReceipt({ hash });
  return { hash };
}

export async function createAndLockWithQuote(
  order: Order,
  account: Address,
  quote: LocalSettlementQuote,
): Promise<LocalTxResult> {
  const token = requireAddress(mockUsdcAddress, "NEXT_PUBLIC_MOCK_USDC_ADDRESS");
  const escrow = requireAddress(escrowAddress, "NEXT_PUBLIC_MARKETPLACE_ESCROW_ADDRESS");
  const payee = requireAddress(localPayeeAddress, "NEXT_PUBLIC_LOCAL_PAYEE_ADDRESS");
  const { publicClient, walletClient } = clients();
  const settlementId = orderBytes32(order.settlementId);
  const orderId = orderBytes32(order.id);
  const baseTermsHash = order.termsHash as Hex;
  const hash = await walletClient.writeContract({
    account,
    address: escrow,
    abi: escrowAbi,
    functionName: "createAndLockWithQuote",
    args: [
      settlementId,
      orderId,
      baseTermsHash,
      quote.quoteHash,
      quote.termsHash,
      payee,
      token,
      orderAmountToUsdcUnits(order),
      quote.minSettlementAmount,
      quote.quoteDeadline,
    ],
  });
  await publicClient.waitForTransactionReceipt({ hash });
  return { hash };
}

export async function releaseCrossChain(order: Order, account: Address): Promise<LocalReleaseResult> {
  const escrow = requireAddress(escrowAddress, "NEXT_PUBLIC_MARKETPLACE_ESCROW_ADDRESS");
  const { publicClient, walletClient } = clients();
  const settlementId = orderBytes32(order.settlementId);
  const hash = await walletClient.writeContract({
    account,
    address: escrow,
    abi: escrowAbi,
    functionName: "releaseCrossChain",
    args: [settlementId, orderAmountToUsdcUnits(order)],
  });
  await publicClient.waitForTransactionReceipt({ hash });
  const messageId = await publicClient.readContract({
    address: escrow,
    abi: escrowAbi,
    functionName: "releaseMessageIds",
    args: [settlementId],
  });
  return { hash, messageId };
}

export function localConfigSummary(): Record<string, string | undefined> {
  return {
    localChainId: String(localChainId),
    mockUsdcAddress,
    quoteProviderAddress,
    escrowAddress,
    localPayeeAddress,
  };
}

