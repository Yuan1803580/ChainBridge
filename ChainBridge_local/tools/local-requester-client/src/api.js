import { newRequestId } from "./utils.js";

async function parseResponse(response) {
  const body = await response.json().catch(() => null);
  if (!response.ok) {
    const detail = body?.detail || body?.error?.message || body?.message || response.statusText;
    throw new Error(`${response.status} ${typeof detail === "string" ? detail : JSON.stringify(detail)}`);
  }
  return body;
}

class HttpClient {
  constructor(baseUrl, token, timeoutMs) {
    this.baseUrl = baseUrl;
    this.token = token;
    this.timeoutMs = timeoutMs;
  }

  async request(path, { method = "GET", body, authenticated = true, idempotency = false } = {}) {
    const headers = { Accept: "application/json" };
    if (body !== undefined) headers["Content-Type"] = "application/json";
    if (authenticated && this.token) headers.Authorization = `Bearer ${this.token}`;
    if (idempotency) headers["Idempotency-Key"] = newRequestId("requester");
    const response = await fetch(`${this.baseUrl}${path}`, {
      method,
      headers,
      body: body === undefined ? undefined : JSON.stringify(body),
      signal: AbortSignal.timeout(this.timeoutMs),
    });
    return parseResponse(response);
  }
}

export class PlatformClient extends HttpClient {
  constructor(config) {
    super(config.platformUrl, config.platformToken, config.requestTimeoutMs);
    this.did = config.requesterDid;
  }

  async authenticate() {
    if (this.token) return;
    const result = await this.request("/api/v1/auth/dev-login", {
      method: "POST",
      body: { did: this.did },
      authenticated: false,
    });
    this.token = result.accessToken;
  }

  publicConfig() {
    return this.request("/api/v1/local-protocol/public-config", { authenticated: false });
  }

  async faucet(body) {
    await this.authenticate();
    return this.request("/api/v1/local-protocol/faucet", { method: "POST", body });
  }

  workflow(id) {
    return this.request(`/api/v1/workflows/${id}`, { authenticated: false });
  }

  async confirmFunding(id, body) {
    await this.authenticate();
    return this.request(`/api/v1/workflows/${id}/funding-confirmations`, {
      method: "POST", body, idempotency: true,
    });
  }

  async coordinate(id, maxActions = 1) {
    await this.authenticate();
    return this.request(`/api/v1/workflows/${id}/coordinate`, {
      method: "POST", body: { maxActions }, idempotency: true,
    });
  }

  async resume(id, maxActions = 4) {
    await this.authenticate();
    return this.request(`/api/v1/workflows/${id}/resume`, {
      method: "POST", body: { maxActions }, idempotency: true,
    });
  }
}

export class CoordinatorClient extends HttpClient {
  constructor(config, profileId = "") {
    const usesPlatformProxy = Boolean(profileId);
    const baseUrl = usesPlatformProxy
      ? `${config.platformUrl}/api/v1/coordinator-runtime/${encodeURIComponent(profileId)}`
      : config.coordinatorUrl;
    super(baseUrl, usesPlatformProxy ? "" : config.coordinatorToken, config.requestTimeoutMs);
    this.usesPlatformProxy = usesPlatformProxy;
  }

  requireToken() {
    if (!this.usesPlatformProxy && !this.token) throw new Error("COORDINATOR_API_TOKEN is required");
  }

  chat(scenario) {
    this.requireToken();
    return this.request("/chat", {
      method: "POST",
      body: {
        message: scenario.message,
        budget: scenario.budget,
        platformFee: scenario.platformFee || "1.10",
        currency: scenario.currency || "USDC",
        inputPayload: scenario.inputPayload || {},
      },
    });
  }

  conversation(conversationId) {
    this.requireToken();
    return this.request(`/conversations/${conversationId}`);
  }

  createWorkflow(conversationId, payerAddress) {
    this.requireToken();
    return this.request(`/conversations/${conversationId}/workflows`, {
      method: "POST",
      body: { fundingMode: "ONCHAIN", payerAddress },
    });
  }

  coordinate(conversationId, maxActions = 1) {
    this.requireToken();
    return this.request(`/conversations/${conversationId}/coordinate`, {
      method: "POST",
      body: { maxActions },
    });
  }

  summary(conversationId) {
    this.requireToken();
    return this.request(`/conversations/${conversationId}/summary`);
  }
}
