import type { Address, Hex } from "viem";
import { readFileSync } from "node:fs";

function required(name: string): string {
  const value = process.env[name]?.trim();
  if (!value) throw new Error(`${name} is required`);
  return value;
}

function integer(name: string, fallback: number): number {
  const value = Number(process.env[name] ?? fallback);
  if (!Number.isInteger(value) || value < 0) throw new Error(`${name} must be a non-negative integer`);
  return value;
}

/**
 * Resolve an ordered RPC endpoint list. `<NAME>S` (plural, comma separated) takes precedence
 * and enables failover; `<NAME>` remains supported for single-endpoint deployments.
 */
function rpcUrls(singularName: string): string[] {
  const plural = process.env[`${singularName}S`]?.trim();
  const urls = plural
    ? plural.split(",").map((item) => item.trim()).filter(Boolean)
    : [required(singularName)];
  if (urls.length === 0) throw new Error(`${singularName}S must contain at least one URL`);
  for (const url of urls) {
    if (!/^https?:\/\//i.test(url)) throw new Error(`${singularName}S contains a non-HTTP endpoint: ${url}`);
  }
  return urls;
}

/**
 * Start blocks must be pinned to the contract deployment block in production.
 *
 * A value of 0 makes the scanner backfill millions of blocks, which exhausts public RPC rate
 * limits and means the cursor never reaches chain head - settlements stall indefinitely.
 */
function startBlock(name: string, isProduction: boolean): bigint {
  const raw = process.env[name]?.trim();
  const value = BigInt(raw && raw.length > 0 ? raw : "0");
  if (isProduction && value === 0n) {
    throw new Error(
      `${name} must be pinned to the contract deployment block in production; `
        + "a zero start block forces a full-chain backfill that never catches up to head",
    );
  }
  return value;
}

export function resolveWorkerHmacSecret(
  workerId: string,
  env: NodeJS.ProcessEnv = process.env,
): string {
  const dedicated = env.CHAIN_WORKER_HMAC_SECRET?.trim();
  if (dedicated) return dedicated;

  for (const item of (env.LISTENER_HMAC_KEYS ?? "").split(",")) {
    const separator = item.indexOf(":");
    if (separator < 1) continue;
    const configuredId = item.slice(0, separator).trim();
    const secret = item.slice(separator + 1).trim();
    if (configuredId === workerId && secret) return secret;
  }

  if ((env.APP_ENV ?? "development") !== "production") {
    const legacy = env.LISTENER_HMAC_SECRET?.trim();
    if (legacy) return legacy;
  }
  throw new Error(
    `No HMAC secret configured for CHAIN_WORKER_ID=${workerId}; set CHAIN_WORKER_HMAC_SECRET `
      + "or add the worker to LISTENER_HMAC_KEYS",
  );
}

export type WorkerConfig = {
  workerId: string;
  apiUrl: string;
  hmacSecret: string;
  sourceRpcUrl: string;
  destinationRpcUrl: string;
  sourceRpcUrls: string[];
  destinationRpcUrls: string[];
  sourceChainId: number;
  destinationChainId: number;
  sourceChainSelector: string;
  destinationChainSelector: string;
  escrowAddress: Address;
  receiverAddress: Address;
  sourceSettlementToken: Address;
  destinationSettlementToken: Address;
  relayerPrivateKey?: Hex;
  confirmations: number;
  destinationConfirmations: number;
  reorgDepth: number;
  blockChunkSize: number;
  pollIntervalMs: number;
  startSourceBlock: bigint;
  startDestinationBlock: bigint;
  databasePath: string;
  maxRelayerAttempts: number;
  relayerLeaseSeconds: number;
  relayerHeartbeatMs: number;
  relayerReceiptTimeoutMs: number;
  relayerDroppedAfterMs: number;
  eventMaxAttempts: number;
  eventRetryBaseMs: number;
  stalledAfterSeconds: number;
  healthPort: number;
  alertWebhookUrl?: string;
  signerMode: "env" | "kms";
};

export function loadConfig(): WorkerConfig {
  const isProduction = (process.env.APP_ENV ?? "development") === "production";
  const signerMode = (process.env.RELAYER_SIGNER_MODE ?? "env") === "kms" ? "kms" : "env";
  const workerId = process.env.CHAIN_WORKER_ID ?? `chain-worker-${process.pid}`;
  const sourceEndpoints = rpcUrls("SOURCE_RPC_URL");
  const destinationEndpoints = rpcUrls("DESTINATION_RPC_URL");
  const privateKeyFile = process.env.RELAYER_PRIVATE_KEY_FILE?.trim();
  if (privateKeyFile && process.env.RELAYER_PRIVATE_KEY?.trim()) {
    throw new Error("Configure only one of RELAYER_PRIVATE_KEY_FILE or RELAYER_PRIVATE_KEY");
  }
  const relayerPrivateKey = signerMode === "env"
    ? (privateKeyFile ? readFileSync(privateKeyFile, "utf8").trim() : required("RELAYER_PRIVATE_KEY"))
    : undefined;
  if (relayerPrivateKey && !/^0x[0-9a-fA-F]{64}$/.test(relayerPrivateKey)) {
    throw new Error("Relayer private key must be a 32-byte 0x-prefixed hex value");
  }
  return {
    workerId,
    apiUrl: (process.env.API_URL ?? "http://127.0.0.1:8000").replace(/\/$/, ""),
    hmacSecret: resolveWorkerHmacSecret(workerId),
    sourceRpcUrl: sourceEndpoints[0],
    destinationRpcUrl: destinationEndpoints[0],
    sourceRpcUrls: sourceEndpoints,
    destinationRpcUrls: destinationEndpoints,
    sourceChainId: integer("SOURCE_CHAIN_ID", 11155111),
    destinationChainId: integer("DESTINATION_CHAIN_ID", 421614),
    sourceChainSelector: required("SOURCE_CHAIN_SELECTOR"),
    destinationChainSelector: required("DESTINATION_CHAIN_SELECTOR"),
    escrowAddress: required("REAL_MARKETPLACE_ESCROW_ADDRESS") as Address,
    receiverAddress: required("REAL_CCIP_RECEIVER_ADDRESS") as Address,
    sourceSettlementToken: required("SOURCE_SETTLEMENT_TOKEN") as Address,
    destinationSettlementToken: required("DESTINATION_SETTLEMENT_TOKEN") as Address,
    relayerPrivateKey: relayerPrivateKey as Hex | undefined,
    confirmations: integer("SOURCE_CONFIRMATIONS", 3),
    destinationConfirmations: integer("DESTINATION_CONFIRMATIONS", 5),
    reorgDepth: integer("CHAIN_REORG_DEPTH", 12),
    blockChunkSize: integer("CHAIN_BLOCK_CHUNK_SIZE", 1000),
    pollIntervalMs: integer("CHAIN_POLL_INTERVAL_MS", 5000),
    startSourceBlock: startBlock("SOURCE_START_BLOCK", isProduction),
    startDestinationBlock: startBlock("DESTINATION_START_BLOCK", isProduction),
    databasePath: process.env.CHAIN_WORKER_DB ?? "./data/chain-worker.db",
    maxRelayerAttempts: integer("RELAYER_MAX_ATTEMPTS", 8),
    relayerLeaseSeconds: integer("RELAYER_LEASE_SECONDS", 300),
    relayerHeartbeatMs: integer("RELAYER_HEARTBEAT_MS", 30_000),
    relayerReceiptTimeoutMs: integer("RELAYER_RECEIPT_TIMEOUT_MS", 120_000),
    relayerDroppedAfterMs: integer("RELAYER_DROPPED_AFTER_MS", 600_000),
    eventMaxAttempts: integer("CHAIN_EVENT_MAX_ATTEMPTS", 8),
    eventRetryBaseMs: integer("CHAIN_EVENT_RETRY_BASE_MS", 1_000),
    stalledAfterSeconds: integer("CCIP_STALLED_AFTER_SECONDS", 3600),
    healthPort: integer("CHAIN_WORKER_HEALTH_PORT", 9090),
    alertWebhookUrl: process.env.CHAIN_ALERT_WEBHOOK_URL?.trim() || undefined,
    signerMode,
  };
}
