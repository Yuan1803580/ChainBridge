import {
  createPublicClient,
  createWalletClient,
  custom,
  defineChain,
  formatEther,
  formatUnits,
  getAddress,
  parseEther,
  parseUnits,
  type Address,
  type Hex,
} from "viem";

import type { RawWorkflow, WorkflowFundingConfig } from "./workflow-api";

type EthereumProvider = {
  request: (args: { method: string; params?: unknown[] }) => Promise<unknown>;
};

export type WorkflowWalletState = {
  account?: Address;
  chainId?: number;
  ready: boolean;
  message: string;
};

export type WorkflowWalletBalances = {
  tokenSymbol: string;
  tokenDecimals: number;
  tokenBalance: bigint;
  tokenBalanceDisplay: string;
  nativeBalance: bigint;
  nativeBalanceDisplay: string;
};

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
  {
    type: "function",
    name: "balanceOf",
    stateMutability: "view",
    inputs: [{ name: "account", type: "address" }],
    outputs: [{ name: "", type: "uint256" }],
  },
  {
    type: "function",
    name: "decimals",
    stateMutability: "view",
    inputs: [],
    outputs: [{ name: "", type: "uint8" }],
  },
  {
    type: "function",
    name: "symbol",
    stateMutability: "view",
    inputs: [],
    outputs: [{ name: "", type: "string" }],
  },
  {
    type: "function",
    name: "mint",
    stateMutability: "nonpayable",
    inputs: [
      { name: "to", type: "address" },
      { name: "amount", type: "uint256" },
    ],
    outputs: [],
  },
] as const;

const taskEscrowAbi = [
  {
    type: "function",
    name: "lockBudget",
    stateMutability: "nonpayable",
    inputs: [
      { name: "workflowId", type: "bytes32" },
      { name: "termsHash", type: "bytes32" },
      { name: "token", type: "address" },
      { name: "budget", type: "uint256" },
      { name: "platformFeeLimit", type: "uint256" },
    ],
    outputs: [],
  },
] as const;

const gasTreasuryAbi = [
  {
    type: "function",
    name: "depositSourceGas",
    stateMutability: "payable",
    inputs: [{ name: "workflowId", type: "bytes32" }],
    outputs: [],
  },
] as const;

function browserProvider(): EthereumProvider {
  const provider = (window as Window & { ethereum?: EthereumProvider }).ethereum;
  if (!provider) throw new Error("当前浏览器未检测到 MetaMask。请安装或启用钱包后重试。");
  return provider;
}

function chainFor(config: WorkflowFundingConfig) {
  return defineChain({
    id: config.chainId,
    name: config.chainName,
    nativeCurrency: { name: "Ether", symbol: "ETH", decimals: 18 },
    rpcUrls: {
      default: { http: [process.env.NEXT_PUBLIC_LOCAL_RPC_URL || "http://127.0.0.1:8545"] },
    },
  });
}

function clients(config: WorkflowFundingConfig) {
  const provider = browserProvider();
  const chain = chainFor(config);
  return {
    publicClient: createPublicClient({ chain, transport: custom(provider) }),
    walletClient: createWalletClient({ chain, transport: custom(provider) }),
  };
}

function address(value: string, label: string): Address {
  try {
    return getAddress(value);
  } catch {
    throw new Error(`${label} 地址无效，当前协议部署清单不可用。`);
  }
}

function bytes32(value: string | undefined, label: string): Hex {
  if (!value || !/^0x[0-9a-fA-F]{64}$/.test(value)) {
    throw new Error(`${label} 不是有效的 bytes32 值。`);
  }
  return value as Hex;
}

async function waitForSuccess(config: WorkflowFundingConfig, hash: Hex): Promise<Hex> {
  const { publicClient } = clients(config);
  const receipt = await publicClient.waitForTransactionReceipt({ hash });
  if (receipt.status !== "success") throw new Error(`链上交易执行失败：${hash}`);
  return hash;
}

function numericChainId(value: unknown): number | undefined {
  return typeof value === "string" && /^0x[0-9a-fA-F]+$/.test(value)
    ? Number(BigInt(value))
    : undefined;
}

export async function getWorkflowWalletState(config: WorkflowFundingConfig): Promise<WorkflowWalletState> {
  if (typeof window === "undefined" || !(window as Window & { ethereum?: EthereumProvider }).ethereum) {
    return { ready: false, message: "未检测到 MetaMask" };
  }
  const provider = browserProvider();
  const [accounts, chainIdValue] = await Promise.all([
    provider.request({ method: "eth_accounts" }) as Promise<string[]>,
    provider.request({ method: "eth_chainId" }),
  ]);
  const chainId = numericChainId(chainIdValue);
  const account = accounts[0] ? address(accounts[0], "钱包账户") : undefined;
  return {
    account,
    chainId,
    ready: Boolean(account && chainId === config.chainId),
    message: !account
      ? "钱包未连接"
      : chainId === config.chainId
        ? `已连接 ${config.chainName}`
        : `当前网络 ${chainId ?? "未知"}，需要切换到 ${config.chainId}`,
  };
}

export async function connectWorkflowWallet(config: WorkflowFundingConfig): Promise<WorkflowWalletState> {
  const provider = browserProvider();
  await provider.request({ method: "eth_requestAccounts" });
  return getWorkflowWalletState(config);
}

export async function switchWorkflowChain(config: WorkflowFundingConfig): Promise<WorkflowWalletState> {
  const provider = browserProvider();
  const chainId = `0x${config.chainId.toString(16)}`;
  try {
    await provider.request({ method: "wallet_switchEthereumChain", params: [{ chainId }] });
  } catch (reason) {
    const code = typeof reason === "object" && reason && "code" in reason ? Number(reason.code) : undefined;
    if (code !== 4902 || !config.localTestnet) throw reason;
    await provider.request({
      method: "wallet_addEthereumChain",
      params: [{
        chainId,
        chainName: config.chainName,
        nativeCurrency: { name: "Ether", symbol: "ETH", decimals: 18 },
        rpcUrls: [process.env.NEXT_PUBLIC_LOCAL_RPC_URL || "http://127.0.0.1:8545"],
      }],
    });
  }
  return getWorkflowWalletState(config);
}

export async function readWorkflowWalletBalances(
  config: WorkflowFundingConfig,
  account: Address,
): Promise<WorkflowWalletBalances> {
  const token = address(config.contracts.sourceToken, "结算 Token");
  const { publicClient } = clients(config);
  const [tokenDecimals, tokenSymbol, tokenBalance, nativeBalance] = await Promise.all([
    publicClient.readContract({ address: token, abi: erc20Abi, functionName: "decimals" }),
    publicClient.readContract({ address: token, abi: erc20Abi, functionName: "symbol" }),
    publicClient.readContract({ address: token, abi: erc20Abi, functionName: "balanceOf", args: [account] }),
    publicClient.getBalance({ address: account }),
  ]);
  return {
    tokenSymbol,
    tokenDecimals,
    tokenBalance,
    tokenBalanceDisplay: formatUnits(tokenBalance, tokenDecimals),
    nativeBalance,
    nativeBalanceDisplay: formatEther(nativeBalance),
  };
}

export async function mintLocalWorkflowToken(
  config: WorkflowFundingConfig,
  account: Address,
  amount: string,
): Promise<Hex> {
  if (!config.localTestnet) throw new Error("当前网络不支持领取本地测试 Token。");
  const token = address(config.contracts.sourceToken, "结算 Token");
  const { publicClient, walletClient } = clients(config);
  const decimals = await publicClient.readContract({ address: token, abi: erc20Abi, functionName: "decimals" });
  const hash = await walletClient.writeContract({
    account,
    address: token,
    abi: erc20Abi,
    functionName: "mint",
    args: [account, parseUnits(amount, decimals)],
  });
  return waitForSuccess(config, hash);
}

export async function approveWorkflowBudget(
  config: WorkflowFundingConfig,
  workflow: RawWorkflow,
  account: Address,
): Promise<Hex> {
  const token = address(config.contracts.sourceToken, "结算 Token");
  const escrow = address(config.contracts.taskEscrow, "TaskEscrow");
  const { publicClient, walletClient } = clients(config);
  const decimals = await publicClient.readContract({ address: token, abi: erc20Abi, functionName: "decimals" });
  const amount = parseUnits(workflow.budget, decimals);
  const balance = await publicClient.readContract({ address: token, abi: erc20Abi, functionName: "balanceOf", args: [account] });
  if (balance < amount) {
    throw new Error(`Token 余额不足：需要 ${workflow.budget}，当前 ${formatUnits(balance, decimals)}。`);
  }
  const hash = await walletClient.writeContract({
    account,
    address: token,
    abi: erc20Abi,
    functionName: "approve",
    args: [escrow, amount],
  });
  return waitForSuccess(config, hash);
}

export async function lockWorkflowBudget(
  config: WorkflowFundingConfig,
  workflow: RawWorkflow,
  account: Address,
): Promise<Hex> {
  const token = address(config.contracts.sourceToken, "结算 Token");
  const escrow = address(config.contracts.taskEscrow, "TaskEscrow");
  const { publicClient, walletClient } = clients(config);
  const decimals = await publicClient.readContract({ address: token, abi: erc20Abi, functionName: "decimals" });
  const hash = await walletClient.writeContract({
    account,
    address: escrow,
    abi: taskEscrowAbi,
    functionName: "lockBudget",
    args: [
      bytes32(workflow.onchainWorkflowId, "onchainWorkflowId"),
      bytes32(workflow.termsHash, "termsHash"),
      token,
      parseUnits(workflow.budget, decimals),
      parseUnits(workflow.platformFee, decimals),
    ],
  });
  return waitForSuccess(config, hash);
}

export async function depositWorkflowSourceGas(
  config: WorkflowFundingConfig,
  workflow: RawWorkflow,
  account: Address,
  amount: string,
): Promise<Hex | undefined> {
  const value = parseEther(amount);
  if (value === BigInt(0)) return undefined;
  const gasTreasury = address(config.contracts.gasTreasury, "GasTreasury");
  const { walletClient } = clients(config);
  const hash = await walletClient.writeContract({
    account,
    address: gasTreasury,
    abi: gasTreasuryAbi,
    functionName: "depositSourceGas",
    args: [bytes32(workflow.onchainWorkflowId, "onchainWorkflowId")],
    value,
  });
  return waitForSuccess(config, hash);
}
