import { createPublicClient, http, parseAbi } from "viem";

import { InternalApi } from "./api.ts";
import { loadConfig } from "./config.ts";
import { startHealthServer, type WorkerHealth } from "./health.ts";
import { Relayer } from "./relayer.ts";
import { ChainScanner, flushPendingEvents } from "./scanner.ts";
import { StateStore } from "./state.ts";

const erc20Abi = parseAbi(["function decimals() view returns (uint8)"]);
const config = loadConfig();
const state = new StateStore(config.databasePath);
const api = new InternalApi(config.apiUrl, config.hmacSecret, config.workerId);
const sourceClient = createPublicClient({ transport: http(config.sourceRpcUrl) });
const destinationClient = createPublicClient({ transport: http(config.destinationRpcUrl) });

async function validateDeployment(): Promise<void> {
  const [sourceChainId, destinationChainId, escrowCode, receiverCode, sourceDecimals, destinationDecimals] =
    await Promise.all([
      sourceClient.getChainId(),
      destinationClient.getChainId(),
      sourceClient.getCode({ address: config.escrowAddress }),
      destinationClient.getCode({ address: config.receiverAddress }),
      sourceClient.readContract({ address: config.sourceSettlementToken, abi: erc20Abi, functionName: "decimals" }),
      destinationClient.readContract({
        address: config.destinationSettlementToken,
        abi: erc20Abi,
        functionName: "decimals",
      }),
    ]);
  if (sourceChainId !== config.sourceChainId) throw new Error("SOURCE_RPC_URL chain ID mismatch");
  if (destinationChainId !== config.destinationChainId) throw new Error("DESTINATION_RPC_URL chain ID mismatch");
  if (!escrowCode || escrowCode === "0x") throw new Error("Escrow contract is not deployed on the source chain");
  if (!receiverCode || receiverCode === "0x") throw new Error("Receiver contract is not deployed on the destination chain");
  if (Number(sourceDecimals) !== 6 || Number(destinationDecimals) !== 6) {
    throw new Error("Configured settlement tokens must use 6 decimals");
  }
}

await validateDeployment();

const scannerOptions = {
  eventMaxAttempts: config.eventMaxAttempts,
  eventRetryBaseMs: config.eventRetryBaseMs,
};
const sourceScanner = new ChainScanner({
  ...scannerOptions,
  chainKey: "source",
  chainId: config.sourceChainId,
  chainSelector: config.sourceChainSelector,
  contractAddress: config.escrowAddress,
  confirmations: config.confirmations,
  reorgDepth: config.reorgDepth,
  chunkSize: config.blockChunkSize,
  startBlock: config.startSourceBlock,
  client: sourceClient,
  state,
  api,
});
const destinationScanner = new ChainScanner({
  ...scannerOptions,
  chainKey: "destination",
  chainId: config.destinationChainId,
  chainSelector: config.destinationChainSelector,
  contractAddress: config.receiverAddress,
  confirmations: config.destinationConfirmations,
  reorgDepth: config.reorgDepth,
  chunkSize: config.blockChunkSize,
  startBlock: config.startDestinationBlock,
  client: destinationClient,
  state,
  api,
});
const relayer = new Relayer(config, state, api);
const health: WorkerHealth = { startedAt: new Date().toISOString() };
const healthServer = startHealthServer(config.healthPort, state, health);

async function alert(kind: string, message: string): Promise<void> {
  console.error(new Date().toISOString(), kind, message);
  if (!config.alertWebhookUrl) return;
  try {
    await fetch(config.alertWebhookUrl, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ service: "chain-worker", workerId: config.workerId, kind, message }),
    });
  } catch (error) {
    console.error("alert_webhook_failed", error instanceof Error ? error.message : error);
  }
}

async function isolated(name: string, action: () => Promise<void>): Promise<boolean> {
  try {
    await action();
    return true;
  } catch (error) {
    const message = error instanceof Error ? error.message : String(error);
    health.lastError = `${name}: ${message}`;
    await alert(name, message);
    return false;
  }
}

let stopping = false;
for (const signal of ["SIGINT", "SIGTERM"] as const) {
  process.on(signal, () => { stopping = true; });
}

console.log(`chain-worker ${config.workerId} started; health port ${config.healthPort}`);
while (!stopping) {
  await isolated("pending_events", async () => {
    const outcome = await flushPendingEvents(
      state,
      api,
      config.eventMaxAttempts,
      config.eventRetryBaseMs,
    );
    if (outcome.dead > 0) await alert("dead_letter", `${outcome.dead} event(s) moved to dead-letter`);
  });
  if (await isolated("source_scan", () => sourceScanner.scan())) health.sourceScanAt = new Date().toISOString();
  if (await isolated("destination_scan", () => destinationScanner.scan())) {
    health.destinationScanAt = new Date().toISOString();
  }
  await isolated("relayer_balance", async () => {
    health.relayerBalanceWei = (await sourceClient.getBalance({ address: relayer.address })).toString();
  });
  if (await isolated("relayer", () => relayer.processOne())) health.relayerAt = new Date().toISOString();
  await isolated("stalled_check", async () => {
    const stalled = await api.checkStalled(config.stalledAfterSeconds);
    if (stalled.count > 0) await alert("ccip_stalled", `${stalled.count} settlement(s) require review`);
  });
  health.lastCycleAt = new Date().toISOString();
  if (!stopping) await new Promise((resolve) => setTimeout(resolve, config.pollIntervalMs));
}
healthServer.close();
state.close();
console.log("chain-worker stopped");
