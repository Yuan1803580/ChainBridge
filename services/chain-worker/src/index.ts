import { createPublicClient, fallback, http, parseAbi } from "viem";

import { InternalApi } from "./api.ts";
import { loadConfig } from "./config.ts";
import { startHealthServer, type WorkerHealth } from "./health.ts";
import { Relayer } from "./relayer.ts";
import { ChainScanner, flushPendingEvents, scannerDiagnostics } from "./scanner.ts";
import { StateStore } from "./state.ts";

const erc20Abi = parseAbi(["function decimals() view returns (uint8)"]);
const config = loadConfig();
const state = new StateStore(config.databasePath);
const api = new InternalApi(config.apiUrl, config.hmacSecret, config.workerId);
/**
 * Public testnet RPC endpoints return sporadic 429/503 responses. A single endpoint therefore
 * stalls the scanner; `fallback` rotates to the next endpoint on transport errors and ranks
 * endpoints by observed latency/stability.
 */
function transportFor(urls: string[]) {
  return urls.length === 1
    ? http(urls[0], { retryCount: 3, retryDelay: 500 })
    : fallback(urls.map((url) => http(url, { retryCount: 2, retryDelay: 500 })), { rank: false });
}

const sourceClient = createPublicClient({ transport: transportFor(config.sourceRpcUrls) });
const destinationClient = createPublicClient({ transport: transportFor(config.destinationRpcUrls) });

async function validateDeployment(): Promise<{ sourceDecimals: number; destinationDecimals: number }> {
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
  if (Number(sourceDecimals) !== Number(destinationDecimals)) {
    throw new Error("Source and destination settlement token decimals must match");
  }
  return { sourceDecimals: Number(sourceDecimals), destinationDecimals: Number(destinationDecimals) };
}

const tokenDecimals = await validateDeployment();

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
  tokenDecimals: tokenDecimals.sourceDecimals,
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
  tokenDecimals: tokenDecimals.destinationDecimals,
});
const relayer = new Relayer(config, state, api);
const health: WorkerHealth = { startedAt: new Date().toISOString(), failingSteps: {} };
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

/**
 * Runs one loop step without letting its failure abort the cycle, and keeps `failingSteps` in
 * sync so `/health` reflects what is broken NOW rather than what broke once at startup.
 */
async function isolated(name: string, action: () => Promise<void>): Promise<boolean> {
  try {
    await action();
    delete health.failingSteps[name];
    return true;
  } catch (error) {
    const message = error instanceof Error ? error.message : String(error);
    const previous = health.failingSteps[name];
    health.failingSteps[name] = {
      message,
      since: previous?.since ?? new Date().toISOString(),
      consecutive: (previous?.consecutive ?? 0) + 1,
    };
    health.lastError = `${name}: ${message}`;
    await alert(name, message);
    return false;
  }
}

let stopping = false;
for (const signal of ["SIGINT", "SIGTERM"] as const) {
  process.on(signal, () => { stopping = true; });
}

/**
 * Duplicate-eventId conflicts are also raised by the flushes the scanners run internally, so the
 * alert watches the global counter rather than a single call site's return value.
 */
let seenConflicts = scannerDiagnostics.duplicateConflictCount;
async function alertOnNewConflicts(): Promise<void> {
  const total = scannerDiagnostics.duplicateConflictCount;
  if (total <= seenConflicts) return;
  const fresh = total - seenConflicts;
  seenConflicts = total;
  await alert(
    "duplicate_event_conflict",
    `${fresh} chain event(s) rejected with HTTP 409 - the same eventId already exists with `
      + `different chain data; dead-lettered without retry. `
      + `last=${scannerDiagnostics.lastDuplicateConflictDetail ?? "n/a"}`,
  );
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
  await alertOnNewConflicts();
  health.lastCycleAt = new Date().toISOString();
  if (!stopping) await new Promise((resolve) => setTimeout(resolve, config.pollIntervalMs));
}
healthServer.close();
state.close();
console.log("chain-worker stopped");
