import { resolve } from "node:path";

import type { Hex } from "viem";

import { ExperimentEventRelay } from "./experiment-v2-relay.ts";

function integer(name: string, fallback: number): number {
  const value = Number(process.env[name] ?? fallback);
  if (!Number.isInteger(value) || value < 0) throw new Error(`${name} must be a non-negative integer`);
  return value;
}

const relay = new ExperimentEventRelay({
  manifestPath: resolve(process.env.EXPERIMENT_V2_MANIFEST ?? ".runtime/experiment-v2/deployment.json"),
  databasePath: resolve(process.env.EXPERIMENT_V2_RELAY_DB ?? "data/experiment-v2-relay.db"),
  privateKey: (process.env.EXPERIMENT_V2_RELAY_PRIVATE_KEY
    ?? "0x59c6995e998f97a5a0044966f0945389dc9e86dae88c7a8412f4603b6b78690d") as Hex,
  pollIntervalMs: integer("EXPERIMENT_V2_RELAY_POLL_MS", 1_000),
  deliveryDelayMs: integer("EXPERIMENT_V2_RELAY_DELAY_MS", 0),
  reorgDepth: integer("EXPERIMENT_V2_REORG_DEPTH", 12),
  blockChunkSize: integer("EXPERIMENT_V2_BLOCK_CHUNK_SIZE", 500),
  maxAttempts: integer("EXPERIMENT_V2_RELAY_MAX_ATTEMPTS", 8),
  retryBaseMs: integer("EXPERIMENT_V2_RELAY_RETRY_MS", 1_000),
  failTargetChainId: process.env.EXPERIMENT_V2_FAIL_TARGET_CHAIN
    ? integer("EXPERIMENT_V2_FAIL_TARGET_CHAIN", 0)
    : undefined,
  reversePending: process.env.EXPERIMENT_V2_REVERSE_PENDING === "1",
  crashAfterEnqueue: process.env.EXPERIMENT_V2_CRASH_AFTER_ENQUEUE === "1",
});

for (const signal of ["SIGINT", "SIGTERM"] as const) process.on(signal, () => relay.stop());

try {
  if (process.env.EXPERIMENT_V2_RELAY_ONCE === "1") {
    console.log(JSON.stringify(await relay.cycle()));
  } else {
    await relay.run();
  }
} finally {
  relay.close();
}
