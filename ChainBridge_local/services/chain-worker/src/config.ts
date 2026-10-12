import type { Address, Hex } from "viem";

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
  const signerMode = (process.env.RELAYER_SIGNER_MODE ?? "env") === "kms" ? "kms" : "env";
  const workerId = process.env.CHAIN_WORKER_ID ?? `chain-worker-${process.pid}`;
  return {
    workerId,
    apiUrl: (process.env.API_URL ?? "http://127.0.0.1:8000").replace(/\/$/, ""),
    hmacSecret: resolveWorkerHmacSecret(workerId),
    sourceRpcUrl: required("SOURCE_RPC_URL"),
    destinationRpcUrl: required("DESTINATION_RPC_URL"),
    sourceChainId: integer("SOURCE_CHAIN_ID", 11155111),
    destinationChainId: integer("DESTINATION_CHAIN_ID", 421614),
    sourceChainSelector: required("SOURCE_CHAIN_SELECTOR"),
    destinationChainSelector: required("DESTINATION_CHAIN_SELECTOR"),
    escrowAddress: required("REAL_MARKETPLACE_ESCROW_ADDRESS") as Address,
    receiverAddress: required("REAL_CCIP_RECEIVER_ADDRESS") as Address,
    sourceSettlementToken: required("SOURCE_SETTLEMENT_TOKEN") as Address,
    destinationSettlementToken: required("DESTINATION_SETTLEMENT_TOKEN") as Address,
    relayerPrivateKey: signerMode === "env" ? required("RELAYER_PRIVATE_KEY") as Hex : undefined,
    confirmations: integer("SOURCE_CONFIRMATIONS", 3),
    destinationConfirmations: integer("DESTINATION_CONFIRMATIONS", 5),
    reorgDepth: integer("CHAIN_REORG_DEPTH", 12),
    blockChunkSize: integer("CHAIN_BLOCK_CHUNK_SIZE", 1000),
    pollIntervalMs: integer("CHAIN_POLL_INTERVAL_MS", 5000),
    startSourceBlock: BigInt(process.env.SOURCE_START_BLOCK ?? "0"),
    startDestinationBlock: BigInt(process.env.DESTINATION_START_BLOCK ?? "0"),
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
