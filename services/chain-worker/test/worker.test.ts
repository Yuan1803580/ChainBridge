import assert from "node:assert/strict";
import { createHmac } from "node:crypto";
import test from "node:test";

import { ApiError, signRequest } from "../src/api.ts";
import { resolveWorkerHmacSecret } from "../src/config.ts";
import { AgentIntentStore, canonicalUtcTimestamp } from "../src/intent-store.ts";
import { ChainScanner, eventId, flushPendingEvents, mapSourceLog, scannerDiagnostics } from "../src/scanner.ts";
import { Relayer } from "../src/relayer.ts";
import { StateStore } from "../src/state.ts";

const txHash = `0x${"1".repeat(64)}`;
const settlementId = `0x${"a".repeat(64)}`;

test("HMAC signature covers timestamp, path, and exact body", () => {
  const body = '{"eventId":"one"}';
  const expected = `sha256=${createHmac("sha256", "secret")
    .update(`POST\n100\nnonce-1\n/api/v1/integration/settlement-events\n${body}`)
    .digest("hex")}`;
  assert.equal(
    signRequest("secret", "POST", "100", "nonce-1", "/api/v1/integration/settlement-events", body),
    expected,
  );
});

test("worker selects only its dedicated HMAC secret", () => {
  assert.equal(resolveWorkerHmacSecret("worker-b", {
    APP_ENV: "production",
    LISTENER_HMAC_KEYS: "worker-a:secret-a,worker-b:secret-b",
  }), "secret-b");
  assert.equal(resolveWorkerHmacSecret("worker-b", {
    APP_ENV: "production",
    CHAIN_WORKER_HMAC_SECRET: "container-secret",
    LISTENER_HMAC_KEYS: "worker-b:api-secret",
  }), "container-secret");
  assert.throws(
    () => resolveWorkerHmacSecret("unknown", { APP_ENV: "production", LISTENER_HMAC_KEYS: "worker-a:a" }),
    /No HMAC secret configured/,
  );
});

test("Agent intent storage reuses the exact first intent across retries", async () => {
  const runtimeDir = `.runtime/intent-store-test-${process.pid}-${Date.now()}`;
  const store = new AgentIntentStore(runtimeDir);
  const first = await store.getOrCreate("request-1", async () => ({
    requestId: "request-1",
    observedPendingNonce: 7,
    observedAt: "2026-08-05T01:02:03.123000Z",
  }));
  const retry = await store.getOrCreate("request-1", async () => ({
    requestId: "request-1",
    observedPendingNonce: 99,
    observedAt: "2026-08-05T02:00:00Z",
  }));
  assert.deepEqual(retry, first);
  assert.equal(retry.observedPendingNonce, 7);
});

test("Worker timestamps use the canonical Python serialization form", () => {
  assert.equal(canonicalUtcTimestamp(new Date("2026-08-05T01:02:03.000Z")), "2026-08-05T01:02:03Z");
  assert.equal(canonicalUtcTimestamp(new Date("2026-08-05T01:02:03.123Z")), "2026-08-05T01:02:03.123000Z");
});

test("state persists cursors, pending events, and relayer submissions", () => {
  const state = new StateStore(":memory:");
  assert.equal(state.getCursor("source", 7n), 7n);
  state.setCursor("source", 12n);
  assert.equal(state.getCursor("source", 0n), 12n);
  assert.equal(state.getStoredCursor("source"), 12n);
  assert.equal(state.getStoredCursor("destination"), undefined);
  state.enqueueEvent({ eventId: "1:event:0", eventType: "funds_locked" });
  state.enqueueEvent({ eventId: "1:event:0", eventType: "funds_locked" });
  assert.equal(state.pendingEvents().length, 1);
  state.failEvent("1:event:0", "temporary", 1, 0);
  assert.equal(state.deadLetters()[0].attempts, 1);
  assert.equal(state.replayDeadLetter("1:event:0"), true);
  state.acknowledgeEvent("1:event:0");
  assert.equal(state.pendingEvents().length, 0);
  state.saveSubmission("task", txHash);
  assert.equal(state.getSubmission("task"), txHash);
  state.clearSubmission("task");
  assert.equal(state.getSubmission("task"), undefined);
  state.close();
});

test("source log mapping uses deterministic event IDs and USDC decimals", () => {
  const payload = mapSourceLog(
    {
      eventName: "FundsLocked",
      transactionHash: txHash,
      blockHash: `0x${"b".repeat(64)}`,
      logIndex: 4,
      blockNumber: 10n,
      args: { settlementId, amount: 250000n },
    },
    11155111,
    3,
    "16015286601757825753",
  );
  assert.equal(payload.eventId, eventId(11155111, txHash, 4));
  assert.equal(payload.amount, "0.25");
  assert.equal(payload.eventType, "funds_locked");
});

test("source log mapping uses configured CCIP-BnM decimals", () => {
  const payload = mapSourceLog(
    {
      eventName: "FundsLocked",
      transactionHash: txHash,
      blockHash: `0x${"b".repeat(64)}`,
      logIndex: 5,
      blockNumber: 11n,
      args: { settlementId, amount: 250000000000000000n },
    },
    11155111,
    3,
    "16015286601757825753",
    18,
  );
  assert.equal(payload.amount, "0.25");
});

test("scanner advances cursor only after API acknowledgement", async () => {
  const successfulState = new StateStore(":memory:");
  const log = {
    eventName: "FundsLocked",
    transactionHash: txHash,
    logIndex: 0,
    blockNumber: 10n,
    args: { settlementId, amount: 250000n },
  };
  const client = {
    getBlockNumber: async () => 12n,
    getLogs: async ({ event }: any) => event.name === "FundsLocked" ? [log] : [],
  };
  const accepted: Record<string, unknown>[] = [];
  const api = { reconcile: async (payload: Record<string, unknown>) => { accepted.push(payload); return {}; } };
  const scanner = new ChainScanner({
    chainKey: "source",
    chainId: 11155111,
    chainSelector: "16015286601757825753",
    contractAddress: "0x1111111111111111111111111111111111111111",
    confirmations: 3,
    reorgDepth: 0,
    chunkSize: 100,
    startBlock: 10n,
    client: client as any,
    state: successfulState,
    api: api as any,
  });
  await scanner.scan();
  assert.equal(accepted.length, 1);
  assert.equal(successfulState.getCursor("source", 0n), 11n);
  successfulState.close();

  const failedState = new StateStore(":memory:");
  const failedScanner = new ChainScanner({
    chainKey: "source",
    chainId: 11155111,
    chainSelector: "16015286601757825753",
    contractAddress: "0x1111111111111111111111111111111111111111",
    confirmations: 3,
    reorgDepth: 0,
    chunkSize: 100,
    startBlock: 10n,
    client: client as any,
    state: failedState,
    api: { reconcile: async () => { throw new Error("API unavailable"); } } as any,
  });
  await failedScanner.scan();
  assert.equal(failedState.getCursor("source", 10n), 10n);
  assert.equal(failedState.metrics().pendingEvents, 1);
  failedState.close();
});

test("relayer resumes a persisted transaction without broadcasting twice", async () => {
  const state = new StateStore(":memory:");
  state.saveSubmission("task-1", txHash);
  const reports: Record<string, unknown>[] = [];
  const api = {
    lease: async () => ({ task: {
      id: "task-1",
      taskType: "confirm_release",
      attempts: 1,
      leaseToken: "00000000-0000-0000-0000-000000000001",
      onchainSettlementId: settlementId,
      ccipMessageId: `0x${"b".repeat(64)}`,
    } }),
    report: async (_id: string, payload: Record<string, unknown>) => { reports.push(payload); return {}; },
  };
  const config: any = {
    workerId: "test-worker",
    relayerPrivateKey: `0x${"1".repeat(64)}`,
    sourceRpcUrl: "http://unused",
    escrowAddress: "0x1111111111111111111111111111111111111111",
    maxRelayerAttempts: 3,
    relayerLeaseSeconds: 300,
    relayerHeartbeatMs: 30_000,
    relayerReceiptTimeoutMs: 1_000,
    relayerDroppedAfterMs: 600_000,
  };
  const publicClient = {
    waitForTransactionReceipt: async () => ({ status: "success" }),
  };
  const walletClient = {
    writeContract: async () => { throw new Error("must not broadcast"); },
  };
  const relayer = new Relayer(config, state, api as any, {
    publicClient,
    walletClient,
    account: { address: "0x2222222222222222222222222222222222222222" },
  });
  await relayer.processOne();
  assert.equal(reports.length, 1);
  assert.equal(reports[0].outcome, "submitted");
  assert.equal(reports[0].transactionHash, txHash);
  assert.equal(state.getSubmission("task-1"), undefined);
  state.close();
});

test("scanner reports a previously confirmed event when its block is reorganized", async () => {
  const state = new StateStore(":memory:");
  const oldBlockHash = `0x${"b".repeat(64)}`;
  const newBlockHash = `0x${"c".repeat(64)}`;
  let includeLog = true;
  const client = {
    getBlockNumber: async () => 12n,
    getBlock: async () => ({ hash: newBlockHash }),
    getLogs: async ({ event }: any) => {
      if (!includeLog || event.name !== "FundsLocked") return [];
      return [{
        eventName: "FundsLocked",
        transactionHash: txHash,
        blockHash: oldBlockHash,
        logIndex: 0,
        blockNumber: 10n,
        args: {
          settlementId,
          token: "0x1111111111111111111111111111111111111111",
          amount: 250000n,
        },
      }];
    },
  };
  const orphaned: Record<string, unknown>[] = [];
  const api = {
    reconcile: async () => ({}),
    orphan: async (payload: Record<string, unknown>) => { orphaned.push(payload); return {}; },
  };
  const scanner = new ChainScanner({
    chainKey: "source",
    chainId: 11155111,
    chainSelector: "16015286601757825753",
    contractAddress: "0x1111111111111111111111111111111111111111",
    confirmations: 3,
    reorgDepth: 12,
    chunkSize: 100,
    startBlock: 10n,
    client: client as any,
    state,
    api: api as any,
  });
  await scanner.scan();
  includeLog = false;
  await scanner.scan();
  assert.equal(orphaned.length, 1);
  assert.equal(orphaned[0].observedBlockHash, oldBlockHash);
  assert.equal(orphaned[0].canonicalBlockHash, newBlockHash);
  state.close();
});

test("relayer heartbeats while waiting for a receipt", async () => {
  const state = new StateStore(":memory:");
  state.saveSubmission("task-heartbeat", txHash, 1);
  let heartbeats = 0;
  const leaseToken = "00000000-0000-0000-0000-000000000002";
  const api = {
    lease: async () => ({ task: {
      id: "task-heartbeat",
      taskType: "confirm_release",
      attempts: 1,
      leaseToken,
      onchainSettlementId: settlementId,
      ccipMessageId: `0x${"d".repeat(64)}`,
    } }),
    heartbeat: async () => { heartbeats += 1; return {}; },
    report: async () => ({}),
  };
  const relayer = new Relayer({
    workerId: "test-worker",
    sourceRpcUrl: "http://unused",
    escrowAddress: "0x1111111111111111111111111111111111111111",
    relayerLeaseSeconds: 300,
    relayerHeartbeatMs: 1,
    relayerReceiptTimeoutMs: 1_000,
    relayerDroppedAfterMs: 600_000,
    maxRelayerAttempts: 3,
  } as any, state, api as any, {
    publicClient: {
      waitForTransactionReceipt: async () => {
        await new Promise((resolve) => setTimeout(resolve, 10));
        return { status: "success" };
      },
    },
    walletClient: {},
    account: { address: "0x2222222222222222222222222222222222222222" },
  });
  await relayer.processOne();
  assert.ok(heartbeats > 0);
  state.close();
});

test("scanner refuses to advance when strict decoding silently drops a log", async () => {
  const state = new StateStore(":memory:");
  const before = scannerDiagnostics.abiMismatchCount;
  // Strict decoding yields nothing (simulating an `indexed` mismatch against the deployed
  // contract) while the raw topic0 probe still sees the log.
  const client = {
    getBlockNumber: async () => 12n,
    getBlock: async () => ({ hash: `0x${"c".repeat(64)}` }),
    getLogs: async () => [],
    request: async () => [{
      transactionHash: txHash,
      logIndex: "0x0",
      blockNumber: "0xa",
      topics: [`0x${"d".repeat(64)}`, settlementId],
    }],
  };
  const scanner = new ChainScanner({
    chainKey: "source",
    chainId: 11155111,
    chainSelector: "16015286601757825753",
    contractAddress: "0x1111111111111111111111111111111111111111",
    confirmations: 3,
    reorgDepth: 12,
    chunkSize: 100,
    startBlock: 10n,
    client: client as any,
    state,
    api: { reconcile: async () => ({}), orphan: async () => ({}) } as any,
  });

  await assert.rejects(() => scanner.scan(), /Event ABI mismatch/);
  assert.equal(scannerDiagnostics.abiMismatchCount, before + 1);
  // The cursor must stay put so the range is re-scanned once the ABI is corrected.
  assert.equal(state.getStoredCursor("source"), undefined);
  state.close();
});

test("scanner keeps working when the raw ABI probe is unavailable", async () => {
  const state = new StateStore(":memory:");
  const before = scannerDiagnostics.abiMismatchCount;
  const client = {
    getBlockNumber: async () => 12n,
    getBlock: async () => ({ hash: `0x${"c".repeat(64)}` }),
    getLogs: async ({ event }: any) => {
      if (event.name !== "FundsLocked") return [];
      return [{
        eventName: "FundsLocked",
        transactionHash: txHash,
        blockHash: `0x${"b".repeat(64)}`,
        logIndex: 0,
        blockNumber: 10n,
        args: { settlementId, token: "0x1111111111111111111111111111111111111111", amount: 250000n },
      }];
    },
    request: async () => { throw new Error("raw eth_getLogs topics filter not supported"); },
  };
  const reconciled: Record<string, unknown>[] = [];
  const scanner = new ChainScanner({
    chainKey: "source",
    chainId: 11155111,
    chainSelector: "16015286601757825753",
    contractAddress: "0x1111111111111111111111111111111111111111",
    confirmations: 3,
    reorgDepth: 12,
    chunkSize: 100,
    startBlock: 10n,
    client: client as any,
    state,
    api: {
      reconcile: async (payload: Record<string, unknown>) => { reconciled.push(payload); return {}; },
      orphan: async () => ({}),
    } as any,
  });

  await scanner.scan();
  assert.equal(reconciled.length, 1);
  assert.equal(scannerDiagnostics.abiMismatchCount, before);
  state.close();
});

test("a 409 duplicate-eventId conflict is dead-lettered immediately and counted", async () => {
  const state = new StateStore(":memory:");
  state.enqueueEvent({ eventId: "11155111:0xabc:0", blockNumber: 10, blockHash: "0xdead" }, "source");
  const beforeConflicts = scannerDiagnostics.duplicateConflictCount;

  const api = {
    reconcile: async () => {
      throw new ApiError(
        "eventId was already used with different chain data",
        409,
        "/api/v1/integration/settlement-events",
      );
    },
  };

  const outcome = await flushPendingEvents(state, api as any, 8, 1);

  assert.equal(outcome.conflicts, 1, "the conflict must be reported to the alerting path");
  // maxAttempts is overridden to 1 for conflicts: retrying can never succeed.
  assert.equal(outcome.dead, 1, "a conflict must skip the backoff budget and dead-letter at once");
  assert.equal(state.metrics().pendingEvents, 0);
  assert.equal(state.metrics().deadLetters, 1);
  assert.equal(scannerDiagnostics.duplicateConflictCount, beforeConflicts + 1);
  assert.match(String(scannerDiagnostics.lastDuplicateConflictDetail), /11155111:0xabc:0/);
  state.close();
});

test("a benign idempotent replay is acknowledged and counted separately from conflicts", async () => {
  const state = new StateStore(":memory:");
  state.enqueueEvent({ eventId: "11155111:0xfeed:1", blockNumber: 11, blockHash: "0xbeef" }, "source");
  const beforeReplays = scannerDiagnostics.duplicateReplayCount;
  const beforeConflicts = scannerDiagnostics.duplicateConflictCount;

  const api = { reconcile: async () => ({ duplicate: true, event: {} }) };
  const outcome = await flushPendingEvents(state, api as any, 8, 1);

  assert.equal(outcome.acknowledged, 1);
  assert.equal(outcome.duplicates, 1);
  assert.equal(outcome.conflicts, 0, "HTTP 200 duplicate:true is the healthy idempotency path");
  assert.equal(scannerDiagnostics.duplicateReplayCount, beforeReplays + 1);
  assert.equal(scannerDiagnostics.duplicateConflictCount, beforeConflicts);
  assert.equal(state.metrics().pendingEvents, 0);
  assert.equal(state.metrics().deadLetters, 0);
  state.close();
});

test("a transient non-409 failure keeps its retry budget", async () => {
  const state = new StateStore(":memory:");
  state.enqueueEvent({ eventId: "11155111:0x0bad:2", blockNumber: 12, blockHash: "0xcafe" }, "source");
  const beforeConflicts = scannerDiagnostics.duplicateConflictCount;

  const api = {
    reconcile: async () => {
      throw new ApiError("upstream unavailable", 503, "/api/v1/integration/settlement-events");
    },
  };
  const outcome = await flushPendingEvents(state, api as any, 8, 1);

  assert.equal(outcome.failed, 1);
  assert.equal(outcome.dead, 0, "a 503 must stay retryable");
  assert.equal(outcome.conflicts, 0);
  assert.equal(scannerDiagnostics.duplicateConflictCount, beforeConflicts);
  assert.equal(state.metrics().pendingEvents, 1);
  state.close();
});
