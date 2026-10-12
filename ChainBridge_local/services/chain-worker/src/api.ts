import { createHmac, randomUUID } from "node:crypto";

export function signRequest(
  secret: string,
  method: string,
  timestamp: string,
  nonce: string,
  path: string,
  body: string,
): string {
  return `sha256=${createHmac("sha256", secret)
    .update(`${method.toUpperCase()}\n${timestamp}\n${nonce}\n${path}\n${body}`)
    .digest("hex")}`;
}

export class InternalApi {
  private baseUrl: string;
  private secret: string;
  private listenerId: string;
  private fetcher: typeof fetch;

  constructor(
    baseUrl: string,
    secret: string,
    listenerId: string,
    fetcher: typeof fetch = fetch,
  ) {
    this.baseUrl = baseUrl;
    this.secret = secret;
    this.listenerId = listenerId;
    this.fetcher = fetcher;
  }

  async post(path: string, payload: Record<string, unknown>): Promise<Record<string, any>> {
    const body = JSON.stringify(payload);
    const timestamp = String(Math.floor(Date.now() / 1000));
    const nonce = randomUUID();
    const response = await this.fetcher(`${this.baseUrl}${path}`, {
      method: "POST",
      body,
      headers: {
        "Content-Type": "application/json",
        "X-Listener-Id": this.listenerId,
        "X-Listener-Timestamp": timestamp,
        "X-Listener-Nonce": nonce,
        "X-Listener-Signature": signRequest(this.secret, "POST", timestamp, nonce, path, body),
      },
    });
    const result = await response.json() as Record<string, any>;
    if (!response.ok) {
      const message = result?.error?.message ?? `Internal API returned HTTP ${response.status}`;
      throw new Error(message);
    }
    return result;
  }

  reconcile(payload: Record<string, unknown>): Promise<Record<string, any>> {
    return this.post("/api/v1/integration/settlement-events", payload);
  }

  orphan(payload: Record<string, unknown>): Promise<Record<string, any>> {
    return this.post("/api/v1/integration/settlement-events/orphaned", payload);
  }

  lease(workerId: string, leaseSeconds: number): Promise<Record<string, any>> {
    return this.post("/api/v1/integration/relayer-tasks/lease", { workerId, leaseSeconds });
  }

  report(taskId: string, payload: Record<string, unknown>): Promise<Record<string, any>> {
    return this.post(`/api/v1/integration/relayer-tasks/${taskId}/result`, payload);
  }

  heartbeat(taskId: string, payload: Record<string, unknown>): Promise<Record<string, any>> {
    return this.post(`/api/v1/integration/relayer-tasks/${taskId}/heartbeat`, payload);
  }

  checkStalled(thresholdSeconds: number): Promise<Record<string, any>> {
    return this.post("/api/v1/integration/stalled-settlements/check", { thresholdSeconds });
  }
}
