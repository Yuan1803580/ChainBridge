import assert from "node:assert/strict";
import { createHmac } from "node:crypto";
import test from "node:test";

import { signRequest } from "../src/api.ts";
import { resolveWorkerHmacSecret } from "../src/config.ts";
import { ChainScanner, eventId, mapSourceLog } from "../src/scanner.ts";
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

test("submission journal prevents a task from being rebound to another transaction", () => {
  const state = new StateStore(":memory:");
  const raw = `0x${"3".repeat(128)}` as `0x${string}`;
  state.savePreparedSubmission("task-bound", txHash, 7, raw, "worker-a");
  const prepared = state.getSubmissionRecord("task-bound");
  assert.equal(prepared?.status, "prepared");
  assert.equal(prepared?.rawTransaction, raw);
  assert.equal(prepared?.workerId, "worker-a");
  assert.throws(
    () => state.saveSubmission("task-bound", `0x${"2".repeat(64)}`, 8),
    /submission_conflict/,
  );
  state.markSubmissionBroadcast("task-bound", txHash);
  assert.equal(state.getSubmissionRecord("task-bound")?.status, "broadcast");
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

test("relayer rebroadcasts the same prepared raw transaction after a crash", async () => {
  const state = new StateStore(":memory:");
  const raw = `0x${"4".repeat(128)}` as `0x${string}`;
  state.savePreparedSubmission("task-prepared", txHash, 2, raw, "worker-a");
  let rawBroadcasts = 0;
  const api = {
    lease: async () => ({ task: {
      id: "task-prepared",
      taskType: "confirm_release",
      attempts: 1,
      leaseToken: "00000000-0000-0000-0000-000000000003",
      onchainSettlementId: settlementId,
      ccipMessageId: `0x${"b".repeat(64)}`,
    } }),
    report: async () => ({}),
  };
  const relayer = new Relayer({
    workerId: "worker-a",
    sourceRpcUrl: "http://unused",
    escrowAddress: "0x1111111111111111111111111111111111111111",
    relayerLeaseSeconds: 300,
    relayerHeartbeatMs: 30_000,
    relayerReceiptTimeoutMs: 1_000,
    relayerDroppedAfterMs: 600_000,
    maxRelayerAttempts: 3,
  } as any, state, api as any, {
    publicClient: { waitForTransactionReceipt: async () => ({ status: "success" }) },
    walletClient: {
      sendRawTransaction: async ({ serializedTransaction }: any) => {
        assert.equal(serializedTransaction, raw);
        rawBroadcasts += 1;
        return txHash;
      },
      writeContract: async () => { throw new Error("must not create a replacement transaction"); },
    },
    account: { address: "0x2222222222222222222222222222222222222222" },
  });
  await relayer.processOne();
  assert.equal(rawBroadcasts, 1);
  assert.equal(state.getSubmission("task-prepared"), undefined);
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
