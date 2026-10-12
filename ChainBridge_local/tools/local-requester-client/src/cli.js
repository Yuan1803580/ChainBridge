#!/usr/bin/env node
import { existsSync } from "node:fs";
import { readFile } from "node:fs/promises";
import path from "node:path";
import process from "node:process";
import readline from "node:readline/promises";
import { randomUUID } from "node:crypto";

import { CoordinatorClient, PlatformClient } from "./api.js";
import {
  assertTrustedDeployment,
  balanceSnapshot,
  chainContext,
  executeFunding,
  verifyFaucetEvidence,
} from "./contracts.js";
import { loadConfig, ROOT_DIR } from "./config.js";
import { exportEvidence } from "./evidence.js";
import { planSummary, validatePlan, verifyTermsHash } from "./planning.js";
import { createWallet, unlockWallet, walletAddress } from "./wallet.js";
import { newRequestId, parseArgs, readJson, sleep, writeJsonAtomic } from "./utils.js";

const TERMINAL_STATUSES = new Set(["COMPLETED", "FAILED", "FAILED_REFUNDED", "CANCELLED"]);

function usage() {
  console.log(`Local Requester Client

Commands:
  wallet:create                 Create an encrypted EVM keystore
  wallet:show                   Show the requester address
  deployment:check             Verify the pinned chain and contracts
  deployment:trust             Pin the current LOCAL_EXPERIMENT deployment
  faucet [--token 100 --native 1]
  plan --scenario scenario.json
  fund [--auto-confirm]
  run [--max-cycles 180]
  monitor
  evidence
  experiment --scenario scenario.json --auto-confirm

Secrets are read from .env or environment variables. Passwords and private keys are never accepted as command-line arguments.`);
}

async function loadState(config, required = true) {
  if (!existsSync(config.stateFile)) {
    if (required) throw new Error("Requester state does not exist; create a wallet first");
    return {};
  }
  return readJson(config.stateFile);
}

async function saveState(config, state) {
  state.updatedAt = new Date().toISOString();
  await writeJsonAtomic(config.stateFile, state);
}

async function confirmAction(config, message, autoConfirm) {
  if (autoConfirm) {
    if (config.mode !== "LOCAL_EXPERIMENT") throw new Error("--auto-confirm is allowed only in LOCAL_EXPERIMENT mode");
    return;
  }
  if (!process.stdin.isTTY) throw new Error("Interactive confirmation is required; use --auto-confirm only for local experiments");
  const terminal = readline.createInterface({ input: process.stdin, output: process.stdout });
  const answer = await terminal.question(`${message} [yes/no] `);
  terminal.close();
  if (answer.trim().toLowerCase() !== "yes") throw new Error("Cancelled by user");
}

async function publicAndTrusted(config) {
  const platform = new PlatformClient(config);
  const publicConfig = await platform.publicConfig();
  const trustedDeployment = await assertTrustedDeployment(publicConfig, config.trustedDeploymentFile);
  return { platform, publicConfig, trustedDeployment };
}

async function commandWalletCreate(config, args) {
  const created = await createWallet(config.walletFile, { overwrite: Boolean(args.overwrite) });
  const state = {
    schemaVersion: "local-requester-state/v1",
    runId: randomUUID(),
    walletAddress: created.address,
    createdAt: new Date().toISOString(),
    balances: {},
  };
  await saveState(config, state);
  console.log(JSON.stringify({ address: created.address, keystore: created.keystore }, null, 2));
}

async function commandWalletShow(config) {
  console.log(JSON.stringify({ address: await walletAddress(config.walletFile), keystore: config.walletFile }, null, 2));
}

async function commandDeploymentCheck(config) {
  const { publicConfig, trustedDeployment } = await publicAndTrusted(config);
  console.log(JSON.stringify({ trusted: true, publicConfig, trustedDeployment }, null, 2));
}

async function commandDeploymentTrust(config, args) {
  const platform = new PlatformClient(config);
  const publicConfig = await platform.publicConfig();
  await confirmAction(config, "Pin the currently reported local deployment? Verify these addresses out of band first.", args["auto-confirm"]);
  const trusted = Object.fromEntries(
    ["mode", "chainId", "deploymentId", "sourceToken", "taskEscrow", "gasTreasury"].map((key) => [key, publicConfig[key]]),
  );
  await writeJsonAtomic(config.trustedDeploymentFile, trusted);
  await assertTrustedDeployment(publicConfig, config.trustedDeploymentFile);
  console.log(JSON.stringify({ trusted: true, file: config.trustedDeploymentFile, deploymentId: trusted.deploymentId }, null, 2));
}

async function commandFaucet(config, args) {
  const state = await loadState(config);
  const address = await walletAddress(config.walletFile);
  if (address.toLowerCase() !== state.walletAddress.toLowerCase()) throw new Error("State wallet does not match encrypted keystore");
  const { platform, publicConfig, trustedDeployment } = await publicAndTrusted(config);
  const before = await balanceSnapshot(publicConfig, address);
  const verificationBaseline = state.balances?.beforeFaucet || before;
  const requestId = state.faucet?.requestId || newRequestId("faucet");
  const response = await platform.faucet({
    requestId,
    walletAddress: address,
    tokenAmount: String(args.token || "100"),
    nativeAmount: String(args.native || "1"),
  });
  const verified = await verifyFaucetEvidence(publicConfig, address, response, verificationBaseline);
  state.trustedDeployment = trustedDeployment;
  state.publicConfig = publicConfig;
  state.faucet = { requestId, response, verifiedAt: new Date().toISOString() };
  state.balances = { ...(state.balances || {}), beforeFaucet: verificationBaseline, afterFaucet: verified.after };
  await saveState(config, state);
  console.log(JSON.stringify({ verified: true, requestId, balances: verified.after, transactions: verified.receipts }, null, 2));
}

async function commandPlan(config, args) {
  if (!args.scenario) throw new Error("--scenario is required");
  const scenarioFile = path.resolve(process.cwd(), args.scenario);
  const scenario = JSON.parse(await readFile(scenarioFile, "utf8"));
  const coordinator = new CoordinatorClient(config);
  const conversation = await coordinator.chat(scenario);
  const totals = validatePlan(conversation);
  const state = await loadState(config);
  state.scenarioFile = scenarioFile;
  state.scenario = scenario;
  state.conversationId = conversation.conversationId;
  state.conversation = conversation;
  state.planTotals = Object.fromEntries(Object.entries(totals).map(([key, value]) => [key, value.toString()]));
  delete state.workflow;
  delete state.funding;
  await saveState(config, state);
  console.log(JSON.stringify(planSummary(conversation), null, 2));
}

async function commandFund(config, args) {
  const state = await loadState(config);
  if (!state.conversationId || !state.conversation) throw new Error("No confirmed plan; run plan first");
  const address = await walletAddress(config.walletFile);
  const { platform, publicConfig, trustedDeployment } = await publicAndTrusted(config);
  await confirmAction(
    config,
    `Sign exact approval, lock ${state.conversation.draft.budget} USDC, and deposit ${publicConfig.sourceGasAmount} ETH on Chain A?`,
    args["auto-confirm"],
  );

  const coordinator = new CoordinatorClient(config);
  let workflow = state.workflow;
  if (!workflow) {
    const created = await coordinator.createWorkflow(state.conversationId, address);
    workflow = created.workflow;
    verifyTermsHash(state.conversation, workflow);
    state.workflow = workflow;
    state.workflowId = workflow.id;
    await saveState(config, state);
  } else {
    verifyTermsHash(state.conversation, workflow);
  }
  if (workflow.fundingWallet.toLowerCase() !== address.toLowerCase()) throw new Error("Workflow payer does not match requester wallet");
  if (workflow.fundingStatus === "CONFIRMED") {
    console.log(JSON.stringify({ alreadyConfirmed: true, workflowId: workflow.id }, null, 2));
    return;
  }
  state.trustedDeployment = trustedDeployment;
  state.publicConfig = publicConfig;
  state.balances = state.balances || {};
  state.balances.beforeFunding ||= await balanceSnapshot(publicConfig, address);
  await saveState(config, state);

  const signer = await unlockWallet(config.walletFile);
  const funding = await executeFunding(
    publicConfig,
    signer,
    workflow,
    state.funding || {},
    async (progress) => {
      state.funding = { ...(state.funding || {}), ...progress };
      await saveState(config, state);
    },
  );
  state.funding = funding;
  await saveState(config, state);
  const confirmation = await platform.confirmFunding(workflow.id, {
    walletAddress: address,
    termsHash: workflow.termsHash,
    approveTxHash: funding.approveTxHash,
    lockTxHash: funding.lockTxHash,
    sourceGasTxHash: funding.sourceGasTxHash,
    sourceGasAmount: funding.sourceGasAmount,
    deploymentId: publicConfig.deploymentId,
    verificationMode: "RPC",
  });
  state.workflow = confirmation.workflow;
  state.balances.afterFunding = await balanceSnapshot(publicConfig, address);
  await saveState(config, state);
  console.log(JSON.stringify({ confirmed: true, workflowId: workflow.id, funding }, null, 2));
}

function workflowSummary(workflow) {
  return {
    id: workflow.id,
    referenceCode: workflow.referenceCode,
    status: workflow.status,
    fundingStatus: workflow.fundingStatus,
    contractSyncStatus: workflow.contractSyncStatus,
    paidAmount: workflow.paidAmount,
    refundedAmount: workflow.refundedAmount,
    nodes: workflow.nodes.map((node) => ({
      nodeKey: node.nodeKey,
      status: node.status,
      currentAttemptId: node.currentAttemptId,
      attempts: node.attempts.map((attempt) => ({
        attemptNumber: attempt.attemptNumber,
        agentKey: attempt.agentKey,
        chainId: attempt.chainId,
        status: attempt.status,
        payoutStatus: attempt.payoutStatus,
      })),
    })),
  };
}

async function commandMonitor(config) {
  const state = await loadState(config);
  if (!state.workflowId) throw new Error("No workflow has been created");
  const workflow = await new PlatformClient(config).workflow(state.workflowId);
  state.workflow = workflow;
  await saveState(config, state);
  console.log(JSON.stringify(workflowSummary(workflow), null, 2));
  return workflow;
}

async function commandRun(config, args) {
  const state = await loadState(config);
  if (!state.workflowId || !state.conversationId) throw new Error("No funded Coordinator workflow exists");
  const maxCycles = Number(args["max-cycles"] || config.maxRunCycles);
  const platform = new PlatformClient(config);
  let workflow = await platform.workflow(state.workflowId);
  if (workflow.fundingStatus !== "CONFIRMED") throw new Error("Workflow funding is not confirmed");
  for (let cycle = 0; cycle < maxCycles && !TERMINAL_STATUSES.has(workflow.status); cycle += 1) {
    workflow = await platform.workflow(state.workflowId);
    const action = "waiting_for_coordinator_core";
    console.log(`[${cycle + 1}/${maxCycles}] ${workflow.status} - ${action}`);
    state.workflow = workflow;
    state.lastAction = [{ action }];
    await saveState(config, state);
    if (!TERMINAL_STATUSES.has(workflow.status)) await sleep(config.pollIntervalMs);
  }
  if (!TERMINAL_STATUSES.has(workflow.status)) throw new Error(`Workflow did not finish within ${maxCycles} cycles`);
  console.log(JSON.stringify(workflowSummary(workflow), null, 2));
  return workflow;
}

async function commandEvidence(config) {
  const state = await loadState(config);
  if (!state.workflowId || !state.publicConfig) throw new Error("No funded workflow evidence is available");
  const workflow = await new PlatformClient(config).workflow(state.workflowId);
  const result = await exportEvidence(config, state.publicConfig, state, workflow);
  state.evidenceFile = result.file;
  await saveState(config, state);
  console.log(JSON.stringify({ exported: true, file: result.file, workflowStatus: workflow.status }, null, 2));
}

async function commandExperiment(config, args) {
  if (!args.scenario) throw new Error("--scenario is required");
  if (!args["auto-confirm"]) throw new Error("experiment requires --auto-confirm in LOCAL_EXPERIMENT mode");
  if (!existsSync(config.walletFile)) await commandWalletCreate(config, {});
  await commandFaucet(config, args);
  await commandPlan(config, args);
  await commandFund(config, args);
  await commandRun(config, args);
  await commandEvidence(config);
}

async function main() {
  const args = parseArgs(process.argv.slice(2));
  const command = args._[0];
  if (!command || command === "help" || args.help) {
    usage();
    return;
  }
  const config = await loadConfig();
  const commands = {
    "wallet:create": commandWalletCreate,
    "wallet:show": commandWalletShow,
    "deployment:check": commandDeploymentCheck,
    "deployment:trust": commandDeploymentTrust,
    faucet: commandFaucet,
    plan: commandPlan,
    fund: commandFund,
    run: commandRun,
    monitor: commandMonitor,
    evidence: commandEvidence,
    experiment: commandExperiment,
  };
  const handler = commands[command];
  if (!handler) throw new Error(`Unknown command: ${command}`);
  await handler(config, args);
}

main().catch((error) => {
  console.error(`Requester Client failed: ${error.message}`);
  process.exitCode = 1;
});
