import { agents, auditEntries, capabilities, credentials, demands, notifications, orders } from "./mock-data";
import type { Agent, AgentRole, ApiResult, AuditEntry, Bid, Capability, Credential, Demand, DevIdentity, JsonSchema, Notification, Order, OrderEvent, OrderMessage, SettlementInfo, SettlementQuote } from "./types";

const API_URL = (process.env.NEXT_PUBLIC_API_URL || "http://localhost:8000").replace(/\/$/, "");
const MOCK_FALLBACK = process.env.NEXT_PUBLIC_ENABLE_MOCK_FALLBACK === "true";

type RequestOptions = RequestInit & { idempotencyKey?: string };
type JsonObject = Record<string, unknown>;

async function request<T>(path: string, options: RequestOptions = {}): Promise<T> {
  const { idempotencyKey, ...init } = options;
  const token = typeof window !== "undefined" ? window.localStorage.getItem("agentMarketToken") : null;
  const headers = new Headers(init.headers);
  headers.set("Accept", "application/json");
  if (init.body) headers.set("Content-Type", "application/json");
  if (token) headers.set("Authorization", `Bearer ${token}`);
  if (idempotencyKey) headers.set("Idempotency-Key", idempotencyKey);

  const response = await fetch(`${API_URL}${path}`, {
    ...init,
    headers,
    cache: "no-store",
    signal: init.signal || AbortSignal.timeout(4000)
  });
  if (!response.ok) {
    const body = await response.json().catch(() => null) as { detail?: string | { message?: string }; error?: { message?: string } } | null;
    const detail = typeof body?.detail === "string" ? body.detail : body?.detail?.message;
    throw new Error(body?.error?.message || detail || `请求失败（${response.status}）`);
  }
  return response.json() as Promise<T>;
}

function object(value: unknown): JsonObject {
  return value && typeof value === "object" && !Array.isArray(value) ? value as JsonObject : {};
}

function listFrom(value: unknown): unknown[] {
  if (Array.isArray(value)) return value;
  const record = object(value);
  return Array.isArray(record.items) ? record.items : [];
}

function textValue(value: unknown, fallback = ""): string {
  return typeof value === "string" ? value : typeof value === "number" ? String(value) : fallback;
}

function numberValue(value: unknown, fallback = 0): number {
  return typeof value === "number" ? value : typeof value === "string" && !Number.isNaN(Number(value)) ? Number(value) : fallback;
}

function stringList(value: unknown): string[] {
  return Array.isArray(value) ? value.filter((item): item is string => typeof item === "string") : [];
}

function schemaValue(value: unknown): JsonSchema {
  const record = object(value);
  return record.type === "object" && record.properties && typeof record.properties === "object"
    ? record as JsonSchema
    : { type: "object", properties: {} };
}

function duration(seconds: unknown): string {
  const value = numberValue(seconds, 60);
  return value < 60 ? `${value} 秒` : `${Math.ceil(value / 60)} 分钟`;
}

function categoryFor(value: string): string {
  if (/统计|数据/.test(value)) return "数据分析";
  if (/代码|开发/.test(value)) return "开发工具";
  if (/哈希|审计|安全/.test(value)) return "安全审计";
  if (/摘要|文本/.test(value)) return "文本处理";
  return "通用能力";
}

function normalizeAgent(value: unknown): Agent {
  const raw = object(value);
  const roleValues = stringList(raw.roles).filter((role): role is AgentRole => ["requester", "provider", "admin"].includes(role));
  const rawCapabilities = Array.isArray(raw.capabilities) ? raw.capabilities : [];
  const names = rawCapabilities.map((item) => textValue(object(item).name)).filter(Boolean);
  const status = ["active", "pending", "suspended"].includes(textValue(raw.status)) ? textValue(raw.status) as Agent["status"] : "active";
  return {
    id: textValue(raw.id),
    did: textValue(raw.did),
    name: textValue(raw.displayName, textValue(raw.name, "未命名 Agent")),
    description: textValue(raw.description, "该 Agent 尚未填写简介。"),
    roles: roleValues,
    status,
    verified: status === "active",
    availability: status === "active" ? "online" : "offline",
    rating: textValue(raw.reputationScore, "0.0"),
    completedOrders: numberValue(raw.completedOrders),
    successRate: numberValue(raw.completedOrders) > 0 ? "100%" : "暂无记录",
    responseTime: "未统计",
    capabilities: names,
    serviceEndpoint: textValue(raw.endpoint, "未配置"),
    createdAt: textValue(raw.createdAt, new Date(0).toISOString())
  };
}

function normalizeCredential(value: unknown): Credential {
  const raw = object(value);
  const revoked = raw.revoked === true;
  return {
    id: textValue(raw.id), type: textValue(raw.credentialType, "VerifiableCredential"), issuer: textValue(raw.issuerDid),
    subject: textValue(raw.subjectDid, textValue(raw.agentId)), issuedAt: textValue(raw.createdAt, new Date(0).toISOString()),
    expiresAt: textValue(raw.expiresAt, "2099-12-31T00:00:00Z"), status: revoked ? "revoked" : "valid", claims: object(raw.claims) as Record<string, string>
  };
}

function normalizeCapability(value: unknown): Capability {
  const raw = object(value);
  const provider = object(raw.provider);
  const title = textValue(raw.name, textValue(raw.title, "未命名能力"));
  const exampleRequest = object(raw.exampleInput ?? raw.exampleRequest);
  const mockMatch = capabilities.find((item) => item.title.includes(title) || title.includes(item.title.replace("长文本结构化", "文本")));
  return {
    id: textValue(raw.id), providerId: textValue(raw.providerId),
    providerName: textValue(provider.displayName, mockMatch?.providerName || "已验证供给方"),
    providerDid: textValue(provider.did, mockMatch?.providerDid || "did:local:verified-provider"),
    title, summary: textValue(raw.description, textValue(raw.summary)), category: categoryFor(title),
    tags: mockMatch?.tags || [categoryFor(title), "DID 已验证"], endpoint: textValue(raw.endpoint), method: "POST",
    pricingModel: (["per_request", "per_token", "fixed"].includes(textValue(raw.pricingModel)) ? textValue(raw.pricingModel) : "fixed") as Capability["pricingModel"],
    basePrice: textValue(raw.basePrice, "0"), currency: textValue(raw.currency, "USDC"), estimatedDuration: duration(raw.estimatedDurationSeconds),
    availability: (["available", "busy", "offline"].includes(textValue(raw.availability)) ? textValue(raw.availability) : "offline") as Capability["availability"],
    version: textValue(raw.version, "1.0.0"), inputSchema: schemaValue(raw.inputSchema), outputSchema: schemaValue(raw.outputSchema),
    exampleRequest, exampleResponse: mockMatch?.exampleResponse || { status: "completed", output: {} },
    completedOrders: numberValue(raw.completedOrders), rating: textValue(raw.rating, "暂无"), updatedAt: textValue(raw.updatedAt, textValue(raw.createdAt, new Date(0).toISOString()))
  };
}

function normalizeBid(value: unknown): Bid {
  const raw = object(value);
  const provider = object(raw.provider);
  return {
    id: textValue(raw.id), demandId: textValue(raw.demandId), providerId: textValue(raw.providerId),
    providerName: textValue(provider.displayName, "已验证供给方"), providerDid: textValue(provider.did, "did:local:verified-provider"),
    amount: textValue(raw.amount, "0"), currency: textValue(raw.currency, "USDC"), estimatedDuration: duration(raw.estimatedDurationSeconds),
    proposal: textValue(raw.proposal), status: textValue(raw.status, "pending") as Bid["status"], createdAt: textValue(raw.createdAt, new Date(0).toISOString())
  };
}

function normalizeDemand(value: unknown): Demand {
  const raw = object(value);
  const requester = object(raw.requester);
  const bidValues = Array.isArray(raw.bids) ? raw.bids.map(normalizeBid) : undefined;
  const title = textValue(raw.title, "未命名需求");
  const criteria = textValue(raw.acceptanceCriteria);
  const budget = textValue(raw.budget, textValue(raw.budgetMax, "0"));
  return {
    id: textValue(raw.id), requesterId: textValue(raw.requesterId), requesterName: textValue(requester.displayName, "已验证需求方"),
    requesterDid: textValue(requester.did, "did:local:verified-requester"), title, description: textValue(raw.description),
    category: categoryFor(title), tags: [categoryFor(title)], budgetMin: textValue(raw.budgetMin, budget), budgetMax: textValue(raw.budgetMax, budget),
    currency: textValue(raw.currency, "USDC"), deadline: textValue(raw.deadline, textValue(raw.createdAt, new Date(0).toISOString())),
    acceptanceCriteria: criteria.split(/\r?\n/).filter(Boolean), status: textValue(raw.status, "published") as Demand["status"],
    bidCount: bidValues?.length || numberValue(raw.bidCount), bids: bidValues, createdAt: textValue(raw.createdAt, new Date(0).toISOString())
  };
}

const eventTitles: Record<string, string> = { "order.created": "订单已创建", "order.funded": "资金已锁定", "order.started": "开始执行", "order.delivered": "等待验收", "order.accepted": "交付已验收", "order.completed": "订单已完成", "order.disputed": "订单争议" };

function normalizeEvent(value: unknown): OrderEvent {
  const raw = object(value);
  const eventType = textValue(raw.eventType, "order.updated");
  return { id: textValue(raw.id), type: eventType, title: eventTitles[eventType] || eventType, description: `状态更新为 ${textValue(raw.toStatus, "未知")}`, actorName: "已验证 Agent", createdAt: textValue(raw.createdAt, new Date(0).toISOString()) };
}

function normalizeMessage(value: unknown): OrderMessage {
  const raw = object(value);
  return { id: textValue(raw.id), senderId: textValue(raw.senderId), senderName: "订单参与方", content: textValue(raw.content), createdAt: textValue(raw.createdAt, new Date(0).toISOString()) };
}

function normalizeSettlementQuote(value: unknown): SettlementQuote | undefined {
  const raw = object(value);
  const quoteHash = textValue(raw.quoteHash);
  if (!quoteHash) return undefined;
  return {
    sourceAsset: textValue(raw.sourceAsset, "ETH-like"),
    settlementToken: textValue(raw.settlementToken, "USDC"),
    sourceAmount: textValue(raw.sourceAmount, "0"),
    quotedSettlementAmount: textValue(raw.quotedSettlementAmount, "0"),
    minSettlementAmount: textValue(raw.minSettlementAmount, "0"),
    maxSlippageBps: numberValue(raw.maxSlippageBps),
    quoteDeadline: textValue(raw.quoteDeadline, new Date(0).toISOString()),
    quoteHash,
    rate: textValue(raw.rate, "0"),
    termsHash: textValue(raw.termsHash)
  };
}

function normalizeSettlement(value: unknown): SettlementInfo | undefined {
  const raw = object(value);
  const id = textValue(raw.id);
  if (!id) return undefined;
  return {
    id,
    status: textValue(raw.status, "created") as Order["settlementStatus"],
    amount: textValue(raw.amount, "0"),
    currency: textValue(raw.currency, "USDC"),
    payerAccount: textValue(raw.payerAccount) || undefined,
    payeeAccount: textValue(raw.payeeAccount) || undefined,
    assetId: textValue(raw.assetId) || undefined,
    sourceChain: textValue(raw.sourceChain) || undefined,
    destinationChain: textValue(raw.destinationChain) || undefined,
    transactionHash: textValue(raw.transactionHash) || undefined,
    ccipMessageId: textValue(raw.ccipMessageId) || undefined
  };
}

function normalizeOrder(value: unknown): Order {
  const raw = object(value);
  const requester = object(raw.requester);
  const provider = object(raw.provider);
  const settlement = object(raw.settlement);
  const termsSnapshot = object(raw.termsSnapshot);
  const settlementInfo = normalizeSettlement(raw.settlement);
  const settlementQuote = normalizeSettlementQuote(termsSnapshot.settlementQuote);
  const delivery = raw.delivery ? object(raw.delivery) : undefined;
  const sourceType = textValue(raw.sourceType);
  const id = textValue(raw.id);
  return {
    id, orderNumber: textValue(raw.orderNumber, `ORD-${id.slice(0, 8).toUpperCase()}`), source: sourceType === "direct" ? "capability" : "demand",
    sourceTitle: textValue(raw.title, "未命名订单"), requesterId: textValue(raw.requesterId), requesterName: textValue(requester.displayName, "需求方 Agent"),
    providerId: textValue(raw.providerId), providerName: textValue(provider.displayName, "供给方 Agent"), amount: textValue(raw.amount, "0"), currency: textValue(raw.currency, "USDC"),
    status: textValue(raw.status, "awaiting_funding") as Order["status"], settlementStatus: textValue(settlement.status, "created") as Order["settlementStatus"],
    settlementId: textValue(raw.settlementId, textValue(settlement.id, "尚未创建")), settlement: settlementInfo, settlementQuote, termsHash: textValue(raw.termsHash, "尚未生成"), input: object(raw.inputPayload),
    events: Array.isArray(raw.events) ? raw.events.map(normalizeEvent) : [], messages: Array.isArray(raw.messages) ? raw.messages.map(normalizeMessage) : [],
    delivery: delivery ? { id: textValue(delivery.id), summary: textValue(delivery.summary), uri: textValue(delivery.artifactUri), contentHash: textValue(delivery.contentHash), createdAt: textValue(delivery.createdAt, new Date(0).toISOString()) } : undefined,
    createdAt: textValue(raw.createdAt, new Date(0).toISOString()), updatedAt: textValue(raw.updatedAt, textValue(raw.createdAt, new Date(0).toISOString()))
  };
}

async function withFallback<T>(load: () => Promise<T>, fallback: T): Promise<ApiResult<T>> {
  if (MOCK_FALLBACK) return { data: fallback, source: "mock" };
  try { return { data: await load(), source: "api" }; }
  catch (error) { if (!MOCK_FALLBACK) throw error; return { data: fallback, source: "mock" }; }
}

const mockIdentities: DevIdentity[] = agents.map((agent) => ({ id: agent.id, did: agent.did, displayName: agent.name, roles: agent.roles }));

export const api = {
  baseUrl: API_URL,
  auth: {
    devIdentities: () => withFallback(async () => listFrom(await request<unknown>("/api/v1/dev/identities")).map((value) => { const raw = object(value); return { id: textValue(raw.id), did: textValue(raw.did), displayName: textValue(raw.displayName), roles: stringList(raw.roles) as AgentRole[] }; }), mockIdentities),
    devLogin: async (did: string) => {
      const result = await request<{ accessToken: string; expiresAt: string }>("/api/v1/auth/dev-login", { method: "POST", body: JSON.stringify({ did }), idempotencyKey: crypto.randomUUID() });
      window.localStorage.setItem("agentMarketToken", result.accessToken);
      window.localStorage.setItem("agentMarketTokenExpiresAt", result.expiresAt);
      window.localStorage.setItem("agentMarketCurrentDid", did);
      window.dispatchEvent(new CustomEvent("agent-market-auth-changed", { detail: { did } }));
      return result;
    },
    me: () => withFallback(async () => normalizeAgent(await request<unknown>("/api/v1/me")), agents[0])
  },
  agents: {
    list: () => withFallback(async () => listFrom(await request<unknown>("/api/v1/agents")).map(normalizeAgent), agents),
    get: (id: string) => withFallback(async () => normalizeAgent(await request<unknown>(`/api/v1/agents/${id}`)), agents.find((item) => item.id === id) || agents[0]),
    credentials: (id: string) => withFallback(async () => listFrom(await request<unknown>(`/api/v1/agents/${id}/credentials`)).map(normalizeCredential), credentials)
  },
  capabilities: {
    list: () => withFallback(async () => listFrom(await request<unknown>("/api/v1/capabilities")).map(normalizeCapability), capabilities),
    get: (id: string) => withFallback(async () => normalizeCapability(await request<unknown>(`/api/v1/capabilities/${id}`)), capabilities.find((item) => item.id === id) || capabilities[0]),
    invoke: (id: string, input: Record<string, unknown>) => request<Record<string, unknown>>(`/api/v1/capabilities/${id}/invoke`, { method: "POST", body: JSON.stringify({ inputPayload: input }), idempotencyKey: crypto.randomUUID() }),
    order: async (id: string, input: Record<string, unknown>) => normalizeOrder(await request<unknown>(`/api/v1/capabilities/${id}/orders`, { method: "POST", body: JSON.stringify({ inputPayload: input }), idempotencyKey: crypto.randomUUID() })),
    create: (payload: { name: string; slug: string; description: string; inputSchema: JsonSchema; outputSchema: JsonSchema; exampleInput: Record<string, unknown>; endpoint: string; pricingModel: string; basePrice: string; currency: string; estimatedDurationSeconds: number; availability: string; version: string }) => request<unknown>("/api/v1/capabilities", { method: "POST", body: JSON.stringify(payload), idempotencyKey: crypto.randomUUID() })
  },
  demands: {
    list: () => withFallback(async () => listFrom(await request<unknown>("/api/v1/demands")).map(normalizeDemand), demands),
    get: (id: string) => withFallback(async () => normalizeDemand(await request<unknown>(`/api/v1/demands/${id}`)), demands.find((item) => item.id === id) || demands[0]),
    bid: (id: string, payload: { amount: string; currency: string; estimatedDurationSeconds: number; proposal: string }) => request(`/api/v1/demands/${id}/bids`, { method: "POST", body: JSON.stringify(payload), idempotencyKey: crypto.randomUUID() }),
    create: (payload: { title: string; description: string; inputPayload: Record<string, unknown>; acceptanceCriteria: string; budget: string; currency: string; deadline?: string; status: "published" | "draft" }) => request<unknown>("/api/v1/demands", { method: "POST", body: JSON.stringify(payload), idempotencyKey: crypto.randomUUID() }),
    acceptBid: async (id: string) => normalizeOrder(await request<unknown>(`/api/v1/bids/${id}/accept`, { method: "POST", idempotencyKey: crypto.randomUUID() }))
  },
  orders: {
    list: () => withFallback(async () => listFrom(await request<unknown>("/api/v1/orders")).map(normalizeOrder), orders),
    get: (id: string) => withFallback(async () => normalizeOrder(await request<unknown>(`/api/v1/orders/${id}`)), orders.find((item) => item.id === id) || orders[0]),
    quoteSettlement: async (id: string, body: { sourceAsset: string; settlementToken: string; sourceAmount: string; maxSlippageBps: number; sourceDecimals: number; settlementDecimals: number }) => request<SettlementQuote>(`/api/v1/orders/${id}/settlement-quote`, { method: "POST", body: JSON.stringify(body) }),
    action: async (id: string, action: "fund" | "start" | "accept" | "dispute" | "refund", body?: Record<string, unknown>) => normalizeOrder(await request<unknown>(`/api/v1/orders/${id}/${action}`, { method: "POST", body: body ? JSON.stringify(body) : undefined, idempotencyKey: crypto.randomUUID() })),
    deliver: async (id: string, body: { artifactUri: string; contentHash: string; summary: string; outputPayload?: Record<string, unknown> }) => normalizeOrder(await request<unknown>(`/api/v1/orders/${id}/deliver`, { method: "POST", body: JSON.stringify(body), idempotencyKey: crypto.randomUUID() })),
    message: (id: string, content: string) => request(`/api/v1/orders/${id}/messages`, { method: "POST", body: JSON.stringify({ content }), idempotencyKey: crypto.randomUUID() })
  },
  notifications: () => withFallback(async () => listFrom(await request<unknown>("/api/v1/notifications")).map((value): Notification => { const raw = object(value); return { id: textValue(raw.id), title: textValue(raw.title), body: textValue(raw.body), read: Boolean(raw.readAt), createdAt: textValue(raw.createdAt, new Date(0).toISOString()) }; }), notifications),
  audit: () => withFallback(async () => listFrom(await request<unknown>("/api/v1/audit-logs")).map((value): AuditEntry => { const raw = object(value); return { id: textValue(raw.id), actor: textValue(raw.actorId, "system"), action: textValue(raw.action), resource: `${textValue(raw.resourceType)}:${textValue(raw.resourceId)}`, result: "success", createdAt: textValue(raw.createdAt, new Date(0).toISOString()) }; }), auditEntries),
  health: () => request<{ status: string }>("/api/v1/health")
};
