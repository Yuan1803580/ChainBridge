import { readFileSync } from "node:fs";

import {
  createPublicClient,
  createWalletClient,
  decodeAbiParameters,
  http,
  isAddressEqual,
  parseAbi,
  parseAbiItem,
  type Address,
  type Hex,
  type PublicClient,
  type WalletClient,
} from "viem";
import { privateKeyToAccount } from "viem/accounts";

import { StateStore, type PendingEvent } from "./state.ts";

const messageEvent = parseAbiItem(
  "event ExperimentMessageSent(uint8 indexed messageType, bytes32 indexed messageId, bytes32 indexed attemptId, bytes encodedEnvelope, bytes payload)",
);
const receiverAbi = parseAbi([
  "error StaleAttempt(bytes32 expectedAttemptId, bytes32 providedAttemptId)",
  "error StaleAttemptNumber(uint32 latestAttemptNumber, uint32 providedAttemptNumber)",
  "function receiveAssignmentV2((uint8 messageType, bytes32 messageId, uint64 sourceChainId, uint64 targetChainId, address sourceSender, address targetReceiver, bytes32 workflowId, bytes32 nodeId, bytes32 attemptId, uint32 attemptNumber, bytes32 routeKey, uint32 routeVersion, uint256 nonce, bytes32 payloadHash) envelope, bytes payload)",
  "function receiveResultProofV2((uint8 messageType, bytes32 messageId, uint64 sourceChainId, uint64 targetChainId, address sourceSender, address targetReceiver, bytes32 workflowId, bytes32 nodeId, bytes32 attemptId, uint32 attemptNumber, bytes32 routeKey, uint32 routeVersion, uint256 nonce, bytes32 payloadHash) envelope, bytes payload)",
  "function receiveSettlementV2((uint8 messageType, bytes32 messageId, uint64 sourceChainId, uint64 targetChainId, address sourceSender, address targetReceiver, bytes32 workflowId, bytes32 nodeId, bytes32 attemptId, uint32 attemptNumber, bytes32 routeKey, uint32 routeVersion, uint256 nonce, bytes32 payloadHash) envelope, bytes payload)",
  "function receiveConfirmationV2((uint8 messageType, bytes32 messageId, uint64 sourceChainId, uint64 targetChainId, address sourceSender, address targetReceiver, bytes32 workflowId, bytes32 nodeId, bytes32 attemptId, uint32 attemptNumber, bytes32 routeKey, uint32 routeVersion, uint256 nonce, bytes32 payloadHash) envelope, bytes payload)",
  "function processedMessages(bytes32 messageId) view returns (bool)",
  "function processedConfirmations(bytes32 messageId) view returns (bool)",
  "function payoutStatus(bytes32 settlementMessageId) view returns (uint8)",
  "function executePayout(bytes32 settlementMessageId) returns (uint256 payoutAmount, bytes32 confirmationMessageId)",
]);
const envelopeParameter = {
  type: "tuple",
  components: [
    { name: "messageType", type: "uint8" },
    { name: "messageId", type: "bytes32" },
    { name: "sourceChainId", type: "uint64" },
    { name: "targetChainId", type: "uint64" },
    { name: "sourceSender", type: "address" },
    { name: "targetReceiver", type: "address" },
    { name: "workflowId", type: "bytes32" },
    { name: "nodeId", type: "bytes32" },
    { name: "attemptId", type: "bytes32" },
    { name: "attemptNumber", type: "uint32" },
    { name: "routeKey", type: "bytes32" },
    { name: "routeVersion", type: "uint32" },
    { name: "nonce", type: "uint256" },
    { name: "payloadHash", type: "bytes32" },
  ],
} as const;

export type ExperimentEnvelope = {
  messageType: number;
  messageId: Hex;
  sourceChainId: bigint;
  targetChainId: bigint;
  sourceSender: Address;
  targetReceiver: Address;
  workflowId: Hex;
  nodeId: Hex;
  attemptId: Hex;
  attemptNumber: number;
  routeKey: Hex;
  routeVersion: number;
  nonce: bigint;
  payloadHash: Hex;
};

export type ExperimentChainManifest = {
  key: string;
  chainId: number;
  rpcUrl: string;
  confirmations: number;
  startBlock: string;
  enabled: boolean;
  messageSenders: Address[];
  allowedReceivers: Address[];
};

export type ExperimentRelayManifest = {
  version?: number;
  schemaVersion?: number;
  chains: ExperimentChainManifest[];
};

type RelayEventPayload = Record<string, unknown> & {
  eventId: string;
  messageId: Hex;
  messageType: number;
  attemptId: Hex;
  attemptNumber: number;
  sourceChainId: string;
  targetChainId: string;
  sourceSender: Address;
  targetReceiver: Address;
  encodedEnvelope: Hex;
  payload: Hex;
  blockNumber: string;
  blockHash: Hex;
  availableAt: string;
};

type ChainRuntime = {
  manifest: ExperimentChainManifest;
  publicClient: PublicClient;
  walletClient: WalletClient;
};

export type ExperimentRelayOptions = {
  manifestPath: string;
  databasePath: string;
  privateKey: Hex;
  pollIntervalMs?: number;
  deliveryDelayMs?: number;
  reorgDepth?: number;
  blockChunkSize?: number;
  maxAttempts?: number;
  retryBaseMs?: number;
  failTargetChainId?: number;
  reversePending?: boolean;
  crashAfterEnqueue?: boolean;
  state?: StateStore;
  onTransaction?: (record: {
    chainId: number;
    hash: Hex;
    blockNumber: bigint;
    gasUsed: bigint;
    durationMs: number;
    functionName: string;
  }) => void;
};

export class ExperimentEventRelay {
  private readonly options: Required<Omit<ExperimentRelayOptions, "state" | "failTargetChainId" | "onTransaction">>
    & Pick<ExperimentRelayOptions, "failTargetChainId">;
  private readonly account;
  private readonly state: StateStore;
  private readonly ownsState: boolean;
  private readonly onTransaction?: ExperimentRelayOptions["onTransaction"];
  private manifest?: ExperimentRelayManifest;
  private runtimes = new Map<number, ChainRuntime>();
  private stopped = false;

  constructor(options: ExperimentRelayOptions) {
    this.options = {
      manifestPath: options.manifestPath,
      databasePath: options.databasePath,
      privateKey: options.privateKey,
      pollIntervalMs: options.pollIntervalMs ?? 1_000,
      deliveryDelayMs: options.deliveryDelayMs ?? 0,
      reorgDepth: options.reorgDepth ?? 12,
      blockChunkSize: options.blockChunkSize ?? 500,
      maxAttempts: options.maxAttempts ?? 8,
      retryBaseMs: options.retryBaseMs ?? 1_000,
      reversePending: options.reversePending ?? false,
      crashAfterEnqueue: options.crashAfterEnqueue ?? false,
      failTargetChainId: options.failTargetChainId,
    };
    this.account = privateKeyToAccount(options.privateKey);
    this.state = options.state ?? new StateStore(options.databasePath);
    this.ownsState = !options.state;
    this.onTransaction = options.onTransaction;
  }

  async reloadManifest(): Promise<ExperimentRelayManifest> {
    const parsed = JSON.parse(readFileSync(this.options.manifestPath, "utf8")) as ExperimentRelayManifest;
    const version = parsed.schemaVersion ?? parsed.version;
    if (!Number.isInteger(version) || !Array.isArray(parsed.chains)) throw new Error("Invalid experiment relay manifest");
    const chainIds = new Set<number>();
    for (const chain of parsed.chains) {
      if (!chain.key || !Number.isInteger(chain.chainId) || chainIds.has(chain.chainId)) {
        throw new Error(`Invalid or duplicate chain in relay manifest: ${chain.key || chain.chainId}`);
      }
      chainIds.add(chain.chainId);
      if (!Array.isArray(chain.messageSenders) || !Array.isArray(chain.allowedReceivers)) {
        throw new Error(`Chain ${chain.key} must declare messageSenders and allowedReceivers`);
      }
    }
    this.manifest = parsed;
    for (const chain of parsed.chains.filter((item) => item.enabled)) await this.runtime(chain);
    return parsed;
  }

  async cycle(): Promise<{ scanned: number; delivered: number; rejected: number; failed: number }> {
    const manifest = await this.reloadManifest();
    let scanned = 0;
    for (const chain of manifest.chains.filter((item) => item.enabled)) scanned += await this.scanChain(chain);
    const delivery = await this.deliverPending();
    return { scanned, ...delivery };
  }

  async run(): Promise<void> {
    while (!this.stopped) {
      try {
        const result = await this.cycle();
        if (result.scanned || result.delivered || result.failed) {
          console.log(JSON.stringify({ at: new Date().toISOString(), ...result, metrics: this.state.metrics() }));
        }
      } catch (error) {
        console.error("experiment_relay_cycle_failed", error instanceof Error ? error.message : error);
      }
      if (!this.stopped) await new Promise((resolve) => setTimeout(resolve, this.options.pollIntervalMs));
    }
  }

  stop(): void {
    this.stopped = true;
  }

  close(): void {
    if (this.ownsState) this.state.close();
  }

  async scanChain(chain: ExperimentChainManifest): Promise<number> {
    const runtime = await this.runtime(chain);
    const latest = await runtime.publicClient.getBlockNumber();
    const offset = BigInt(Math.max(0, chain.confirmations - 1));
    if (latest < offset) return 0;
    const safeHead = latest - offset;
    let scanned = 0;
    for (const sender of chain.messageSenders) {
      const chainKey = `${chain.key}:${sender.toLowerCase()}`;
      await this.verifyCanonical(runtime.publicClient, chainKey, safeHead);
      const startBlock = BigInt(chain.startBlock);
      const cursor = this.state.getCursor(chainKey, startBlock);
      const rewind = BigInt(this.options.reorgDepth);
      let fromBlock = cursor > rewind ? cursor - rewind : startBlock;
      if (fromBlock < startBlock) fromBlock = startBlock;
      if (fromBlock > safeHead) continue;
      while (fromBlock <= safeHead) {
        const candidate = fromBlock + BigInt(this.options.blockChunkSize - 1);
        const toBlock = candidate < safeHead ? candidate : safeHead;
        const logs = await runtime.publicClient.getLogs({
          address: sender,
          event: messageEvent,
          fromBlock,
          toBlock,
          strict: true,
        });
        for (const log of logs) {
          const [envelope] = decodeAbiParameters([envelopeParameter], log.args.encodedEnvelope) as readonly [ExperimentEnvelope];
          if (envelope.messageId.toLowerCase() !== log.args.messageId.toLowerCase()) {
            throw new Error(`Indexed and encoded messageId differ in ${log.transactionHash}`);
          }
          if (!isAddressEqual(envelope.sourceSender, sender)) {
            throw new Error(`Envelope sourceSender differs from emitting contract in ${log.transactionHash}`);
          }
          const payload: RelayEventPayload = {
            eventId: `${chain.chainId}:${log.transactionHash}:${log.logIndex}`,
            messageId: envelope.messageId,
            messageType: Number(envelope.messageType),
            attemptId: envelope.attemptId,
            attemptNumber: Number(envelope.attemptNumber),
            sourceChainId: envelope.sourceChainId.toString(),
            targetChainId: envelope.targetChainId.toString(),
            sourceSender: envelope.sourceSender,
            targetReceiver: envelope.targetReceiver,
            encodedEnvelope: log.args.encodedEnvelope,
            payload: log.args.payload,
            blockNumber: log.blockNumber.toString(),
            blockHash: log.blockHash,
            availableAt: new Date(Date.now() + this.options.deliveryDelayMs).toISOString(),
          };
          this.state.enqueueEvent(payload, chainKey);
          scanned += 1;
          if (this.options.crashAfterEnqueue) throw new Error("FAULT_INJECTION_CRASH_AFTER_ENQUEUE");
        }
        if (this.state.hasPendingEvents(chainKey, fromBlock, toBlock)) break;
        this.state.setCursor(chainKey, toBlock + 1n);
        fromBlock = toBlock + 1n;
      }
    }
    return scanned;
  }

  async deliverPending(now = new Date()): Promise<{ delivered: number; rejected: number; failed: number }> {
    const pending = this.state.pendingEvents(now).filter((item) => {
      const availableAt = String(item.payload.availableAt ?? "1970-01-01T00:00:00.000Z");
      return availableAt <= now.toISOString();
    });
    pending.sort((left, right) => this.options.reversePending
      ? Number(right.payload.attemptNumber ?? 0) - Number(left.payload.attemptNumber ?? 0)
      : left.eventId.localeCompare(right.eventId));
    const result = { delivered: 0, rejected: 0, failed: 0 };
    for (const event of pending) {
      try {
        await this.deliver(event);
        this.state.acknowledgeEvent(event.eventId);
        result.delivered += 1;
      } catch (error) {
        if (this.isTerminalProtocolRejection(error)) {
          this.state.acknowledgeEvent(event.eventId);
          result.rejected += 1;
          console.log(JSON.stringify({
            at: new Date().toISOString(),
            event: "protocol_message_rejected",
            eventId: event.eventId,
            messageId: event.payload.messageId,
            reason: error instanceof Error ? error.message : String(error),
          }));
          continue;
        }
        this.state.failEvent(
          event.eventId,
          error instanceof Error ? error.message : String(error),
          this.options.maxAttempts,
          this.options.retryBaseMs,
        );
        result.failed += 1;
      }
    }
    return result;
  }

  private isTerminalProtocolRejection(error: unknown): boolean {
    const message = error instanceof Error ? `${error.name}:${error.message}` : String(error);
    return ["StaleAttempt", "StaleAttemptNumber"].some((name) => message.includes(name));
  }

  private async deliver(event: PendingEvent): Promise<void> {
    const payload = event.payload as RelayEventPayload;
    const targetChainId = Number(payload.targetChainId);
    if (this.options.failTargetChainId === targetChainId) throw new Error(`FAULT_INJECTION_RPC_${targetChainId}`);
    const chain = this.manifest?.chains.find((item) => item.enabled && item.chainId === targetChainId);
    if (!chain) throw new Error(`Target chain ${targetChainId} is not active in the manifest`);
    if (!chain.allowedReceivers.some((address) => isAddressEqual(address, payload.targetReceiver))) {
      throw new Error(`Target receiver ${payload.targetReceiver} is not allowed on chain ${chain.key}`);
    }
    const runtime = await this.runtime(chain);
    const [envelope] = decodeAbiParameters([envelopeParameter], payload.encodedEnvelope) as readonly [ExperimentEnvelope];
    const base = {
      address: payload.targetReceiver,
      abi: receiverAbi,
      account: this.account,
    } as const;

    if (payload.messageType === 1) {
      if (!await this.processed(runtime, payload.targetReceiver, payload.messageId, "processedMessages")) {
        await this.write(runtime, { ...base, functionName: "receiveAssignmentV2", args: [envelope, payload.payload] });
      }
      return;
    }
    if (payload.messageType === 2) {
      if (!await this.processed(runtime, payload.targetReceiver, payload.messageId, "processedMessages")) {
        await this.write(runtime, { ...base, functionName: "receiveResultProofV2", args: [envelope, payload.payload] });
      }
      return;
    }
    if (payload.messageType === 3) {
      if (!await this.processed(runtime, payload.targetReceiver, payload.messageId, "processedMessages")) {
        await this.write(runtime, { ...base, functionName: "receiveSettlementV2", args: [envelope, payload.payload] });
      }
      const status = Number(await runtime.publicClient.readContract({
        address: payload.targetReceiver,
        abi: receiverAbi,
        functionName: "payoutStatus",
        args: [payload.messageId],
      }));
      if (status === 1) {
        await this.write(runtime, { ...base, functionName: "executePayout", args: [payload.messageId] });
        return;
      }
      if (status !== 2) throw new Error(`Unexpected payout status ${status} for ${payload.messageId}`);
      return;
    }
    if (payload.messageType === 4) {
      if (!await this.processed(runtime, payload.targetReceiver, payload.messageId, "processedConfirmations")) {
        await this.write(runtime, { ...base, functionName: "receiveConfirmationV2", args: [envelope, payload.payload] });
      }
      return;
    }
    throw new Error(`Unsupported experiment message type ${payload.messageType}`);
  }

  private async processed(
    runtime: ChainRuntime,
    address: Address,
    messageId: Hex,
    functionName: "processedMessages" | "processedConfirmations",
  ): Promise<boolean> {
    return Boolean(await runtime.publicClient.readContract({ address, abi: receiverAbi, functionName, args: [messageId] }));
  }

  private async write(runtime: ChainRuntime, request: any): Promise<void> {
    const started = performance.now();
    const hash = await runtime.walletClient.writeContract(request);
    const receipt = await runtime.publicClient.waitForTransactionReceipt({ hash, confirmations: runtime.manifest.confirmations });
    if (receipt.status !== "success") throw new Error(`Relay transaction ${hash} reverted`);
    this.onTransaction?.({
      chainId: runtime.manifest.chainId,
      hash,
      blockNumber: receipt.blockNumber,
      gasUsed: receipt.gasUsed,
      durationMs: Math.round((performance.now() - started) * 100) / 100,
      functionName: String(request.functionName),
    });
  }

  private async runtime(chain: ExperimentChainManifest): Promise<ChainRuntime> {
    const existing = this.runtimes.get(chain.chainId);
    if (existing && existing.manifest.rpcUrl === chain.rpcUrl) {
      existing.manifest = chain;
      return existing;
    }
    const publicClient = createPublicClient({ transport: http(chain.rpcUrl), cacheTime: 0 });
    const actualChainId = await publicClient.getChainId();
    if (actualChainId !== chain.chainId) throw new Error(`RPC chainId mismatch for ${chain.key}: expected ${chain.chainId}, got ${actualChainId}`);
    const runtime: ChainRuntime = {
      manifest: chain,
      publicClient,
      walletClient: createWalletClient({ account: this.account, transport: http(chain.rpcUrl) }),
    };
    this.runtimes.set(chain.chainId, runtime);
    return runtime;
  }

  private async verifyCanonical(client: PublicClient, chainKey: string, safeHead: bigint): Promise<void> {
    const depth = BigInt(this.options.reorgDepth);
    const minimum = safeHead > depth ? safeHead - depth : 0n;
    for (const event of this.state.recentProcessedEvents(chainKey, minimum)) {
      const block = await client.getBlock({ blockNumber: event.blockNumber });
      if (block.hash?.toLowerCase() === event.blockHash.toLowerCase()) continue;
      this.state.markProcessedOrphaned(event.eventId);
      throw new Error(`REORG_DETECTED:${event.eventId}`);
    }
  }
}
