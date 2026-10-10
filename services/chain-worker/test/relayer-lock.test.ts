import assert from "node:assert/strict";
import test from "node:test";

import { Relayer } from "../src/relayer.ts";
import { StateStore } from "../src/state.ts";

// ---------------------------------------------------------------------------
// 锁仓自动化（agent 自治）回归测试
//
// 这里钉住两个曾经导致「静默失效」的坑：
//   1. escrow.settlements 是 public mapping 的自动 getter，返回值是**摊平的 8 个**，
//      不是 tuple 对象。误当对象读会得到 undefined -> NaN -> 恒判「已锁仓」，
//      于是一次都不会真正锁，而且全程不报错、不留日志。
//   2. 已锁仓时绝不能重复广播 createAndLock，否则会二次扣款。
// ---------------------------------------------------------------------------

const txHash = `0x${"1".repeat(64)}`;
const settlementId = `0x${"a".repeat(64)}`;
const lockOrderId = `0x${"c".repeat(64)}`;
const lockTermsHash = `0x${"d".repeat(64)}`;
const escrowAddress = "0x1111111111111111111111111111111111111111";
const relayerAddress = "0x2222222222222222222222222222222222222222";
const payeeAddress = "0x3333333333333333333333333333333333333333";
const tokenAddress = "0x4444444444444444444444444444444444444444";

function lockConfig(): any {
  return {
    workerId: "test-worker",
    relayerPrivateKey: `0x${"1".repeat(64)}`,
    sourceRpcUrl: "http://unused",
    escrowAddress,
    sourceSettlementToken: tokenAddress,
    startSourceBlock: 100n,
    blockChunkSize: 2000,
    maxRelayerAttempts: 3,
    relayerLeaseSeconds: 300,
    relayerHeartbeatMs: 30_000,
    relayerReceiptTimeoutMs: 1_000,
    relayerDroppedAfterMs: 600_000,
  };
}

function lockTask() {
  return {
    id: "task-lock-1",
    taskType: "lock",
    attempts: 1,
    leaseToken: "00000000-0000-0000-0000-000000000002",
    onchainSettlementId: settlementId,
    onchainOrderId: lockOrderId,
    termsHash: lockTermsHash,
    payee: payeeAddress,
    token: tokenAddress,
    payableAmount: "12.5",
  };
}

// status 是第 8 个返回值（下标 7）。Status.None === 0 表示链上尚未创建该结算。
function settlementsTuple(status: number) {
  return [lockOrderId, lockTermsHash, relayerAddress, payeeAddress, tokenAddress, 0n, 0n, status];
}

function buildRelayer(
  publicClient: Record<string, unknown>,
  walletClient: Record<string, unknown>,
  reports: Record<string, unknown>[],
  state: StateStore,
) {
  const api = {
    lease: async () => ({ task: lockTask() }),
    report: async (_id: string, payload: Record<string, unknown>) => {
      reports.push(payload);
      return {};
    },
  };
  return new Relayer(lockConfig(), state, api as any, {
    publicClient,
    walletClient,
    account: { address: relayerAddress },
  });
}

test("relayer auto-locks funds by broadcasting createAndLock with the exact escrow arguments", async () => {
  const state = new StateStore(":memory:");
  const reports: Record<string, unknown>[] = [];
  const writes: Record<string, any>[] = [];
  const publicClient = {
    readContract: async ({ functionName }: any) => {
      if (functionName === "settlements") return settlementsTuple(0);
      if (functionName === "balanceOf") return 999_000_000n;
      if (functionName === "allowance") return 999_000_000n; // 已授权，无需 approve
      throw new Error(`unexpected read ${functionName}`);
    },
    getTransactionCount: async () => 7n,
    simulateContract: async (request: any) => ({ request }),
    waitForTransactionReceipt: async () => ({ status: "success" }),
  };
  const walletClient = {
    writeContract: async (request: any) => {
      writes.push(request);
      return txHash;
    },
  };

  await buildRelayer(publicClient, walletClient, reports, state).processOne();

  assert.equal(writes.length, 1, "只应广播一笔 createAndLock");
  assert.equal(writes[0].functionName, "createAndLock");
  // 12.5 USDC -> 6 位小数 -> 12_500_000
  assert.deepEqual(writes[0].args, [
    settlementId,
    lockOrderId,
    lockTermsHash,
    payeeAddress,
    tokenAddress,
    12_500_000n,
  ]);
  assert.equal(reports.length, 1);
  assert.equal(reports[0].outcome, "submitted");
  assert.equal(reports[0].transactionHash, txHash);
  state.close();
});

test("relayer approves the escrow first when its USDC allowance is short", async () => {
  const state = new StateStore(":memory:");
  const reports: Record<string, unknown>[] = [];
  const writes: Record<string, any>[] = [];
  const publicClient = {
    readContract: async ({ functionName }: any) => {
      if (functionName === "settlements") return settlementsTuple(0);
      if (functionName === "balanceOf") return 999_000_000n;
      if (functionName === "allowance") return 0n; // 授权不足
      throw new Error(`unexpected read ${functionName}`);
    },
    getTransactionCount: async () => 7n,
    simulateContract: async (request: any) => ({ request }),
    waitForTransactionReceipt: async () => ({ status: "success" }),
  };
  const walletClient = {
    writeContract: async (request: any) => {
      writes.push(request);
      return txHash;
    },
  };

  await buildRelayer(publicClient, walletClient, reports, state).processOne();

  assert.equal(writes.length, 2, "授权不足时应先 approve 再 createAndLock");
  assert.equal(writes[0].functionName, "approve");
  assert.deepEqual(writes[0].args, [escrowAddress, 12_500_000n]);
  assert.equal(writes[1].functionName, "createAndLock");
  state.close();
});

test("relayer never double-locks a settlement that is already locked on chain", async () => {
  const state = new StateStore(":memory:");
  const reports: Record<string, unknown>[] = [];
  const publicClient = {
    // Status.Locked === 2
    readContract: async ({ functionName }: any) => {
      if (functionName === "settlements") return settlementsTuple(2);
      throw new Error(`unexpected read ${functionName}`);
    },
    getBlockNumber: async () => 500_000n,
    getLogs: async ({ fromBlock }: any) => {
      // 绝不允许全量扫描：公共 RPC 对 eth_getLogs 区间有上限，fromBlock: 0n 会直接报错。
      assert.ok(fromBlock > 0n, "getLogs 必须使用有界区间");
      return [{ transactionHash: txHash }];
    },
    waitForTransactionReceipt: async () => ({ status: "success" }),
  };
  const walletClient = {
    writeContract: async () => {
      throw new Error("must not broadcast a second lock");
    },
  };

  await buildRelayer(publicClient, walletClient, reports, state).processOne();

  assert.equal(reports.length, 1);
  assert.equal(reports[0].outcome, "submitted");
  assert.equal(reports[0].transactionHash, txHash);
  state.close();
});

test("relayer refuses to lock when its own USDC balance cannot cover the budget", async () => {
  const state = new StateStore(":memory:");
  const reports: Record<string, unknown>[] = [];
  const publicClient = {
    readContract: async ({ functionName }: any) => {
      if (functionName === "settlements") return settlementsTuple(0);
      if (functionName === "balanceOf") return 1n; // 余额远不足
      throw new Error(`unexpected read ${functionName}`);
    },
    getTransactionCount: async () => 7n,
    simulateContract: async (request: any) => ({ request }),
    waitForTransactionReceipt: async () => ({ status: "success" }),
  };
  const walletClient = {
    writeContract: async () => {
      throw new Error("must not broadcast without funds");
    },
  };

  await buildRelayer(publicClient, walletClient, reports, state).processOne();

  assert.equal(reports.length, 1);
  assert.equal(reports[0].outcome, "retry");
  assert.match(String(reports[0].error), /insufficient_lock_balance/);
  state.close();
});

test("relayer surfaces an ABI drift on settlements instead of silently skipping the lock", async () => {
  const state = new StateStore(":memory:");
  const reports: Record<string, unknown>[] = [];
  const publicClient = {
    // 模拟把摊平返回值误读成 tuple 对象的情形：下标 7 取不到 -> NaN
    readContract: async ({ functionName }: any) => {
      if (functionName === "settlements") return { status: 0 } as any;
      throw new Error(`unexpected read ${functionName}`);
    },
    waitForTransactionReceipt: async () => ({ status: "success" }),
  };
  const walletClient = {
    writeContract: async () => {
      throw new Error("must not broadcast on ABI drift");
    },
  };

  await buildRelayer(publicClient, walletClient, reports, state).processOne();

  assert.equal(reports.length, 1);
  assert.equal(reports[0].outcome, "retry");
  assert.match(String(reports[0].error), /lock_status_unreadable/);
  state.close();
});
