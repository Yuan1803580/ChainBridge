import assert from "node:assert/strict";
import { createHash, randomUUID } from "node:crypto";
import { mkdirSync, readFileSync, writeFileSync } from "node:fs";
import { resolve } from "node:path";

import {
  createPublicClient,
  createWalletClient,
  defineChain,
  formatEther,
  formatUnits,
  http,
  keccak256,
  parseEther,
  parseUnits,
  stringToHex,
  type Abi,
  type Address,
  type Hash,
  type Hex,
} from "viem";

type Artifact = { abi: Abi; bytecode: Hex };
type ChainRuntime = {
  chainId: number;
  rpcUrl: string;
  publicClient: ReturnType<typeof createPublicClient>;
  walletClient: ReturnType<typeof createWalletClient>;
  accounts: Address[];
};

const root = resolve(import.meta.dirname, "..");
const apiUrl = (process.env.API_URL || "http://127.0.0.1:8000").replace(/\/$/, "");
const runtimeDir = process.env.LOCAL_PROTOCOL_RUNTIME_DIR || resolve(root, ".runtime", "local-v1");
if ((process.env.AUTO_INVOKE_AGENTS || "false").toLowerCase() !== "true") {
  throw new Error("Integrated acceptance requires AUTO_INVOKE_AGENTS=true");
}
const base = JSON.parse(readFileSync(resolve(runtimeDir, "protocol-base.json"), "utf8")) as {
  contracts: Record<string, Address>;
};
const artifact = (name: string): Artifact => JSON.parse(readFileSync(
  resolve(root, `hardhat-artifacts/contracts/src/${name}.sol/${name}.json`),
  "utf8",
)) as Artifact;
const artifacts = {
  token: artifact("MockUSDC"),
  payoutToken: artifact("MockPayoutToken"),
  escrow: artifact("TaskEscrow"),
  gasTreasury: artifact("GasTreasuryA"),
  board: artifact("RemoteWorkBoard"),
};

const chainDefinitions = [
  { chainId: Number(process.env.LOCAL_CHAIN_A_ID || 31337), rpcUrl: process.env.LOCAL_CHAIN_A_RPC_URL || "http://127.0.0.1:8545" },
  { chainId: Number(process.env.LOCAL_CHAIN_B_ID || 31338), rpcUrl: process.env.LOCAL_CHAIN_B_RPC_URL || "http://127.0.0.1:8546" },
  { chainId: Number(process.env.LOCAL_CHAIN_C_ID || 31339), rpcUrl: process.env.LOCAL_CHAIN_C_RPC_URL || "http://127.0.0.1:8547" },
  { chainId: Number(process.env.LOCAL_CHAIN_D_ID || 31340), rpcUrl: process.env.LOCAL_CHAIN_D_RPC_URL || "http://127.0.0.1:8548" },
];
const chains = new Map<number, ChainRuntime>();
for (const item of chainDefinitions) {
  const chain = defineChain({
    id: item.chainId,
    name: `Local ${item.chainId}`,
    nativeCurrency: { name: "Local ETH", symbol: "ETH", decimals: 18 },
    rpcUrls: { default: { http: [item.rpcUrl] } },
  });
  const publicClient = createPublicClient({ chain, transport: http(item.rpcUrl) });
  const walletClient = createWalletClient({ chain, transport: http(item.rpcUrl) });
  assert.equal(await publicClient.getChainId(), item.chainId);
  const accounts = await walletClient.getAddresses();
  chains.set(item.chainId, { ...item, publicClient, walletClient, accounts });
}
const chainA = chains.get(chainDefinitions[0].chainId)!;
const coordinator = chainA.accounts[0];
const requesterWallet = chainA.accounts[1];
const treasury = chainA.accounts[5];

async function login(did: string): Promise<string> {
  const response = await fetch(`${apiUrl}/api/v1/auth/dev-login`, {
    method: "POST",
    headers: { "Content-Type": "application/json", "Idempotency-Key": randomUUID() },
    body: JSON.stringify({ did }),
  });
  const body = await response.json().catch(() => null) as Record<string, any> | null;
  assert.equal(response.status, 200, JSON.stringify(body));
  return body!.accessToken;
}

async function api(path: string, options: { method?: string; token?: string; body?: Record<string, unknown> } = {}) {
  const headers = new Headers({ Accept: "application/json" });
  if (options.token) headers.set("Authorization", `Bearer ${options.token}`);
  if (options.body) headers.set("Content-Type", "application/json");
  if (options.method && options.method !== "GET") headers.set("Idempotency-Key", randomUUID());
  const response = await fetch(`${apiUrl}${path}`, {
    method: options.method || "GET",
    headers,
    body: options.body ? JSON.stringify(options.body) : undefined,
  });
  const body = await response.json().catch(() => null);
  assert.ok(response.ok, `${path}: ${response.status} ${JSON.stringify(body)}`);
  return body;
}

async function write(chain: ChainRuntime, contract: Artifact, address: Address, functionName: string, args: readonly unknown[], account: Address, value?: bigint): Promise<Hash> {
  const transactionHash = await chain.walletClient.writeContract({ account, address, abi: contract.abi, functionName, args, value } as never);
  const receipt = await chain.publicClient.waitForTransactionReceipt({ hash: transactionHash });
  assert.equal(receipt.status, "success", `${functionName} reverted on ${chain.chainId}`);
  return transactionHash;
}

const delay = (milliseconds: number) => new Promise((resolvePromise) => setTimeout(resolvePromise, milliseconds));

type JobSelector = {
  afterCount?: number;
  attemptId?: string;
};

async function waitForJob(
  adminToken: string,
  workflowId: string,
  jobType: string,
  selector: JobSelector = {},
): Promise<Record<string, any>> {
  for (let attempt = 0; attempt < 300; attempt += 1) {
    const response = await api("/api/v1/protocol-jobs?status=&limit=500", { token: adminToken });
    const jobs = (response.items as Record<string, any>[]).filter((item) => (
      item.workflowId === workflowId
      && item.jobType === jobType
      && (!selector.attemptId || item.attemptId === selector.attemptId)
    ));
    if (jobs.length > (selector.afterCount || 0)) {
      const job = jobs.at(-1)!;
      if (job.status === "FAILED") throw new Error(`${jobType} failed: ${job.lastError}`);
      if (job.status === "COMPLETED") return job;
    }
    await delay(100);
  }
  throw new Error(`Timed out waiting for ${jobType}`);
}

async function workflow(workflowId: string) {
  return api(`/api/v1/workflows/${workflowId}`);
}

function node(value: Record<string, any>, nodeKey: string): Record<string, any> {
  return value.nodes.find((item: Record<string, any>) => item.nodeKey === nodeKey);
}

async function coordinate(token: string, workflowId: string) {
  return api(`/api/v1/workflows/${workflowId}/coordinate`, { method: "POST", token, body: { maxActions: 1 } });
}

function canonical(value: unknown): Hash {
  const stable = (item: unknown): string => {
    if (Array.isArray(item)) return `[${item.map(stable).join(",")}]`;
    if (item && typeof item === "object") return `{${Object.entries(item as Record<string, unknown>).sort(([a], [b]) => a.localeCompare(b)).map(([key, child]) => `${JSON.stringify(key)}:${stable(child)}`).join(",")}}`;
    return JSON.stringify(item);
  };
  return `0x${createHash("sha256").update(stable(value)).digest("hex")}` as Hash;
}

async function ensureDispatch(requesterToken: string, adminToken: string, workflowId: string, nodeKey: string): Promise<Record<string, any>> {
  let before = await workflow(workflowId);
  const previousDeployments = (await api("/api/v1/protocol-jobs?status=&limit=500", { token: adminToken })).items
    .filter((item: Record<string, any>) => item.workflowId === workflowId && item.jobType === "DEPLOY_ROUTE_STACK").length;
  let response = await coordinate(requesterToken, workflowId);
  if (response.actions[0].action === "route_deployment_queued") {
    await waitForJob(adminToken, workflowId, "DEPLOY_ROUTE_STACK", { afterCount: previousDeployments });
    response = await coordinate(requesterToken, workflowId);
  }
  assert.equal(response.actions[0].action, "node_dispatched");
  before = response.workflow;
  const currentAttempt = node(before, nodeKey).currentAttempt;
  await waitForJob(adminToken, workflowId, "DISPATCH_ASSIGNMENT", { attemptId: currentAttempt.id });
  if ((process.env.AUTO_INVOKE_AGENTS || "false").toLowerCase() === "true") {
    await waitForJob(adminToken, workflowId, "RELAY_RESULT_PROOF", { attemptId: currentAttempt.id });
    const updated = await workflow(workflowId);
    return node(updated, nodeKey).attempts.find((item: Record<string, any>) => item.id === currentAttempt.id);
  }
  return currentAttempt;
}

async function submitResult(
  adminToken: string,
  providerToken: string,
  workflowId: string,
  nodeKey: string,
  attempt: Record<string, any>,
  output: Record<string, unknown>,
) {
  const deployments = await api("/api/v1/route-deployments");
  const deployment = deployments.items.find((item: Record<string, any>) => item.routeId === attempt.routeId && item.routeVersion === attempt.routeVersion && item.status === "READY");
  assert.ok(deployment, `No READY deployment for ${attempt.routeId}:${attempt.routeVersion}`);
  const chain = chains.get(attempt.chainId)!;
  const board = deployment.contracts.remoteWorkBoard as Address;
  const resultMessageId = keccak256(stringToHex(`${workflowId}:${attempt.id}:result`));
  const resultHash = canonical(output);
  const proofHash = keccak256(stringToHex(`${attempt.id}:proof`));
  await write(chain, artifacts.board, board, "startWork", [opaque(attempt.id)], attempt.homeWallet);
  const executionTxHash = await write(chain, artifacts.board, board, "submitResult", [resultMessageId, opaque(attempt.id), resultHash, proofHash], attempt.homeWallet);
  await api(`/api/v1/workflows/${workflowId}/nodes/${nodeKey}/attempts/${attempt.id}/results`, {
    method: "POST",
    token: providerToken,
    body: { output, proofHash, resultMessageId, transactionHash: executionTxHash, gasCost: "0", gasSymbol: "ETH" },
  });
  await waitForJob(adminToken, workflowId, "RELAY_RESULT_PROOF", { attemptId: attempt.id });
  return { executionTxHash, resultMessageId, resultHash, proofHash };
}

function opaque(value: string): Hash {
  const compact = value.replaceAll("-", "");
  return `0x${compact.padEnd(64, "0")}` as Hash;
}

const requesterToken = await login("did:local:campus-requester");
const adminToken = await login("did:local:platform-admin");

const created = await api("/api/v1/workflows", {
  method: "POST",
  token: requesterToken,
  body: {
    title: "Protocol Worker A/B/C/D acceptance",
    objective: "Research evidence, reject a false verification, retry with the standby Agent, and settle on each Agent home chain.",
    budget: "20.00",
    platformFee: "1.10",
    fundingMode: "ONCHAIN",
    payerAddress: requesterWallet,
  },
});
const workflowId = created.id as string;
const onchainWorkflowId = created.onchainWorkflowId as Hash;
const budget = parseUnits("20", 6);
await write(chainA, artifacts.token, base.contracts.sourceToken, "mint", [requesterWallet, budget], coordinator);
await write(chainA, artifacts.token, base.contracts.sourceToken, "approve", [base.contracts.taskEscrow, budget], requesterWallet);
const lockTxHash = await write(chainA, artifacts.escrow, base.contracts.taskEscrow, "lockBudget", [onchainWorkflowId, created.termsHash, base.contracts.sourceToken, budget, parseUnits("1.1", 6)], requesterWallet);
const sourceGasTxHash = await write(chainA, artifacts.gasTreasury, base.contracts.gasTreasury, "depositSourceGas", [onchainWorkflowId], requesterWallet, parseEther("0.01"));
await api(`/api/v1/workflows/${workflowId}/funding-confirmations`, {
  method: "POST",
  token: requesterToken,
  body: { walletAddress: requesterWallet, lockTxHash, sourceGasTxHash, sourceGasAmount: "0.01", verificationMode: "RPC" },
});
await waitForJob(adminToken, workflowId, "REGISTER_WORKFLOW");

const attemptB = await ensureDispatch(requesterToken, adminToken, workflowId, "N1");
assert.equal(attemptB.agentKey, "Agent-B1");
assert.equal(attemptB.settlementAsset, "USDC");
assert.equal(attemptB.payoutAsset, "BTK");
assert.equal(attemptB.payoutRate, "2");
let response = await coordinate(requesterToken, workflowId);
assert.equal(response.actions[0].action, "review_passed");
await waitForJob(adminToken, workflowId, "RECORD_REVIEW", { attemptId: attemptB.id });
await waitForJob(adminToken, workflowId, "SYNC_REVIEW_V2", { attemptId: attemptB.id });
response = await coordinate(requesterToken, workflowId);
assert.equal(response.actions[0].action, "settlement_pending");
await waitForJob(adminToken, workflowId, "CROSS_CHAIN_SETTLEMENT", { attemptId: attemptB.id });

const attemptC = await ensureDispatch(requesterToken, adminToken, workflowId, "N2");
assert.equal(attemptC.agentKey, "Agent-C1");
assert.equal(attemptC.payoutAsset, "CTK");
assert.equal(attemptC.payoutRate, "3");
response = await coordinate(requesterToken, workflowId);
assert.equal(response.actions[0].action, "review_failed");
await waitForJob(adminToken, workflowId, "RECORD_REVIEW", { attemptId: attemptC.id });
await waitForJob(adminToken, workflowId, "SYNC_REVIEW_V2", { attemptId: attemptC.id });

const attemptD = await ensureDispatch(requesterToken, adminToken, workflowId, "N2");
assert.equal(attemptD.agentKey, "Agent-D1");
assert.equal(attemptD.attemptNumber, 2);
assert.equal(attemptD.payoutAsset, "DTK");
assert.equal(attemptD.payoutRate, "4");
const persistedC = JSON.parse(readFileSync(resolve(runtimeDir, "agent-results", `${attemptC.id}.json`), "utf8"));
const stale = await api(`/api/v1/workflows/${workflowId}/nodes/N2/attempts/${attemptC.id}/results`, {
  method: "POST",
  token: adminToken,
  body: {
    output: persistedC.runtimeEnvelope.output,
    proofHash: persistedC.proofHash,
    runtimeEnvelope: persistedC.runtimeEnvelope,
    resultMessageId: persistedC.resultMessageId,
    transactionHash: persistedC.executionTxHash,
  },
});
assert.equal(stale.accepted, false);
assert.equal(stale.code, "STALE_ATTEMPT");
response = await coordinate(requesterToken, workflowId);
assert.equal(response.actions[0].action, "review_passed");
await waitForJob(adminToken, workflowId, "RECORD_REVIEW", { attemptId: attemptD.id });
await waitForJob(adminToken, workflowId, "SYNC_REVIEW_V2", { attemptId: attemptD.id });
response = await coordinate(requesterToken, workflowId);
assert.equal(response.actions[0].action, "settlement_pending");
await waitForJob(adminToken, workflowId, "CROSS_CHAIN_SETTLEMENT", { attemptId: attemptD.id });
response = await coordinate(requesterToken, workflowId);
assert.equal(response.actions[0].action, "workflow_finalization_pending");
await waitForJob(adminToken, workflowId, "FINALIZE_WORKFLOW");

const finalWorkflow = await workflow(workflowId);
assert.equal(finalWorkflow.status, "COMPLETED");
assert.equal(finalWorkflow.contractSyncStatus, "FINALIZED");
const sourceGasDeposit = parseEther(finalWorkflow.sourceGasDeposit);
const sourceGasRefunded = parseEther(finalWorkflow.sourceGasRefunded);
assert.equal(sourceGasDeposit, parseEther("0.01"));
assert.ok(sourceGasRefunded > 0n && sourceGasRefunded < sourceGasDeposit);
assert.ok(sourceGasDeposit - sourceGasRefunded > 0n);
assert.equal(node(finalWorkflow, "N1").status, "SETTLED");
assert.equal(node(finalWorkflow, "N2").status, "SETTLED");
const finalAttempts = new Map<string, Record<string, any>>(
  finalWorkflow.nodes.flatMap((item: Record<string, any>) => item.attempts).map((attempt: Record<string, any>) => [attempt.id, attempt]),
);
assert.equal(finalAttempts.get(attemptB.id)?.payoutStatus, "SWAPPED");
assert.equal(finalAttempts.get(attemptC.id)?.payoutStatus, "NOT_EXECUTED");
assert.equal(finalAttempts.get(attemptD.id)?.payoutStatus, "SWAPPED");

const sourceBalance = async (address: Address) => chainA.publicClient.readContract({ address: base.contracts.sourceToken, abi: artifacts.token.abi, functionName: "balanceOf", args: [address] }) as Promise<bigint>;
const destinationBalance = async (chainId: number, token: Address, address: Address) => chains.get(chainId)!.publicClient.readContract({ address: token, abi: artifacts.payoutToken.abi, functionName: "balanceOf", args: [address] }) as Promise<bigint>;
const deployments = (await api("/api/v1/route-deployments")).items as Record<string, any>[];
const deploymentForAttempt = (attempt: Record<string, any>) => deployments.find((item) => item.routeId === attempt.routeId && item.routeVersion === attempt.routeVersion);
const deploymentB = deploymentForAttempt(attemptB);
const deploymentC = deploymentForAttempt(attemptC);
const deploymentD = deploymentForAttempt(attemptD);
const balances = {
  requesterRefund: await sourceBalance(requesterWallet),
  platformFee: await sourceBalance(treasury),
  sourceEscrow: await sourceBalance(base.contracts.taskEscrow),
  sourceSettlementLocked: await sourceBalance(base.contracts.dynamicSettlementSender),
  agentB: await destinationBalance(31338, deploymentB.contracts.payoutToken, attemptB.homeWallet),
  agentC: await destinationBalance(31339, deploymentC.contracts.payoutToken, attemptC.homeWallet),
  agentD: await destinationBalance(31340, deploymentD.contracts.payoutToken, attemptD.homeWallet),
  swapPoolB: await destinationBalance(31338, deploymentB.contracts.destinationToken, deploymentB.contracts.payoutSwap),
  swapPoolC: await destinationBalance(31339, deploymentC.contracts.destinationToken, deploymentC.contracts.payoutSwap),
  swapPoolD: await destinationBalance(31340, deploymentD.contracts.destinationToken, deploymentD.contracts.payoutSwap),
  gasTreasuryWei: await chainA.publicClient.getBalance({ address: base.contracts.gasTreasury }),
};
assert.deepEqual(balances, {
  requesterRefund: parseUnits("7.9", 6),
  platformFee: parseUnits("1.1", 6),
  sourceEscrow: 0n,
  sourceSettlementLocked: parseUnits("11", 6),
  agentB: parseUnits("8.4", 18),
  agentC: 0n,
  agentD: parseUnits("27.2", 6),
  swapPoolB: parseUnits("4.2", 6),
  swapPoolC: 0n,
  swapPoolD: parseUnits("6.8", 6),
  gasTreasuryWei: 0n,
});

const allJobs = (await api("/api/v1/protocol-jobs?status=&limit=500", { token: adminToken })).items as Record<string, any>[];
const allMessages = (await api("/api/v1/cross-chain-messages")).items as Record<string, any>[];
const usedRouteSnapshots = new Set([attemptB, attemptC, attemptD].map((attempt) => `${attempt.routeId}:${attempt.routeVersion}`));
const report = {
  accepted: true,
  workflowId,
  generatedAt: new Date().toISOString(),
  chains: chainDefinitions,
  baseContracts: base.contracts,
  funding: {
    mode: finalWorkflow.fundingMode,
    status: finalWorkflow.fundingStatus,
    wallet: finalWorkflow.fundingWallet,
    lockTxHash: finalWorkflow.lockTxHash,
    sourceGasDeposit: finalWorkflow.sourceGasDeposit,
    sourceGasRefunded: finalWorkflow.sourceGasRefunded,
    contractSyncStatus: finalWorkflow.contractSyncStatus,
  },
  attempts: [attemptB, attemptC, attemptD].map((attempt) => ({
    ...finalAttempts.get(attempt.id),
    attemptId: attempt.id,
    agentKey: attempt.agentKey,
    homeChainId: attempt.chainId,
    homeWallet: attempt.homeWallet,
    routeId: attempt.routeId,
    routeVersion: attempt.routeVersion,
    settlementAsset: attempt.settlementAsset,
    payoutAsset: attempt.payoutAsset,
    payoutRate: attempt.payoutRate,
  })),
  routeDeployments: deployments
    .filter((deployment) => usedRouteSnapshots.has(`${deployment.routeId}:${deployment.routeVersion}`))
    .map((deployment) => ({
      routeId: deployment.routeId,
      routeVersion: deployment.routeVersion,
      homeChainId: deployment.homeChainId,
      status: deployment.status,
      contracts: deployment.contracts,
      transactionHashes: deployment.transactionHashes,
      codeHashes: deployment.codeHashes,
      manifestHash: deployment.manifestHash,
    })),
  messages: allMessages
    .filter((message) => message.workflowId === workflowId)
    .map((message) => ({
      messageId: message.messageId,
      messageType: message.messageType,
      attemptId: message.attemptId,
      sourceChainId: message.sourceChainId,
      destinationChainId: message.destinationChainId,
      routeId: message.routeId,
      routeVersion: message.routeVersion,
      status: message.status,
      transactionHash: message.transactionHash,
    })),
  jobs: allJobs.filter((job) => job.workflowId === workflowId).map((job) => ({
    jobType: job.jobType,
    attemptId: job.attemptId,
    status: job.status,
    attempts: job.attempts,
    transactionHash: job.transactionHash,
  })),
  nodes: finalWorkflow.nodes.map((item: Record<string, any>) => ({
    nodeKey: item.nodeKey,
    status: item.status,
    currentAttemptId: item.currentAttemptId,
    attempts: item.attempts.map((attempt: Record<string, any>) => ({
      attemptId: attempt.id,
      attemptNumber: attempt.attemptNumber,
      agentKey: attempt.agentKey,
      chainId: attempt.chainId,
      status: attempt.status,
      resultProofStatus: attempt.resultProofStatus,
      settlementStatus: attempt.settlementStatus,
      settlementAsset: attempt.settlementAsset,
      payoutAsset: attempt.payoutAsset,
      payoutQuoteAmount: attempt.payoutQuoteAmount,
      payoutMinAmount: attempt.payoutMinAmount,
      payoutStatus: attempt.payoutStatus,
      payoutTxHash: attempt.payoutTxHash,
    })),
  })),
  tokenConservation: {
    locked: "20",
    agentPayouts: "11",
    platformFee: "1.1",
    tokenRefund: "7.9",
    remainingTaskEscrow: "0",
    balanced: true,
  },
  ledgerSummary: finalWorkflow.ledgerSummary,
  balances: {
    requesterRefundUsdc: formatUnits(balances.requesterRefund, 6),
    platformFeeUsdc: formatUnits(balances.platformFee, 6),
    sourceEscrowUsdc: formatUnits(balances.sourceEscrow, 6),
    sourceSettlementLockedUsdc: formatUnits(balances.sourceSettlementLocked, 6),
    agentBToken: formatUnits(balances.agentB, 18),
    agentCToken: formatUnits(balances.agentC, 8),
    agentDToken: formatUnits(balances.agentD, 6),
    swapPoolBUsdc: formatUnits(balances.swapPoolB, 6),
    swapPoolCUsdc: formatUnits(balances.swapPoolC, 6),
    swapPoolDUsdc: formatUnits(balances.swapPoolD, 6),
    gasTreasuryEth: formatEther(balances.gasTreasuryWei),
  },
};
mkdirSync(runtimeDir, { recursive: true });
const reportPath = resolve(runtimeDir, "protocol-worker-acceptance.json");
writeFileSync(reportPath, `${JSON.stringify(report, null, 2)}\n`, "utf8");
console.log("LOCAL_PROTOCOL_WORKER_ACCEPTED");
console.log(`workflow=${workflowId} B=8.4_BTK C=0_CTK D=27.2_DTK fee=1.1_USDC refund=7.9_USDC sourceGasRefund=${finalWorkflow.sourceGasRefunded}_ETH`);
console.log(`report=${reportPath}`);
