import assert from "node:assert/strict";
import { randomUUID } from "node:crypto";
import { mkdir, readFile, rm, writeFile } from "node:fs/promises";
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

import { ExperimentEventRelay } from "../services/chain-worker/src/experiment-v2-relay.ts";
import {
  addChainD,
  AGENT_PRIVATE_KEYS,
  CONTROLLER_PRIVATE_KEY,
  RELAY_PRIVATE_KEY,
  type DestinationDeployment,
  type ExperimentV2Deployment,
} from "./experiment-v2-deploy-lib.ts";

const REQUESTER_PRIVATE_KEY = "0x8b3a350cf5c34c9194ca85829a2df0ec3153be0318b5e2d3348e872092edffba" as Hex;
const id = (value: string) => keccak256(stringToHex(value));
const projectRoot = resolve(import.meta.dirname, "..");
const manifestPath = resolve(process.env.EXPERIMENT_V2_MANIFEST ?? ".runtime/experiment-v2/deployment.json");
const relayDatabasePath = resolve(process.env.EXPERIMENT_V2_RELAY_DB ?? "data/experiment-v2-relay-acceptance.db");
const resultsPath = resolve(process.env.EXPERIMENT_V2_RESULTS ?? ".runtime/experiment-v2/results/latest.json");

type Artifact = { abi: Abi; bytecode: Hex };
type Runtime = { chainId: number; key: string; rpcUrl: string; publicClient: PublicClient; wallets: Map<string, WalletClient> };
type TransactionMetric = {
  phase: string;
  chainId: number;
  hash: Hex;
  blockNumber: string;
  gasUsed: string;
  durationMs: number;
};

const metrics: TransactionMetric[] = [];

async function artifact(name: string): Promise<Artifact> {
  const path = resolve(projectRoot, "hardhat-artifacts", "contracts", "src", `${name}.sol`, `${name}.json`);
  return JSON.parse(await readFile(path, "utf8")) as Artifact;
}

function runtime(key: string, chainId: number, rpcUrl: string): Runtime {
  return { key, chainId, rpcUrl, publicClient: createPublicClient({ transport: http(rpcUrl) }), wallets: new Map() };
}

function wallet(context: Runtime, privateKey: Hex): WalletClient {
  const existing = context.wallets.get(privateKey);
  if (existing) return existing;
  const account = privateKeyToAccount(privateKey);
  const client = createWalletClient({ account, transport: http(context.rpcUrl) });
  context.wallets.set(privateKey, client);
  return client;
}

async function write(
  context: Runtime,
  privateKey: Hex,
  contractName: string,
  address: Address,
  functionName: string,
  args: readonly unknown[],
  phase: string,
): Promise<Hex> {
  const compiled = await artifact(contractName);
  const account = privateKeyToAccount(privateKey);
  const started = performance.now();
  const hash = await wallet(context, privateKey).writeContract({
    account,
    address,
    abi: compiled.abi,
    functionName,
    args,
  } as any);
  const receipt = await context.publicClient.waitForTransactionReceipt({ hash });
  if (receipt.status !== "success") throw new Error(`${phase} reverted on chain ${context.chainId}`);
  metrics.push({
    phase,
    chainId: context.chainId,
    hash,
    blockNumber: receipt.blockNumber.toString(),
    gasUsed: receipt.gasUsed.toString(),
    durationMs: Math.round((performance.now() - started) * 100) / 100,
  });
  return hash;
}

async function read(
  context: Runtime,
  contractName: string,
  address: Address,
  functionName: string,
  args: readonly unknown[] = [],
): Promise<any> {
  const compiled = await artifact(contractName);
  return context.publicClient.readContract({ address, abi: compiled.abi, functionName, args } as any);
}

async function balance(context: Runtime, token: Address, owner: Address): Promise<bigint> {
  return BigInt(await read(context, "MockUSDC", token, "balanceOf", [owner]));
}

async function totalSupply(context: Runtime, token: Address): Promise<bigint> {
  return BigInt(await read(context, "MockUSDC", token, "totalSupply"));
}

async function sumBalances(context: Runtime, token: Address, owners: Address[]): Promise<bigint> {
  let sum = 0n;
  for (const owner of owners) sum += await balance(context, token, owner);
  return sum;
}

function relayOptions(crashAfterEnqueue = false) {
  return {
    manifestPath,
    databasePath: relayDatabasePath,
    privateKey: RELAY_PRIVATE_KEY,
    pollIntervalMs: 50,
    deliveryDelayMs: 0,
    reorgDepth: 6,
    blockChunkSize: 200,
    maxAttempts: 3,
    retryBaseMs: 1,
    crashAfterEnqueue,
    onTransaction: (record: {
      chainId: number;
      hash: Hex;
      blockNumber: bigint;
      gasUsed: bigint;
      durationMs: number;
      functionName: string;
    }) => metrics.push({
      phase: `relay-${record.functionName}`,
      chainId: record.chainId,
      hash: record.hash,
      blockNumber: record.blockNumber.toString(),
      gasUsed: record.gasUsed.toString(),
      durationMs: record.durationMs,
    }),
  };
}

async function runRelay(relay: ExperimentEventRelay, cycles = 1) {
  const total = { scanned: 0, delivered: 0, rejected: 0, failed: 0 };
  for (let index = 0; index < cycles; index += 1) {
    const result = await relay.cycle();
    total.scanned += result.scanned;
    total.delivered += result.delivered;
    total.rejected += result.rejected;
    total.failed += result.failed;
  }
  return total;
}

async function assignAndRelay(
  relay: ExperimentEventRelay,
  source: Runtime,
  sourceContracts: ExperimentV2Deployment["source"]["contracts"],
  destination: DestinationDeployment,
  nodeId: Hex,
  attemptId: Hex,
  attemptNumber: number,
  reward: bigint,
) {
  await write(source, CONTROLLER_PRIVATE_KEY, "WorkflowEngine", sourceContracts.workflowEngine, "assignNode", [
    nodeId,
    attemptId,
    destination.routeKey,
    destination.routeVersion,
    destination.agentAddress,
  ], `assign-${destination.key}-${attemptNumber}`);
  await write(source, CONTROLLER_PRIVATE_KEY, "ExperimentEscrow", sourceContracts.experimentEscrow, "reserve", [
    id("experiment:v2:workflow"),
    nodeId,
    attemptId,
    reward,
  ], `reserve-${destination.key}-${attemptNumber}`);
  await write(source, CONTROLLER_PRIVATE_KEY, "ExperimentDispatchSender", sourceContracts.dispatchSender, "sendAssignment", [
    nodeId,
    attemptId,
    attemptNumber,
    destination.agentDidHash,
    reward,
  ], `dispatch-${destination.key}-${attemptNumber}`);
  return runRelay(relay, 1);
}

async function submitResultAndRelay(
  relay: ExperimentEventRelay,
  destination: DestinationDeployment,
  context: Runtime,
  attemptId: Hex,
  resultHash: Hex,
  proofHash: Hex,
  phase: string,
) {
  const privateKey = AGENT_PRIVATE_KEYS[destination.chainId];
  await write(context, privateKey, "RemoteWorkBoard", destination.contracts.remoteWorkBoard, "startWork", [attemptId], `${phase}-start`);
  await write(context, privateKey, "RemoteWorkBoard", destination.contracts.remoteWorkBoard, "submitResultV2", [
    attemptId,
    resultHash,
    proofHash,
  ], `${phase}-submit-result`);
  return runRelay(relay, 1);
}

async function settle(
  relay: ExperimentEventRelay,
  source: Runtime,
  target: Runtime,
  sourceContracts: ExperimentV2Deployment["source"]["contracts"],
  destination: DestinationDeployment,
  nodeId: Hex,
  attemptId: Hex,
  attemptNumber: number,
  sourceSpend: bigint,
  label: string,
) {
  const sourceBlock = await source.publicClient.getBlock();
  const targetBlock = await target.publicClient.getBlock();
  const bridgeAmount = BigInt(await read(source, "ExperimentConstantProductAMM", sourceContracts.sourceAMM, "quoteExactInput", [
    sourceContracts.sourceToken,
    sourceSpend,
  ]));
  const payoutAmount = BigInt(await read(target, "ExperimentConstantProductAMM", destination.contracts.destinationAMM, "quoteExactInput", [
    destination.contracts.bridgeAsset,
    bridgeAmount,
  ]));
  await write(source, CONTROLLER_PRIVATE_KEY, "ExperimentEscrow", sourceContracts.experimentEscrow, "prepareSettlement", [[
    attemptId,
    attemptNumber,
    bridgeAmount,
    sourceBlock.timestamp + 86_400n,
    destination.agentAddress,
    destination.contracts.payoutToken,
    payoutAmount,
    targetBlock.timestamp + 86_400n,
  ]], `${label}-prepare-settlement`);
  const settlementMessageId = await read(source, "ExperimentEscrow", sourceContracts.experimentEscrow, "settlementMessageIds", [attemptId]) as Hex;
  await write(source, CONTROLLER_PRIVATE_KEY, "WorkflowEngine", sourceContracts.workflowEngine, "markSettlementPending", [
    nodeId,
    attemptId,
    settlementMessageId,
  ], `${label}-settlement-pending`);
  await assert.rejects(write(source, CONTROLLER_PRIVATE_KEY, "WorkflowEngine", sourceContracts.workflowEngine, "markSettled", [
    nodeId,
    attemptId,
  ], `${label}-premature-settle`));
  await runRelay(relay, 1);
  await runRelay(relay, 1);
  assert.equal(await read(source, "ExperimentSourceVault", sourceContracts.sourceVault, "isAttemptConfirmed", [attemptId]), true);
  await write(source, CONTROLLER_PRIVATE_KEY, "WorkflowEngine", sourceContracts.workflowEngine, "markSettled", [
    nodeId,
    attemptId,
  ], `${label}-settled`);
  return { bridgeAmount, payoutAmount, settlementMessageId };
}

await rm(relayDatabasePath, { force: true });
await rm(`${relayDatabasePath}-wal`, { force: true });
await rm(`${relayDatabasePath}-shm`, { force: true });
let deployment = JSON.parse(await readFile(manifestPath, "utf8")) as ExperimentV2Deployment;
assert.deepEqual(deployment.chains.map((item) => item.chainId), [31337, 31338, 31339]);
const source = runtime("A", deployment.sourceChainId, deployment.sourceRpcUrl);
assert.equal(await source.publicClient.getChainId(), 31337);
const targetB = runtime("B", 31338, deployment.destinations["31338"].rpcUrl);
const targetC = runtime("C", 31339, deployment.destinations["31339"].rpcUrl);
const requester = privateKeyToAccount(REQUESTER_PRIVATE_KEY);
const sourceContracts = deployment.source.contracts;
const workflowId = id("experiment:v2:workflow");
const nodeOne = id("experiment:v2:node:1");
const nodeTwo = id("experiment:v2:node:2");
const attemptB = id("experiment:v2:attempt:b:1");
const attemptC = id("experiment:v2:attempt:c:1");
const attemptD = id("experiment:v2:attempt:d:2");
const budget = 30_000_000n;
const rewardB = 8_000_000n;
const rewardD = 7_000_000n;
const platformFee = 1_000_000n;

await write(source, CONTROLLER_PRIVATE_KEY, "WorkflowEngine", sourceContracts.workflowEngine, "createWorkflow", [
  workflowId,
  requester.address,
  id("experiment:v2:terms"),
], "workflow-create");
await write(source, CONTROLLER_PRIVATE_KEY, "WorkflowEngine", sourceContracts.workflowEngine, "registerNode", [
  workflowId,
  nodeOne,
  id("experiment:v2:criteria:1"),
  [],
], "node-1-register");
await write(source, CONTROLLER_PRIVATE_KEY, "WorkflowEngine", sourceContracts.workflowEngine, "registerNode", [
  workflowId,
  nodeTwo,
  id("experiment:v2:criteria:2"),
  [nodeOne],
], "node-2-register");
await write(source, CONTROLLER_PRIVATE_KEY, "MockUSDC", sourceContracts.sourceToken, "mint", [requester.address, budget], "budget-mint");
await write(source, REQUESTER_PRIVATE_KEY, "MockUSDC", sourceContracts.sourceToken, "approve", [sourceContracts.experimentEscrow, budget], "budget-approve");
await write(source, REQUESTER_PRIVATE_KEY, "ExperimentEscrow", sourceContracts.experimentEscrow, "lockBudget", [workflowId, budget, platformFee], "budget-lock");

let crashRelay = new ExperimentEventRelay(relayOptions(true));
await write(source, CONTROLLER_PRIVATE_KEY, "WorkflowEngine", sourceContracts.workflowEngine, "assignNode", [
  nodeOne,
  attemptB,
  deployment.destinations["31338"].routeKey,
  1,
  deployment.destinations["31338"].agentAddress,
], "assign-b-1");
await write(source, CONTROLLER_PRIVATE_KEY, "ExperimentEscrow", sourceContracts.experimentEscrow, "reserve", [workflowId, nodeOne, attemptB, rewardB], "reserve-b-1");
await write(source, CONTROLLER_PRIVATE_KEY, "ExperimentDispatchSender", sourceContracts.dispatchSender, "sendAssignment", [
  nodeOne,
  attemptB,
  1,
  deployment.destinations["31338"].agentDidHash,
  rewardB,
], "dispatch-b-1");
await assert.rejects(crashRelay.cycle(), /FAULT_INJECTION_CRASH_AFTER_ENQUEUE/);
crashRelay.close();
let relay = new ExperimentEventRelay(relayOptions(false));
await runRelay(relay, 1);
assert.equal(await read(targetB, "RemoteWorkBoard", deployment.destinations["31338"].contracts.remoteWorkBoard, "currentAttemptByNode", [nodeOne]), attemptB);
relay.close();
relay = new ExperimentEventRelay(relayOptions(false));
await runRelay(relay, 1);

const resultB = id("experiment:v2:result:b");
const proofB = id("experiment:v2:proof:b");
await submitResultAndRelay(relay, deployment.destinations["31338"], targetB, attemptB, resultB, proofB, "b-1");
await write(source, CONTROLLER_PRIVATE_KEY, "CoordinatorReview", sourceContracts.coordinatorReview, "recordReview", [
  attemptB,
  id("experiment:v2:criteria:1"),
  resultB,
  proofB,
  1,
  id("PASS"),
  id("experiment:v2:review:b"),
], "review-b-pass");
await write(source, CONTROLLER_PRIVATE_KEY, "WorkflowEngine", sourceContracts.workflowEngine, "markSucceeded", [nodeOne, attemptB], "node-1-success");
const settlementB = await settle(relay, source, targetB, sourceContracts, deployment.destinations["31338"], nodeOne, attemptB, 1, rewardB, "b-1");
assert.equal(Number((await read(source, "WorkflowEngine", sourceContracts.workflowEngine, "nodes", [nodeTwo]))[7]), 2);

await assignAndRelay(relay, source, sourceContracts, deployment.destinations["31339"], nodeTwo, attemptC, 1, rewardD);
await write(targetC, AGENT_PRIVATE_KEYS[31339], "RemoteWorkBoard", deployment.destinations["31339"].contracts.remoteWorkBoard, "startWork", [attemptC], "c-1-start");
await write(source, CONTROLLER_PRIVATE_KEY, "WorkflowEngine", sourceContracts.workflowEngine, "markAttemptFailed", [nodeTwo, attemptC], "c-1-timeout");
await write(source, CONTROLLER_PRIVATE_KEY, "ExperimentEscrow", sourceContracts.experimentEscrow, "releaseReservation", [attemptC], "c-1-release-reservation");
assert.equal(await balance(targetC, deployment.destinations["31339"].contracts.payoutToken, deployment.destinations["31339"].agentAddress), 0n);

deployment = await addChainD(manifestPath);
assert.deepEqual(deployment.chains.map((item) => item.chainId), [31337, 31338, 31339, 31340]);
const targetD = runtime("D", 31340, deployment.destinations["31340"].rpcUrl);
await assignAndRelay(relay, source, sourceContracts, deployment.destinations["31340"], nodeTwo, attemptD, 2, rewardD);

await write(targetC, AGENT_PRIVATE_KEYS[31339], "RemoteWorkBoard", deployment.destinations["31339"].contracts.remoteWorkBoard, "submitResultV2", [
  attemptC,
  id("experiment:v2:late-result:c"),
  id("experiment:v2:late-proof:c"),
], "c-1-late-result");
const staleOutcome = await runRelay(relay, 1);
assert.equal(staleOutcome.rejected, 1, "late C result must be rejected as a stale attempt");

const resultD = id("experiment:v2:result:d");
const proofD = id("experiment:v2:proof:d");
await submitResultAndRelay(relay, deployment.destinations["31340"], targetD, attemptD, resultD, proofD, "d-2");
await write(source, CONTROLLER_PRIVATE_KEY, "CoordinatorReview", sourceContracts.coordinatorReview, "recordReview", [
  attemptD,
  id("experiment:v2:criteria:2"),
  resultD,
  proofD,
  1,
  id("PASS"),
  id("experiment:v2:review:d"),
], "review-d-pass");
await write(source, CONTROLLER_PRIVATE_KEY, "WorkflowEngine", sourceContracts.workflowEngine, "markSucceeded", [nodeTwo, attemptD], "node-2-success");
const settlementD = await settle(relay, source, targetD, sourceContracts, deployment.destinations["31340"], nodeTwo, attemptD, 2, rewardD, "d-2");

await write(source, CONTROLLER_PRIVATE_KEY, "ExperimentEscrow", sourceContracts.experimentEscrow, "payPlatformFee", [workflowId, platformFee], "platform-fee");
await write(source, REQUESTER_PRIVATE_KEY, "ExperimentEscrow", sourceContracts.experimentEscrow, "refundRemainder", [workflowId], "budget-refund");
relay.close();

const expectedRefund = budget - rewardB - rewardD - platformFee;
assert.equal(await balance(source, sourceContracts.sourceToken, requester.address), expectedRefund);
assert.equal(await balance(source, sourceContracts.sourceToken, privateKeyToAccount(RELAY_PRIVATE_KEY).address), platformFee);
assert.equal(await balance(targetB, deployment.destinations["31338"].contracts.payoutToken, deployment.destinations["31338"].agentAddress), settlementB.payoutAmount);
assert.equal(await balance(targetD, deployment.destinations["31340"].contracts.payoutToken, deployment.destinations["31340"].agentAddress), settlementD.payoutAmount);
assert.equal(await balance(targetC, deployment.destinations["31339"].contracts.payoutToken, deployment.destinations["31339"].agentAddress), 0n);
assert.equal(await read(source, "ExperimentSourceVault", sourceContracts.sourceVault, "lockedBacking"), settlementB.bridgeAmount + settlementD.bridgeAmount);

const controller = privateKeyToAccount(CONTROLLER_PRIVATE_KEY).address;
const treasury = privateKeyToAccount(RELAY_PRIVATE_KEY).address;
const sourceTokenConserved = await sumBalances(source, sourceContracts.sourceToken, [
  controller,
  requester.address,
  treasury,
  sourceContracts.sourceAMM,
  sourceContracts.experimentEscrow,
]);
const sourceBridgeConserved = await sumBalances(source, sourceContracts.bridgeAsset, [
  controller,
  sourceContracts.sourceAMM,
  sourceContracts.sourceVault,
]);
assert.equal(sourceTokenConserved, await totalSupply(source, sourceContracts.sourceToken));
assert.equal(sourceBridgeConserved, await totalSupply(source, sourceContracts.bridgeAsset));
for (const destination of Object.values(deployment.destinations)) {
  const context = destination.chainId === 31338 ? targetB : destination.chainId === 31339 ? targetC : targetD;
  assert.equal(await sumBalances(context, destination.contracts.bridgeAsset, [
    controller,
    destination.contracts.destinationAMM,
    destination.contracts.destinationVault,
  ]), await totalSupply(context, destination.contracts.bridgeAsset));
  assert.equal(await sumBalances(context, destination.contracts.payoutToken, [
    controller,
    destination.contracts.destinationAMM,
    destination.agentAddress,
  ]), await totalSupply(context, destination.contracts.payoutToken));
}

const workflowState = await read(source, "WorkflowEngine", sourceContracts.workflowEngine, "workflows", [workflowId]);
assert.equal(workflowState[3], 2);
assert.equal(workflowState[4], false);
const gasByChain: Record<string, string> = {};
for (const metric of metrics) gasByChain[String(metric.chainId)] = (
  BigInt(gasByChain[String(metric.chainId)] ?? "0") + BigInt(metric.gasUsed)
).toString();
const result = {
  schemaVersion: 1,
  runId: randomUUID(),
  completedAt: new Date().toISOString(),
  environment: "independent-local-evm",
  chains: deployment.chains.map((chain) => ({ key: chain.key, chainId: chain.chainId, rpcUrl: chain.rpcUrl })),
  workflow: {
    workflowId,
    nodeOne: { attemptId: attemptB, chainId: 31338, status: "SETTLED", payout: settlementB.payoutAmount.toString() },
    nodeTwo: {
      failedAttempt: { attemptId: attemptC, chainId: 31339, status: "STALE_REJECTED", payout: "0" },
      successfulAttempt: { attemptId: attemptD, chainId: 31340, status: "SETTLED", payout: settlementD.payoutAmount.toString() },
    },
  },
  funds: {
    budget: budget.toString(),
    sourceSpent: (rewardB + rewardD).toString(),
    platformFee: platformFee.toString(),
    refund: expectedRefund.toString(),
    sourceBacking: (settlementB.bridgeAmount + settlementD.bridgeAmount).toString(),
    conserved: true,
  },
  faultEvidence: {
    crashAfterEnqueueRecovered: true,
    relayRestartDidNotDuplicate: true,
    staleAttemptRejected: true,
    paymentRequiredBeforeSettled: true,
  },
  metrics: {
    measuredTransactions: metrics.length,
    gasByChain,
    transactions: metrics,
  },
};
await mkdir(dirname(resultsPath), { recursive: true });
await writeFile(resultsPath, JSON.stringify(result, null, 2), "utf8");
console.log(JSON.stringify({ event: "EXPERIMENT_V2_ACCEPTED", resultsPath, summary: result }, null, 2));
