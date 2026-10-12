import { formatUnits, parseAbiItem, type PublicClient } from "viem";

import type { InternalApi } from "./api.ts";
import type { StateStore } from "./state.ts";

const fundsLocked = parseAbiItem(
  "event FundsLocked(bytes32 indexed settlementId, address indexed payer, address indexed token, uint256 amount)",
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
): Record<string, unknown> {
  const common = base(log, chainId, confirmations, chainSelector);
  if (log.eventName === "FundsLocked") {
    return {
      ...common,
      eventType: "funds_locked",
      onchainSettlementId: log.args.settlementId,
      tokenAddress: log.args.token,
      amount: formatUnits(log.args.amount, 6),
    };
  }
  if (log.eventName === "RealCrossChainFundsReleased") {
    return {
      ...common,
      eventType: "release_submitted",
      onchainSettlementId: log.args.settlementId,
      ccipMessageId: log.args.messageId,
      amount: formatUnits(log.args.paidAmount, 6),
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
): Record<string, unknown> {
  return {
    ...base(log, chainId, confirmations, chainSelector),
    eventType: "settlement_received",
    onchainSettlementId: log.args.settlementId,
    ccipMessageId: log.args.messageId,
    tokenAddress: log.args.token,
    amount: formatUnits(log.args.amount, 6),
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
          ? mapSourceLog(log, this.options.chainId, confirmations, this.options.chainSelector)
          : mapDestinationLog(log, this.options.chainId, confirmations, this.options.chainSelector);
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
    const groups = await Promise.all(events.map((event) => this.options.client.getLogs({
      address: this.options.contractAddress,
      event,
      fromBlock,
      toBlock,
      strict: true,
    })));
    return groups.flat().sort((left, right) => {
      if (left.blockNumber !== right.blockNumber) return left.blockNumber < right.blockNumber ? -1 : 1;
      return Number(left.logIndex) - Number(right.logIndex);
    });
  }
}

export async function flushPendingEvents(
  state: StateStore,
  api: InternalApi,
  maxAttempts = 8,
  baseDelayMs = 1_000,
): Promise<{ acknowledged: number; failed: number; dead: number }> {
  const result = { acknowledged: 0, failed: 0, dead: 0 };
  for (const event of state.pendingEvents()) {
    try {
      await api.reconcile(event.payload);
      state.acknowledgeEvent(event.eventId);
      result.acknowledged += 1;
    } catch (error) {
      const status = state.failEvent(
        event.eventId,
        error instanceof Error ? error.message : String(error),
        maxAttempts,
        baseDelayMs,
      );
      result.failed += 1;
      if (status === "dead") result.dead += 1;
    }
  }
  return result;
}
