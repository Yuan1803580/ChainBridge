import {
  createPublicClient,
  http,
  keccak256,
  parseAbi,
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
]);

type Broadcast = { transactionHash: Hex; nonce: number };

function classifyError(error: unknown): string {
  const message = error instanceof Error ? error.message : String(error);
  const lower = message.toLowerCase();
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
      if (submission) await this.ensureSubmissionBroadcast(submission);
      if (submission && await this.isDropped(submission)) {
        throw new Error(`receipt_pending: persisted transaction ${submission.transactionHash} is not yet available`);
      }
      const broadcast = submission
        ? { transactionHash: submission.transactionHash as Hex, nonce: submission.nonce ?? 0 }
        : task.taskType === "release" ? await this.release(task) : await this.confirm(task);
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
    return this.broadcastPrepared(task.id, simulation.request, nonce);
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
    return this.broadcastPrepared(task.id, simulation.request, nonce);
  }

  private async broadcastPrepared(
    taskId: string,
    request: Record<string, any>,
    nonce: number,
  ): Promise<Broadcast> {
    if (typeof this.account.signTransaction !== "function" || typeof this.walletClient.sendRawTransaction !== "function") {
      const transactionHash = await this.walletClient.writeContract(request);
      this.state.saveSubmission(taskId, transactionHash, nonce);
      return { transactionHash, nonce };
    }
    const { account: _account, ...transaction } = request;
    const rawTransaction = await this.account.signTransaction(transaction) as Hex;
    const transactionHash = keccak256(rawTransaction);
    this.state.savePreparedSubmission(
      taskId,
      transactionHash,
      nonce,
      rawTransaction,
      this.config.workerId,
    );
    await this.sendPersistedRaw(taskId, transactionHash, rawTransaction);
    return { transactionHash, nonce };
  }

  private async ensureSubmissionBroadcast(submission: Submission): Promise<void> {
    if (submission.status !== "prepared" || !submission.rawTransaction) return;
    await this.sendPersistedRaw(
      submission.taskId,
      submission.transactionHash as Hex,
      submission.rawTransaction,
    );
  }

  private async sendPersistedRaw(
    taskId: string,
    transactionHash: Hex,
    rawTransaction: Hex,
  ): Promise<void> {
    try {
      const returnedHash = await this.walletClient.sendRawTransaction({
        serializedTransaction: rawTransaction,
      });
      if (String(returnedHash).toLowerCase() !== transactionHash.toLowerCase()) {
        throw new Error(`RPC returned a different transaction hash for task ${taskId}`);
      }
    } catch (error) {
      const message = error instanceof Error ? error.message.toLowerCase() : String(error).toLowerCase();
      const idempotent = message.includes("already known") || message.includes("known transaction") || message.includes("nonce too low");
      if (!idempotent) throw error;
    }
    this.state.markSubmissionBroadcast(taskId, transactionHash);
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
      if (submission.rawTransaction) {
        await this.sendPersistedRaw(
          submission.taskId,
          submission.transactionHash as Hex,
          submission.rawTransaction,
        );
        return false;
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
