import { createHmac, randomUUID } from "node:crypto";

export type ProtocolJob = {
  id: string;
  workflowId?: string;
  nodeId?: string;
  attemptId?: string;
  routeId?: string;
  routeVersion?: number;
  jobType: string;
  status: string;
  payload: Record<string, any>;
  attempts: number;
  transactionHash?: string;
  lastError?: string;
};

export type ProtocolRoute = {
  id: string;
  routeKey: string;
  routeVersion: number;
  sourceChainId: number;
  destinationChainId: number;
  rpcUrl: string;
  transport?: "LOCAL_MOCK" | "CHAINLINK_CCIP";
  sourceChainSelector?: string;
  destinationChainSelector?: string;
  sourceRouterAddress?: string;
  destinationRouterAddress?: string;
  sourceGatewayAddress?: string;
  remoteWorkBoardAddress?: string;
  payoutReceiverAddress?: string;
  tokenAddress?: string;
  sourceTokenAddress?: string;
  destinationTokenAddress?: string;
  tokenDecimals?: number;
  settlementMode?: "mock" | "ccip_direct" | "ccip_dex";
  confirmationBlocks?: number;
  settlementAsset?: string;
  payoutAsset?: string;
  payoutTokenAddress?: string;
  payoutSwapAddress?: string;
  payoutDecimals?: number;
  payoutRate?: string;
  payoutSlippageBps?: number;
  deploymentStatus?: string;
  deploymentManifestHash?: string;
};

export type RouteDeployment = {
  id: string;
  routeId: string;
  routeVersion: number;
  homeChainId: number;
  status: string;
  contracts: Record<string, string>;
  transactionHashes: Record<string, string>;
  codeHashes: Record<string, string>;
  manifestHash?: string;
};

export class ProtocolApi {
  private token?: string;
  private readonly baseUrl: string;
  private readonly adminDid: string;
  private readonly fetcher: typeof fetch;
  private readonly listenerId: string;
  private readonly listenerSecret: string;

  constructor(
    baseUrl: string,
    adminDid = "did:local:platform-admin",
    fetcher: typeof fetch = fetch,
    listenerId = process.env.PROTOCOL_WORKER_LISTENER_ID || "protocol-worker-local",
    listenerSecret = process.env.PROTOCOL_WORKER_LISTENER_SECRET
      || process.env.LISTENER_HMAC_SECRET
      || "listener-local-development-only-secret",
  ) {
    this.baseUrl = baseUrl;
    this.adminDid = adminDid;
    this.fetcher = fetcher;
    this.listenerId = listenerId;
    this.listenerSecret = listenerSecret;
    this.token = process.env.PROTOCOL_WORKER_API_TOKEN?.trim() || undefined;
  }

  private async listenerRequest<T>(method: string, path: string, payload: Record<string, unknown>): Promise<T> {
    const body = JSON.stringify(payload);
    const timestamp = Math.floor(Date.now() / 1000).toString();
    const nonce = randomUUID();
    const signed = [method.toUpperCase(), timestamp, nonce, path, body].join("\n");
    const signature = `sha256=${createHmac("sha256", this.listenerSecret).update(signed).digest("hex")}`;
    const response = await this.fetcher(`${this.baseUrl}${path}`, {
      method,
      headers: {
        Accept: "application/json",
        "Content-Type": "application/json",
        "X-Listener-Id": this.listenerId,
        "X-Listener-Timestamp": timestamp,
        "X-Listener-Nonce": nonce,
        "X-Listener-Signature": signature,
      },
      body,
    });
    const result = await response.json().catch(() => null) as Record<string, any> | null;
    if (!response.ok) {
      throw new Error(result?.detail ?? result?.error?.message ?? `Protocol listener API returned HTTP ${response.status}`);
    }
    return result as T;
  }

  private async login(): Promise<void> {
    const serviceToken = process.env.PROTOCOL_WORKER_API_TOKEN?.trim();
    if (serviceToken) {
      this.token = serviceToken;
      return;
    }
    if (process.env.CHAIN_WORKER_PROFILE === "testnet-ccip") {
      throw new Error("testnet-ccip requires PROTOCOL_WORKER_API_TOKEN; development login is disabled");
    }
    const response = await this.fetcher(`${this.baseUrl}/api/v1/auth/dev-login`, {
      method: "POST",
      headers: { "Content-Type": "application/json", "Idempotency-Key": randomUUID() },
      body: JSON.stringify({ did: this.adminDid }),
    });
    const body = await response.json() as Record<string, any>;
    if (!response.ok || typeof body.accessToken !== "string") {
      throw new Error(body?.error?.message ?? `Protocol Worker login failed (${response.status})`);
    }
    this.token = body.accessToken;
  }

  private async request<T>(
    method: string,
    path: string,
    payload?: Record<string, unknown>,
    authenticated = true,
    retryAuth = true,
  ): Promise<T> {
    if (authenticated && !this.token) await this.login();
    const headers = new Headers({ Accept: "application/json" });
    if (payload) headers.set("Content-Type", "application/json");
    if (authenticated && this.token) headers.set("Authorization", `Bearer ${this.token}`);
    if (method !== "GET") headers.set("Idempotency-Key", randomUUID());
    const response = await this.fetcher(`${this.baseUrl}${path}`, {
      method,
      headers,
      body: payload ? JSON.stringify(payload) : undefined,
    });
    if (response.status === 401 && authenticated && retryAuth) {
      this.token = undefined;
      return this.request(method, path, payload, authenticated, false);
    }
    const body = await response.json().catch(() => null) as Record<string, any> | null;
    if (!response.ok) {
      throw new Error(body?.error?.message ?? body?.detail ?? `Protocol API returned HTTP ${response.status}`);
    }
    return body as T;
  }

  async queuedJobs(limit = 20): Promise<ProtocolJob[]> {
    const response = await this.request<{ items: ProtocolJob[] }>(
      "GET",
      `/api/v1/protocol-jobs?status=QUEUED&limit=${limit}`,
    );
    return response.items;
  }

  updateJob(jobId: string, payload: { status: "RUNNING" | "COMPLETED" | "RETRY" | "FAILED"; transactionHash?: string; error?: string; retryAfterSeconds?: number }): Promise<ProtocolJob> {
    return this.request("PATCH", `/api/v1/protocol-jobs/${jobId}`, payload);
  }

  registerRuntimeContract(role: string, payload: Record<string, unknown>): Promise<Record<string, any>> {
    return this.listenerRequest("PUT", `/api/v1/protocol-runtime/contracts/${role}`, payload);
  }

  confirmReviewChainState(workflowId: string, nodeId: string, attemptId: string, payload: Record<string, unknown>): Promise<Record<string, any>> {
    return this.listenerRequest(
      "POST",
      `/api/v1/workflows/${workflowId}/nodes/${nodeId}/attempts/${attemptId}/review-chain-confirmations`,
      payload,
    );
  }

  async routes(): Promise<ProtocolRoute[]> {
    const response = await this.request<{ items: ProtocolRoute[] }>("GET", "/api/v1/chain-routes", undefined, false);
    return response.items;
  }

  async routeDeployments(): Promise<RouteDeployment[]> {
    const response = await this.request<{ items: RouteDeployment[] }>("GET", "/api/v1/route-deployments", undefined, false);
    return response.items;
  }

  completeRouteDeployment(deploymentId: string, payload: Record<string, unknown>): Promise<RouteDeployment> {
    return this.request("POST", `/api/v1/route-deployments/${deploymentId}/complete`, payload);
  }

  workflow(workflowId: string): Promise<Record<string, any>> {
    return this.request("GET", `/api/v1/workflows/${workflowId}`, undefined, false);
  }

  confirmResultProof(workflowId: string, nodeId: string, attemptId: string, payload: Record<string, unknown>): Promise<Record<string, any>> {
    return this.request(
      "POST",
      `/api/v1/workflows/${workflowId}/nodes/${nodeId}/attempts/${attemptId}/result-proof-confirmations`,
      payload,
    );
  }

  submitAttemptResult(workflowId: string, nodeId: string, attemptId: string, payload: Record<string, unknown>): Promise<Record<string, any>> {
    return this.request(
      "POST",
      `/api/v1/workflows/${workflowId}/nodes/${nodeId}/attempts/${attemptId}/results`,
      payload,
    );
  }

  reportAttemptFailure(workflowId: string, nodeId: string, attemptId: string, payload: Record<string, unknown>): Promise<Record<string, any>> {
    return this.request(
      "POST",
      `/api/v1/workflows/${workflowId}/nodes/${nodeId}/attempts/${attemptId}/failures`,
      payload,
    );
  }

  confirmSettlement(workflowId: string, nodeId: string, attemptId: string, payload: Record<string, unknown>): Promise<Record<string, any>> {
    return this.request(
      "POST",
      `/api/v1/workflows/${workflowId}/nodes/${nodeId}/attempts/${attemptId}/settlement-confirmations`,
      payload,
    );
  }

  confirmFailedRefund(workflowId: string, payload: Record<string, unknown>): Promise<Record<string, any>> {
    return this.request("POST", `/api/v1/workflows/${workflowId}/failed-refund-confirmations`, payload);
  }

  confirmWorkflowFinalization(workflowId: string, payload: Record<string, unknown>): Promise<Record<string, any>> {
    return this.request("POST", `/api/v1/workflows/${workflowId}/finalization-confirmations`, payload);
  }
}
