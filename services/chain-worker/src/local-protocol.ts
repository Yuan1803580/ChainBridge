import { createHash, createHmac, createPublicKey, randomUUID, verify as verifySignature } from "node:crypto";
import { mkdirSync, readFileSync, renameSync, writeFileSync } from "node:fs";
import { resolve } from "node:path";

import {
  createPublicClient,
  createWalletClient,
  decodeEventLog,
  defineChain,
  encodeFunctionData,
  formatEther,
  getAddress,
  http,
  keccak256,
  parseEther,
  parseAbi,
  parseTransaction,
  parseUnits,
  recoverTransactionAddress,
  stringToHex,
  type Abi,
  type Address,
  type Hash,
  type Hex,
} from "viem";
import { privateKeyToAccount, type PrivateKeyAccount } from "viem/accounts";

import { ProtocolApi, type ProtocolJob, type ProtocolRoute, type RouteDeployment } from "./protocol-api.ts";
import { AgentIntentStore, canonicalUtcTimestamp, type UnsignedTransactionIntent } from "./intent-store.ts";

type Artifact = { abi: Abi; bytecode: Hex };
type DeploymentResult = { address: Address; transactionHash: Hash };
type ChainRuntime = {
  chainId: number;
  rpcUrl: string;
  publicClient: ReturnType<typeof createPublicClient>;
  walletClient: ReturnType<typeof createWalletClient>;
  accounts: Address[];
  signingAccount?: PrivateKeyAccount;
};
type BaseManifest = {
  chainId: number;
  contracts: Record<string, Address>;
  transactionHashes: Record<string, Hash>;
  codeHashes: Record<string, Hash>;
  manifestHash: Hash;
};
type RouteManifest = {
  routeId: string;
  routeKey: string;
  routeVersion: number;
  homeChainId: number;
  contracts: Record<string, Address>;
  transactionHashes: Record<string, Hash>;
  codeHashes: Record<string, Hash>;
  manifestHash: Hash;
};
type SignedManagedAgentTransaction = {
  assignedNonce: number;
  rawTransaction: `0x02${string}`;
  transactionHash: Hash;
};

export type LocalProtocolConfig = {
  projectRoot: string;
  runtimeDir: string;
  apiUrl: string;
  adminDid: string;
  sourceChainId: number;
  sourceRpcUrl: string;
  homeRpcUrls: Record<number, string>;
  pollIntervalMs: number;
  maxJobAttempts: number;
  sourceSettlementToken?: Address;
  relayerPrivateKey?: Hex;
  ccipGasLimit: bigint;
  ccipDeliveryTimeoutMs: number;
  ccipPollIntervalMs: number;
  agentRunnerApiToken?: string;
  agentInvocationTimeoutMs: number;
  agentTransactionReceiptTimeoutMs: number;
  autoInvokeAgents: boolean;
  workerProfile: "local-v1" | "testnet-ccip";
  workerIntentHmacSecret?: string;
};

const ZERO_ADDRESS = "0x0000000000000000000000000000000000000000";
const ZERO_BYTES32 = `0x${"0".repeat(64)}`;
const ERC20_BALANCE_ABI = parseAbi(["function balanceOf(address account) view returns (uint256)", "function decimals() view returns (uint8)"]);

class DeferredProtocolJobError extends Error {
  readonly retryAfterSeconds: number;

  constructor(message: string, retryAfterSeconds: number) {
    super(message);
    this.name = "DeferredProtocolJobError";
    this.retryAfterSeconds = Math.max(1, retryAfterSeconds);
  }
}

export function loadLocalProtocolConfig(projectRoot = resolve(import.meta.dirname, "..", "..", "..")): LocalProtocolConfig {
  const configuredHomeRpcs = process.env.HOME_CHAIN_RPC_URLS
    ? JSON.parse(process.env.HOME_CHAIN_RPC_URLS) as Record<number, string>
    : {};
  const configuredPrivateKeyFile = process.env.PROTOCOL_RELAYER_PRIVATE_KEY_FILE?.trim();
  if (configuredPrivateKeyFile && process.env.PROTOCOL_RELAYER_PRIVATE_KEY?.trim()) {
    throw new Error("Configure only one of PROTOCOL_RELAYER_PRIVATE_KEY_FILE or PROTOCOL_RELAYER_PRIVATE_KEY");
  }
  const configuredPrivateKey = (
    configuredPrivateKeyFile
      ? readFileSync(configuredPrivateKeyFile, "utf8").trim()
      : process.env.PROTOCOL_RELAYER_PRIVATE_KEY?.trim()
  ) as Hex | undefined;
  if (configuredPrivateKey && !/^0x[0-9a-fA-F]{64}$/.test(configuredPrivateKey)) {
    throw new Error("PROTOCOL_RELAYER_PRIVATE_KEY must be a 32-byte hex private key");
  }
  const configuredSourceToken = process.env.DYNAMIC_SOURCE_SETTLEMENT_TOKEN;
  const configuredAgentKeys = process.env.AGENT_HOME_WALLET_PRIVATE_KEYS_JSON?.trim();
  if (configuredAgentKeys && configuredAgentKeys !== "{}") {
    throw new Error("Worker must not load AGENT_HOME_WALLET_PRIVATE_KEYS_JSON; Agent Runner signs home-chain transactions");
  }
  const workerProfile = process.env.CHAIN_WORKER_PROFILE === "testnet-ccip" ? "testnet-ccip" : "local-v1";
  const config: LocalProtocolConfig = {
    projectRoot,
    runtimeDir: process.env.LOCAL_PROTOCOL_RUNTIME_DIR || resolve(projectRoot, ".runtime", "local-v1"),
    apiUrl: (process.env.API_URL || "http://127.0.0.1:8000").replace(/\/$/, ""),
    adminDid: process.env.PROTOCOL_ADMIN_DID || "did:local:platform-admin",
    sourceChainId: Number(process.env.DYNAMIC_SOURCE_CHAIN_ID || 31337),
    sourceRpcUrl: process.env.DYNAMIC_SOURCE_RPC_URL || process.env.CHAIN_A_RPC_URL || process.env.LOCAL_CHAIN_A_RPC_URL || "http://127.0.0.1:8545",
    homeRpcUrls: {
      31338: process.env.CHAIN_B_RPC_URL || process.env.LOCAL_CHAIN_B_RPC_URL || "http://127.0.0.1:8546",
      31339: process.env.CHAIN_C_RPC_URL || process.env.LOCAL_CHAIN_C_RPC_URL || "http://127.0.0.1:8547",
      31340: process.env.CHAIN_D_RPC_URL || process.env.LOCAL_CHAIN_D_RPC_URL || "http://127.0.0.1:8548",
      ...configuredHomeRpcs,
    },
    pollIntervalMs: Number(process.env.PROTOCOL_POLL_INTERVAL_MS || 1000),
    maxJobAttempts: Number(process.env.PROTOCOL_MAX_JOB_ATTEMPTS || 4),
    sourceSettlementToken: configuredSourceToken ? getAddress(configuredSourceToken) : undefined,
    relayerPrivateKey: configuredPrivateKey,
    ccipGasLimit: BigInt(process.env.DYNAMIC_CCIP_GAS_LIMIT || "300000"),
    ccipDeliveryTimeoutMs: Number(process.env.DYNAMIC_CCIP_DELIVERY_TIMEOUT_MS || 1_200_000),
    ccipPollIntervalMs: Number(process.env.DYNAMIC_CCIP_POLL_INTERVAL_MS || 15_000),
    agentRunnerApiToken: process.env.AGENT_RUNNER_API_TOKEN,
    agentInvocationTimeoutMs: Number(process.env.AGENT_INVOCATION_TIMEOUT_SECONDS || 120) * 1000,
    agentTransactionReceiptTimeoutMs: Number(process.env.AGENT_TRANSACTION_RECEIPT_TIMEOUT_SECONDS || 120) * 1000,
    autoInvokeAgents: (process.env.AUTO_INVOKE_AGENTS || "false").toLowerCase() === "true",
    workerProfile,
    workerIntentHmacSecret: process.env.WORKER_INTENT_HMAC_SECRET,
  };
  if (workerProfile === "testnet-ccip") {
    if (config.sourceChainId !== 11155111) throw new Error("testnet-ccip requires source chain 11155111");
    if (!config.sourceSettlementToken) throw new Error("testnet-ccip requires DYNAMIC_SOURCE_SETTLEMENT_TOKEN");
    if (!config.workerIntentHmacSecret) throw new Error("testnet-ccip requires WORKER_INTENT_HMAC_SECRET");
    if (config.runtimeDir.toLowerCase().includes("local-v1")) throw new Error("testnet-ccip requires an isolated runtime directory");
  }
  return config;
}

export function opaqueIdToBytes32(value: string): Hash {
  if (/^0x[0-9a-fA-F]{64}$/.test(value)) return value as Hash;
  const compact = value.replaceAll("-", "");
  if (/^[0-9a-fA-F]{1,64}$/.test(compact)) return `0x${compact.padEnd(64, "0")}` as Hash;
  return keccak256(stringToHex(value));
}

export function homeRpcUrl(config: LocalProtocolConfig, chainId: number): string {
  const rpcUrl = config.homeRpcUrls[chainId];
  if (!rpcUrl) throw new Error(`Unsupported Agent homeChainId ${chainId}`);
  return rpcUrl;
}

export function routeRpcUrl(config: LocalProtocolConfig, chainId: number, storedRpcUrl: string): string {
  return config.workerProfile === "testnet-ccip" ? homeRpcUrl(config, chainId) : storedRpcUrl;
}

export function formatGasCost(wei: bigint): string {
  const [whole, fraction = ""] = formatEther(wei).split(".");
  const ledgerFraction = fraction.slice(0, 8).replace(/0+$/, "");
  return ledgerFraction ? `${whole}.${ledgerFraction}` : whole;
}

export function replacementFee(value: bigint): bigint {
  return (value * 1125n + 999n) / 1000n;
}

export function buildRecoveryIntent(
  original: UnsignedTransactionIntent,
  purpose: "replace" | "cancel",
  homeWallet: Address,
  observedPendingNonce: number,
  observedBlockNumber: number,
  originalStatus: "PENDING" | "DROPPED",
  now = new Date(),
): UnsignedTransactionIntent {
  const requestId = `${original.requestId}:${purpose}:v1`;
  const maxFeePerGas = replacementFee(BigInt(String(original.maxFeePerGas)));
  const maxPriorityFeePerGas = replacementFee(BigInt(String(original.maxPriorityFeePerGas)));
  return {
    ...original,
    intentId: keccak256(stringToHex(`${requestId}:${String(original.routeKey)}:${String(original.routeVersion)}`)),
    requestId,
    purpose,
    to: purpose === "cancel" ? homeWallet : original.to,
    data: purpose === "cancel" ? "0x" : original.data,
    value: "0",
    gasLimit: purpose === "cancel" ? 21_000 : original.gasLimit,
    maxFeePerGas: maxFeePerGas.toString(),
    maxPriorityFeePerGas: maxPriorityFeePerGas.toString(),
    observedPendingNonce,
    observedBlockNumber,
    observedAt: canonicalUtcTimestamp(now),
    expiresAt: canonicalUtcTimestamp(new Date(now.getTime() + 60_000)),
    replacesRequestId: original.requestId,
    originalStatus,
  };
}

export function formatNativeAmount(wei: bigint): string {
  const value = formatEther(wei);
  return value.includes(".") ? value.replace(/0+$/, "").replace(/\.$/, "") : value;
}

export function topologicallySortWorkflowNodes(nodes: Record<string, any>[]): Record<string, any>[] {
  const byKey = new Map(nodes.map((node) => [String(node.nodeKey), node]));
  const remaining = new Set(byKey.keys());
  const ordered: Record<string, any>[] = [];
  while (remaining.size > 0) {
    const ready = nodes.filter((node) => {
      const key = String(node.nodeKey);
      if (!remaining.has(key)) return false;
      const dependencies = Array.isArray(node.dependsOn) ? node.dependsOn.map(String) : [];
      if (dependencies.some((dependency) => !byKey.has(dependency))) {
        throw new Error(`Node ${key} references an unknown dependency`);
      }
      return dependencies.every((dependency) => !remaining.has(dependency));
    });
    if (ready.length === 0) throw new Error("Workflow node dependencies contain a cycle");
    for (const node of ready) {
      ordered.push(node);
      remaining.delete(String(node.nodeKey));
    }
  }
  return ordered;
}

function stable(value: unknown): string {
  if (Array.isArray(value)) return `[${value.map(stable).join(",")}]`;
  if (value && typeof value === "object") {
    return `{${Object.entries(value as Record<string, unknown>)
      .sort(([left], [right]) => left.localeCompare(right))
      .map(([key, item]) => `${JSON.stringify(key)}:${stable(item)}`)
      .join(",")}}`;
  }
  return JSON.stringify(value);
}

export function canonicalSha256(value: unknown): Hash {
  return `0x${createHash("sha256").update(stable(value), "utf8").digest("hex")}` as Hash;
}

export function signTaskOffer(secret: string, offer: Record<string, unknown>): string {
  return createHmac("sha256", secret).update(stable(offer), "utf8").digest("base64url");
}

function normalizedPublicKey(value: string): string {
  return value.replace(/^es256k:/i, "").replace(/^0x/i, "").toLowerCase();
}

export function verifyRuntimeEnvelope(
  envelope: Record<string, any>,
  expected: Record<string, unknown>,
  expectedDid: string,
  expectedSlug: string,
  expectedPublicKey: string,
): void {
  const signature = envelope.signature as Record<string, unknown> | undefined;
  if (!signature || signature.algorithm !== "ES256K" || typeof signature.value !== "string") {
    throw new Error("Agent result is missing an ES256K signature");
  }
  if (envelope.did !== expectedDid || envelope.agentId !== expectedSlug) {
    throw new Error("Agent result identity does not match the selected Agent");
  }
  for (const [key, value] of Object.entries(expected)) {
    if (envelope[key] !== value) throw new Error(`Agent result ${key} does not match the accepted offer`);
  }
  const outputHash = `sha256:${canonicalSha256(envelope.output).slice(2)}`;
  if (envelope.outputHash !== outputHash) throw new Error("Agent result outputHash does not match output");
  const publicKeyHex = normalizedPublicKey(String(signature.publicKeyHex || expectedPublicKey));
  if (publicKeyHex !== normalizedPublicKey(expectedPublicKey) || !/^04[0-9a-f]{128}$/.test(publicKeyHex)) {
    throw new Error("Agent result public key does not match the registered operational key");
  }
  const signedFields = [
    "agentId", "capabilityId", "completedAt", "did", "outputHash", "requestId",
    "workflowId", "nodeId", "attemptId", "attemptNumber", "reservationId",
    "deadline", "nonce", "offerId",
  ];
  if (signedFields.some((field) => !(field in envelope))) {
    throw new Error("Agent result signed context is incomplete");
  }
  const signedPayload = Object.fromEntries(signedFields.map((field) => [field, envelope[field]]));
  const coordinate = 2 + 64;
  const publicKey = createPublicKey({
    key: {
      kty: "EC",
      crv: "secp256k1",
      x: Buffer.from(publicKeyHex.slice(2, coordinate), "hex").toString("base64url"),
      y: Buffer.from(publicKeyHex.slice(coordinate), "hex").toString("base64url"),
    },
    format: "jwk",
  });
  if (!verifySignature("sha256", Buffer.from(stable(signedPayload), "utf8"), publicKey, Buffer.from(signature.value, "base64url"))) {
    throw new Error("Agent result signature is invalid");
  }
}

class AgentInvocationError extends Error {
  readonly reasonCode: "AGENT_TIMEOUT" | "AGENT_INVALID_SIGNATURE";

  constructor(message: string, reasonCode: "AGENT_TIMEOUT" | "AGENT_INVALID_SIGNATURE") {
    super(message);
    this.reasonCode = reasonCode;
  }
}

function manifestHash(value: Record<string, unknown>): Hash {
  return keccak256(stringToHex(stable(value)));
}

export class LocalProtocolWorker {
  private readonly artifacts = new Map<string, Artifact>();
  private readonly chains = new Map<number, ChainRuntime>();
  private readonly agentIntents: AgentIntentStore;
  readonly config: LocalProtocolConfig;
  readonly api: ProtocolApi;

  constructor(
    config: LocalProtocolConfig,
    api = new ProtocolApi(config.apiUrl, config.adminDid),
  ) {
    this.config = config;
    this.api = api;
    mkdirSync(config.runtimeDir, { recursive: true });
    this.agentIntents = new AgentIntentStore(config.runtimeDir);
  }

  async initialize(): Promise<BaseManifest> {
    const source = await this.chain(this.config.sourceChainId);
    if (source.chainId !== this.config.sourceChainId) throw new Error("Chain A RPC chainId mismatch");
    const base = await this.ensureBaseStack();
    await this.reconcileRouteDeployments();
    return base;
  }

  private async reconcileRouteDeployments(): Promise<void> {
    const routes = await this.api.routes();
    const deployments = await this.api.routeDeployments();
    for (const route of routes.filter((item) => item.deploymentStatus === "READY")) {
      const deployment = deployments.find((item) => item.routeId === route.id && item.routeVersion === route.routeVersion);
      if (!deployment || deployment.status !== "READY") continue;
      const destination = await this.chain(
        route.destinationChainId,
        routeRpcUrl(this.config, route.destinationChainId, route.rpcUrl),
      );
      const source = await this.chain(this.config.sourceChainId);
      const contractLocations = route.transport === "CHAINLINK_CCIP"
        ? [
            [source, deployment.contracts.dynamicCCIPWorkflowSenderA],
            [destination, deployment.contracts.dynamicCCIPWorkflowReceiver],
            [destination, deployment.contracts.remoteWorkBoard],
            [destination, deployment.contracts.agentIdentityRegistry],
            [destination, deployment.contracts.destinationToken],
          ] as const
        : [
            [destination, deployment.contracts.remoteWorkBoard],
            [destination, deployment.contracts.agentIdentityRegistry],
            [destination, deployment.contracts.dynamicSettlementReceiver],
            [destination, deployment.contracts.destinationToken],
            [destination, deployment.contracts.payoutToken],
            [destination, deployment.contracts.payoutSwap],
          ] as const;
      let valid = contractLocations.every(([, address]) => Boolean(address));
      for (const [chain, address] of contractLocations) {
        if (!address) continue;
        const code = await chain.publicClient.getCode({ address: address as Address });
        if (!code || code === "0x") valid = false;
      }
      if (!valid) {
        await this.api.completeRouteDeployment(deployment.id, {
          status: "FAILED",
          contracts: deployment.contracts || {},
          transactionHashes: deployment.transactionHashes || {},
          codeHashes: deployment.codeHashes || {},
          manifestHash: deployment.manifestHash,
          failureReason: "Local RPC was reset and the persisted route contracts no longer have bytecode",
        });
      }
    }
  }

  async processOne(): Promise<boolean> {
    const job = (await this.api.queuedJobs(20))[0];
    if (!job) return false;
    await this.api.updateJob(job.id, { status: "RUNNING" });
    try {
      const transactionHash = await this.execute(job);
      await this.api.updateJob(job.id, { status: "COMPLETED", transactionHash });
    } catch (error) {
      const message = (error instanceof Error ? error.message : String(error)).slice(0, 5000);
      if (error instanceof DeferredProtocolJobError) {
        await this.api.updateJob(job.id, {
          status: "RETRY",
          error: message,
          retryAfterSeconds: error.retryAfterSeconds,
        });
        return true;
      }
      const terminal = job.attempts + 1 >= this.config.maxJobAttempts;
      if (job.jobType === "DEPLOY_ROUTE_STACK" && terminal && typeof job.payload.deploymentId === "string") {
        await this.api.completeRouteDeployment(job.payload.deploymentId, {
          status: "FAILED",
          contracts: {},
          transactionHashes: {},
          codeHashes: {},
          failureReason: message,
        });
      }
      await this.api.updateJob(job.id, { status: terminal ? "FAILED" : "RETRY", error: message });
    }
    return true;
  }

  private async execute(job: ProtocolJob): Promise<Hash | undefined> {
    switch (job.jobType) {
      case "DEPLOY_ROUTE_STACK": return this.deployRouteStack(job);
      case "REGISTER_WORKFLOW": return this.registerWorkflow(job);
      case "DISPATCH_ASSIGNMENT": return this.dispatchAssignment(job);
      case "RELAY_RESULT_PROOF": return this.relayResultProof(job);
      case "RECORD_REVIEW": return this.recordReview(job);
      case "SYNC_REVIEW_V2": return this.syncReviewV2(job);
      case "CROSS_CHAIN_SETTLEMENT": return this.crossChainSettlement(job);
      case "FINALIZE_WORKFLOW": return this.finalizeWorkflow(job);
      case "REFUND_FAILED_WORKFLOW": return this.refundFailedWorkflow(job);
      case "SYSTEM_RETRY": return this.systemRetry(job);
      case "CANCEL_AGENT_TRANSACTION": return this.cancelAgentTransaction(job);
      default: throw new Error(`Unsupported protocol job type ${job.jobType}`);
    }
  }

  private artifact(name: string): Artifact {
    const cached = this.artifacts.get(name);
    if (cached) return cached;
    const path = resolve(this.config.projectRoot, `hardhat-artifacts/contracts/src/${name}.sol/${name}.json`);
    const value = JSON.parse(readFileSync(path, "utf8")) as Artifact;
    if (!value.bytecode || value.bytecode === "0x") throw new Error(`Artifact ${name} has no bytecode`);
    this.artifacts.set(name, value);
    return value;
  }

  private async chain(chainId: number, configuredRpcUrl?: string): Promise<ChainRuntime> {
    const cached = this.chains.get(chainId);
    if (cached) {
      if (configuredRpcUrl && cached.rpcUrl !== configuredRpcUrl) {
        throw new Error(`Chain ${chainId} was already bound to ${cached.rpcUrl}, not ${configuredRpcUrl}`);
      }
      return cached;
    }
    const rpcUrl = chainId === this.config.sourceChainId
      ? this.config.sourceRpcUrl
      : configuredRpcUrl || homeRpcUrl(this.config, chainId);
    const definition = defineChain({
      id: chainId,
      name: `Local Chain ${chainId}`,
      nativeCurrency: { name: "Local ETH", symbol: "ETH", decimals: 18 },
      rpcUrls: { default: { http: [rpcUrl] } },
    });
    const publicClient = createPublicClient({ chain: definition, transport: http(rpcUrl) });
    const configuredAccount = this.config.relayerPrivateKey
      ? privateKeyToAccount(this.config.relayerPrivateKey)
      : undefined;
    const walletClient = createWalletClient({
      account: configuredAccount,
      chain: definition,
      transport: http(rpcUrl),
    });
    const observedChainId = await publicClient.getChainId();
    if (observedChainId !== chainId) throw new Error(`RPC ${rpcUrl} returned chainId ${observedChainId}, expected ${chainId}`);
    const accounts = configuredAccount ? [configuredAccount.address] : await walletClient.getAddresses();
    if (accounts.length === 0) {
      throw new Error(`Chain ${chainId} exposes no unlocked account and PROTOCOL_RELAYER_PRIVATE_KEY is not configured`);
    }
    const runtime = { chainId, rpcUrl, publicClient, walletClient, accounts, signingAccount: configuredAccount };
    this.chains.set(chainId, runtime);
    return runtime;
  }

  private async deploy(chain: ChainRuntime, name: string, args: readonly unknown[] = []): Promise<DeploymentResult> {
    const artifact = this.artifact(name);
    const transactionHash = await chain.walletClient.deployContract({
      account: chain.signingAccount || chain.accounts[0],
      abi: artifact.abi,
      bytecode: artifact.bytecode,
      args,
    } as never);
    const receipt = await chain.publicClient.waitForTransactionReceipt({ hash: transactionHash });
    if (receipt.status !== "success" || !receipt.contractAddress) throw new Error(`${name} deployment reverted on ${chain.chainId}`);
    return { address: receipt.contractAddress, transactionHash };
  }

  private async write(
    chain: ChainRuntime,
    contractName: string,
    address: Address,
    functionName: string,
    args: readonly unknown[],
    account = chain.accounts[0],
    value?: bigint,
  ): Promise<Hash> {
    const signer = chain.signingAccount && chain.signingAccount.address.toLowerCase() === account.toLowerCase()
      ? chain.signingAccount
      : account;
    const transactionHash = await chain.walletClient.writeContract({
      account: signer,
      address,
      abi: this.artifact(contractName).abi,
      functionName,
      args,
      value,
    } as never);
    const receipt = await chain.publicClient.waitForTransactionReceipt({ hash: transactionHash });
    if (receipt.status !== "success") throw new Error(`${contractName}.${functionName} reverted on ${chain.chainId}`);
    return transactionHash;
  }

  private async signManagedAgentIntent(
    chain: ChainRuntime,
    agentSlug: string,
    agentDid: string,
    runtimeId: string,
    homeWallet: Address,
    runtimeEndpoint: string,
    intent: UnsignedTransactionIntent,
  ): Promise<SignedManagedAgentTransaction> {
    const signed = await this.runnerRequest(
      `${runtimeEndpoint}/wallet-transactions/sign`,
      this.config.agentRunnerApiToken!,
      intent,
      this.workerPayloadSignature(intent),
    );
    const rawTransaction = signed.rawTransaction as `0x02${string}`;
    const assignedNonce = Number(signed.assignedNonce);
    if (!/^0x02[0-9a-fA-F]+$/.test(rawTransaction || "") || !Number.isInteger(assignedNonce)) {
      throw new Error(`Agent ${agentSlug} returned an invalid signed transaction`);
    }
    const [recovered, parsed] = await Promise.all([
      recoverTransactionAddress({ serializedTransaction: rawTransaction }),
      Promise.resolve(parseTransaction(rawTransaction)),
    ]);
    if (
      recovered.toLowerCase() !== homeWallet.toLowerCase()
      || String(signed.signer || "").toLowerCase() !== homeWallet.toLowerCase()
      || Number(parsed.chainId) !== chain.chainId
      || parsed.to?.toLowerCase() !== String(intent.to).toLowerCase()
      || (parsed.data || "0x").toLowerCase() !== String(intent.data).toLowerCase()
      || parsed.value !== BigInt(String(intent.value))
      || Number(parsed.nonce) !== assignedNonce
      || parsed.gas !== BigInt(Number(intent.gasLimit))
      || parsed.maxFeePerGas !== BigInt(String(intent.maxFeePerGas))
      || parsed.maxPriorityFeePerGas !== BigInt(String(intent.maxPriorityFeePerGas))
    ) {
      throw new Error(`Agent ${agentSlug} signed transaction does not match the immutable Worker intent`);
    }
    if (String(intent.runtimeId) !== runtimeId || String(intent.did) !== agentDid) {
      throw new Error("Managed Agent intent identity changed before signing");
    }
    const transactionHash = keccak256(rawTransaction);
    if (signed.transactionHash && String(signed.transactionHash).toLowerCase() !== transactionHash.toLowerCase()) {
      throw new Error(`Agent ${agentSlug} returned a mismatched signed transaction hash`);
    }
    return { assignedNonce, rawTransaction, transactionHash };
  }

  private async reportManagedAgentTransactionStatus(
    chain: ChainRuntime,
    agentDid: string,
    runtimeId: string,
    homeWallet: Address,
    runtimeEndpoint: string,
    requestId: string,
    transactionHash: Hash,
    status: "BROADCAST" | "CONFIRMED" | "DROPPED" | "REVERTED" | "NONCE_CONFLICT",
  ): Promise<void> {
    const statusPayload: Record<string, unknown> = {
      runtimeId,
      did: agentDid,
      status,
      transactionHash,
      observedPendingNonce: await chain.publicClient.getTransactionCount({ address: homeWallet, blockTag: "pending" }),
      observedBlockNumber: Number(await chain.publicClient.getBlockNumber()),
      observedAt: canonicalUtcTimestamp(new Date()),
    };
    await this.runnerRequest(
      `${runtimeEndpoint}/wallet-transactions/${encodeURIComponent(requestId)}/status`,
      this.config.agentRunnerApiToken!,
      statusPayload,
      this.workerPayloadSignature(statusPayload),
    );
  }

  private async recoverManagedAgentTransaction(
    chain: ChainRuntime,
    agentSlug: string,
    agentDid: string,
    runtimeId: string,
    homeWallet: Address,
    runtimeEndpoint: string,
    originalIntent: UnsignedTransactionIntent,
    originalSigned: SignedManagedAgentTransaction,
    purpose: "replace" | "cancel",
    evidenceAttemptId: string,
  ): Promise<Hash> {
    try {
      const receipt = await chain.publicClient.getTransactionReceipt({ hash: originalSigned.transactionHash });
      if (receipt.status === "success") {
        await this.reportManagedAgentTransactionStatus(chain, agentDid, runtimeId, homeWallet, runtimeEndpoint, originalIntent.requestId, originalSigned.transactionHash, "CONFIRMED");
        if (purpose === "cancel") throw new Error("Confirmed Agent transaction cannot be cancelled");
        return originalSigned.transactionHash;
      }
      await this.reportManagedAgentTransactionStatus(chain, agentDid, runtimeId, homeWallet, runtimeEndpoint, originalIntent.requestId, originalSigned.transactionHash, "REVERTED");
      throw new Error("Reverted Agent transaction cannot be replaced or cancelled");
    } catch (error) {
      const message = error instanceof Error ? error.message : String(error);
      if (message.includes("cannot be")) throw error;
    }

    let originalStatus: "PENDING" | "DROPPED";
    try {
      const transaction = await chain.publicClient.getTransaction({ hash: originalSigned.transactionHash });
      originalStatus = transaction.blockNumber == null ? "PENDING" : "DROPPED";
    } catch {
      const pendingNonce = await chain.publicClient.getTransactionCount({ address: homeWallet, blockTag: "pending" });
      if (pendingNonce > originalSigned.assignedNonce) {
        await this.reportManagedAgentTransactionStatus(chain, agentDid, runtimeId, homeWallet, runtimeEndpoint, originalIntent.requestId, originalSigned.transactionHash, "NONCE_CONFLICT");
        throw new Error(`Agent nonce ${originalSigned.assignedNonce} was consumed by another transaction`);
      }
      originalStatus = "DROPPED";
    }

    const [observedPendingNonce, observedBlockNumber] = await Promise.all([
      chain.publicClient.getTransactionCount({ address: homeWallet, blockTag: "pending" }),
      chain.publicClient.getBlockNumber(),
    ]);
    const recoveryIntent = await this.agentIntents.getOrCreate(
      `${originalIntent.requestId}:${purpose}:v1`,
      async () => buildRecoveryIntent(
        originalIntent,
        purpose,
        homeWallet,
        observedPendingNonce,
        Number(observedBlockNumber),
        originalStatus,
      ),
    );
    const recoverySigned = await this.signManagedAgentIntent(
      chain,
      agentSlug,
      agentDid,
      runtimeId,
      homeWallet,
      runtimeEndpoint,
      recoveryIntent,
    );
    this.recordManagedAgentTransactionEvidence(evidenceAttemptId, homeWallet, recoveryIntent, recoverySigned);
    if (recoverySigned.assignedNonce !== originalSigned.assignedNonce) {
      throw new Error(`${purpose} changed the reserved Agent nonce`);
    }

    let transactionHash: Hash;
    try {
      transactionHash = await chain.publicClient.sendRawTransaction({ serializedTransaction: recoverySigned.rawTransaction });
    } catch (error) {
      try {
        const receipt = await chain.publicClient.getTransactionReceipt({ hash: originalSigned.transactionHash });
        if (receipt.status === "success" && purpose === "replace") return originalSigned.transactionHash;
      } catch { /* preserve the original broadcast error */ }
      throw error;
    }
    if (transactionHash.toLowerCase() !== recoverySigned.transactionHash.toLowerCase()) {
      throw new Error(`RPC returned a different hash for Agent ${purpose} transaction`);
    }
    await this.reportManagedAgentTransactionStatus(chain, agentDid, runtimeId, homeWallet, runtimeEndpoint, recoveryIntent.requestId, transactionHash, "BROADCAST");
    await this.reportManagedAgentTransactionStatus(chain, agentDid, runtimeId, homeWallet, runtimeEndpoint, originalIntent.requestId, originalSigned.transactionHash, "DROPPED");
    const receipt = await chain.publicClient.waitForTransactionReceipt({
      hash: transactionHash,
      timeout: this.config.agentTransactionReceiptTimeoutMs,
    });
    if (receipt.status !== "success") {
      await this.reportManagedAgentTransactionStatus(chain, agentDid, runtimeId, homeWallet, runtimeEndpoint, recoveryIntent.requestId, transactionHash, "REVERTED");
      throw new Error(`Agent ${purpose} transaction reverted on ${chain.chainId}`);
    }
    await this.reportManagedAgentTransactionStatus(chain, agentDid, runtimeId, homeWallet, runtimeEndpoint, recoveryIntent.requestId, transactionHash, "CONFIRMED");
    return transactionHash;
  }

  private async writeAsAgent(
    chain: ChainRuntime,
    agentSlug: string,
    agentDid: string,
    runtimeId: string,
    homeWallet: Address,
    contractName: string,
    address: Address,
    functionName: string,
    args: readonly unknown[],
    runtimeEndpoint: string,
    applicationAttemptId: string,
    attemptId: Hash,
    workflowId: string,
    resultMessageId: Hash,
    resultHash: Hash,
    proofHash: Hash,
    route: ProtocolRoute,
  ): Promise<Hash> {
    if (!runtimeEndpoint || !this.config.agentRunnerApiToken) {
      throw new Error(`Agent ${agentSlug} Runner endpoint and API token are required for home-chain signing`);
    }
    const data = encodeFunctionData({
      abi: this.artifact(contractName).abi,
      functionName,
      args,
    } as never);
    if (this.config.workerProfile === "testnet-ccip") {
      if (!runtimeId || !agentDid || route.transport !== "CHAINLINK_CCIP") {
        throw new Error("Managed Agent signing requires runtimeId, DID, and a CHAINLINK_CCIP route");
      }
      const canonicalPayload: Record<string, unknown> = {
        runtimeId,
        did: agentDid,
        workflowId,
        attemptId,
        resultMessageId,
        resultHash,
        proofHash,
        routeKey: route.routeKey,
        routeVersion: route.routeVersion,
      };
      const canonicalAttestation = await this.runnerRequest(
        `${runtimeEndpoint}/canonical-results/sign`,
        this.config.agentRunnerApiToken,
        canonicalPayload,
        this.workerPayloadSignature(canonicalPayload),
      );
      this.saveAgentAcceptanceEvidence(applicationAttemptId, {
        canonical: { payload: canonicalPayload, attestation: canonicalAttestation },
      });
      const requestId = `${applicationAttemptId}:submit-result:v1`;
      const intent = await this.agentIntents.getOrCreate(requestId, async () => {
        const [observedPendingNonce, estimatedGas, feeEstimate, observedBlockNumber] = await Promise.all([
          chain.publicClient.getTransactionCount({ address: homeWallet, blockTag: "pending" }),
          chain.publicClient.estimateGas({ account: homeWallet, to: address, data }),
          chain.publicClient.estimateFeesPerGas(),
          chain.publicClient.getBlockNumber(),
        ]);
        const now = new Date();
        return {
          intentId: keccak256(stringToHex(`${requestId}:${route.routeKey}:${route.routeVersion}`)),
          requestId,
          runtimeId,
          did: agentDid,
          purpose: "submit_result",
          chainId: chain.chainId,
          to: address,
          data,
          value: "0",
          gasLimit: Number((estimatedGas * 120n) / 100n),
          maxFeePerGas: String(feeEstimate.maxFeePerGas),
          maxPriorityFeePerGas: String(feeEstimate.maxPriorityFeePerGas),
          observedPendingNonce,
          observedBlockNumber: Number(observedBlockNumber),
          observedAt: canonicalUtcTimestamp(now),
          routeKey: route.routeKey,
          routeVersion: route.routeVersion,
          policyVersion: "agent-wallet-v1",
          expiresAt: canonicalUtcTimestamp(new Date(now.getTime() + 60_000)),
        } satisfies UnsignedTransactionIntent;
      });
      const immutableIntent = {
        requestId,
        runtimeId,
        did: agentDid,
        purpose: "submit_result",
        chainId: chain.chainId,
        to: address.toLowerCase(),
        data: data.toLowerCase(),
        value: "0",
        routeKey: route.routeKey,
        routeVersion: route.routeVersion,
      };
      for (const [field, expected] of Object.entries(immutableIntent)) {
        const actual = field === "to" || field === "data"
          ? String(intent[field]).toLowerCase()
          : intent[field];
        if (actual !== expected) {
          throw new Error(`Persisted Agent transaction intent changed immutable field ${field}`);
        }
      }
      const signed = await this.signManagedAgentIntent(
        chain,
        agentSlug,
        agentDid,
        runtimeId,
        homeWallet,
        runtimeEndpoint,
        intent,
      );
      this.recordManagedAgentTransactionEvidence(applicationAttemptId, homeWallet, intent, signed);

      const currentPendingNonce = await chain.publicClient.getTransactionCount({ address: homeWallet, blockTag: "pending" });
      let transactionHash: Hash;
      if (signed.assignedNonce > currentPendingNonce) {
        throw new DeferredProtocolJobError(
          `Agent nonce ${signed.assignedNonce} is waiting for lower pending nonce ${currentPendingNonce}`,
          15,
        );
      }
      if (signed.assignedNonce < currentPendingNonce) {
        try {
          await chain.publicClient.getTransaction({ hash: signed.transactionHash });
          transactionHash = signed.transactionHash;
        } catch {
          await this.reportManagedAgentTransactionStatus(chain, agentDid, runtimeId, homeWallet, runtimeEndpoint, requestId, signed.transactionHash, "NONCE_CONFLICT");
          throw new Error(`Agent nonce ${signed.assignedNonce} was consumed by another transaction`);
        }
      } else {
        transactionHash = await chain.publicClient.sendRawTransaction({ serializedTransaction: signed.rawTransaction });
        if (transactionHash.toLowerCase() !== signed.transactionHash.toLowerCase()) {
          throw new Error(`RPC returned a different hash for Agent ${agentSlug} transaction`);
        }
        await this.reportManagedAgentTransactionStatus(chain, agentDid, runtimeId, homeWallet, runtimeEndpoint, requestId, transactionHash, "BROADCAST");
      }
      let receipt;
      try {
        receipt = await chain.publicClient.waitForTransactionReceipt({
          hash: transactionHash,
          timeout: this.config.agentTransactionReceiptTimeoutMs,
        });
      } catch {
        return this.recoverManagedAgentTransaction(
          chain,
          agentSlug,
          agentDid,
          runtimeId,
          homeWallet,
          runtimeEndpoint,
          intent,
          signed,
          "replace",
          applicationAttemptId,
        );
      }
      if (receipt.status !== "success") {
        await this.reportManagedAgentTransactionStatus(chain, agentDid, runtimeId, homeWallet, runtimeEndpoint, requestId, transactionHash, "REVERTED");
        throw new Error(`${contractName}.${functionName} reverted on ${chain.chainId}`);
      }
      await this.reportManagedAgentTransactionStatus(chain, agentDid, runtimeId, homeWallet, runtimeEndpoint, requestId, transactionHash, "CONFIRMED");
      return transactionHash;
    }
    const [nonce, estimatedGas, gasPrice] = await Promise.all([
      chain.publicClient.getTransactionCount({ address: homeWallet, blockTag: "pending" }),
      chain.publicClient.estimateGas({ account: homeWallet, to: address, data }),
      chain.publicClient.getGasPrice(),
    ]);
    const requestNonce = `0x${createHash("sha256").update(randomUUID()).digest("hex")}` as Hash;
    const signed = await this.runnerRequest(
      `${runtimeEndpoint}/wallet-transactions/sign`,
      this.config.agentRunnerApiToken,
      {
        chainId: chain.chainId,
        to: address,
        data,
        valueWei: "0",
        nonce,
        gas: Number((estimatedGas * 120n) / 100n),
        gasPriceWei: gasPrice.toString(),
        attemptId: applicationAttemptId,
        attemptIdBytes32: attemptId,
        requestNonce,
      },
    );
    if (String(signed.signer || "").toLowerCase() !== homeWallet.toLowerCase()) {
      throw new Error(`Agent ${agentSlug} Runner signed with an unexpected wallet`);
    }
    const rawTransaction = signed.rawTransaction as Hex;
    if (!/^0x[0-9a-fA-F]+$/.test(rawTransaction || "")) {
      throw new Error(`Agent ${agentSlug} Runner returned an invalid raw transaction`);
    }
    const transactionHash = await chain.publicClient.sendRawTransaction({ serializedTransaction: rawTransaction });
    if (signed.transactionHash && String(signed.transactionHash).toLowerCase() !== transactionHash.toLowerCase()) {
      throw new Error(`Agent ${agentSlug} Runner transaction hash does not match the broadcast transaction`);
    }
    const receipt = await chain.publicClient.waitForTransactionReceipt({ hash: transactionHash });
    if (receipt.status !== "success") throw new Error(`${contractName}.${functionName} reverted on ${chain.chainId}`);
    return transactionHash;
  }

  private async cancelAgentTransaction(job: ProtocolJob): Promise<Hash> {
    if (this.config.workerProfile !== "testnet-ccip" || !job.workflowId || !job.nodeId || !job.attemptId) {
      throw new Error("CANCEL_AGENT_TRANSACTION is available only for complete managed-testnet attempts");
    }
    const route = await this.routeFor(job);
    const deployment = await this.deploymentFor(job);
    const workflow = await this.api.workflow(job.workflowId);
    const node = (workflow.nodes as Record<string, any>[]).find((item) => item.id === job.nodeId);
    const attempt = (node?.attempts as Record<string, any>[] | undefined)?.find((item) => item.id === job.attemptId);
    const board = deployment.contracts.remoteWorkBoard as Address | undefined;
    const homeWallet = attempt?.homeWallet as Address | undefined;
    const agentSlug = String(job.payload.agentSlug || "");
    const agentDid = String(job.payload.agentDid || "");
    const runtimeId = String(job.payload.agentRuntimeId || "");
    const runtimeEndpoint = String(job.payload.agentRuntimeEndpoint || "").replace(/\/+$/, "").replace(/\/invoke$/, "");
    if (!node || !attempt || !board || !homeWallet || !agentSlug || !agentDid || !runtimeId || !runtimeEndpoint) {
      throw new Error("Agent cancel job is missing its immutable attempt or Runtime binding");
    }
    const originalRequestId = `${job.attemptId}:submit-result:v1`;
    const originalIntent = this.agentIntents.load(originalRequestId);
    if (!originalIntent) throw new Error(`Original Agent intent ${originalRequestId} was not found`);
    const latestIntent = this.agentIntents.load(`${originalRequestId}:replace:v1`) || originalIntent;
    const destination = await this.chain(route.destinationChainId, route.rpcUrl);
    const latestSigned = await this.signManagedAgentIntent(
      destination,
      agentSlug,
      agentDid,
      runtimeId,
      homeWallet,
      runtimeEndpoint,
      latestIntent,
    );
    const transactionHash = await this.recoverManagedAgentTransaction(
      destination,
      agentSlug,
      agentDid,
      runtimeId,
      homeWallet,
      runtimeEndpoint,
      latestIntent,
      latestSigned,
      "cancel",
      job.attemptId,
    );
    await this.failAgentInvocation(
      job,
      workflow,
      board,
      destination,
      new AgentInvocationError("Agent transaction was explicitly cancelled by the platform operator", "AGENT_TIMEOUT"),
    );
    return transactionHash;
  }

  private async codeHashes(chain: ChainRuntime, contracts: Record<string, Address>): Promise<Record<string, Hash>> {
    const values: Record<string, Hash> = {};
    for (const [name, address] of Object.entries(contracts)) {
      const code = await chain.publicClient.getCode({ address });
      if (!code || code === "0x") throw new Error(`${name} has no code at ${address} on chain ${chain.chainId}`);
      values[name] = keccak256(code);
    }
    return values;
  }

  private async transactionGasCost(chain: ChainRuntime, transactionHash: Hash): Promise<bigint> {
    const receipt = await chain.publicClient.getTransactionReceipt({ hash: transactionHash });
    return receipt.gasUsed * (receipt.effectiveGasPrice || 0n);
  }

  private async chargeSourceGas(
    base: BaseManifest,
    source: ChainRuntime,
    workflowId: Hash,
    label: string,
    transactionHashes: Hash[],
  ): Promise<bigint> {
    const hashes = [...new Set(transactionHashes.filter(Boolean))];
    let amount = 0n;
    for (const transactionHash of hashes) amount += await this.transactionGasCost(source, transactionHash);
    if (amount === 0n) return 0n;
    const chargeId = keccak256(stringToHex(`${workflowId}:${label}`));
    const processed = await source.publicClient.readContract({
      address: base.contracts.gasTreasury,
      abi: this.artifact("GasTreasuryA").abi,
      functionName: "processedCharges",
      args: [chargeId],
    }) as boolean;
    if (!processed) {
      await this.write(source, "GasTreasuryA", base.contracts.gasTreasury, "charge", [
        workflowId,
        chargeId,
        source.accounts[0],
        amount,
      ]);
    }
    return amount;
  }

  private basePath(): string {
    return resolve(this.config.runtimeDir, "protocol-base.json");
  }

  private routePath(deploymentId: string): string {
    return resolve(this.config.runtimeDir, `route-${deploymentId}.json`);
  }

  private agentResultPath(attemptId: string): string {
    return resolve(this.config.runtimeDir, "agent-results", `${attemptId}.json`);
  }

  private agentAcceptanceEvidencePath(attemptId: string): string {
    const safeId = attemptId.replace(/[^A-Za-z0-9_.-]/g, "_");
    return resolve(this.config.runtimeDir, "acceptance", `agent-transaction-${safeId}.json`);
  }

  private saveAgentAcceptanceEvidence(attemptId: string, update: Record<string, unknown>): void {
    const path = this.agentAcceptanceEvidencePath(attemptId);
    mkdirSync(resolve(path, ".."), { recursive: true });
    let current: Record<string, any> = {};
    try { current = JSON.parse(readFileSync(path, "utf8")); } catch { /* first evidence write */ }
    const next = {
      schemaVersion: 1,
      attemptId,
      ...current,
      ...update,
      recordedAt: canonicalUtcTimestamp(new Date()),
    };
    const temporary = `${path}.${randomUUID()}.tmp`;
    writeFileSync(temporary, `${JSON.stringify(next, null, 2)}\n`, "utf8");
    renameSync(temporary, path);
  }

  private recordManagedAgentTransactionEvidence(
    attemptId: string,
    signer: Address,
    intent: UnsignedTransactionIntent,
    signed: SignedManagedAgentTransaction,
  ): void {
    const path = this.agentAcceptanceEvidencePath(attemptId);
    let current: Record<string, any> = {};
    try { current = JSON.parse(readFileSync(path, "utf8")); } catch { /* canonical evidence may be written later */ }
    const transactions = Array.isArray(current.transactions) ? current.transactions : [];
    const transaction = {
      requestId: intent.requestId,
      purpose: intent.purpose,
      intent,
      workerIntentSignature: this.workerPayloadSignature(intent),
      assignedNonce: signed.assignedNonce,
      signer,
      rawTransaction: signed.rawTransaction,
      transactionHash: signed.transactionHash,
    };
    const nextTransactions = transactions.filter((item: Record<string, unknown>) => item.requestId !== intent.requestId);
    nextTransactions.push(transaction);
    this.saveAgentAcceptanceEvidence(attemptId, {
      transactions: nextTransactions,
      finalTransactionHash: signed.transactionHash,
    });
  }

  private saveAgentResult(attemptId: string, value: Record<string, unknown>): void {
    const path = this.agentResultPath(attemptId);
    mkdirSync(resolve(path, ".."), { recursive: true });
    const temporary = `${path}.${randomUUID()}.tmp`;
    writeFileSync(temporary, `${JSON.stringify(value, null, 2)}\n`, "utf8");
    renameSync(temporary, path);
  }

  private loadAgentResult(attemptId: string): Record<string, any> | undefined {
    try {
      return JSON.parse(readFileSync(this.agentResultPath(attemptId), "utf8")) as Record<string, any>;
    } catch {
      return undefined;
    }
  }

  private async validManifest(path: string, chainId: number): Promise<BaseManifest | RouteManifest | undefined> {
    try {
      const value = JSON.parse(readFileSync(path, "utf8")) as BaseManifest | RouteManifest;
      if ("homeChainId" in value && (!value.contracts.payoutToken || !value.contracts.payoutSwap)) return undefined;
      const destination = await this.chain(chainId);
      const source = "homeChainId" in value ? await this.chain(this.config.sourceChainId) : destination;
      for (const [name, address] of Object.entries(value.contracts)) {
        const chain = "homeChainId" in value && (name.endsWith("A") || name === "sourceTokenA") ? source : destination;
        const code = await chain.publicClient.getCode({ address });
        if (!code || code === "0x") return undefined;
        if (!value.codeHashes[name] || keccak256(code) !== value.codeHashes[name]) return undefined;
      }
      const expectedHash = "homeChainId" in value
        ? manifestHash({
            routeId: value.routeId,
            routeKey: value.routeKey,
            routeVersion: value.routeVersion,
            homeChainId: value.homeChainId,
            contracts: value.contracts,
            transactionHashes: value.transactionHashes,
            codeHashes: value.codeHashes,
          })
        : manifestHash({
            chainId: value.chainId,
            contracts: value.contracts,
            transactionHashes: value.transactionHashes,
            codeHashes: value.codeHashes,
          });
      if (expectedHash !== value.manifestHash) return undefined;
      return value;
    } catch {
      return undefined;
    }
  }

  async ensureBaseStack(): Promise<BaseManifest> {
    const existing = await this.validManifest(this.basePath(), this.config.sourceChainId);
    if (
      existing
      && "chainId" in existing
      && Boolean(existing.contracts.reviewManagerV2)
      && (!this.config.sourceSettlementToken
        || existing.contracts.sourceToken.toLowerCase() === this.config.sourceSettlementToken.toLowerCase())
    ) {
      await this.registerReviewManagerRuntime(existing);
      return existing;
    }
    const chain = await this.chain(this.config.sourceChainId);
    const coordinator = chain.accounts[0];
    const treasury = chain.accounts[5] ?? coordinator;
    const contracts: Record<string, Address> = {};
    const transactionHashes: Record<string, Hash> = {};
    const add = (name: string, deployed: DeploymentResult) => {
      contracts[name] = deployed.address;
      transactionHashes[name] = deployed.transactionHash;
    };
    if (this.config.sourceSettlementToken) {
      contracts.sourceToken = this.config.sourceSettlementToken;
    } else {
      add("sourceToken", await this.deploy(chain, "MockUSDC"));
    }
    add("workflowEngine", await this.deploy(chain, "WorkflowEngine", [coordinator]));
    add("chainRouteRegistry", await this.deploy(chain, "ChainRouteRegistry", [coordinator]));
    add("resultProofGateway", await this.deploy(chain, "ResultProofGateway", [coordinator, coordinator]));
    add("feePolicy", await this.deploy(chain, "FeePolicy", [coordinator]));
    add("resultRelayGasVault", await this.deploy(chain, "ResultRelayGasVault", [coordinator, contracts.feePolicy]));
    add("gasTreasury", await this.deploy(chain, "GasTreasuryA", [coordinator]));
    add("coordinatorReview", await this.deploy(chain, "CoordinatorReview", [coordinator, contracts.resultProofGateway]));
    add("reviewManagerV2", await this.deploy(chain, "ReviewManagerV2", [coordinator]));
    add("failureClassifier", await this.deploy(chain, "FailureClassifier", [coordinator]));
    add("taskEscrow", await this.deploy(chain, "TaskEscrow", [coordinator, treasury]));
    add("dynamicSettlementSender", await this.deploy(chain, "DynamicSettlementSender", [coordinator, coordinator, contracts.taskEscrow, contracts.sourceToken]));
    transactionHashes.configureTaskEscrow = await this.write(chain, "TaskEscrow", contracts.taskEscrow, "configureProtocol", [contracts.workflowEngine, contracts.coordinatorReview, contracts.sourceToken]);
    transactionHashes.configureReviewManagerV2 = await this.write(chain, "TaskEscrow", contracts.taskEscrow, "configureReviewManagerV2", [contracts.reviewManagerV2]);
    transactionHashes.configureGasTreasury = await this.write(chain, "GasTreasuryA", contracts.gasTreasury, "configureWorkflowEngine", [contracts.workflowEngine]);
    const codeHashes = await this.codeHashes(chain, contracts);
    const hash = manifestHash({ chainId: chain.chainId, contracts, transactionHashes, codeHashes });
    const manifest: BaseManifest = { chainId: chain.chainId, contracts, transactionHashes, codeHashes, manifestHash: hash };
    writeFileSync(this.basePath(), `${JSON.stringify(manifest, null, 2)}\n`, "utf8");
    await this.registerReviewManagerRuntime(manifest);
    return manifest;
  }

  private async registerReviewManagerRuntime(base: BaseManifest): Promise<void> {
    const address = base.contracts.reviewManagerV2;
    const codeHash = base.codeHashes.reviewManagerV2;
    if (!address || !codeHash) throw new Error("Base manifest is missing ReviewManagerV2 runtime data");
    await this.api.registerRuntimeContract("REVIEW_MANAGER_V2", {
      chainId: base.chainId,
      contractAddress: address,
      codeHash,
      manifestHash: base.manifestHash,
    });
  }

  private async routeFor(job: ProtocolJob): Promise<ProtocolRoute> {
    const route = (await this.api.routes()).find((item) => item.id === job.routeId && item.routeVersion === job.routeVersion);
    if (!route) throw new Error(`Route ${job.routeId}:${job.routeVersion} was not found`);
    if (this.config.workerProfile === "testnet-ccip") {
      if (
        route.routeKey !== "SEPOLIA_TO_ARBITRUM_SEPOLIA_CCIP_BNM_V1"
        || route.sourceChainId !== 11155111
        || route.destinationChainId !== 421614
        || route.transport !== "CHAINLINK_CCIP"
        || route.settlementMode !== "ccip_direct"
        || route.settlementAsset !== "CCIP-BNM"
        || route.payoutAsset !== "CCIP-BNM"
      ) {
        throw new Error("testnet-ccip refused a route outside the pinned Sepolia CCIP-BnM profile");
      }
      if (!route.sourceTokenAddress || !route.destinationTokenAddress || !route.tokenDecimals) {
        throw new Error("testnet-ccip route is missing explicit source/destination token semantics");
      }
      if (route.sourceTokenAddress.toLowerCase() !== this.config.sourceSettlementToken?.toLowerCase()) {
        throw new Error("testnet-ccip route source token does not match DYNAMIC_SOURCE_SETTLEMENT_TOKEN");
      }
      route.rpcUrl = routeRpcUrl(this.config, route.destinationChainId, route.rpcUrl);
    }
    return route;
  }

  private async deploymentFor(job: ProtocolJob): Promise<RouteDeployment> {
    const deployment = (await this.api.routeDeployments()).find(
      (item) => item.routeId === job.routeId && item.routeVersion === job.routeVersion,
    );
    if (!deployment) throw new Error(`Route deployment ${job.routeId}:${job.routeVersion} was not found`);
    return deployment;
  }

  private async deployRouteStack(job: ProtocolJob): Promise<Hash> {
    const deploymentId = String(job.payload.deploymentId || "");
    const homeChainId = Number(job.payload.homeChainId);
    if (!deploymentId) throw new Error("DEPLOY_ROUTE_STACK is missing deploymentId");
    const route = await this.routeFor(job);
    if (route.destinationChainId !== homeChainId) throw new Error("Job homeChainId does not match its route destination");
    const persisted = await this.validManifest(this.routePath(deploymentId), homeChainId);
    if (persisted && "homeChainId" in persisted) {
      await this.api.completeRouteDeployment(deploymentId, {
        status: "READY",
        contracts: persisted.contracts,
        transactionHashes: persisted.transactionHashes,
        codeHashes: persisted.codeHashes,
        manifestHash: persisted.manifestHash,
      });
      return Object.values(persisted.transactionHashes).at(-1)!;
    }
    if (route.transport === "CHAINLINK_CCIP") {
      return this.deployCCIPRouteStack(route, deploymentId, homeChainId);
    }
    const base = await this.ensureBaseStack();
    const source = await this.chain(this.config.sourceChainId);
    const destination = await this.chain(homeChainId, route.rpcUrl);
    const relay = destination.accounts[0];
    const routeKey = keccak256(stringToHex(route.routeKey));
    const contracts: Record<string, Address> = {
      resultProofGatewayA: base.contracts.resultProofGateway,
      workflowEngineA: base.contracts.workflowEngine,
      taskEscrowA: base.contracts.taskEscrow,
      chainRouteRegistryA: base.contracts.chainRouteRegistry,
      feePolicyA: base.contracts.feePolicy,
      resultRelayGasVaultA: base.contracts.resultRelayGasVault,
      gasTreasuryA: base.contracts.gasTreasury,
      coordinatorReviewA: base.contracts.coordinatorReview,
      failureClassifierA: base.contracts.failureClassifier,
      dynamicSettlementSenderA: base.contracts.dynamicSettlementSender,
      sourceTokenA: base.contracts.sourceToken,
    };
    const transactionHashes: Record<string, Hash> = {};
    const destinationToken = await this.deploy(destination, "MockUSDC");
    contracts.destinationToken = destinationToken.address;
    transactionHashes.destinationToken = destinationToken.transactionHash;
    const payoutAsset = route.payoutAsset || `T${homeChainId}`;
    const payoutDecimals = route.payoutDecimals ?? 18;
    const payoutRate = route.payoutRate || "1";
    const payoutToken = await this.deploy(destination, "MockPayoutToken", [
      `Local Chain ${homeChainId} ${payoutAsset}`,
      payoutAsset,
      payoutDecimals,
      relay,
    ]);
    contracts.payoutToken = payoutToken.address;
    transactionHashes.payoutToken = payoutToken.transactionHash;
    const payoutSwap = await this.deploy(destination, "LocalPayoutSwap", [
      relay,
      destinationToken.address,
      payoutToken.address,
      6,
      payoutDecimals,
      parseUnits(payoutRate, 18),
    ]);
    contracts.payoutSwap = payoutSwap.address;
    transactionHashes.payoutSwap = payoutSwap.transactionHash;
    const board = await this.deploy(destination, "RemoteWorkBoard", [BigInt(homeChainId), BigInt(this.config.sourceChainId), relay]);
    contracts.remoteWorkBoard = board.address;
    transactionHashes.remoteWorkBoard = board.transactionHash;
    const identityRegistry = await this.deploy(destination, "AgentIdentityRegistry", [relay]);
    contracts.agentIdentityRegistry = identityRegistry.address;
    transactionHashes.agentIdentityRegistry = identityRegistry.transactionHash;
    const receiver = await this.deploy(destination, "DynamicSettlementReceiver", [relay, relay, destinationToken.address]);
    contracts.dynamicSettlementReceiver = receiver.address;
    transactionHashes.dynamicSettlementReceiver = receiver.transactionHash;
    transactionHashes.configurePayoutSwap = await this.write(destination, "LocalPayoutSwap", payoutSwap.address, "configureReceiver", [receiver.address]);
    transactionHashes.configurePayoutReceiver = await this.write(destination, "DynamicSettlementReceiver", receiver.address, "configurePayout", [payoutSwap.address, payoutToken.address, true]);
    transactionHashes.seedPayoutLiquidity = await this.write(destination, "MockPayoutToken", payoutToken.address, "mint", [payoutSwap.address, parseUnits("1000000", payoutDecimals)]);
    transactionHashes.configureWorkBoard = await this.write(destination, "RemoteWorkBoard", board.address, "configureRoute", [routeKey, route.routeVersion, base.contracts.resultProofGateway, true]);
    transactionHashes.configureIdentityRegistry = await this.write(destination, "RemoteWorkBoard", board.address, "configureIdentityRegistry", [identityRegistry.address]);
    transactionHashes.configureResultGateway = await this.write(source, "ResultProofGateway", base.contracts.resultProofGateway, "configureRoute", [routeKey, route.routeVersion, BigInt(homeChainId), board.address, true]);
    transactionHashes.configureSettlementReceiver = await this.write(destination, "DynamicSettlementReceiver", receiver.address, "configureSource", [BigInt(this.config.sourceChainId), base.contracts.dynamicSettlementSender, true]);
    transactionHashes.configureSettlementSender = await this.write(source, "DynamicSettlementSender", base.contracts.dynamicSettlementSender, "configureRoute", [BigInt(homeChainId), receiver.address, destinationToken.address, true]);
    const caps = Array.from({ length: 7 }, () => parseEther("0.01"));
    transactionHashes.configureFeePolicy = await this.write(source, "FeePolicy", base.contracts.feePolicy, "configurePolicy", [routeKey, route.routeVersion, caps, BigInt(Math.floor(Date.now() / 1000) + 365 * 24 * 60 * 60)]);
    transactionHashes.registerRoute = await this.write(source, "ChainRouteRegistry", base.contracts.chainRouteRegistry, "registerRoute", [routeKey, BigInt(this.config.sourceChainId), BigInt(homeChainId), base.contracts.resultProofGateway, board.address, receiver.address, destinationToken.address]);
    const destinationContracts = {
      destinationToken: destinationToken.address,
      payoutToken: payoutToken.address,
      payoutSwap: payoutSwap.address,
      remoteWorkBoard: board.address,
      agentIdentityRegistry: identityRegistry.address,
      dynamicSettlementReceiver: receiver.address,
    };
    const codeHashes = { ...base.codeHashes, ...await this.codeHashes(destination, destinationContracts) };
    const hash = manifestHash({ routeId: route.id, routeKey: route.routeKey, routeVersion: route.routeVersion, homeChainId, contracts, transactionHashes, codeHashes });
    const manifest: RouteManifest = { routeId: route.id, routeKey: route.routeKey, routeVersion: route.routeVersion, homeChainId, contracts, transactionHashes, codeHashes, manifestHash: hash };
    writeFileSync(this.routePath(deploymentId), `${JSON.stringify(manifest, null, 2)}\n`, "utf8");
    await this.api.completeRouteDeployment(deploymentId, {
      status: "READY",
      contracts,
      transactionHashes,
      codeHashes,
      manifestHash: hash,
    });
    return transactionHashes.registerRoute;
  }

  private async deployCCIPRouteStack(
    route: ProtocolRoute,
    deploymentId: string,
    homeChainId: number,
  ): Promise<Hash> {
    if (
      !route.sourceChainSelector
      || !route.destinationChainSelector
      || !route.sourceRouterAddress
      || !route.destinationRouterAddress
      || !(route.destinationTokenAddress || route.tokenAddress)
    ) {
      throw new Error("CHAINLINK_CCIP route is missing selectors, Router addresses, or destination token");
    }
    if (route.payoutAsset && route.settlementAsset && route.payoutAsset !== route.settlementAsset) {
      throw new Error("CHAINLINK_CCIP V1 supports direct settlement-token payout only");
    }

    const base = await this.ensureBaseStack();
    const source = await this.chain(this.config.sourceChainId);
    const destination = await this.chain(homeChainId, route.rpcUrl);
    const coordinator = source.accounts[0];
    const destinationController = destination.accounts[0];
    const sourceRouter = getAddress(route.sourceRouterAddress);
    const destinationRouter = getAddress(route.destinationRouterAddress);
    const destinationToken = getAddress(route.destinationTokenAddress || route.tokenAddress!);
    for (const [chain, name, address] of [
      [source, "source CCIP Router", sourceRouter],
      [destination, "destination CCIP Router", destinationRouter],
      [destination, "destination settlement token", destinationToken],
    ] as const) {
      const code = await chain.publicClient.getCode({ address });
      if (!code || code === "0x") throw new Error(`${name} has no bytecode on chain ${chain.chainId}`);
    }

    const routeKey = keccak256(stringToHex(route.routeKey));
    const contracts: Record<string, Address> = {
      resultProofGatewayA: base.contracts.resultProofGateway,
      workflowEngineA: base.contracts.workflowEngine,
      taskEscrowA: base.contracts.taskEscrow,
      chainRouteRegistryA: base.contracts.chainRouteRegistry,
      feePolicyA: base.contracts.feePolicy,
      resultRelayGasVaultA: base.contracts.resultRelayGasVault,
      gasTreasuryA: base.contracts.gasTreasury,
      coordinatorReviewA: base.contracts.coordinatorReview,
      failureClassifierA: base.contracts.failureClassifier,
      sourceTokenA: base.contracts.sourceToken,
      destinationToken,
    };
    const transactionHashes: Record<string, Hash> = {};

    const board = await this.deploy(destination, "RemoteWorkBoard", [
      BigInt(homeChainId),
      BigInt(this.config.sourceChainId),
      destinationController,
    ]);
    contracts.remoteWorkBoard = board.address;
    transactionHashes.remoteWorkBoard = board.transactionHash;
    const identityRegistry = await this.deploy(destination, "AgentIdentityRegistry", [destinationController]);
    contracts.agentIdentityRegistry = identityRegistry.address;
    transactionHashes.agentIdentityRegistry = identityRegistry.transactionHash;
    const receiver = await this.deploy(destination, "DynamicCCIPWorkflowReceiver", [destinationRouter]);
    contracts.dynamicCCIPWorkflowReceiver = receiver.address;
    transactionHashes.dynamicCCIPWorkflowReceiver = receiver.transactionHash;
    const sender = await this.deploy(source, "DynamicCCIPWorkflowSender", [
      coordinator,
      base.contracts.taskEscrow,
      sourceRouter,
      base.contracts.sourceToken,
      this.config.ccipGasLimit,
    ]);
    contracts.dynamicCCIPWorkflowSenderA = sender.address;
    transactionHashes.dynamicCCIPWorkflowSenderA = sender.transactionHash;

    transactionHashes.configureCCIPReceiver = await this.write(
      destination,
      "DynamicCCIPWorkflowReceiver",
      receiver.address,
      "configureSource",
      [BigInt(route.sourceChainSelector), sender.address, true],
    );
    transactionHashes.configureCCIPSender = await this.write(
      source,
      "DynamicCCIPWorkflowSender",
      sender.address,
      "configureRoute",
      [BigInt(homeChainId), BigInt(route.destinationChainSelector), receiver.address, destinationToken, true],
    );
    transactionHashes.configureWorkBoard = await this.write(
      destination,
      "RemoteWorkBoard",
      board.address,
      "configureRoute",
      [routeKey, route.routeVersion, base.contracts.resultProofGateway, true],
    );
    transactionHashes.configureIdentityRegistry = await this.write(
      destination,
      "RemoteWorkBoard",
      board.address,
      "configureIdentityRegistry",
      [identityRegistry.address],
    );
    transactionHashes.configureResultGateway = await this.write(
      source,
      "ResultProofGateway",
      base.contracts.resultProofGateway,
      "configureRoute",
      [routeKey, route.routeVersion, BigInt(homeChainId), board.address, true],
    );
    const caps = Array.from({ length: 7 }, () => parseEther("0.01"));
    transactionHashes.configureFeePolicy = await this.write(
      source,
      "FeePolicy",
      base.contracts.feePolicy,
      "configurePolicy",
      [routeKey, route.routeVersion, caps, BigInt(Math.floor(Date.now() / 1000) + 365 * 24 * 60 * 60)],
    );
    transactionHashes.registerRoute = await this.write(
      source,
      "ChainRouteRegistry",
      base.contracts.chainRouteRegistry,
      "registerRoute",
      [
        routeKey,
        BigInt(this.config.sourceChainId),
        BigInt(homeChainId),
        base.contracts.resultProofGateway,
        board.address,
        receiver.address,
        destinationToken,
      ],
    );

    const sourceContracts = { dynamicCCIPWorkflowSenderA: sender.address };
    const destinationContracts = {
      destinationToken,
      remoteWorkBoard: board.address,
      agentIdentityRegistry: identityRegistry.address,
      dynamicCCIPWorkflowReceiver: receiver.address,
    };
    const codeHashes = {
      ...base.codeHashes,
      ...await this.codeHashes(source, sourceContracts),
      ...await this.codeHashes(destination, destinationContracts),
    };
    const hash = manifestHash({
      routeId: route.id,
      routeKey: route.routeKey,
      routeVersion: route.routeVersion,
      homeChainId,
      contracts,
      transactionHashes,
      codeHashes,
    });
    const manifest: RouteManifest = {
      routeId: route.id,
      routeKey: route.routeKey,
      routeVersion: route.routeVersion,
      homeChainId,
      contracts,
      transactionHashes,
      codeHashes,
      manifestHash: hash,
    };
    writeFileSync(this.routePath(deploymentId), `${JSON.stringify(manifest, null, 2)}\n`, "utf8");
    await this.api.completeRouteDeployment(deploymentId, {
      status: "READY",
      contracts,
      transactionHashes,
      codeHashes,
      manifestHash: hash,
    });
    return transactionHashes.registerRoute;
  }

  private async registerWorkflow(job: ProtocolJob): Promise<Hash | undefined> {
    if (!job.workflowId) throw new Error("REGISTER_WORKFLOW is missing workflowId");
    const base = await this.ensureBaseStack();
    const chain = await this.chain(this.config.sourceChainId);
    const workflow = await this.api.workflow(job.workflowId);
    const workflowId = opaqueIdToBytes32(String(job.payload.onchainWorkflowId || workflow.onchainWorkflowId));
    const current = await chain.publicClient.readContract({ address: base.contracts.workflowEngine, abi: this.artifact("WorkflowEngine").abi, functionName: "workflows", args: [workflowId] }) as readonly unknown[];
    let latest: Hash | undefined;
    const sourceTransactionHashes: Hash[] = [];
    if (String(current[0]).toLowerCase() === ZERO_ADDRESS) {
      latest = await this.write(chain, "WorkflowEngine", base.contracts.workflowEngine, "createWorkflow", [workflowId, job.payload.payerAddress as Address, job.payload.termsHash as Hash]);
      sourceTransactionHashes.push(latest);
    }
    const sortedNodes = topologicallySortWorkflowNodes(workflow.nodes as Record<string, any>[]);
    const nodeByKey = new Map<string, Record<string, any>>(sortedNodes.map((node: Record<string, any>) => [node.nodeKey, node]));
    for (const node of sortedNodes) {
      const nodeId = opaqueIdToBytes32(node.id);
      const existing = await chain.publicClient.readContract({ address: base.contracts.workflowEngine, abi: this.artifact("WorkflowEngine").abi, functionName: "nodes", args: [nodeId] }) as readonly unknown[];
      if (String(existing[0]) !== ZERO_BYTES32) continue;
      const dependencies = (node.dependsOn as string[]).map((key) => opaqueIdToBytes32(nodeByKey.get(key)!.id));
      latest = await this.write(chain, "WorkflowEngine", base.contracts.workflowEngine, "registerNode", [workflowId, nodeId, node.criteriaHash as Hash, dependencies]);
      sourceTransactionHashes.push(latest);
    }
    if (sourceTransactionHashes.length > 0) {
      await this.chargeSourceGas(base, chain, workflowId, "REGISTER_WORKFLOW", sourceTransactionHashes);
    }
    return latest;
  }

  private async runnerRequest(
    url: string,
    token: string,
    payload: Record<string, unknown>,
    workerSignature?: string,
  ): Promise<Record<string, any>> {
    let response: Response;
    try {
      response = await fetch(url, {
        method: "POST",
        headers: {
          Authorization: `Bearer ${token}`,
          "Content-Type": "application/json",
          ...(workerSignature ? { "X-Worker-Intent-Signature": workerSignature } : {}),
        },
        body: JSON.stringify(payload),
        signal: AbortSignal.timeout(this.config.agentInvocationTimeoutMs),
      });
    } catch (error) {
      throw new AgentInvocationError(
        `Agent Runner request failed: ${error instanceof Error ? error.message : String(error)}`,
        "AGENT_TIMEOUT",
      );
    }
    const body = await response.json().catch(() => null) as Record<string, any> | null;
    if (!response.ok || !body) {
      const detail = typeof body?.detail === "string" ? body.detail : JSON.stringify(body?.detail || body || {});
      if (response.status === 401 || response.status === 403 || response.status === 503) {
        throw new Error(`Agent Runner authentication/configuration failed (${response.status}): ${detail}`);
      }
      throw new AgentInvocationError(
        `Agent Runner returned HTTP ${response.status}: ${detail}`,
        response.status >= 500 || response.status === 408 ? "AGENT_TIMEOUT" : "AGENT_INVALID_SIGNATURE",
      );
    }
    return body;
  }

  private workerPayloadSignature(payload: Record<string, unknown>): string {
    if (!this.config.workerIntentHmacSecret) throw new Error("WORKER_INTENT_HMAC_SECRET is required for managed Agent signing");
    return `sha256=${createHmac("sha256", this.config.workerIntentHmacSecret).update(stable(payload), "utf8").digest("hex")}`;
  }

  private workflowInput(workflow: Record<string, any>, node: Record<string, any>): Record<string, unknown> {
    const dependencies: Record<string, unknown> = {};
    for (const dependencyKey of Array.isArray(node.dependsOn) ? node.dependsOn : []) {
      const dependency = (workflow.nodes as Record<string, any>[]).find((item) => item.nodeKey === dependencyKey);
      if (dependency) dependencies[String(dependencyKey)] = dependency.outputPayload || {};
    }
    return {
      objective: workflow.objective,
      input: workflow.inputPayload || {},
      dependencies,
    };
  }

  private async invokeAssignedAgent(
    job: ProtocolJob,
    workflow: Record<string, any>,
    node: Record<string, any>,
    attempt: Record<string, any>,
    board: Address,
    destination: ChainRuntime,
  ): Promise<Hash | undefined> {
    if (!this.config.autoInvokeAgents) return undefined;
    if (!this.config.agentRunnerApiToken) throw new Error("AUTO_INVOKE_AGENTS requires AGENT_RUNNER_API_TOKEN");
    const agentSlug = String(job.payload.agentSlug || "");
    const agentDid = String(job.payload.agentDid || "");
    const agentRuntimeId = String(job.payload.agentRuntimeId || "");
    const agentPublicKey = String(job.payload.agentOperationalPublicKey || "");
    const endpoint = String(job.payload.agentRuntimeEndpoint || "").replace(/\/+$/, "").replace(/\/invoke$/, "");
    if (!agentSlug || !agentDid || !agentPublicKey || !endpoint) {
      throw new Error("Selected Agent runtime identity or endpoint is incomplete");
    }
    const reservation = (node.reservations as Record<string, any>[]).find((item) => item.attemptId === attempt.id);
    if (!reservation) throw new Error("Selected attempt has no budget reservation");
    const route = await this.routeFor(job);

    const attemptId = opaqueIdToBytes32(attempt.id);
    const persisted = this.loadAgentResult(attempt.id);
    let envelope = persisted?.runtimeEnvelope as Record<string, any> | undefined;
    let offerId = String(persisted?.offerId || "");
    let resultMessageId = persisted?.resultMessageId as Hash | undefined;
    let resultHash = persisted?.resultHash as Hash | undefined;
    let proofHash = persisted?.proofHash as Hash | undefined;
    let executionTxHash = persisted?.executionTxHash as Hash | undefined;

    if (!envelope) {
      offerId = randomUUID();
      const deadline = new Date(Date.now() + 15 * 60_000).toISOString();
      const nonce = createHash("sha256").update(randomUUID()).digest("base64url");
      const unsignedOffer: Record<string, unknown> = {
        offerId,
        workflowId: workflow.id,
        nodeId: node.id,
        attemptId: attempt.id,
        attemptNumber: Number(attempt.attemptNumber),
        reservationId: reservation.id,
        capabilityId: node.capability,
        criteriaHash: job.payload.criteriaHash,
        reward: String(reservation.amount),
        settlementAsset: attempt.settlementAsset,
        payoutAsset: attempt.payoutAsset,
        deadline,
        nonce,
      };
      const offer = {
        ...unsignedOffer,
        coordinatorSignature: signTaskOffer(this.config.agentRunnerApiToken, unsignedOffer),
      };
      const acceptance = await this.runnerRequest(`${endpoint}/task-offers`, this.config.agentRunnerApiToken, offer);
      if (acceptance.accepted !== true || acceptance.attemptId !== attempt.id || acceptance.offerId !== offerId) {
        throw new AgentInvocationError("Agent did not accept the current signed offer", "AGENT_INVALID_SIGNATURE");
      }
      const invocation = {
        requestId: offerId,
        offerId,
        workflowId: workflow.id,
        nodeId: node.id,
        attemptId: attempt.id,
        attemptNumber: Number(attempt.attemptNumber),
        reservationId: reservation.id,
        deadline,
        nonce,
        input: this.workflowInput(workflow, node),
      };
      envelope = await this.runnerRequest(`${endpoint}/invoke`, this.config.agentRunnerApiToken, invocation);
      try {
        verifyRuntimeEnvelope(
          envelope,
          {
            workflowId: workflow.id,
            nodeId: node.id,
            attemptId: attempt.id,
            attemptNumber: Number(attempt.attemptNumber),
            reservationId: reservation.id,
            offerId,
            deadline,
            nonce,
          },
          agentDid,
          agentSlug,
          agentPublicKey,
        );
      } catch (error) {
        throw new AgentInvocationError(
          error instanceof Error ? error.message : String(error),
          "AGENT_INVALID_SIGNATURE",
        );
      }
      if (envelope.capabilityId !== node.capability || !envelope.output || typeof envelope.output !== "object") {
        throw new AgentInvocationError("Agent result capability or output is invalid", "AGENT_INVALID_SIGNATURE");
      }
      resultMessageId = keccak256(stringToHex(`${workflow.id}:${attempt.id}:result`));
      resultHash = canonicalSha256(envelope.output);
      proofHash = canonicalSha256({ offerId, runtimeSignature: envelope.signature });
      this.saveAgentResult(attempt.id, { offerId, resultMessageId, resultHash, proofHash, runtimeEnvelope: envelope });
    }
    if (!resultMessageId || !resultHash || !proofHash || !envelope) {
      throw new Error("Persisted Agent result is incomplete");
    }

    const assignment = await destination.publicClient.readContract({
      address: board,
      abi: this.artifact("RemoteWorkBoard").abi,
      functionName: "assignments",
      args: [attemptId],
    }) as readonly unknown[];
    const assignmentStatus = Number(assignment[8]);
    if (assignmentStatus === 4) throw new Error("Current destination-chain attempt was cancelled");
    if (assignmentStatus !== 3) {
      executionTxHash = await this.writeAsAgent(
        destination,
        agentSlug,
        agentDid,
        agentRuntimeId,
        attempt.homeWallet as Address,
        "RemoteWorkBoard",
        board,
        "submitResult",
        [resultMessageId, attemptId, resultHash, proofHash],
        endpoint,
        attempt.id,
        attemptId,
        workflow.id,
        resultMessageId,
        resultHash,
        proofHash,
        route,
      );
      this.saveAgentResult(attempt.id, {
        offerId,
        resultMessageId,
        resultHash,
        proofHash,
        executionTxHash,
        runtimeEnvelope: envelope,
      });
    }
    if (!executionTxHash) {
      const events = await destination.publicClient.getContractEvents({
        address: board,
        abi: this.artifact("RemoteWorkBoard").abi,
        eventName: "ResultSubmitted",
        args: { attemptId },
        fromBlock: 0n,
        toBlock: "latest",
      } as never) as Array<{ transactionHash: Hash }>;
      executionTxHash = events.at(-1)?.transactionHash;
    }
    if (!executionTxHash) throw new Error("Destination-chain result transaction could not be recovered");
    const gasCost = await this.transactionGasCost(destination, executionTxHash);
    const submission = await this.api.submitAttemptResult(workflow.id, node.id, attempt.id, {
      output: envelope.output,
      proofHash,
      runtimeEnvelope: envelope,
      resultMessageId,
      gasCost: formatNativeAmount(gasCost),
      gasSymbol: "ETH",
      transactionHash: executionTxHash,
    });
    if (submission.accepted === false && submission.code !== "STALE_ATTEMPT") {
      throw new Error(`Platform rejected Agent result: ${String(submission.code || "UNKNOWN")}`);
    }
    return executionTxHash;
  }

  private async failAgentInvocation(
    job: ProtocolJob,
    workflow: Record<string, any>,
    board: Address,
    destination: ChainRuntime,
    error: AgentInvocationError,
  ): Promise<void> {
    if (!job.workflowId || !job.nodeId || !job.attemptId) return;
    const base = await this.ensureBaseStack();
    const source = await this.chain(this.config.sourceChainId);
    const nodeId = opaqueIdToBytes32(job.nodeId);
    const attemptId = opaqueIdToBytes32(job.attemptId);
    const sourceTransactions: Hash[] = [];
    const assignment = await destination.publicClient.readContract({
      address: board,
      abi: this.artifact("RemoteWorkBoard").abi,
      functionName: "assignments",
      args: [attemptId],
    }) as readonly unknown[];
    if ([1, 2].includes(Number(assignment[8]))) {
      await this.write(destination, "RemoteWorkBoard", board, "cancelAttempt", [attemptId]);
    }
    const nodeState = await source.publicClient.readContract({
      address: base.contracts.workflowEngine,
      abi: this.artifact("WorkflowEngine").abi,
      functionName: "nodes",
      args: [nodeId],
    }) as readonly unknown[];
    if (String(nodeState[2]) === attemptId && Number(nodeState[7]) === 3) {
      sourceTransactions.push(await this.write(source, "WorkflowEngine", base.contracts.workflowEngine, "markAttemptFailed", [nodeId, attemptId]));
    }
    const reservation = await source.publicClient.readContract({
      address: base.contracts.taskEscrow,
      abi: this.artifact("TaskEscrow").abi,
      functionName: "reservations",
      args: [attemptId],
    }) as readonly unknown[];
    if (Number(reservation[3]) === 1) {
      sourceTransactions.push(await this.write(source, "TaskEscrow", base.contracts.taskEscrow, "releaseReservation", [attemptId]));
    }
    if (sourceTransactions.length > 0) {
      await this.chargeSourceGas(base, source, opaqueIdToBytes32(workflow.onchainWorkflowId), `AGENT_FAILURE:${attemptId}`, sourceTransactions);
    }
    await this.api.reportAttemptFailure(job.workflowId, job.nodeId, job.attemptId, {
      reasonCode: error.reasonCode,
      evidenceHash: canonicalSha256({ message: error.message, phase: "AGENT_RUNTIME" }),
      detail: error.message.slice(0, 5000),
    });
  }

  private async dispatchAssignment(job: ProtocolJob): Promise<Hash> {
    if (!job.workflowId || !job.nodeId || !job.attemptId) throw new Error("DISPATCH_ASSIGNMENT identifiers are incomplete");
    const base = await this.ensureBaseStack();
    const route = await this.routeFor(job);
    const deployment = await this.deploymentFor(job);
    const source = await this.chain(this.config.sourceChainId);
    const destination = await this.chain(route.destinationChainId, route.rpcUrl);
    const workflow = await this.api.workflow(job.workflowId);
    const node = (workflow.nodes as Record<string, any>[]).find((item) => item.id === job.nodeId);
    if (!node) throw new Error("Dispatch node was not found in workflow detail");
    const attempt = (node.attempts as Record<string, any>[]).find((item) => item.id === job.attemptId);
    if (!attempt?.homeWallet) throw new Error("Selected attempt has no immutable homeWallet");
    const workflowId = opaqueIdToBytes32(workflow.onchainWorkflowId);
    const nodeId = opaqueIdToBytes32(job.nodeId);
    const attemptId = opaqueIdToBytes32(job.attemptId);
    const attemptNumber = Number(attempt.attemptNumber);
    const routeKey = keccak256(stringToHex(route.routeKey));
    const agentDid = String(job.payload.agentDid || "");
    if (!agentDid || !attemptNumber) throw new Error("Dispatch is missing Agent DID or attemptNumber");
    const agentDidHash = keccak256(stringToHex(agentDid));
    const rolesHash = keccak256(stringToHex(stable(job.payload.agentRoles || ["provider"])));
    const capabilitiesHash = keccak256(stringToHex(stable(job.payload.agentCapabilities || [])));
    const reward = parseUnits(String(job.payload.reward), 6);
    const sourceTransactionHashes: Hash[] = [];
    const nodeState = await source.publicClient.readContract({ address: base.contracts.workflowEngine, abi: this.artifact("WorkflowEngine").abi, functionName: "nodes", args: [nodeId] }) as readonly unknown[];
    if (String(nodeState[2]) !== attemptId) {
      sourceTransactionHashes.push(await this.write(source, "WorkflowEngine", base.contracts.workflowEngine, "assignNode", [nodeId, attemptId, routeKey, route.routeVersion, attempt.homeWallet as Address]));
    }
    const reservation = await source.publicClient.readContract({ address: base.contracts.taskEscrow, abi: this.artifact("TaskEscrow").abi, functionName: "reservations", args: [attemptId] }) as readonly unknown[];
    if (String(reservation[0]) === ZERO_BYTES32) {
      sourceTransactionHashes.push(await this.write(source, "TaskEscrow", base.contracts.taskEscrow, "reserve", [workflowId, nodeId, attemptId, reward]));
    }
    const board = deployment.contracts.remoteWorkBoard as Address;
    const identityRegistry = deployment.contracts.agentIdentityRegistry as Address;
    const identityActive = await destination.publicClient.readContract({
      address: identityRegistry,
      abi: this.artifact("AgentIdentityRegistry").abi,
      functionName: "isActiveAgent",
      args: [agentDidHash, attempt.homeWallet as Address, BigInt(route.destinationChainId)],
    }) as boolean;
    if (!identityActive) {
      await this.write(destination, "AgentIdentityRegistry", identityRegistry, "registerOrUpdate", [
        agentDidHash,
        attempt.homeWallet as Address,
        BigInt(route.destinationChainId),
        rolesHash,
        capabilitiesHash,
        true,
      ]);
    }
    const assignment = await destination.publicClient.readContract({ address: board, abi: this.artifact("RemoteWorkBoard").abi, functionName: "assignments", args: [attemptId] }) as readonly unknown[];
    if (sourceTransactionHashes.length > 0) {
      await this.chargeSourceGas(base, source, workflowId, `DISPATCH:${attemptId}`, sourceTransactionHashes);
    }
    let assignmentTxHash = job.payload.messageId as Hash;
    if (String(assignment[0]) === ZERO_BYTES32) {
      assignmentTxHash = await this.write(destination, "RemoteWorkBoard", board, "receiveAssignment", [[
        job.payload.messageId as Hash,
        BigInt(this.config.sourceChainId),
        base.contracts.resultProofGateway,
        workflowId,
        nodeId,
        attemptId,
        attemptNumber,
        agentDidHash,
        routeKey,
        route.routeVersion,
        job.payload.criteriaHash as Hash,
        attempt.homeWallet as Address,
        reward,
      ]]);
    }
    try {
      await this.invokeAssignedAgent(job, workflow, node, attempt, board, destination);
    } catch (error) {
      if (!(error instanceof AgentInvocationError)) throw error;
      await this.failAgentInvocation(job, workflow, board, destination, error);
    }
    return assignmentTxHash;
  }

  private async relayResultProof(job: ProtocolJob): Promise<Hash> {
    if (!job.workflowId || !job.nodeId || !job.attemptId) throw new Error("RELAY_RESULT_PROOF identifiers are incomplete");
    const base = await this.ensureBaseStack();
    const route = await this.routeFor(job);
    const deployment = await this.deploymentFor(job);
    const destination = await this.chain(route.destinationChainId, route.rpcUrl);
    const executionTxHash = job.payload.executionTxHash as Hash;
    if (!executionTxHash) throw new Error("RESULT_PROOF requires the execution-chain transaction hash");
    const receipt = await destination.publicClient.getTransactionReceipt({ hash: executionTxHash });
    if (receipt.status !== "success") throw new Error("Execution-chain result transaction reverted");
    const attemptId = opaqueIdToBytes32(job.attemptId);
    const board = deployment.contracts.remoteWorkBoard as Address;
    const matched = receipt.logs.some((log) => {
      if (log.address.toLowerCase() !== board.toLowerCase()) return false;
      try {
        const decoded = decodeEventLog({ abi: this.artifact("RemoteWorkBoard").abi, data: log.data, topics: log.topics });
        const args = decoded.args as unknown as Record<string, unknown>;
        return decoded.eventName === "ResultSubmitted"
          && args.attemptId === attemptId
          && args.resultMessageId === job.payload.messageId
          && args.resultHash === job.payload.resultHash
          && args.proofHash === job.payload.proofHash;
      } catch { return false; }
    });
    if (!matched) throw new Error("Execution transaction does not contain the expected RemoteWorkBoard.ResultSubmitted event");
    const source = await this.chain(this.config.sourceChainId);
    const workflow = await this.api.workflow(job.workflowId);
    const routeKey = keccak256(stringToHex(route.routeKey));
    let transactionHash = job.transactionHash as Hash | undefined;
    if (!transactionHash) {
      const processed = await source.publicClient.readContract({
        address: base.contracts.resultProofGateway,
        abi: this.artifact("ResultProofGateway").abi,
        functionName: "processedMessages",
        args: [job.payload.messageId as Hash],
      }) as boolean;
      if (processed) {
        const events = await source.publicClient.getContractEvents({
          address: base.contracts.resultProofGateway,
          abi: this.artifact("ResultProofGateway").abi,
          eventName: "ResultProofDelivered",
          args: { messageId: job.payload.messageId as Hash },
          fromBlock: 0n,
          toBlock: "latest",
        } as never) as Array<{ transactionHash: Hash }>;
        transactionHash = events.at(-1)?.transactionHash;
        if (!transactionHash) throw new Error("Processed RESULT_PROOF is missing its delivery event");
      } else {
        transactionHash = await this.write(source, "ResultProofGateway", base.contracts.resultProofGateway, "receiveResultProof", [job.payload.messageId as Hash, BigInt(route.destinationChainId), board, routeKey, route.routeVersion, opaqueIdToBytes32(workflow.onchainWorkflowId), opaqueIdToBytes32(job.nodeId), attemptId, job.payload.resultHash as Hash, job.payload.proofHash as Hash]);
      }
      await this.api.updateJob(job.id, { status: "RUNNING", transactionHash });
    }
    const proofValid = await source.publicClient.readContract({
      address: base.contracts.resultProofGateway,
      abi: this.artifact("ResultProofGateway").abi,
      functionName: "hasProof",
      args: [attemptId, job.payload.resultHash as Hash, job.payload.proofHash as Hash],
    }) as boolean;
    if (!proofValid) throw new Error("Chain A RESULT_PROOF does not match the submitted result and proof hashes");
    const gasCost = await this.chargeSourceGas(
      base,
      source,
      opaqueIdToBytes32(workflow.onchainWorkflowId),
      `RESULT_PROOF:${attemptId}`,
      [transactionHash],
    );
    await this.api.confirmResultProof(job.workflowId, job.nodeId, job.attemptId, {
      messageId: job.payload.messageId,
      transactionHash,
      resultHash: job.payload.resultHash,
      proofHash: job.payload.proofHash,
      gasCost: formatGasCost(gasCost),
      gasSymbol: "ETH",
    });
    return transactionHash;
  }

  private async recordReview(job: ProtocolJob): Promise<Hash | undefined> {
    if (!job.nodeId || !job.attemptId) throw new Error("RECORD_REVIEW identifiers are incomplete");
    const base = await this.ensureBaseStack();
    const chain = await this.chain(this.config.sourceChainId);
    const attemptId = opaqueIdToBytes32(job.attemptId);
    const nodeId = opaqueIdToBytes32(job.nodeId);
    const review = await chain.publicClient.readContract({ address: base.contracts.coordinatorReview, abi: this.artifact("CoordinatorReview").abi, functionName: "reviews", args: [attemptId] }) as readonly unknown[];
    let latest: Hash | undefined;
    const sourceTransactionHashes: Hash[] = [];
    const passed = Boolean(job.payload.passed);
    if (Number(review[5]) === 0) {
      latest = await this.write(chain, "CoordinatorReview", base.contracts.coordinatorReview, "recordReview", [attemptId, job.payload.criteriaHash as Hash, job.payload.resultHash as Hash, job.payload.proofHash as Hash, passed ? 1 : 2, keccak256(stringToHex(passed ? "PASS" : "AGENT_RESULT_REJECTED")), job.payload.reviewHash as Hash]);
      sourceTransactionHashes.push(latest);
    }
    const node = await chain.publicClient.readContract({ address: base.contracts.workflowEngine, abi: this.artifact("WorkflowEngine").abi, functionName: "nodes", args: [nodeId] }) as readonly unknown[];
    if (Number(node[7]) === 3) {
      latest = await this.write(chain, "WorkflowEngine", base.contracts.workflowEngine, passed ? "markSucceeded" : "markAttemptFailed", [nodeId, attemptId]);
      sourceTransactionHashes.push(latest);
      if (!passed) {
        sourceTransactionHashes.push(await this.write(chain, "TaskEscrow", base.contracts.taskEscrow, "releaseReservation", [attemptId]));
      }
    }
    if (sourceTransactionHashes.length > 0 && job.workflowId) {
      const workflow = await this.api.workflow(job.workflowId);
      await this.chargeSourceGas(base, chain, opaqueIdToBytes32(workflow.onchainWorkflowId), `REVIEW:${attemptId}`, sourceTransactionHashes);
    }
    return latest;
  }

  private async syncReviewV2(job: ProtocolJob): Promise<Hash | undefined> {
    if (!job.workflowId || !job.nodeId || !job.attemptId) {
      throw new Error("SYNC_REVIEW_V2 identifiers are incomplete");
    }
    const base = await this.ensureBaseStack();
    const chain = await this.chain(this.config.sourceChainId);
    const workflow = await this.api.workflow(job.workflowId);
    const node = (workflow.nodes as Record<string, any>[]).find((item) => item.id === job.nodeId);
    const attempt = (node?.attempts as Record<string, any>[] | undefined)?.find((item) => item.id === job.attemptId);
    if (!node || !attempt) throw new Error("SYNC_REVIEW_V2 workflow snapshot is incomplete");

    const manager = base.contracts.reviewManagerV2;
    const attemptId = opaqueIdToBytes32(job.attemptId);
    const workflowId = opaqueIdToBytes32(job.workflowId);
    const nodeId = opaqueIdToBytes32(job.nodeId);
    const resultHash = String(attempt.resultHash || job.payload.resultHash || ZERO_BYTES32) as Hash;
    const reviewPolicyHash = String(node.reviewPolicyHash || job.payload.reviewPolicyHash || ZERO_BYTES32) as Hash;
    const expectedStatus = String(job.payload.expectedStatus || "APPROVALS_PENDING");
    const sourceTransactionHashes: Hash[] = [];
    let latest: Hash | undefined;
    const reviewState = async () => chain.publicClient.readContract({
      address: manager,
      abi: this.artifact("ReviewManagerV2").abi,
      functionName: "reviews",
      args: [attemptId],
    }) as Promise<readonly unknown[]>;
    let state = await reviewState();

    if (Number(state[11]) === 0) {
      latest = await this.write(chain, "ReviewManagerV2", manager, "createReview", [
        attemptId,
        workflowId,
        nodeId,
        resultHash,
        reviewPolicyHash,
        chain.accounts[0],
        Number(job.payload.requiredApprovals || Math.max(1, Number(node.requiredApprovals || 0))),
        BigInt(Number(job.payload.challengePeriodSeconds || 0)),
        Number(job.payload.arbitrationThreshold || 2),
      ]);
      sourceTransactionHashes.push(latest);
      state = await reviewState();
    } else if (
      String(state[0]).toLowerCase() !== workflowId.toLowerCase()
      || String(state[1]).toLowerCase() !== nodeId.toLowerCase()
      || String(state[2]).toLowerCase() !== resultHash.toLowerCase()
      || String(state[3]).toLowerCase() !== reviewPolicyHash.toLowerCase()
    ) {
      throw new Error("ReviewManagerV2 immutable review snapshot does not match the API workflow");
    }

    const approvals = Array.isArray(attempt.reviewApprovals) ? attempt.reviewApprovals as Record<string, any>[] : [];
    for (const approval of approvals) {
      if (Number(state[11]) !== 1) break;
      const reviewerWallet = getAddress(String(approval.reviewerWallet || ZERO_ADDRESS));
      if (!approval.signature || !approval.nonce || !approval.deadline) {
        throw new Error("RISK_REVIEW_V2 approval is missing its EIP-712 signature, nonce, or deadline");
      }
      const submitted = await chain.publicClient.readContract({
        address: manager,
        abi: this.artifact("ReviewManagerV2").abi,
        functionName: "approvalSubmitted",
        args: [attemptId, reviewerWallet],
      }) as boolean;
      if (submitted) continue;
      const active = await chain.publicClient.readContract({
        address: manager,
        abi: this.artifact("ReviewManagerV2").abi,
        functionName: "reviewers",
        args: [reviewerWallet],
      }) as boolean;
      if (!active) {
        latest = await this.write(chain, "ReviewManagerV2", manager, "configureReviewer", [reviewerWallet, true]);
        sourceTransactionHashes.push(latest);
      }
      const deadline = BigInt(Math.floor(new Date(String(approval.deadline)).getTime() / 1000));
      latest = await this.write(chain, "ReviewManagerV2", manager, "submitApproval", [
        attemptId,
        String(approval.decision) === "PASS" ? 1 : 2,
        approval.evidenceHash as Hash,
        approval.notesHash as Hash,
        approval.nonce as Hash,
        deadline,
        approval.signature as Hex,
      ]);
      sourceTransactionHashes.push(latest);
      state = await reviewState();
    }

    if (approvals.length === 0 && Number(state[11]) === 1 && ["APPROVED", "REJECTED"].includes(expectedStatus)) {
      const coordinator = chain.accounts[0];
      const active = await chain.publicClient.readContract({
        address: manager,
        abi: this.artifact("ReviewManagerV2").abi,
        functionName: "reviewers",
        args: [coordinator],
      }) as boolean;
      if (!active) {
        latest = await this.write(chain, "ReviewManagerV2", manager, "configureReviewer", [coordinator, true]);
        sourceTransactionHashes.push(latest);
      }
      const decision = expectedStatus === "APPROVED" ? 1 : 2;
      const evidenceHash = String(attempt.proofHash || resultHash) as Hash;
      const notesHash = String(job.payload.coordinatorReviewHash || keccak256(stringToHex(expectedStatus))) as Hash;
      const nonce = keccak256(stringToHex(`COORDINATOR_AUTO_REVIEW:${job.attemptId}:${expectedStatus}`));
      const block = await chain.publicClient.getBlock();
      const deadline = block.timestamp + 3600n;
      const signature = await chain.walletClient.signTypedData({
        account: chain.signingAccount || coordinator,
        domain: {
          name: "AgentCrosschainReview",
          version: "2",
          chainId: chain.chainId,
          verifyingContract: manager,
        },
        primaryType: "ReviewApproval",
        types: {
          ReviewApproval: [
            { name: "workflowId", type: "bytes32" },
            { name: "nodeId", type: "bytes32" },
            { name: "attemptId", type: "bytes32" },
            { name: "resultHash", type: "bytes32" },
            { name: "reviewPolicyHash", type: "bytes32" },
            { name: "decision", type: "uint8" },
            { name: "evidenceHash", type: "bytes32" },
            { name: "notesHash", type: "bytes32" },
            { name: "nonce", type: "bytes32" },
            { name: "deadline", type: "uint256" },
          ],
        },
        message: { workflowId, nodeId, attemptId, resultHash, reviewPolicyHash, decision, evidenceHash, notesHash, nonce, deadline },
      } as never);
      latest = await this.write(chain, "ReviewManagerV2", manager, "submitApproval", [
        attemptId, decision, evidenceHash, notesHash, nonce, deadline, signature,
      ]);
      sourceTransactionHashes.push(latest);
      state = await reviewState();
    }

    const challenges = Array.isArray(attempt.reviewChallenges) ? attempt.reviewChallenges as Record<string, any>[] : [];
    const challenge = challenges[0];
    if (challenge && Number(state[11]) === 2) {
      latest = await this.write(chain, "ReviewManagerV2", manager, "challenge", [
        attemptId,
        opaqueIdToBytes32(String(challenge.id)),
        challenge.evidenceHash as Hash,
      ]);
      sourceTransactionHashes.push(latest);
      state = await reviewState();
    }

    const votes = Array.isArray(attempt.arbitrationVotes) ? attempt.arbitrationVotes as Record<string, any>[] : [];
    for (const vote of votes) {
      if (Number(state[11]) !== 3) break;
      const voterWallet = getAddress(String(vote.voterWallet || ZERO_ADDRESS));
      const submitted = await chain.publicClient.readContract({
        address: manager,
        abi: this.artifact("ReviewManagerV2").abi,
        functionName: "arbitrationSubmitted",
        args: [attemptId, voterWallet],
      }) as boolean;
      if (submitted) continue;
      const active = await chain.publicClient.readContract({
        address: manager,
        abi: this.artifact("ReviewManagerV2").abi,
        functionName: "arbitrators",
        args: [voterWallet],
      }) as boolean;
      if (!active) {
        latest = await this.write(chain, "ReviewManagerV2", manager, "configureArbitrator", [voterWallet, true]);
        sourceTransactionHashes.push(latest);
      }
      const deadline = BigInt(Math.floor(new Date(String(vote.deadline)).getTime() / 1000));
      latest = await this.write(chain, "ReviewManagerV2", manager, "submitArbitrationVote", [
        attemptId,
        opaqueIdToBytes32(String(vote.challengeId)),
        String(vote.resolution) === "UPHOLD_PASS" ? 1 : 2,
        vote.reasonHash as Hash,
        vote.nonce as Hash,
        deadline,
        vote.signature as Hex,
      ]);
      sourceTransactionHashes.push(latest);
      state = await reviewState();
    }

    const reportReviewState = async (current: readonly unknown[]) => {
      const names = ["NONE", "APPROVALS_PENDING", "CHALLENGE_OPEN", "DISPUTED", "APPROVED", "REJECTED"];
      const name = names[Number(current[11])];
      if (!name || name === "NONE") return;
      const deadline = BigInt(current[10] as bigint);
      await this.api.confirmReviewChainState(job.workflowId!, job.nodeId!, job.attemptId!, {
        chainId: chain.chainId,
        contractAddress: manager,
        status: name,
        challengeDeadline: deadline > 0n ? new Date(Number(deadline) * 1000).toISOString() : undefined,
        transactionHash: latest,
      });
    };
    await reportReviewState(state);

    if (expectedStatus === "APPROVED" && Number(state[11]) === 2 && !challenge) {
      const block = await chain.publicClient.getBlock();
      const challengeDeadline = BigInt(state[10] as bigint);
      if (block.timestamp < challengeDeadline) {
        throw new DeferredProtocolJobError(
          "ReviewManagerV2 challenge period is still open",
          Number(challengeDeadline - block.timestamp + 1n),
        );
      }
      latest = await this.write(chain, "ReviewManagerV2", manager, "finalizeUnchallenged", [attemptId]);
      sourceTransactionHashes.push(latest);
      state = await reviewState();
      await reportReviewState(state);
    }

    const status = Number(state[11]);
    const statusNames = ["NONE", "APPROVALS_PENDING", "CHALLENGE_OPEN", "DISPUTED", "APPROVED", "REJECTED"];
    const actualStatus = statusNames[status] || `UNKNOWN_${status}`;
    const acceptable = expectedStatus === "APPROVALS_PENDING"
      ? status >= 1
      : expectedStatus === "CHALLENGE_OPEN"
        ? status >= 2
        : expectedStatus === "DISPUTED"
          ? status >= 3
          : actualStatus === expectedStatus;
    if (!acceptable) {
      if (["APPROVED", "REJECTED"].includes(actualStatus)) {
        throw new Error(`ReviewManagerV2 reached ${actualStatus}, expected ${expectedStatus}`);
      }
      throw new DeferredProtocolJobError(`ReviewManagerV2 is ${actualStatus}, expected ${expectedStatus}`, 5);
    }
    if (sourceTransactionHashes.length > 0) {
      await this.chargeSourceGas(
        base,
        chain,
        opaqueIdToBytes32(workflow.onchainWorkflowId),
        `REVIEW_V2:${attemptId}:${job.id}`,
        sourceTransactionHashes,
      );
    }
    return latest;
  }

  private async crossChainSettlement(job: ProtocolJob): Promise<Hash> {
    if (!job.workflowId || !job.nodeId || !job.attemptId) throw new Error("CROSS_CHAIN_SETTLEMENT identifiers are incomplete");
    const base = await this.ensureBaseStack();
    const reviewAllowed = await (await this.chain(this.config.sourceChainId)).publicClient.readContract({
      address: base.contracts.reviewManagerV2,
      abi: this.artifact("ReviewManagerV2").abi,
      functionName: "isSettlementAllowed",
      args: [opaqueIdToBytes32(job.attemptId)],
    }) as boolean;
    if (!reviewAllowed) {
      throw new DeferredProtocolJobError("Settlement is waiting for final ReviewManagerV2 approval", 10);
    }
    const route = await this.routeFor(job);
    const deployment = await this.deploymentFor(job);
    if (route.transport === "CHAINLINK_CCIP") {
      return this.crossChainCCIPSettlement(job, base, route, deployment);
    }
    const source = await this.chain(this.config.sourceChainId);
    const destination = await this.chain(route.destinationChainId, route.rpcUrl);
    const workflow = await this.api.workflow(job.workflowId);
    const node = (workflow.nodes as Record<string, any>[]).find((item) => item.id === job.nodeId)!;
    const attempt = (node.attempts as Record<string, any>[]).find((item) => item.id === job.attemptId)!;
    if (!attempt?.homeWallet) throw new Error("Settlement attempt has no immutable homeWallet");
    const attemptId = opaqueIdToBytes32(job.attemptId);
    const workflowId = opaqueIdToBytes32(workflow.onchainWorkflowId);
    const nodeId = opaqueIdToBytes32(job.nodeId);
    const messageId = job.payload.messageId as Hash;
    const amount = parseUnits(String(job.payload.amount), route.tokenDecimals ?? 6);
    const reservation = await source.publicClient.readContract({ address: base.contracts.taskEscrow, abi: this.artifact("TaskEscrow").abi, functionName: "reservations", args: [attemptId] }) as readonly unknown[];
    const sourceTransactionHashes: Hash[] = [];
    if (Number(reservation[3]) === 1) {
      sourceTransactionHashes.push(await this.write(source, "TaskEscrow", base.contracts.taskEscrow, "initiateCrossChainPaymentWithMessageId", [attemptId, base.contracts.dynamicSettlementSender, BigInt(route.destinationChainId), attempt.homeWallet as Address, messageId]));
    }
    let nodeState = await source.publicClient.readContract({ address: base.contracts.workflowEngine, abi: this.artifact("WorkflowEngine").abi, functionName: "nodes", args: [nodeId] }) as readonly unknown[];
    if (Number(nodeState[7]) === 4) {
      sourceTransactionHashes.push(await this.write(source, "WorkflowEngine", base.contracts.workflowEngine, "markSettlementPending", [nodeId, attemptId, messageId]));
    }
    const sourceCommitted = await source.publicClient.readContract({
      address: base.contracts.dynamicSettlementSender,
      abi: this.artifact("DynamicSettlementSender").abi,
      functionName: "isCommitted",
      args: [messageId],
    }) as boolean;
    if (!sourceCommitted) {
      sourceTransactionHashes.push(await this.write(source, "DynamicSettlementSender", base.contracts.dynamicSettlementSender, "commitSettlement", [messageId]));
    }
    const receiver = deployment.contracts.dynamicSettlementReceiver as Address;
    const destinationToken = deployment.contracts.destinationToken as Address;
    const payoutMode = String(job.payload.payoutMode || "DIRECT");
    const payoutToken = deployment.contracts.payoutToken as Address | undefined;
    const payoutSwap = deployment.contracts.payoutSwap as Address | undefined;
    const payoutDecimals = Number(job.payload.payoutDecimals ?? 6);
    const expectedPayout = parseUnits(String(job.payload.payoutQuoteAmount || job.payload.amount), payoutDecimals);
    const minPayout = parseUnits(String(job.payload.payoutMinAmount || job.payload.amount), payoutDecimals);
    let destinationTxHash: Hash;
    const destinationProcessed = await destination.publicClient.readContract({
      address: receiver,
      abi: this.artifact("DynamicSettlementReceiver").abi,
      functionName: "processedMessages",
      args: [messageId],
    }) as boolean;
    if (destinationProcessed) {
      const eventName = payoutMode === "SWAP" ? "DynamicSettlementSwapped" : "DynamicSettlementReceived";
      const events = await destination.publicClient.getContractEvents({
        address: receiver,
        abi: this.artifact("DynamicSettlementReceiver").abi,
        eventName,
        args: { messageId },
        fromBlock: 0n,
        toBlock: "latest",
      } as never) as Array<{ transactionHash: Hash }>;
      destinationTxHash = events.at(-1)?.transactionHash as Hash;
      if (!destinationTxHash) throw new Error("Processed settlement is missing its destination event");
    } else if (payoutMode === "SWAP") {
      if (!payoutToken || !payoutSwap) throw new Error("SWAP payout route is missing payout contracts");
      const onchainQuote = await destination.publicClient.readContract({
        address: payoutSwap,
        abi: this.artifact("LocalPayoutSwap").abi,
        functionName: "quoteExactInput",
        args: [amount],
      }) as bigint;
      if (onchainQuote !== expectedPayout || onchainQuote < minPayout) {
        throw new Error("Destination payout quote does not match the immutable attempt snapshot");
      }
      destinationTxHash = await this.write(destination, "DynamicSettlementReceiver", receiver, "receiveSettlementAndSwap", [
        messageId,
        BigInt(this.config.sourceChainId),
        base.contracts.dynamicSettlementSender,
        workflowId,
        attemptId,
        destinationToken,
        attempt.homeWallet as Address,
        amount,
        payoutToken,
        minPayout,
        BigInt(job.payload.payoutQuoteDeadline),
      ]);
    } else {
      destinationTxHash = await this.write(destination, "DynamicSettlementReceiver", receiver, "receiveSettlement", [messageId, BigInt(this.config.sourceChainId), base.contracts.dynamicSettlementSender, workflowId, attemptId, destinationToken, attempt.homeWallet as Address, amount]);
    }
    if (payoutMode === "SWAP") {
      const payoutRecord = await destination.publicClient.readContract({
        address: receiver,
        abi: this.artifact("DynamicSettlementReceiver").abi,
        functionName: "payoutRecords",
        args: [messageId],
      }) as readonly unknown[];
      if (
        String(payoutRecord[0]).toLowerCase() !== String(attempt.homeWallet).toLowerCase()
        || String(payoutRecord[1]).toLowerCase() !== String(payoutToken).toLowerCase()
        || BigInt(payoutRecord[2] as bigint) !== amount
        || BigInt(payoutRecord[3] as bigint) !== expectedPayout
      ) throw new Error("Destination payout record does not match the settlement snapshot");
    }
    const sourceConfirmed = await source.publicClient.readContract({
      address: base.contracts.dynamicSettlementSender,
      abi: this.artifact("DynamicSettlementSender").abi,
      functionName: "isConfirmed",
      args: [messageId],
    }) as boolean;
    const sourceConfirmTxHash = sourceConfirmed
      ? (await source.publicClient.getContractEvents({
          address: base.contracts.dynamicSettlementSender,
          abi: this.artifact("DynamicSettlementSender").abi,
          eventName: "SourceSettlementConfirmed",
          args: { messageId },
          fromBlock: 0n,
          toBlock: "latest",
        } as never) as Array<{ transactionHash: Hash }>).at(-1)?.transactionHash
      : await this.write(source, "DynamicSettlementSender", base.contracts.dynamicSettlementSender, "confirmSettlement", [messageId, destinationTxHash]);
    if (!sourceConfirmTxHash) throw new Error("Source settlement confirmation transaction was not found");
    sourceTransactionHashes.push(sourceConfirmTxHash);
    const confirmationReservation = await source.publicClient.readContract({
      address: base.contracts.taskEscrow,
      abi: this.artifact("TaskEscrow").abi,
      functionName: "reservations",
      args: [attemptId],
    }) as readonly unknown[];
    const reservationStatus = Number(confirmationReservation[3]);
    if (reservationStatus === 4) {
      sourceTransactionHashes.push(await this.write(source, "TaskEscrow", base.contracts.taskEscrow, "confirmCrossChainPayment", [attemptId, attempt.homeWallet as Address]));
    } else if (reservationStatus !== 3) {
      throw new Error(`Settlement reservation has unexpected status ${reservationStatus}`);
    }
    nodeState = await source.publicClient.readContract({ address: base.contracts.workflowEngine, abi: this.artifact("WorkflowEngine").abi, functionName: "nodes", args: [nodeId] }) as readonly unknown[];
    if (Number(nodeState[7]) === 10) {
      sourceTransactionHashes.push(await this.write(source, "WorkflowEngine", base.contracts.workflowEngine, "markSettled", [nodeId, attemptId]));
    }
    const settlementGasCost = await this.chargeSourceGas(base, source, workflowId, `SETTLEMENT:${attemptId}`, sourceTransactionHashes);
    await this.api.confirmSettlement(job.workflowId, job.nodeId, job.attemptId, {
      messageId,
      destinationTxHash,
      sourceConfirmTxHash,
      receivedAmount: String(job.payload.amount),
      homeWallet: attempt.homeWallet,
      settlementFee: formatGasCost(settlementGasCost),
      settlementAsset: String(job.payload.settlementAsset || "USDC"),
      payoutAsset: String(job.payload.payoutAsset || job.payload.settlementAsset || "USDC"),
      payoutTokenAddress: payoutMode === "SWAP" ? payoutToken : destinationToken,
      payoutAmount: String(job.payload.payoutQuoteAmount || job.payload.amount),
      minPayoutAmount: String(job.payload.payoutMinAmount || job.payload.amount),
      payoutRate: String(job.payload.payoutRate || "1"),
      payoutTxHash: destinationTxHash,
    });
    return sourceConfirmTxHash;
  }

  private async crossChainCCIPSettlement(
    job: ProtocolJob,
    base: BaseManifest,
    route: ProtocolRoute,
    deployment: RouteDeployment,
  ): Promise<Hash> {
    if (!job.workflowId || !job.nodeId || !job.attemptId) {
      throw new Error("CHAINLINK_CCIP settlement identifiers are incomplete");
    }
    const sender = deployment.contracts.dynamicCCIPWorkflowSenderA as Address | undefined;
    const receiver = deployment.contracts.dynamicCCIPWorkflowReceiver as Address | undefined;
    const destinationToken = deployment.contracts.destinationToken as Address | undefined;
    if (!sender || !receiver || !destinationToken) {
      throw new Error("CHAINLINK_CCIP route deployment is missing Sender, Receiver, or destination token");
    }
    if (String(job.payload.payoutMode || "DIRECT") !== "DIRECT") {
      throw new Error("CHAINLINK_CCIP V1 does not execute an unregistered destination DEX swap");
    }

    const source = await this.chain(this.config.sourceChainId);
    const destination = await this.chain(route.destinationChainId, route.rpcUrl);
    const workflow = await this.api.workflow(job.workflowId);
    const node = (workflow.nodes as Record<string, any>[]).find((item) => item.id === job.nodeId);
    const attempt = (node?.attempts as Record<string, any>[] | undefined)?.find((item) => item.id === job.attemptId);
    if (!node || !attempt?.homeWallet) throw new Error("Settlement attempt or immutable homeWallet was not found");
    const workflowId = opaqueIdToBytes32(workflow.onchainWorkflowId);
    const nodeId = opaqueIdToBytes32(job.nodeId);
    const attemptId = opaqueIdToBytes32(job.attemptId);
    const amount = parseUnits(String(job.payload.amount), 6);
    const sourceTransactionHashes: Hash[] = [];

    let reservation = await source.publicClient.readContract({
      address: base.contracts.taskEscrow,
      abi: this.artifact("TaskEscrow").abi,
      functionName: "reservations",
      args: [attemptId],
    }) as readonly unknown[];
    let transportMessageId = await source.publicClient.readContract({
      address: base.contracts.taskEscrow,
      abi: this.artifact("TaskEscrow").abi,
      functionName: "settlementMessageIds",
      args: [attemptId],
    }) as Hash;

    if (Number(reservation[3]) === 1) {
      const quotedFee = await source.publicClient.readContract({
        address: sender,
        abi: this.artifact("DynamicCCIPWorkflowSender").abi,
        functionName: "quoteFee",
        args: [workflowId, attemptId, BigInt(route.destinationChainId), attempt.homeWallet as Address, amount],
      }) as bigint;
      const senderBalance = await source.publicClient.getBalance({ address: sender });
      if (senderBalance < quotedFee) {
        const shortfall = quotedFee - senderBalance;
        const chargeId = keccak256(stringToHex(`${workflowId}:CCIP_NETWORK_FEE:${attemptId}:${shortfall}`));
        const processed = await source.publicClient.readContract({
          address: base.contracts.gasTreasury,
          abi: this.artifact("GasTreasuryA").abi,
          functionName: "processedCharges",
          args: [chargeId],
        }) as boolean;
        if (!processed) {
          sourceTransactionHashes.push(await this.write(
            source,
            "GasTreasuryA",
            base.contracts.gasTreasury,
            "charge",
            [workflowId, chargeId, sender, shortfall],
          ));
        }
      }
      sourceTransactionHashes.push(await this.write(
        source,
        "TaskEscrow",
        base.contracts.taskEscrow,
        "initiateCrossChainPayment",
        [attemptId, sender, BigInt(route.destinationChainId), attempt.homeWallet as Address],
      ));
      transportMessageId = await source.publicClient.readContract({
        address: base.contracts.taskEscrow,
        abi: this.artifact("TaskEscrow").abi,
        functionName: "settlementMessageIds",
        args: [attemptId],
      }) as Hash;
      if (transportMessageId === ZERO_BYTES32) throw new Error("CCIP Router returned an empty messageId");
      reservation = await source.publicClient.readContract({
        address: base.contracts.taskEscrow,
        abi: this.artifact("TaskEscrow").abi,
        functionName: "reservations",
        args: [attemptId],
      }) as readonly unknown[];
    }
    if (Number(reservation[3]) !== 4 && Number(reservation[3]) !== 3) {
      throw new Error(`CCIP reservation has unexpected status ${Number(reservation[3])}`);
    }

    let nodeState = await source.publicClient.readContract({
      address: base.contracts.workflowEngine,
      abi: this.artifact("WorkflowEngine").abi,
      functionName: "nodes",
      args: [nodeId],
    }) as readonly unknown[];
    if (Number(nodeState[7]) === 4) {
      sourceTransactionHashes.push(await this.write(
        source,
        "WorkflowEngine",
        base.contracts.workflowEngine,
        "markSettlementPending",
        [nodeId, attemptId, transportMessageId],
      ));
    }

    const deadline = Date.now() + this.config.ccipDeliveryTimeoutMs;
    let receivedEvent: { transactionHash: Hash; blockNumber: bigint; args?: Record<string, unknown> } | undefined;
    do {
      const events = await destination.publicClient.getContractEvents({
        address: receiver,
        abi: this.artifact("DynamicCCIPWorkflowReceiver").abi,
        eventName: "CCIPWorkflowSettlementReceived",
        args: { messageId: transportMessageId },
        fromBlock: 0n,
        toBlock: "latest",
      } as never) as Array<{ transactionHash: Hash; blockNumber: bigint; args?: Record<string, unknown> }>;
      receivedEvent = events.at(-1);
      if (receivedEvent) break;
      await new Promise((resolvePromise) => setTimeout(resolvePromise, this.config.ccipPollIntervalMs));
    } while (Date.now() < deadline);
    if (!receivedEvent) throw new Error(`CCIP delivery ${transportMessageId} is still pending after the configured timeout`);
    const receivedArgs = receivedEvent.args || {};
    if (
      String(receivedArgs.workflowId).toLowerCase() !== workflowId.toLowerCase()
      || String(receivedArgs.attemptId).toLowerCase() !== attemptId.toLowerCase()
      || String(receivedArgs.payee).toLowerCase() !== String(attempt.homeWallet).toLowerCase()
      || String(receivedArgs.token).toLowerCase() !== destinationToken.toLowerCase()
      || BigInt(receivedArgs.amount as bigint) !== amount
    ) {
      throw new Error("CCIP destination receipt does not match the immutable settlement snapshot");
    }
    const destinationTxHash = receivedEvent.transactionHash;
    const destinationReceipt = await destination.publicClient.getTransactionReceipt({ hash: destinationTxHash });
    const destinationConfirmations = BigInt(route.confirmationBlocks ?? 5);
    while (await destination.publicClient.getBlockNumber() < destinationReceipt.blockNumber + destinationConfirmations - 1n) {
      await new Promise((resolvePromise) => setTimeout(resolvePromise, this.config.ccipPollIntervalMs));
    }
    const canonicalDestinationReceipt = await destination.publicClient.getTransactionReceipt({ hash: destinationTxHash });
    if (canonicalDestinationReceipt.blockHash !== destinationReceipt.blockHash || canonicalDestinationReceipt.status !== "success") {
      throw new DeferredProtocolJobError("CCIP destination receipt was reorganized before finality", 30);
    }
    const destinationDecimals = await destination.publicClient.readContract({
      address: destinationToken,
      abi: ERC20_BALANCE_ABI,
      functionName: "decimals",
    });
    if (Number(destinationDecimals) !== (route.tokenDecimals ?? 6)) {
      throw new Error("CCIP destination token decimals no longer match the pinned route manifest");
    }
    const payeeBalance = await destination.publicClient.readContract({
      address: destinationToken,
      abi: ERC20_BALANCE_ABI,
      functionName: "balanceOf",
      args: [attempt.homeWallet as Address],
      blockNumber: canonicalDestinationReceipt.blockNumber,
    });
    if (payeeBalance < amount) {
      throw new Error("CCIP destination event was emitted but the payee BnM balance is insufficient");
    }

    const sourceSettlement = await source.publicClient.readContract({
      address: sender,
      abi: this.artifact("DynamicCCIPWorkflowSender").abi,
      functionName: "settlements",
      args: [transportMessageId],
    }) as readonly unknown[];
    const ccipNetworkFee = BigInt(sourceSettlement[5] as bigint);
    let sourceConfirmTxHash: Hash;
    if (!Boolean(sourceSettlement[7])) {
      sourceConfirmTxHash = await this.write(
        source,
        "DynamicCCIPWorkflowSender",
        sender,
        "confirmSettlement",
        [transportMessageId, destinationTxHash],
      );
      sourceTransactionHashes.push(sourceConfirmTxHash);
    } else {
      const events = await source.publicClient.getContractEvents({
        address: sender,
        abi: this.artifact("DynamicCCIPWorkflowSender").abi,
        eventName: "CCIPWorkflowSettlementConfirmed",
        args: { messageId: transportMessageId },
        fromBlock: 0n,
        toBlock: "latest",
      } as never) as Array<{ transactionHash: Hash }>;
      sourceConfirmTxHash = events.at(-1)?.transactionHash as Hash;
      if (!sourceConfirmTxHash) throw new Error("Confirmed CCIP settlement has no source confirmation event");
    }

    reservation = await source.publicClient.readContract({
      address: base.contracts.taskEscrow,
      abi: this.artifact("TaskEscrow").abi,
      functionName: "reservations",
      args: [attemptId],
    }) as readonly unknown[];
    if (Number(reservation[3]) === 4) {
      sourceTransactionHashes.push(await this.write(
        source,
        "TaskEscrow",
        base.contracts.taskEscrow,
        "confirmCrossChainPayment",
        [attemptId, attempt.homeWallet as Address],
      ));
    }
    nodeState = await source.publicClient.readContract({
      address: base.contracts.workflowEngine,
      abi: this.artifact("WorkflowEngine").abi,
      functionName: "nodes",
      args: [nodeId],
    }) as readonly unknown[];
    if (Number(nodeState[7]) === 10) {
      sourceTransactionHashes.push(await this.write(
        source,
        "WorkflowEngine",
        base.contracts.workflowEngine,
        "markSettled",
        [nodeId, attemptId],
      ));
    }

    const sourceGasCost = await this.chargeSourceGas(
      base,
      source,
      workflowId,
      `CCIP_SETTLEMENT:${attemptId}`,
      sourceTransactionHashes,
    );
    await this.api.confirmSettlement(job.workflowId, job.nodeId, job.attemptId, {
      messageId: job.payload.messageId,
      transportMessageId,
      destinationTxHash,
      sourceConfirmTxHash,
      receivedAmount: String(job.payload.amount),
      homeWallet: attempt.homeWallet,
      settlementFee: formatGasCost(ccipNetworkFee + sourceGasCost),
      settlementAsset: String(job.payload.settlementAsset || "USDC"),
      payoutAsset: String(job.payload.settlementAsset || "USDC"),
      payoutTokenAddress: destinationToken,
      payoutAmount: String(job.payload.amount),
      minPayoutAmount: String(job.payload.amount),
      payoutRate: "1",
      payoutTxHash: destinationTxHash,
    });
    return sourceConfirmTxHash;
  }

  private async refundFailedWorkflow(job: ProtocolJob): Promise<Hash> {
    if (!job.workflowId) throw new Error("REFUND_FAILED_WORKFLOW is missing workflowId");
    const base = await this.ensureBaseStack();
    const chain = await this.chain(this.config.sourceChainId);
    const workflow = await this.api.workflow(job.workflowId);
    const workflowId = opaqueIdToBytes32(workflow.onchainWorkflowId);
    const reasonCode = keccak256(stringToHex(workflow.failureReason || "WORKFLOW_FAILED"));
    const failureStatus = Number(await chain.publicClient.readContract({ address: base.contracts.workflowEngine, abi: this.artifact("WorkflowEngine").abi, functionName: "workflowFailureStatus", args: [workflowId] }));
    const sourceTransactionHashes: Hash[] = [];
    if (failureStatus === 0) sourceTransactionHashes.push(await this.write(chain, "WorkflowEngine", base.contracts.workflowEngine, "startFailedRefund", [workflowId, reasonCode]));
    const tokenRefundTxHash = await this.write(chain, "TaskEscrow", base.contracts.taskEscrow, "refundFailedWorkflow", [workflowId]);
    sourceTransactionHashes.push(tokenRefundTxHash);
    await this.chargeSourceGas(base, chain, workflowId, "FAILED_REFUND", sourceTransactionHashes);
    let gasRefundTxHash: Hash | undefined;
    const gasAccount = await chain.publicClient.readContract({ address: base.contracts.gasTreasury, abi: this.artifact("GasTreasuryA").abi, functionName: "accounts", args: [workflowId] }) as readonly unknown[];
    const gasRefundAmount = BigInt(gasAccount[1] as bigint) - BigInt(gasAccount[2] as bigint) - BigInt(gasAccount[3] as bigint);
    if (gasRefundAmount > 0n) {
      gasRefundTxHash = await this.write(chain, "GasTreasuryA", base.contracts.gasTreasury, "refund", [workflowId]);
    }
    await this.write(chain, "WorkflowEngine", base.contracts.workflowEngine, "confirmFailedRefund", [workflowId]);
    await this.api.confirmFailedRefund(job.workflowId, { tokenRefundTxHash, gasRefundTxHash, sourceGasRefundAmount: formatNativeAmount(gasRefundAmount) });
    return tokenRefundTxHash;
  }

  private async finalizeWorkflow(job: ProtocolJob): Promise<Hash> {
    if (!job.workflowId) throw new Error("FINALIZE_WORKFLOW is missing workflowId");
    const base = await this.ensureBaseStack();
    const chain = await this.chain(this.config.sourceChainId);
    const workflow = await this.api.workflow(job.workflowId);
    const workflowId = opaqueIdToBytes32(workflow.onchainWorkflowId);
    let platformFeeTxHash: Hash | undefined;
    const sourceTransactionHashes: Hash[] = [];
    if (Number(job.payload.platformFee || 0) > 0) {
      platformFeeTxHash = await this.write(
        chain,
        "TaskEscrow",
        base.contracts.taskEscrow,
        "payPlatformFee",
        [workflowId, parseUnits(String(job.payload.platformFee), 6)],
      );
      sourceTransactionHashes.push(platformFeeTxHash);
    }
    const tokenRefundTxHash = await this.write(chain, "TaskEscrow", base.contracts.taskEscrow, "refundRemainder", [workflowId]);
    sourceTransactionHashes.push(tokenRefundTxHash);
    await this.chargeSourceGas(base, chain, workflowId, "FINALIZE_WORKFLOW", sourceTransactionHashes);
    let gasRefundTxHash: Hash | undefined;
    const gasAccount = await chain.publicClient.readContract({ address: base.contracts.gasTreasury, abi: this.artifact("GasTreasuryA").abi, functionName: "accounts", args: [workflowId] }) as readonly unknown[];
    const gasRefundAmount = BigInt(gasAccount[1] as bigint) - BigInt(gasAccount[2] as bigint) - BigInt(gasAccount[3] as bigint);
    if (gasRefundAmount > 0n) {
      gasRefundTxHash = await this.write(chain, "GasTreasuryA", base.contracts.gasTreasury, "refund", [workflowId]);
    }
    await this.api.confirmWorkflowFinalization(job.workflowId, {
      platformFeeTxHash,
      tokenRefundTxHash,
      gasRefundTxHash,
      sourceGasRefundAmount: formatNativeAmount(gasRefundAmount),
    });
    return tokenRefundTxHash;
  }

  private async systemRetry(job: ProtocolJob): Promise<Hash> {
    if (!job.nodeId || !job.attemptId) throw new Error("SYSTEM_RETRY identifiers are incomplete");
    const base = await this.ensureBaseStack();
    const chain = await this.chain(this.config.sourceChainId);
    return this.write(chain, "WorkflowEngine", base.contracts.workflowEngine, "markSystemRetry", [opaqueIdToBytes32(job.nodeId), opaqueIdToBytes32(job.attemptId), keccak256(stringToHex(String(job.payload.reasonCode || "SYSTEM_RETRY")))]);
  }
}
