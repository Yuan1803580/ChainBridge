import assert from "node:assert/strict";
import { randomUUID } from "node:crypto";
import { mkdirSync, readFileSync, writeFileSync } from "node:fs";
import { resolve } from "node:path";

import {
  createPublicClient,
  createWalletClient,
  defineChain,
  formatUnits,
  http,
  parseEther,
  parseUnits,
  type Abi,
  type Address,
  type Hash,
  type Hex,
} from "viem";

type Artifact = { abi: Abi; bytecode: Hex };
type ApiOptions = { method?: string; token?: string; body?: Record<string, unknown> };

const root = resolve(import.meta.dirname, "..");
const apiUrl = (process.env.API_URL || "http://127.0.0.1:8200").replace(/\/$/, "");
const runtimeDir = process.env.LOCAL_PROTOCOL_RUNTIME_DIR || resolve(root, ".runtime", "local-experiment");
const chainAId = Number(process.env.LOCAL_CHAIN_A_ID || 31337);
const chainBId = Number(process.env.LOCAL_CHAIN_B_ID || 31338);
const chainAUrl = process.env.LOCAL_CHAIN_A_RPC_URL || "http://127.0.0.1:8545";
const chainBUrl = process.env.LOCAL_CHAIN_B_RPC_URL || "http://127.0.0.1:8546";

if ((process.env.AUTO_INVOKE_AGENTS || "true").toLowerCase() !== "true") {
  throw new Error("Kimi acceptance requires AUTO_INVOKE_AGENTS=true");
}

const base = JSON.parse(readFileSync(resolve(runtimeDir, "protocol-base.json"), "utf8")) as {
  contracts: Record<string, Address>;
};
const artifact = (name: string): Artifact => JSON.parse(readFileSync(
  resolve(root, `hardhat-artifacts/contracts/src/${name}.sol/${name}.json`),
  "utf8",
)) as Artifact;
const tokenArtifact = artifact("MockUSDC");
const payoutArtifact = artifact("MockPayoutToken");
const escrowArtifact = artifact("TaskEscrow");
const gasTreasuryArtifact = artifact("GasTreasuryA");

const chainA = defineChain({
  id: chainAId,
  name: "Local Chain A",
  nativeCurrency: { name: "Local ETH", symbol: "ETH", decimals: 18 },
  rpcUrls: { default: { http: [chainAUrl] } },
});
const chainB = defineChain({
  id: chainBId,
  name: "Local Chain B",
  nativeCurrency: { name: "Local ETH", symbol: "ETH", decimals: 18 },
  rpcUrls: { default: { http: [chainBUrl] } },
});
const sourcePublic = createPublicClient({ chain: chainA, transport: http(chainAUrl) });
const sourceWallet = createWalletClient({ chain: chainA, transport: http(chainAUrl) });
const destinationPublic = createPublicClient({ chain: chainB, transport: http(chainBUrl) });

assert.equal(await sourcePublic.getChainId(), chainAId);
assert.equal(await destinationPublic.getChainId(), chainBId);
const sourceAccounts = await sourceWallet.getAddresses();
const controller = sourceAccounts[0];
const requesterWallet = sourceAccounts[1];

async function login(did: string): Promise<string> {
  const response = await fetch(`${apiUrl}/api/v1/auth/dev-login`, {
    method: "POST",
    headers: { "Content-Type": "application/json", "Idempotency-Key": randomUUID() },
    body: JSON.stringify({ did }),
  });
  const payload = await response.json().catch(() => null) as Record<string, any> | null;
  assert.equal(response.status, 200, JSON.stringify(payload));
  return String(payload!.accessToken);
}

async function api(path: string, options: ApiOptions = {}): Promise<Record<string, any>> {
  const headers = new Headers({ Accept: "application/json" });
  if (options.token) headers.set("Authorization", `Bearer ${options.token}`);
  if (options.body) headers.set("Content-Type", "application/json");
  if (options.method && options.method !== "GET") headers.set("Idempotency-Key", randomUUID());
  const response = await fetch(`${apiUrl}${path}`, {
    method: options.method || "GET",
    headers,
    body: options.body ? JSON.stringify(options.body) : undefined,
  });
  const payload = await response.json().catch(() => null) as Record<string, any> | null;
  assert.ok(response.ok, `${path}: ${response.status} ${JSON.stringify(payload)}`);
  return payload!;
}

async function write(
  contract: Artifact,
  address: Address,
  functionName: string,
  args: readonly unknown[],
  account: Address,
  value?: bigint,
): Promise<Hash> {
  const hash = await sourceWallet.writeContract({
    account,
    address,
    abi: contract.abi,
    functionName,
    args,
    value,
  } as never);
  const receipt = await sourcePublic.waitForTransactionReceipt({ hash });
  assert.equal(receipt.status, "success", `${functionName} reverted on Chain A`);
  return hash;
}

const delay = (milliseconds: number) => new Promise((resolvePromise) => setTimeout(resolvePromise, milliseconds));

async function waitForJob(
  adminToken: string,
  workflowId: string,
  jobType: string,
  attemptId?: string,
): Promise<Record<string, any>> {
  for (let index = 0; index < 1200; index += 1) {
    const response = await api("/api/v1/protocol-jobs?status=&limit=500", { token: adminToken });
    const jobs = (response.items as Record<string, any>[]).filter((item) => (
      item.workflowId === workflowId
      && item.jobType === jobType
      && (!attemptId || item.attemptId === attemptId)
    ));
    const job = jobs.at(-1);
    if (job?.status === "FAILED") throw new Error(`${jobType} failed: ${job.lastError}`);
    if (job?.status === "COMPLETED") return job;
    await delay(250);
  }
  throw new Error(`Timed out waiting for ${jobType}`);
}

async function readWorkflow(workflowId: string): Promise<Record<string, any>> {
  return api(`/api/v1/workflows/${workflowId}`);
}

function firstNode(workflow: Record<string, any>): Record<string, any> {
  assert.equal(workflow.nodes.length, 1);
  return workflow.nodes[0];
}

async function coordinate(token: string, workflowId: string): Promise<Record<string, any>> {
  return api(`/api/v1/workflows/${workflowId}/coordinate`, {
    method: "POST",
    token,
    body: { maxActions: 1 },
  });
}

async function destinationTokenBalance(token: Address, wallet: Address): Promise<bigint> {
  return destinationPublic.readContract({
    address: token,
    abi: payoutArtifact.abi,
    functionName: "balanceOf",
    args: [wallet],
  }) as Promise<bigint>;
}

const requesterToken = await login("did:local:campus-requester");
const adminToken = await login("did:local:platform-admin");
const profiles = (await api("/api/v1/workflow-agents", { token: adminToken })).items as Record<string, any>[];
const kimi = profiles.find((item) => item.agent?.slug === "workflow-research-kimi-external-v2");
const builtinResearch = profiles.find((item) => item.agent?.slug === "workflow-research-b");
assert.ok(kimi, "Kimi Agent is not registered on the platform");
assert.ok(builtinResearch, "Built-in research Agent is missing");
assert.equal(kimi.active, true, "Kimi Agent is not active");
assert.equal(kimi.availability, "ONLINE", "Kimi Agent is not online");
assert.equal(kimi.gasReady, true, "Kimi Agent wallet is not Gas-ready");
assert.equal(kimi.admissionStatus, "APPROVED", "Kimi Agent admission is not approved");
assert.equal(kimi.chainStatus, "READY", "Kimi Agent home-chain route is not ready");
assert.equal(kimi.matchable, true, "Kimi Agent is not matchable");

const kimiHealth = await fetch(`${String(kimi.agent.endpoint).replace(/\/$/, "")}/health`);
assert.equal(kimiHealth.status, 200, await kimiHealth.text());
const originalBuiltinState = {
  active: Boolean(builtinResearch.active),
  availability: String(builtinResearch.availability),
  capacityAvailable: Boolean(builtinResearch.capacityAvailable),
};

let reportPath = "";
try {
  await api(`/api/v1/workflow-agents/${builtinResearch.id}`, {
    method: "PATCH",
    token: adminToken,
    body: { active: false },
  });

  const budget = parseUnits("6", 6);
  const sourceGas = parseEther("0.01");
  const created = await api("/api/v1/workflows", {
    method: "POST",
    token: requesterToken,
    body: {
      title: "Kimi local on-chain acceptance",
      objective: "Analyze local cross-chain settlement evidence and produce an auditable project-aware summary.",
      budget: "6.00",
      platformFee: "1.10",
      sourceChainId: chainAId,
      inputPayload: { acceptance: "KIMI_E2E_V1" },
      fundingMode: "ONCHAIN",
      payerAddress: requesterWallet,
      nodes: [
        {
          nodeKey: "N1",
          title: "Kimi research execution",
          description: "Execute one real Kimi research task and settle it on Chain B.",
          capability: "research",
          dependsOn: [],
          acceptanceCriteria: { requiredFields: ["summary"], minScore: 0 },
          inputPayload: { evidenceScope: "local-protocol" },
          reward: "4.20",
          riskLevel: "LOW",
          requiredApprovals: 0,
          challengePeriodSeconds: 0,
          samplingRateBps: 0,
          reviewBudget: "0",
          maxAttempts: 1,
        },
      ],
    },
  });
  const workflowId = String(created.id);
  const onchainWorkflowId = created.onchainWorkflowId as Hash;

  await write(tokenArtifact, base.contracts.sourceToken, "mint", [requesterWallet, budget], controller);
  await write(tokenArtifact, base.contracts.sourceToken, "approve", [base.contracts.taskEscrow, budget], requesterWallet);
  const lockTxHash = await write(
    escrowArtifact,
    base.contracts.taskEscrow,
    "lockBudget",
    [onchainWorkflowId, created.termsHash, base.contracts.sourceToken, budget, parseUnits("1.1", 6)],
    requesterWallet,
  );
  const sourceGasTxHash = await write(
    gasTreasuryArtifact,
    base.contracts.gasTreasury,
    "depositSourceGas",
    [onchainWorkflowId],
    requesterWallet,
    sourceGas,
  );
  await api(`/api/v1/workflows/${workflowId}/funding-confirmations`, {
    method: "POST",
    token: requesterToken,
    body: {
      walletAddress: requesterWallet,
      lockTxHash,
      sourceGasTxHash,
      sourceGasAmount: "0.01",
      verificationMode: "RPC",
    },
  });
  await waitForJob(adminToken, workflowId, "REGISTER_WORKFLOW");

  let action = await coordinate(requesterToken, workflowId);
  if (action.actions[0].action === "route_deployment_queued") {
    await waitForJob(adminToken, workflowId, "DEPLOY_ROUTE_STACK");
    action = await coordinate(requesterToken, workflowId);
  }
  assert.equal(action.actions[0].action, "node_dispatched");
  const dispatchedAttempt = firstNode(action.workflow).currentAttempt as Record<string, any>;
  assert.equal(dispatchedAttempt.agentKey, "workflow-research-kimi-external-v2");
  assert.equal(dispatchedAttempt.chainId, chainBId);
  assert.equal(String(dispatchedAttempt.homeWallet).toLowerCase(), String(kimi.homeWallet).toLowerCase());

  await waitForJob(adminToken, workflowId, "DISPATCH_ASSIGNMENT", dispatchedAttempt.id);
  await waitForJob(adminToken, workflowId, "RELAY_RESULT_PROOF", dispatchedAttempt.id);
  let current = await readWorkflow(workflowId);
  let attempt = firstNode(current).attempts.find((item: Record<string, any>) => item.id === dispatchedAttempt.id);
  assert.ok(attempt.resultHash, "Kimi result hash was not recorded");

  const persistedResultPath = resolve(runtimeDir, "agent-results", `${attempt.id}.json`);
  const persisted = JSON.parse(readFileSync(persistedResultPath, "utf8")) as Record<string, any>;
  assert.equal(persisted.runtimeEnvelope.agentId, "workflow-research-kimi-external-v2");
  assert.equal(persisted.runtimeEnvelope.attemptId, attempt.id);
  assert.ok(persisted.runtimeEnvelope.outputHash);
  assert.ok(persisted.runtimeEnvelope.signature?.value);
  const resultProofJobs = (await api("/api/v1/protocol-jobs?status=&limit=500", { token: adminToken })).items as Record<string, any>[];
  const resultProofJob = resultProofJobs.find((item) => (
    item.workflowId === workflowId
    && item.attemptId === attempt.id
    && item.jobType === "RELAY_RESULT_PROOF"
  ));
  const executionTxHash = (persisted.executionTxHash || resultProofJob?.payload?.executionTxHash) as Hash | undefined;
  assert.match(String(executionTxHash || ""), /^0x[0-9a-fA-F]{64}$/, "Kimi Chain B result transaction is missing");
  const resultReceipt = await destinationPublic.getTransactionReceipt({ hash: executionTxHash! });
  const resultTransaction = await destinationPublic.getTransaction({ hash: executionTxHash! });
  assert.equal(resultReceipt.status, "success");
  assert.equal(
    resultTransaction.from.toLowerCase(),
    String(attempt.homeWallet).toLowerCase(),
    "Kimi Chain B result transaction was not signed by the Agent wallet",
  );

  const deployments = (await api("/api/v1/route-deployments", { token: adminToken })).items as Record<string, any>[];
  const deployment = deployments.find((item) => item.routeId === attempt.routeId && item.routeVersion === attempt.routeVersion);
  assert.ok(deployment, "Kimi route deployment is missing");
  const payoutToken = deployment.contracts.payoutToken as Address;
  const balanceBefore = await destinationTokenBalance(payoutToken, attempt.homeWallet);

  action = await coordinate(requesterToken, workflowId);
  assert.equal(action.actions[0].action, "review_passed");
  await waitForJob(adminToken, workflowId, "RECORD_REVIEW", attempt.id);
  await waitForJob(adminToken, workflowId, "SYNC_REVIEW_V2", attempt.id);
  action = await coordinate(requesterToken, workflowId);
  assert.equal(action.actions[0].action, "settlement_pending");
  await waitForJob(adminToken, workflowId, "CROSS_CHAIN_SETTLEMENT", attempt.id);
  action = await coordinate(requesterToken, workflowId);
  assert.equal(action.actions[0].action, "workflow_finalization_pending");
  await waitForJob(adminToken, workflowId, "FINALIZE_WORKFLOW");

  current = await readWorkflow(workflowId);
  attempt = firstNode(current).attempts.find((item: Record<string, any>) => item.id === attempt.id);
  const balanceAfter = await destinationTokenBalance(payoutToken, attempt.homeWallet);
  assert.equal(current.status, "COMPLETED");
  assert.equal(firstNode(current).status, "SETTLED");
  assert.equal(attempt.status, "SETTLED");
  assert.equal(attempt.payoutStatus, "SWAPPED");
  assert.ok(balanceAfter > balanceBefore, "Kimi wallet did not receive payout Token");
  assert.equal(balanceAfter - balanceBefore, parseUnits("8.4", 18));
  assert.equal(current.ledgerSummary.balanced, true);

  const jobs = (await api("/api/v1/protocol-jobs?status=&limit=500", { token: adminToken })).items as Record<string, any>[];
  const messages = (await api("/api/v1/cross-chain-messages", { token: adminToken })).items as Record<string, any>[];
  const report = {
    accepted: true,
    generatedAt: new Date().toISOString(),
    workflowId,
    nodeId: firstNode(current).id,
    attemptId: attempt.id,
    agent: {
      profileId: kimi.id,
      agentId: kimi.agentId,
      agentKey: attempt.agentKey,
      did: kimi.agent.did,
      homeChainId: attempt.chainId,
      homeWallet: attempt.homeWallet,
    },
    funding: {
      payer: requesterWallet,
      lockTxHash,
      sourceGasTxHash,
      sourceGasDeposit: current.sourceGasDeposit,
      sourceGasRefunded: current.sourceGasRefunded,
    },
    taskOffer: {
      offerId: persisted.offerId,
      reservationId: persisted.runtimeEnvelope.reservationId,
      deadline: persisted.runtimeEnvelope.deadline,
      nonce: persisted.runtimeEnvelope.nonce,
    },
    result: {
      output: persisted.runtimeEnvelope.output,
      outputHash: persisted.runtimeEnvelope.outputHash,
      resultHash: attempt.resultHash,
      proofHash: attempt.proofHash,
      signature: persisted.runtimeEnvelope.signature,
      chainBTransactionHash: executionTxHash,
      chainBTransactionSender: resultTransaction.from,
      resultMessageId: persisted.resultMessageId,
      receiptBlockNumber: resultReceipt.blockNumber.toString(),
    },
    settlement: {
      status: attempt.settlementStatus,
      settlementMessageId: attempt.settlementMessageId,
      destinationTransactionHash: attempt.destinationTxHash,
      payoutTransactionHash: attempt.payoutTxHash,
      payoutStatus: attempt.payoutStatus,
      payoutAsset: attempt.payoutAsset,
      payoutAmount: formatUnits(balanceAfter - balanceBefore, 18),
      walletBalanceBefore: formatUnits(balanceBefore, 18),
      walletBalanceAfter: formatUnits(balanceAfter, 18),
    },
    finalState: {
      workflow: current.status,
      node: firstNode(current).status,
      attempt: attempt.status,
      contractSyncStatus: current.contractSyncStatus,
      ledgerBalanced: current.ledgerSummary.balanced,
    },
    relayMessages: messages.filter((item) => item.workflowId === workflowId),
    jobs: jobs.filter((item) => item.workflowId === workflowId).map((item) => ({
      jobType: item.jobType,
      attemptId: item.attemptId,
      status: item.status,
      transactionHash: item.transactionHash,
    })),
  };
  mkdirSync(runtimeDir, { recursive: true });
  reportPath = resolve(runtimeDir, "kimi-e2e-acceptance.json");
  writeFileSync(reportPath, `${JSON.stringify(report, null, 2)}\n`, "utf8");
  console.log("KIMI_E2E_ACCEPTED");
  console.log(`workflow=${workflowId} attempt=${attempt.id} payout=8.4_BTK state=SETTLED`);
  console.log(`report=${reportPath}`);
} finally {
  await api(`/api/v1/workflow-agents/${builtinResearch.id}`, {
    method: "PATCH",
    token: adminToken,
    body: originalBuiltinState,
  });
}

assert.ok(reportPath);
