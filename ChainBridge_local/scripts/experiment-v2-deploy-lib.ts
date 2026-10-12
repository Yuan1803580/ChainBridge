import { mkdir, readFile, rename, writeFile } from "node:fs/promises";
import { dirname, resolve } from "node:path";

import {
  createPublicClient,
  createWalletClient,
  http,
  keccak256,
  stringToHex,
  type Abi,
  type Address,
  type Hex,
  type PublicClient,
  type WalletClient,
} from "viem";
import { privateKeyToAccount } from "viem/accounts";

export const CONTROLLER_PRIVATE_KEY = "0xac0974bec39a17e36ba4a6b4d238ff944bacb478cbed5efcae784d7bf4f2ff80" as Hex;
export const RELAY_PRIVATE_KEY = "0x59c6995e998f97a5a0044966f0945389dc9e86dae88c7a8412f4603b6b78690d" as Hex;
export const AGENT_PRIVATE_KEYS: Record<number, Hex> = {
  31338: "0x5de4111afa1a4b94908f83103eb1f1706367c2e68ca870fc3fb9a804cdab365a",
  31339: "0x7c852118294e51e653712a81e05800f419141751be58f605c371e15141b007a6",
  31340: "0x47e179ec197488593b187f80a00eb0da91f1b9d0b13f8733639f19c30a34926a",
};

const controllerAccount = privateKeyToAccount(CONTROLLER_PRIVATE_KEY);
const relayAccount = privateKeyToAccount(RELAY_PRIVATE_KEY);
const id = (value: string) => keccak256(stringToHex(value));
const projectRoot = resolve(import.meta.dirname, "..");

type Artifact = { abi: Abi; bytecode: Hex };
type ChainContext = {
  key: string;
  chainId: number;
  rpcUrl: string;
  publicClient: PublicClient;
  controllerWallet: WalletClient;
  relayWallet: WalletClient;
};
type ContractRecord = { address: Address; deploymentTransactionHash: Hex; deploymentBlock: string };

export type DestinationDeployment = {
  key: string;
  chainId: number;
  rpcUrl: string;
  routeKey: Hex;
  routeVersion: number;
  agentAddress: Address;
  agentDidHash: Hex;
  contracts: {
    bridgeAsset: Address;
    payoutToken: Address;
    identityRegistry: Address;
    remoteWorkBoard: Address;
    destinationAMM: Address;
    destinationVault: Address;
  };
  deploymentTransactions: Record<string, Hex>;
};

export type ExperimentV2Deployment = {
  schemaVersion: 2;
  generatedAt: string;
  sourceChainId: number;
  sourceRpcUrl: string;
  source: {
    contracts: {
      sourceToken: Address;
      bridgeAsset: Address;
      workflowEngine: Address;
      resultProofGateway: Address;
      coordinatorReview: Address;
      sourceAMM: Address;
      sourceVault: Address;
      experimentEscrow: Address;
      dispatchSender: Address;
    };
    deploymentTransactions: Record<string, Hex>;
  };
  destinations: Record<string, DestinationDeployment>;
  chains: Array<{
    key: string;
    chainId: number;
    rpcUrl: string;
    confirmations: number;
    startBlock: string;
    enabled: boolean;
    messageSenders: Address[];
    allowedReceivers: Address[];
  }>;
  metrics: { transactionCount: number; deploymentGasUsed: string };
};

const deploymentMetrics = { transactionCount: 0, deploymentGasUsed: 0n };

function artifactPath(name: string): string {
  return resolve(projectRoot, "hardhat-artifacts", "contracts", "src", `${name}.sol`, `${name}.json`);
}

async function artifact(name: string): Promise<Artifact> {
  return JSON.parse(await readFile(artifactPath(name), "utf8")) as Artifact;
}

async function chain(key: string, chainId: number, rpcUrl: string): Promise<ChainContext> {
  const publicClient = createPublicClient({ transport: http(rpcUrl) });
  const actual = await publicClient.getChainId();
  if (actual !== chainId) throw new Error(`${key} RPC ${rpcUrl} reports chainId ${actual}, expected ${chainId}`);
  return {
    key,
    chainId,
    rpcUrl,
    publicClient,
    controllerWallet: createWalletClient({ account: controllerAccount, transport: http(rpcUrl) }),
    relayWallet: createWalletClient({ account: relayAccount, transport: http(rpcUrl) }),
  };
}

async function deploy(context: ChainContext, name: string, args: readonly unknown[] = []): Promise<ContractRecord> {
  const compiled = await artifact(name);
  const hash = await context.controllerWallet.deployContract({
    account: controllerAccount,
    abi: compiled.abi,
    bytecode: compiled.bytecode,
    args,
  } as any);
  const receipt = await context.publicClient.waitForTransactionReceipt({ hash });
  if (receipt.status !== "success" || !receipt.contractAddress) throw new Error(`${name} deployment failed on ${context.key}`);
  deploymentMetrics.transactionCount += 1;
  deploymentMetrics.deploymentGasUsed += receipt.gasUsed;
  return { address: receipt.contractAddress, deploymentTransactionHash: hash, deploymentBlock: receipt.blockNumber.toString() };
}

async function write(
  context: ChainContext,
  name: string,
  address: Address,
  functionName: string,
  args: readonly unknown[],
  signer: "controller" | "relay" = "controller",
): Promise<Hex> {
  const compiled = await artifact(name);
  const wallet = signer === "controller" ? context.controllerWallet : context.relayWallet;
  const account = signer === "controller" ? controllerAccount : relayAccount;
  const hash = await wallet.writeContract({ address, abi: compiled.abi, functionName, args, account } as any);
  const receipt = await context.publicClient.waitForTransactionReceipt({ hash });
  if (receipt.status !== "success") throw new Error(`${name}.${functionName} failed on ${context.key}`);
  deploymentMetrics.transactionCount += 1;
  deploymentMetrics.deploymentGasUsed += receipt.gasUsed;
  return hash;
}

function rpc(name: "A" | "B" | "C" | "D", defaultPort: number): string {
  return process.env[`EXPERIMENT_V2_CHAIN_${name}_RPC`] ?? `http://127.0.0.1:${defaultPort}`;
}

function confirmations(): number {
  const value = Number(process.env.EXPERIMENT_V2_CONFIRMATIONS ?? "1");
  if (!Number.isInteger(value) || value < 1) throw new Error("EXPERIMENT_V2_CONFIRMATIONS must be a positive integer");
  return value;
}

function contractAddresses(records: Record<string, ContractRecord>): Record<string, Address> {
  return Object.fromEntries(Object.entries(records).map(([name, record]) => [name, record.address]));
}

function deploymentTransactions(records: Record<string, ContractRecord>): Record<string, Hex> {
  return Object.fromEntries(Object.entries(records).map(([name, record]) => [name, record.deploymentTransactionHash]));
}

async function configureTarget(
  source: ChainContext,
  target: ChainContext,
  sourceContracts: ExperimentV2Deployment["source"]["contracts"],
): Promise<DestinationDeployment> {
  const privateKey = AGENT_PRIVATE_KEYS[target.chainId];
  if (!privateKey) throw new Error(`No benchmark Agent wallet is configured for chain ${target.chainId}`);
  const agentAddress = privateKeyToAccount(privateKey).address;
  const suffix = target.key.toUpperCase();
  const routeKey = id(`EXPERIMENT_A_TO_${suffix}`);
  const agentDidHash = id(`did:experiment:agent-${target.key.toLowerCase()}`);
  const records: Record<string, ContractRecord> = {};
  records.bridgeAsset = await deploy(target, "MockUSDC");
  records.payoutToken = await deploy(target, "MockUSDC");
  records.identityRegistry = await deploy(target, "AgentIdentityRegistry", [relayAccount.address]);
  records.remoteWorkBoard = await deploy(target, "RemoteWorkBoard", [BigInt(target.chainId), BigInt(source.chainId), relayAccount.address]);
  records.destinationAMM = await deploy(target, "ExperimentConstantProductAMM", [records.bridgeAsset.address, records.payoutToken.address]);
  records.destinationVault = await deploy(target, "ExperimentDestinationVault", [
    controllerAccount.address,
    relayAccount.address,
    BigInt(target.chainId),
    records.bridgeAsset.address,
    records.payoutToken.address,
    records.destinationAMM.address,
    records.remoteWorkBoard.address,
  ]);

  await write(target, "RemoteWorkBoard", records.remoteWorkBoard.address, "configureIdentityRegistry", [records.identityRegistry.address], "relay");
  await write(target, "RemoteWorkBoard", records.remoteWorkBoard.address, "configureRoute", [routeKey, 1, sourceContracts.dispatchSender, true], "relay");
  await write(target, "RemoteWorkBoard", records.remoteWorkBoard.address, "configureResultReceiver", [routeKey, 1, sourceContracts.resultProofGateway], "relay");
  await write(target, "AgentIdentityRegistry", records.identityRegistry.address, "registerOrUpdate", [
    agentDidHash,
    agentAddress,
    BigInt(target.chainId),
    id("role:benchmark-executor"),
    id(`capability:${target.key.toLowerCase()}`),
    true,
  ], "relay");
  await write(target, "ExperimentDestinationVault", records.destinationVault.address, "configureSource", [
    routeKey,
    1,
    BigInt(source.chainId),
    sourceContracts.sourceVault,
    true,
  ]);

  const now = (await target.publicClient.getBlock()).timestamp;
  await write(target, "MockUSDC", records.bridgeAsset.address, "mint", [controllerAccount.address, 1_000_000_000n]);
  await write(target, "MockUSDC", records.payoutToken.address, "mint", [controllerAccount.address, 2_000_000_000n]);
  await write(target, "MockUSDC", records.bridgeAsset.address, "approve", [records.destinationAMM.address, 1_000_000_000n]);
  await write(target, "MockUSDC", records.payoutToken.address, "approve", [records.destinationAMM.address, 2_000_000_000n]);
  await write(target, "ExperimentConstantProductAMM", records.destinationAMM.address, "addLiquidity", [
    1_000_000_000n,
    2_000_000_000n,
    1n,
    now + 86_400n,
  ]);
  await write(target, "MockUSDC", records.bridgeAsset.address, "mint", [records.destinationVault.address, 500_000_000n]);

  await write(source, "ExperimentDispatchSender", sourceContracts.dispatchSender, "configureRoute", [
    routeKey,
    1,
    BigInt(target.chainId),
    records.remoteWorkBoard.address,
    true,
  ]);
  await write(source, "ResultProofGateway", sourceContracts.resultProofGateway, "configureRoute", [
    routeKey,
    1,
    BigInt(target.chainId),
    records.remoteWorkBoard.address,
    true,
  ]);
  await write(source, "ExperimentSourceVault", sourceContracts.sourceVault, "configureRoute", [
    routeKey,
    1,
    BigInt(target.chainId),
    records.destinationVault.address,
    true,
  ]);

  return {
    key: target.key,
    chainId: target.chainId,
    rpcUrl: target.rpcUrl,
    routeKey,
    routeVersion: 1,
    agentAddress,
    agentDidHash,
    contracts: contractAddresses(records) as DestinationDeployment["contracts"],
    deploymentTransactions: deploymentTransactions(records),
  };
}

function chainManifest(
  deployment: ExperimentV2Deployment,
  destination?: DestinationDeployment,
): ExperimentV2Deployment["chains"][number] {
  if (!destination) {
    return {
      key: "A",
      chainId: deployment.sourceChainId,
      rpcUrl: deployment.sourceRpcUrl,
      confirmations: confirmations(),
      startBlock: "0",
      enabled: true,
      messageSenders: [deployment.source.contracts.dispatchSender, deployment.source.contracts.sourceVault],
      allowedReceivers: [deployment.source.contracts.resultProofGateway, deployment.source.contracts.sourceVault],
    };
  }
  return {
    key: destination.key,
    chainId: destination.chainId,
    rpcUrl: destination.rpcUrl,
    confirmations: confirmations(),
    startBlock: "0",
    enabled: true,
    messageSenders: [destination.contracts.remoteWorkBoard, destination.contracts.destinationVault],
    allowedReceivers: [destination.contracts.remoteWorkBoard, destination.contracts.destinationVault],
  };
}

export async function saveDeployment(path: string, deployment: ExperimentV2Deployment): Promise<void> {
  const absolute = resolve(path);
  await mkdir(dirname(absolute), { recursive: true });
  const temporary = `${absolute}.tmp`;
  await writeFile(temporary, JSON.stringify(deployment, null, 2), "utf8");
  await rename(temporary, absolute);
}

export async function deployInitialExperiment(path: string): Promise<ExperimentV2Deployment> {
  deploymentMetrics.transactionCount = 0;
  deploymentMetrics.deploymentGasUsed = 0n;
  const source = await chain("A", 31337, rpc("A", 8645));
  const records: Record<string, ContractRecord> = {};
  records.sourceToken = await deploy(source, "MockUSDC");
  records.bridgeAsset = await deploy(source, "MockUSDC");
  records.workflowEngine = await deploy(source, "WorkflowEngine", [controllerAccount.address]);
  records.resultProofGateway = await deploy(source, "ResultProofGateway", [controllerAccount.address, relayAccount.address]);
  await write(source, "ResultProofGateway", records.resultProofGateway.address, "configureWorkflowEngine", [records.workflowEngine.address]);
  records.coordinatorReview = await deploy(source, "CoordinatorReview", [controllerAccount.address, records.resultProofGateway.address]);
  records.sourceAMM = await deploy(source, "ExperimentConstantProductAMM", [records.sourceToken.address, records.bridgeAsset.address]);
  records.sourceVault = await deploy(source, "ExperimentSourceVault", [controllerAccount.address, relayAccount.address, records.bridgeAsset.address]);
  records.experimentEscrow = await deploy(source, "ExperimentEscrow", [
    controllerAccount.address,
    relayAccount.address,
    records.sourceToken.address,
    records.bridgeAsset.address,
    records.workflowEngine.address,
    records.coordinatorReview.address,
    records.sourceAMM.address,
    records.sourceVault.address,
  ]);
  records.dispatchSender = await deploy(source, "ExperimentDispatchSender", [controllerAccount.address, records.workflowEngine.address]);
  await write(source, "ExperimentSourceVault", records.sourceVault.address, "configureEscrow", [records.experimentEscrow.address]);
  await write(source, "WorkflowEngine", records.workflowEngine.address, "configureSettlementVerifier", [records.sourceVault.address]);
  const now = (await source.publicClient.getBlock()).timestamp;
  await write(source, "MockUSDC", records.sourceToken.address, "mint", [controllerAccount.address, 1_000_000_000n]);
  await write(source, "MockUSDC", records.bridgeAsset.address, "mint", [controllerAccount.address, 1_000_000_000n]);
  await write(source, "MockUSDC", records.sourceToken.address, "approve", [records.sourceAMM.address, 1_000_000_000n]);
  await write(source, "MockUSDC", records.bridgeAsset.address, "approve", [records.sourceAMM.address, 1_000_000_000n]);
  await write(source, "ExperimentConstantProductAMM", records.sourceAMM.address, "addLiquidity", [
    1_000_000_000n,
    1_000_000_000n,
    1n,
    now + 86_400n,
  ]);

  const sourceContracts = contractAddresses(records) as ExperimentV2Deployment["source"]["contracts"];
  const deployment: ExperimentV2Deployment = {
    schemaVersion: 2,
    generatedAt: new Date().toISOString(),
    sourceChainId: source.chainId,
    sourceRpcUrl: source.rpcUrl,
    source: { contracts: sourceContracts, deploymentTransactions: deploymentTransactions(records) },
    destinations: {},
    chains: [],
    metrics: { transactionCount: 0, deploymentGasUsed: "0" },
  };
  const targetB = await chain("B", 31338, rpc("B", 8646));
  const targetC = await chain("C", 31339, rpc("C", 8647));
  deployment.destinations[String(targetB.chainId)] = await configureTarget(source, targetB, sourceContracts);
  deployment.destinations[String(targetC.chainId)] = await configureTarget(source, targetC, sourceContracts);
  deployment.chains = [
    chainManifest(deployment),
    chainManifest(deployment, deployment.destinations[String(targetB.chainId)]),
    chainManifest(deployment, deployment.destinations[String(targetC.chainId)]),
  ];
  deployment.metrics = {
    transactionCount: deploymentMetrics.transactionCount,
    deploymentGasUsed: deploymentMetrics.deploymentGasUsed.toString(),
  };
  await saveDeployment(path, deployment);
  return deployment;
}

export async function addChainD(path: string): Promise<ExperimentV2Deployment> {
  const deployment = JSON.parse(await readFile(resolve(path), "utf8")) as ExperimentV2Deployment;
  if (deployment.destinations["31340"]) return deployment;
  deploymentMetrics.transactionCount = 0;
  deploymentMetrics.deploymentGasUsed = 0n;
  const source = await chain("A", deployment.sourceChainId, deployment.sourceRpcUrl);
  const targetD = await chain("D", 31340, rpc("D", 8648));
  const destination = await configureTarget(source, targetD, deployment.source.contracts);
  deployment.destinations[String(targetD.chainId)] = destination;
  deployment.chains.push(chainManifest(deployment, destination));
  deployment.generatedAt = new Date().toISOString();
  deployment.metrics.transactionCount += deploymentMetrics.transactionCount;
  deployment.metrics.deploymentGasUsed = (
    BigInt(deployment.metrics.deploymentGasUsed) + deploymentMetrics.deploymentGasUsed
  ).toString();
  await saveDeployment(path, deployment);
  return deployment;
}
