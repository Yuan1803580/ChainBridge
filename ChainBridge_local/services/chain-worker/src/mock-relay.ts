import { RouteContextRegistry, type RouteContext } from "./routes.ts";

export type RelayEnvelope = {
  messageId: string;
  routeId: string;
  routeVersion: number;
  businessAttemptId: string;
  messageType: "DISPATCH" | "RESULT";
  payload: Record<string, unknown>;
};

export type RelayDelivery = {
  messageId: string;
  routeIdentity: string;
  businessAttemptId: string;
  relayAttempts: number;
  transactionHash?: string;
};

export interface RelayExecutor {
  deliver(route: RouteContext, envelope: RelayEnvelope): Promise<{ transactionHash?: string }>;
}

type QueuedEnvelope = RelayEnvelope & {
  relayAttempts: number;
  notBefore: number;
  lastError?: string;
};

export class AsyncMockCcipRelay {
  private queue: QueuedEnvelope[] = [];
  private knownMessageIds = new Set<string>();
  private deliveries: RelayDelivery[] = [];
  private routes: RouteContextRegistry;
  private executor: RelayExecutor;
  private options: { delayMs?: number; retryBaseMs?: number; maxRelayAttempts?: number };

  constructor(
    routes: RouteContextRegistry,
    executor: RelayExecutor,
    options: { delayMs?: number; retryBaseMs?: number; maxRelayAttempts?: number } = {},
  ) {
    this.routes = routes;
    this.executor = executor;
    this.options = options;
  }

  enqueue(envelope: RelayEnvelope): void {
    if (!envelope.messageId || !envelope.businessAttemptId) throw new Error("Relay message and business attempt IDs are required");
    if (this.knownMessageIds.has(envelope.messageId)) throw new Error(`Relay message ${envelope.messageId} is a replay`);
    this.routes.resolve(envelope.routeId, envelope.routeVersion);
    this.knownMessageIds.add(envelope.messageId);
    this.queue.push({
      ...envelope,
      relayAttempts: 0,
      notBefore: Date.now() + (this.options.delayMs ?? 25),
    });
  }

  async processOne(now = Date.now()): Promise<RelayDelivery | undefined> {
    const index = this.queue.findIndex((item) => item.notBefore <= now);
    if (index < 0) return undefined;
    const [item] = this.queue.splice(index, 1);
    item.relayAttempts += 1;
    try {
      const route = this.routes.resolve(item.routeId, item.routeVersion);
      const result = await this.executor.deliver(route, item);
      const delivery: RelayDelivery = {
        messageId: item.messageId,
        routeIdentity: `${item.routeId}:${item.routeVersion}`,
        businessAttemptId: item.businessAttemptId,
        relayAttempts: item.relayAttempts,
        transactionHash: result.transactionHash,
      };
      this.deliveries.push(delivery);
      return delivery;
    } catch (error) {
      item.lastError = error instanceof Error ? error.message : String(error);
      const max = this.options.maxRelayAttempts ?? 5;
      if (item.relayAttempts >= max) throw new Error(
        `Relay message ${item.messageId} exhausted ${max} transport attempts: ${item.lastError}`,
      );
      const retryBaseMs = this.options.retryBaseMs ?? 50;
      item.notBefore = now + retryBaseMs * (2 ** (item.relayAttempts - 1));
      this.queue.push(item);
      return undefined;
    }
  }

  async drain(): Promise<RelayDelivery[]> {
    const start = this.deliveries.length;
    while (this.queue.length > 0) {
      const next = Math.min(...this.queue.map((item) => item.notBefore));
      const delay = Math.max(0, next - Date.now());
      if (delay > 0) await new Promise((resolve) => setTimeout(resolve, delay));
      await this.processOne();
    }
    return this.deliveries.slice(start);
  }

  pendingCount(): number {
    return this.queue.length;
  }

  delivered(): RelayDelivery[] {
    return this.deliveries.map((item) => ({ ...item }));
  }
}
