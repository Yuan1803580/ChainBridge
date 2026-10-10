import {
  createPublicClient,
  http,
  parseAbi,
  parseAbiItem,
  parseUnits,
  type Hex,
} from "viem";

import type { InternalApi } from "./api.ts";
import type { WorkerConfig } from "./config.ts";
import { createRelayerSigner } from "./signer.ts";
import type { StateStore, Submission } from "./state.ts";

const escrowAbi = parseAbi([
  "function quoteRealCrossChainFee(bytes32 settlementId, uint256 paidAmount) view returns (uint256 fee)",
  "function releaseRealCrossChain(bytes32 settlementId, uint256 paidAmount) payable returns (bytes32 messageId)",
  "function confirmCrossChainRelease(bytes32 settlementId, bytes32 messageId)",
  "function createAndLock(bytes32 settlementId, bytes32 orderId, bytes32 termsHash, address payee, address token, uint256 budgetAmount)",
]);

const erc20Abi = parseAbi([
  "function allowance(address owner, address spender) view returns (uint256)",
  "function approve(address spender, uint256 amount) returns (bool)",
  "function balanceOf(address owner) view returns (uint256)",
]);

// 注意：settlements 是 `mapping(bytes32 => Settlement) public`，Solidity 自动生成的
// getter 会把结构体成员**摊平**成 8 个返回值，而不是返回一个 tuple。写成 tuple 会让
// viem 返回对象取值恒为 undefined（Number(undefined) === NaN），从而静默跳过锁仓。
// 已用 hardhat-artifacts 的编译 ABI 交叉核对过：status 是第 8 个返回值（下标 7）。
const escrowReadAbi = parseAbi([
  "function settlements(bytes32 settlementId) view returns (bytes32 orderId, bytes32 termsHash, address payer, address payee, address token, uint256 budgetAmount, uint256 paidAmount, uint8 status)",
]);

const SETTLEMENT_STATUS_INDEX = 7;
const SETTLEMENT_STATUS_NONE = 0;

type Broadcast = { transactionHash: Hex; nonce: number };

function classifyError(error: unknown): string {
  const message = error instanceof Error ? error.message : String(error);
  const lower = message.toLowerCase();
  // 这两类锁仓错误自带语义前缀，直接透传，避免被下面的泛化规则改写成误导性分类。
  if (lower.startsWith("lock_already_onchain")) return message;
  if (lower.startsWith("insufficient_lock_balance")) return message;
  if (lower.startsWith("lock_status_unreadable")) return message;
  if (lower.includes("nonce")) return `nonce_conflict: ${message}`;
  if (lower.includes("insufficient funds") || lower.includes("balance")) return `insufficient_fee_balance: ${message}`;
  if (lower.includes("timeout") || lower.includes("timed out")) return `receipt_pending: ${message}`;
  if (lower.includes("revert")) return `transaction_reverted: ${message}`;
  if (lower.includes("rpc") || lower.includes("fetch")) return `rpc_unavailable: ${message}`;
  return message;
}

export class Relayer {
  private publicClient;
  private walletClient;
  private account;
  private config: WorkerConfig;
  private state: StateStore;
  private api: InternalApi;

  constructor(
    config: WorkerConfig,
    state: StateStore,
    api: InternalApi,
    clients?: { publicClient: any; walletClient: any; account: any },
  ) {
    this.config = config;
    this.state = state;
    this.api = api;
    const signer = clients ? undefined : createRelayerSigner(config);
    this.account = clients?.account ?? signer!.account;
    this.publicClient = clients?.publicClient ?? createPublicClient({ transport: http(config.sourceRpcUrl) });
    this.walletClient = clients?.walletClient ?? signer!.walletClient;
  }

  get address() {
    return this.account.address;
  }

  async processOne(): Promise<void> {
    const leaseSeconds = this.config.relayerLeaseSeconds ?? 300;
    const leased = await this.api.lease(this.config.workerId, leaseSeconds);
    const task = leased.task;
    if (!task) return;
    const leaseToken = String(task.leaseToken ?? "");
    let heartbeatError: Error | undefined;
    const heartbeat = leaseToken && typeof (this.api as any).heartbeat === "function"
      ? setInterval(() => {
          this.api.heartbeat(task.id, {
            workerId: this.config.workerId,
            leaseToken,
            leaseSeconds,
          }).catch((error) => {
            heartbeatError = error instanceof Error ? error : new Error(String(error));
          });
        }, this.config.relayerHeartbeatMs ?? 30_000)
      : undefined;
    heartbeat?.unref();
    try {
      let submission = this.state.getSubmissionRecord(task.id);
      if (submission && await this.isDropped(submission)) {
        this.state.clearSubmission(task.id);
        submission = undefined;
      }
      const broadcast = submission
        ? { transactionHash: submission.transactionHash as Hex, nonce: submission.nonce ?? 0 }
        : task.taskType === "release"
          ? await this.release(task)
          : task.taskType === "lock"
            ? await this.lock(task)
            : await this.confirm(task);
      if (!submission) this.state.saveSubmission(task.id, broadcast.transactionHash, broadcast.nonce);
      const receipt = await this.publicClient.waitForTransactionReceipt({
        hash: broadcast.transactionHash,
        timeout: this.config.relayerReceiptTimeoutMs ?? 120_000,
      });
      this.state.touchSubmission(task.id);
      if (receipt.status !== "success") {
        this.state.clearSubmission(task.id);
        throw new Error(`Relayer transaction ${broadcast.transactionHash} reverted`);
      }
      if (heartbeatError) throw heartbeatError;
      await this.reportSubmitted(task, leaseToken, broadcast);
    } catch (error) {
      const message = classifyError(error).slice(0, 5000);
      const outcome = Number(task.attempts) >= (this.config.maxRelayerAttempts ?? 8) ? "failed" : "retry";
      await this.api.report(task.id, {
        workerId: this.config.workerId,
        leaseToken,
        outcome,
        error: message,
      });
    } finally {
      if (heartbeat) clearInterval(heartbeat);
    }
  }

  private async nextNonce(): Promise<number> {
    return Number(await this.publicClient.getTransactionCount({
      address: this.account.address,
      blockTag: "pending",
    }));
  }

  private async release(task: Record<string, any>): Promise<Broadcast> {
    const settlementId = task.onchainSettlementId as Hex;
    const amount = parseUnits(String(task.payableAmount), 6);
    const fee = await this.publicClient.readContract({
      address: this.config.escrowAddress,
      abi: escrowAbi,
      functionName: "quoteRealCrossChainFee",
      args: [settlementId, amount],
    });
    const nonce = await this.nextNonce();
    const simulation = await this.publicClient.simulateContract({
      account: this.account,
      address: this.config.escrowAddress,
      abi: escrowAbi,
      functionName: "releaseRealCrossChain",
      args: [settlementId, amount],
      value: fee,
      nonce,
    });
    const transactionHash = await this.walletClient.writeContract(simulation.request);
    return { transactionHash, nonce };
  }

  private async confirm(task: Record<string, any>): Promise<Broadcast> {
    const nonce = await this.nextNonce();
    const simulation = await this.publicClient.simulateContract({
      account: this.account,
      address: this.config.escrowAddress,
      abi: escrowAbi,
      functionName: "confirmCrossChainRelease",
      args: [task.onchainSettlementId as Hex, task.ccipMessageId as Hex],
      nonce,
    });
    const transactionHash = await this.walletClient.writeContract(simulation.request);
    return { transactionHash, nonce };
  }

  /**
   * 锁仓自动化：由 relayer（即 platformSigner）钱包自动广播 escrow.createAndLock。
   * 这样 agent 作为 requester 时，无需人类手动广播即可完成资金锁定。
   */
  private async lock(task: Record<string, any>): Promise<Broadcast> {
    const settlementId = task.onchainSettlementId as Hex;
    const orderId = task.onchainOrderId as Hex;
    const termsHash = task.termsHash as Hex;
    const payee = task.payee as `0x${string}`;
    const token = (task.token ?? this.config.sourceSettlementToken) as `0x${string}`;
    const amount = parseUnits(String(task.payableAmount), 6);

    // 幂等：若结算已在链上存在（例如此前的手动锁仓或本 relayer 的历史广播），
    // 复用其 FundsLocked 事件回执，绝不重复广播（重复广播会二次扣款）。
    const existing = (await this.publicClient.readContract({
      address: this.config.escrowAddress,
      abi: escrowReadAbi,
      functionName: "settlements",
      args: [settlementId],
    })) as unknown as readonly unknown[];
    const existingStatus = Number(existing[SETTLEMENT_STATUS_INDEX]);
    if (!Number.isFinite(existingStatus)) {
      // 宁可显式报错重试，也不要在 ABI 漂移时“看起来成功”地跳过锁仓。
      throw new Error(
        `lock_status_unreadable: settlements(${settlementId}) returned an unexpected shape; check the escrow ABI`,
      );
    }
    if (existingStatus !== SETTLEMENT_STATUS_NONE) {
      // 注意：绝不能用 fromBlock: 0n —— 公共 RPC 对 eth_getLogs 有区间上限，
      // 全量扫描会直接报错。这里退回到扫描器起始高度与近端窗口的较大者。
      const latest = await this.publicClient.getBlockNumber();
      const window = BigInt(this.config.blockChunkSize ?? 2000) * 50n;
      const floor = latest > window ? latest - window : 0n;
      const fromBlock = this.config.startSourceBlock > floor ? this.config.startSourceBlock : floor;
      const logs = await this.publicClient.getLogs({
        address: this.config.escrowAddress,
        event: parseAbiItem(
          "event FundsLocked(bytes32 indexed settlementId, address indexed payer, address token, uint256 amount)",
        ),
        args: { settlementId },
        fromBlock,
        toBlock: "latest",
      });
      const txHash = logs[0]?.transactionHash as Hex | undefined;
      if (txHash) return { transactionHash: txHash, nonce: 0 };
      // 找不到回执时不要伪造 "0x"，否则 waitForTransactionReceipt 会抛出难以定位的错误。
      // 资金其实已经锁好了，交给扫描器投递 funds_locked 事件把任务标记完成即可。
      throw new Error(
        `lock_already_onchain: settlement ${settlementId} is already locked; awaiting scanner reconciliation`,
      );
    }

    // 代付前先确认 relayer 钱包余额，避免落到含义模糊的 ERC20 revert 上。
    const balance = (await this.publicClient.readContract({
      address: token,
      abi: erc20Abi,
      functionName: "balanceOf",
      args: [this.account.address],
    })) as bigint;
    if (balance < amount) {
      throw new Error(
        `insufficient_lock_balance: relayer ${this.account.address} holds ${balance} of ${token}, needs ${amount}`,
      );
    }

    // 确保 relayer(platformSigner) 钱包已授权 escrow 代扣 USDC。
    const allowance = await this.publicClient.readContract({
      address: token,
      abi: erc20Abi,
      functionName: "allowance",
      args: [this.account.address, this.config.escrowAddress],
    });
    if (allowance < amount) {
      const approveNonce = await this.nextNonce();
      const approveSim = await this.publicClient.simulateContract({
        account: this.account,
        address: token,
        abi: erc20Abi,
        functionName: "approve",
        args: [this.config.escrowAddress, amount],
        nonce: approveNonce,
      });
      const approveHash = await this.walletClient.writeContract(approveSim.request);
      await this.publicClient.waitForTransactionReceipt({
        hash: approveHash,
        timeout: this.config.relayerReceiptTimeoutMs ?? 120_000,
      });
    }

    const nonce = await this.nextNonce();
    const simulation = await this.publicClient.simulateContract({
      account: this.account,
      address: this.config.escrowAddress,
      abi: escrowAbi,
      functionName: "createAndLock",
      args: [settlementId, orderId, termsHash, payee, token, amount],
      nonce,
    });
    const transactionHash = await this.walletClient.writeContract(simulation.request);
    return { transactionHash, nonce };
  }

  private async isDropped(submission: Submission): Promise<boolean> {
    const age = Date.now() - Date.parse(`${submission.firstBroadcastAt}Z`.replace("ZZ", "Z"));
    if (age < (this.config.relayerDroppedAfterMs ?? 600_000)) return false;
    try {
      await this.publicClient.getTransaction({ hash: submission.transactionHash as Hex });
      return false;
    } catch {
      if (submission.nonce == null) return false;
      const latestNonce = Number(await this.publicClient.getTransactionCount({
        address: this.account.address,
        blockTag: "latest",
      }));
      if (latestNonce > submission.nonce) {
        throw new Error(`nonce ${submission.nonce} was consumed while transaction was unavailable`);
      }
      return true;
    }
  }

  private async reportSubmitted(task: Record<string, any>, leaseToken: string, broadcast: Broadcast): Promise<void> {
    await this.api.report(task.id, {
      workerId: this.config.workerId,
      leaseToken,
      outcome: "submitted",
      transactionHash: broadcast.transactionHash,
      transactionNonce: broadcast.nonce,
    });
    this.state.clearSubmission(task.id);
  }
}
