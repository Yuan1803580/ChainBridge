import {
  createPublicClient,
  createWalletClient,
  custom,
  defineChain,
  getAddress,
  parseEther,
  parseUnits,
  type Address,
  type Hex,
} from "viem";

import type { LocalProtocolConfig, RawWorkflow } from "./workflow-api";

type EthereumProvider = {
  request: (args: { method: string; params?: unknown[] }) => Promise<unknown>;
};

const erc20Abi = [
  {
    type: "function",
    name: "approve",
    stateMutability: "nonpayable",
    inputs: [{ name: "spender", type: "address" }, { name: "amount", type: "uint256" }],
    outputs: [{ name: "", type: "bool" }],
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

function provider(): EthereumProvider {
  const value = (window as Window & { ethereum?: EthereumProvider }).ethereum;
  if (!value) throw new Error("未检测到 MetaMask 或兼容的 EVM 浏览器钱包");
  return value;
}

function chain(config: LocalProtocolConfig) {
  return defineChain({
    id: config.chainId,
    name: config.chainName,
    nativeCurrency: config.nativeCurrency,
    rpcUrls: { default: { http: [config.rpcUrl] } },
  });
}

async function ensureChain(config: LocalProtocolConfig, ethereum: EthereumProvider) {
  const chainId = `0x${config.chainId.toString(16)}`;
  try {
    await ethereum.request({ method: "wallet_switchEthereumChain", params: [{ chainId }] });
  } catch (reason) {
    const code = Number((reason as { code?: number })?.code);
    if (code !== 4902) throw reason;
    await ethereum.request({
      method: "wallet_addEthereumChain",
      params: [{
        chainId,
        chainName: config.chainName,
        nativeCurrency: config.nativeCurrency,
        rpcUrls: [config.rpcUrl],
      }],
    });
  }
}

function clients(config: LocalProtocolConfig) {
  const ethereum = provider();
  const localChain = chain(config);
  return {
    ethereum,
    publicClient: createPublicClient({ chain: localChain, transport: custom(ethereum) }),
    walletClient: createWalletClient({ chain: localChain, transport: custom(ethereum) }),
  };
}

export async function connectWorkflowWallet(config: LocalProtocolConfig): Promise<Address> {
  const ethereum = provider();
  await ensureChain(config, ethereum);
  const accounts = await ethereum.request({ method: "eth_requestAccounts" }) as string[];
  if (!accounts[0]) throw new Error("浏览器钱包没有返回可用账户");
  return getAddress(accounts[0]);
}

export async function approveWorkflowBudget(
  config: LocalProtocolConfig,
  account: Address,
  budget: string,
): Promise<Hex> {
  const { publicClient, walletClient } = clients(config);
  const hash = await walletClient.writeContract({
    account,
    address: getAddress(config.sourceToken),
    abi: erc20Abi,
    functionName: "approve",
    args: [getAddress(config.taskEscrow), parseUnits(budget, 6)],
  });
  await publicClient.waitForTransactionReceipt({ hash });
  return hash;
}

export async function lockWorkflowBudget(
  config: LocalProtocolConfig,
  account: Address,
  workflow: RawWorkflow,
): Promise<Hex> {
  if (!workflow.onchainWorkflowId) throw new Error("工作流缺少 onchainWorkflowId");
  const { publicClient, walletClient } = clients(config);
  const hash = await walletClient.writeContract({
    account,
    address: getAddress(config.taskEscrow),
    abi: taskEscrowAbi,
    functionName: "lockBudget",
    args: [
      workflow.onchainWorkflowId as Hex,
      workflow.termsHash as Hex,
      getAddress(config.sourceToken),
      parseUnits(workflow.budget, 6),
      parseUnits(workflow.platformFee, 6),
    ],
  });
  await publicClient.waitForTransactionReceipt({ hash });
  return hash;
}

export async function depositWorkflowGas(
  config: LocalProtocolConfig,
  account: Address,
  workflow: RawWorkflow,
): Promise<Hex> {
  if (!workflow.onchainWorkflowId) throw new Error("工作流缺少 onchainWorkflowId");
  const { publicClient, walletClient } = clients(config);
  const hash = await walletClient.writeContract({
    account,
    address: getAddress(config.gasTreasury),
    abi: gasTreasuryAbi,
    functionName: "depositSourceGas",
    args: [workflow.onchainWorkflowId as Hex],
    value: parseEther(config.sourceGasAmount),
  });
  await publicClient.waitForTransactionReceipt({ hash });
  return hash;
}
