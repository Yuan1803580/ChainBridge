import { encodeEventTopics, formatUnits, numberToHex, parseAbiItem, type PublicClient } from "viem";

import { ApiError, type InternalApi } from "./api.ts";
import type { StateStore } from "./state.ts";

/**
 * Scanner self-diagnostics.
 *
 * `abiMismatchCount` guards against the single most dangerous silent failure mode of this
 * service: `getLogs({ strict: true })` DROPS any log whose topic count disagrees with the
 * decoding ABI without throwing and without logging. A one-character `indexed` typo in an
 * event signature therefore takes down cross-chain settlement with zero signal.
 *
 * `duplicateReplayCount` counts BENIGN idempotent replays (API answered 200 `{duplicate:true}`),
 * which is the healthy path after a cursor rewind. `duplicateConflictCount` counts genuine
 * integrity violations (API answered 409): the same `eventId` carrying different chain data.
 * The two must never be conflated - the first is proof idempotency works, the second is a bug.
 */
export const scannerDiagnostics = {
  abiMismatchCount: 0,
  lastAbiMismatchAt: undefined as string | undefined,
  lastAbiMismatchDetail: undefined as string | undefined,
  duplicateReplayCount: 0,
  duplicateConflictCount: 0,
  lastDuplicateConflictAt: undefined as string | undefined,
  lastDuplicateConflictDetail: undefined as string | undefined,
};

const fundsLocked = parseAbiItem(
  "event FundsLocked(bytes32 indexed settlementId, address indexed payer, address token, uint256 amount)",
);
const realFundsReleased = parseAbiItem(
  "event RealCrossChainFundsReleased(bytes32 indexed settlementId, address indexed payee, uint256 paidAmount, uint256 refundAmount, bytes32 indexed messageId, uint256 nativeFee)",
);
const releaseConfirmed = parseAbiItem(
  "event CrossChainReleaseConfirmed(bytes32 indexed settlementId, bytes32 indexed messageId, address indexed confirmer)",
);
const settlementReceived = parseAbiItem(
  "event RealCrossChainSettlementReceived(bytes32 indexed messageId, bytes32 indexed settlementId, bytes32 indexed orderId, uint64 sourceChainSelector, address sender, address token, uint256 amount)",
);

export function eventId(chainId: number, transactionHash: string, logIndex: number): string {
  return `${chainId}:${transactionHash}:${logIndex}`;
}

function base(log: any, chainId: number, confirmations: number, chainSelector: string) {
  return {
    eventId: eventId(chainId, log.transactionHash, Number(log.logIndex)),
    transactionHash: log.transactionHash,
    blockNumber: Number(log.blockNumber),
    blockHash: log.blockHash,
    confirmations,
    chainSelector,
  };
}

export function mapSourceLog(
  log: any,
  chainId: number,
  confirmations: number,
  chainSelector: string,
  tokenDecimals = 6,
): Record<string, unknown> {
  const common = base(log, chainId, confirmations, chainSelector);
  if (log.eventName === "FundsLocked") {
    return {
      ...common,
      eventType: "funds_locked",
      onchainSettlementId: log.args.settlementId,
      tokenAddress: log.args.token,
      amount: formatUnits(log.args.amount, tokenDecimals),
    };
  }
  if (log.eventName === "RealCrossChainFundsReleased") {
    return {
      ...common,
      eventType: "release_submitted",
      onchainSettlementId: log.args.settlementId,
      ccipMessageId: log.args.messageId,
      amount: formatUnits(log.args.paidAmount, tokenDecimals),
    };
  }
  if (log.eventName === "CrossChainReleaseConfirmed") {
    return {
      ...common,
      eventType: "release_confirmed",
      onchainSettlementId: log.args.settlementId,
      ccipMessageId: log.args.messageId,
    };
  }
  throw new Error(`Unsupported source event ${log.eventName}`);
}

export function mapDestinationLog(
  log: any,
  chainId: number,
  confirmations: number,
  chainSelector: string,
  tokenDecimals = 6,
): Record<string, unknown> {
  return {
    ...base(log, chainId, confirmations, chainSelector),
    eventType: "settlement_received",
    onchainSettlementId: log.args.settlementId,
    ccipMessageId: log.args.messageId,
    tokenAddress: log.args.token,
    amount: formatUnits(log.args.amount, tokenDecimals),
  };
}

type ScannerOptions = {
  chainKey: "source" | "destination";
  chainId: number;
  chainSelector: string;
  contractAddress: `0x${string}`;
  confirmations: number;
  reorgDepth: number;
  chunkSize: number;
  startBlock: bigint;
  tokenDecimals?: number;
  eventMaxAttempts?: number;
  eventRetryBaseMs?: number;
  client: PublicClient;
  state: StateStore;
  api: InternalApi;
};

export class ChainScanner {
  private options: ScannerOptions;

  constructor(options: ScannerOptions) {
    this.options = options;
  }

  async scan(): Promise<void> {
    const latest = await this.options.client.getBlockNumber();
    const confirmationOffset = BigInt(Math.max(0, this.options.confirmations - 1));
    if (latest < confirmationOffset) return;
    const safeHead = latest - confirmationOffset;
    await this.verifyCanonicalHistory(safeHead);
    const cursor = this.options.state.getCursor(this.options.chainKey, this.options.startBlock);
    const rewind = BigInt(this.options.reorgDepth);
    let fromBlock = cursor > rewind ? cursor - rewind : this.options.startBlock;
    if (fromBlock < this.options.startBlock) fromBlock = this.options.startBlock;
    if (fromBlock > safeHead) return;

    while (fromBlock <= safeHead) {
      const toBlock = fromBlock + BigInt(this.options.chunkSize - 1) > safeHead
        ? safeHead
        : fromBlock + BigInt(this.options.chunkSize - 1);
      const logs = await this.logs(fromBlock, toBlock);
      for (const log of logs) {
        const confirmations = Number(latest - log.blockNumber + 1n);
        const payload = this.options.chainKey === "source"
          ? mapSourceLog(log, this.options.chainId, confirmations, this.options.chainSelector, this.options.tokenDecimals)
          : mapDestinationLog(log, this.options.chainId, confirmations, this.options.chainSelector, this.options.tokenDecimals);
        this.options.state.enqueueEvent(payload, this.options.chainKey);
      }
      await flushPendingEvents(
        this.options.state,
        this.options.api,
        this.options.eventMaxAttempts,
        this.options.eventRetryBaseMs,
      );
      if (this.options.state.hasPendingEvents(this.options.chainKey, fromBlock, toBlock)) return;
      this.options.state.setCursor(this.options.chainKey, toBlock + 1n);
      fromBlock = toBlock + 1n;
    }
  }

  private async verifyCanonicalHistory(safeHead: bigint): Promise<void> {
    const depth = BigInt(this.options.reorgDepth);
    const minimumBlock = safeHead > depth ? safeHead - depth : this.options.startBlock;
    const events = this.options.state.recentProcessedEvents(this.options.chainKey, minimumBlock);
    const blocks = new Map<string, string>();
    for (const event of events) {
      const key = event.blockNumber.toString();
      let canonicalHash = blocks.get(key);
      if (!canonicalHash) {
        const block = await this.options.client.getBlock({ blockNumber: event.blockNumber });
        canonicalHash = String(block.hash);
        blocks.set(key, canonicalHash);
      }
      if (canonicalHash.toLowerCase() === event.blockHash.toLowerCase()) continue;
      await this.options.api.orphan({
        eventId: event.eventId,
        observedBlockHash: event.blockHash,
        canonicalBlockHash: canonicalHash,
      });
      this.options.state.markProcessedOrphaned(event.eventId);
    }
  }

  private async logs(fromBlock: bigint, toBlock: bigint): Promise<any[]> {
    const events = this.options.chainKey === "source"
      ? [fundsLocked, realFundsReleased, releaseConfirmed]
      : [settlementReceived];
    const topic0s = events.map((event) => encodeEventTopics({ abi: [event] })[0] as string);

    const [groups, rawLogs] = await Promise.all([
      Promise.all(events.map((event) => this.options.client.getLogs({
        address: this.options.contractAddress,
        event,
        fromBlock,
        toBlock,
        strict: true,
      }))),
      this.rawTopicProbe(topic0s, fromBlock, toBlock),
    ]);

    const decoded = groups.flat();
    if (rawLogs !== null) this.assertDecodeCoverage(decoded, rawLogs, fromBlock, toBlock);

    return decoded.sort((left, right) => {
      if (left.blockNumber !== right.blockNumber) return left.blockNumber < right.blockNumber ? -1 : 1;
      return Number(left.logIndex) - Number(right.logIndex);
    });
  }

  /**
   * Undecoded probe over the exact same range/address/topic0 set. Any log the strict decoder
   * silently discarded shows up here as a surplus entry.
   *
   * Returns `null` when the probe cannot run (transport without a raw `request` method, or an
   * RPC failure). The probe is a safety net and must never itself break scanning - a failed
   * probe degrades to the previous behaviour instead of stalling settlement.
   */
  private async rawTopicProbe(topic0s: string[], fromBlock: bigint, toBlock: bigint): Promise<any[] | null> {
    const request = (this.options.client as any)?.request;
    if (typeof request !== "function") return null;
    try {
      const logs = await request({
        method: "eth_getLogs",
        params: [{
          address: this.options.contractAddress,
          fromBlock: numberToHex(fromBlock),
          toBlock: numberToHex(toBlock),
          topics: [topic0s],
        }],
      });
      return Array.isArray(logs) ? logs : null;
    } catch (error) {
      console.warn(
        new Date().toISOString(),
        "abi_probe_unavailable",
        error instanceof Error ? error.message : String(error),
      );
      return null;
    }
  }

  /**
   * Fail loudly when the strict decoder dropped logs that the raw topic0 filter matched.
   *
   * Throwing here is deliberate: `scan()` will not advance the cursor, so the affected range
   * is re-scanned once the ABI is corrected and nothing is lost. A stalled scanner with a
   * screaming alert is strictly better than a scanner that silently discards settlements.
   */
  private assertDecodeCoverage(decoded: any[], rawLogs: any[], fromBlock: bigint, toBlock: bigint): void {
    if (rawLogs.length <= decoded.length) return;

    const identity = (log: any) => `${String(log.transactionHash).toLowerCase()}:${Number(log.logIndex)}`;
    const seen = new Set(decoded.map(identity));
    const dropped = rawLogs.filter((log) => !seen.has(identity(log)));
    const detail = dropped.slice(0, 5).map((log) => {
      const topics: string[] = log.topics ?? [];
      return `topic0=${topics[0]} topics=${topics.length} tx=${log.transactionHash} logIndex=${Number(log.logIndex)}`;
    }).join(" | ");
    const message = `chain=${this.options.chainKey} range=${fromBlock}-${toBlock} `
      + `raw=${rawLogs.length} decoded=${decoded.length} dropped=${dropped.length} :: ${detail}`;

    scannerDiagnostics.abiMismatchCount += 1;
    scannerDiagnostics.lastAbiMismatchAt = new Date().toISOString();
    scannerDiagnostics.lastAbiMismatchDetail = message;
    console.error(new Date().toISOString(), "abi_mismatch_silent_drop", message);
    throw new Error(
      `Event ABI mismatch: strict decoding silently dropped ${dropped.length} log(s). ${message}`,
    );
  }
}

export async function flushPendingEvents(
  state: StateStore,
  api: InternalApi,
  maxAttempts = 8,
  baseDelayMs = 1_000,
): Promise<{ acknowledged: number; failed: number; dead: number; duplicates: number; conflicts: number }> {
  const result = { acknowledged: 0, failed: 0, dead: 0, duplicates: 0, conflicts: 0 };
  for (const event of state.pendingEvents()) {
    try {
      const response = await api.reconcile(event.payload);
      if (response?.duplicate === true) {
        scannerDiagnostics.duplicateReplayCount += 1;
        result.duplicates += 1;
      }
      state.acknowledgeEvent(event.eventId);
      result.acknowledged += 1;
    } catch (error) {
      const message = error instanceof Error ? error.message : String(error);
      // A 409 means the API holds different chain data under this eventId. Retrying is
      // pointless and would hide the conflict behind 8 backoff rounds, so fail it straight
      // into the dead-letter queue (maxAttempts = 1) and surface it to the alerting path.
      const conflict = error instanceof ApiError && error.isConflict;
      if (conflict) {
        const detail = `eventId=${event.eventId} chain=${event.chainKey} :: ${message}`;
        scannerDiagnostics.duplicateConflictCount += 1;
        scannerDiagnostics.lastDuplicateConflictAt = new Date().toISOString();
        scannerDiagnostics.lastDuplicateConflictDetail = detail;
        console.error(new Date().toISOString(), "duplicate_event_conflict", detail);
        result.conflicts += 1;
      }
      const status = state.failEvent(
        event.eventId,
        message,
        conflict ? 1 : maxAttempts,
        baseDelayMs,
      );
      result.failed += 1;
      if (status === "dead") result.dead += 1;
    }
  }
  return result;
}
