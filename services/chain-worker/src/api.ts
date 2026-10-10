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

/**
 * Error carrying the HTTP status of a failed internal API call.
 *
 * The status matters: a 409 from `/settlement-events` means the API already stored a DIFFERENT
 * chain payload under this `eventId` (main.py: "eventId was already used with different chain
 * data"). That is a data-integrity violation, not a transient fault - retrying can never
 * succeed, so callers must dead-letter it immediately and alert instead of burning the
 * exponential-backoff budget. Benign idempotent replays never reach here: the API answers those
 * with HTTP 200 and `{"duplicate": true}`.
 */
export class ApiError extends Error {
  readonly status: number;
  readonly code?: string;
  readonly path: string;

  constructor(message: string, status: number, path: string, code?: string) {
    super(message);
    this.name = "ApiError";
    this.status = status;
    this.path = path;
    this.code = code;
  }

  get isConflict(): boolean {
    return this.status === 409;
  }
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
    const result = await this.readJson(response);
    if (!response.ok) {
      const message = result?.error?.message
        ?? result?.detail
        ?? `Internal API returned HTTP ${response.status}`;
      throw new ApiError(String(message), response.status, path, result?.error?.code);
    }
    return result;
  }

  /** A non-2xx response is not guaranteed to carry a JSON body (proxy 502s return HTML). */
  private async readJson(response: Response): Promise<Record<string, any>> {
    try {
      return await response.json() as Record<string, any>;
    } catch {
      return {};
    }
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
