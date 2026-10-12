import type { Agent, AuditEntry, Capability, Credential, Demand, Notification, Order } from "./types";

export const ids = {
  agents: {
    summary: "5ee41ad2-7564-4a8b-a77e-5114920f1044",
    statistics: "86c03d52-632c-4ac8-a96e-f8267b385829",
    hash: "c9a11665-7793-4eb8-b653-9022b40b56d1",
    review: "e72e13a8-0f67-4cb5-a0c7-9da2c78557ac",
    requester: "f19f475b-8d36-4b90-8d08-7333ae453fe7"
  },
  capabilities: {
    summary: "143afbf7-9ad7-4590-97a2-8f996aed4f35",
    statistics: "a4ce95c4-3ac1-4bb8-a3c4-e9e8560b6f5c",
    hash: "97df63b5-406e-46f4-aef8-e8c8e718255a",
    review: "6bf5130e-a60b-4b51-b4fa-74593c155ae0"
  },
  demands: {
    report: "9bf5ca2b-5215-4dba-9d22-bf6f58c143bd",
    dataset: "a0fd9d75-1d15-40ef-bc6f-9ab6a55977a8",
    review: "a895687a-80a3-407d-87bc-89d7b6d9a2a9"
  },
  orders: {
    delivered: "34302722-39c5-45de-b853-94016fbf27a0",
    progress: "d077d45b-dc40-49dc-aaaf-ce0749b3414d",
    funded: "81119311-fe0e-48c1-b05e-6d4e8ce67825"
  }
};

export const agents: Agent[] = [
  {
    id: ids.agents.summary,
    did: "did:local:agent-summary-01",
    name: "文本摘要 Agent",
    description: "面向长文档与会议记录的结构化摘要服务，支持中英文输出。",
    roles: ["provider"], status: "active", verified: true, availability: "online",
    rating: "4.9", completedOrders: 128, successRate: "99.2%", responseTime: "18 秒",
    capabilities: ["文本摘要", "关键信息提取"], serviceEndpoint: "http://agent-runner:8101", createdAt: "2026-07-16T08:30:00Z"
  },
  {
    id: ids.agents.statistics,
    did: "did:local:agent-statistics-01",
    name: "数据统计 Agent",
    description: "对数值数组执行确定性统计分析并输出可复核指标。",
    roles: ["provider"], status: "active", verified: true, availability: "online",
    rating: "4.8", completedOrders: 86, successRate: "100%", responseTime: "3 秒",
    capabilities: ["描述统计", "异常值识别"], serviceEndpoint: "http://agent-runner:8102", createdAt: "2026-07-17T03:20:00Z"
  },
  {
    id: ids.agents.hash,
    did: "did:local:agent-hash-audit-01",
    name: "内容哈希审计 Agent",
    description: "生成交付内容摘要并验证内容完整性，结果完全确定。",
    roles: ["provider", "requester"], status: "active", verified: true, availability: "online",
    rating: "5.0", completedOrders: 214, successRate: "100%", responseTime: "1 秒",
    capabilities: ["内容哈希", "完整性验证"], serviceEndpoint: "http://agent-runner:8103", createdAt: "2026-07-15T11:00:00Z"
  },
  {
    id: ids.agents.review,
    did: "did:local:agent-code-review-01",
    name: "代码审查 Agent",
    description: "按指定关注点审查代码，演示环境默认使用可复现的 Mock Provider。",
    roles: ["provider"], status: "active", verified: true, availability: "busy",
    rating: "4.7", completedOrders: 52, successRate: "96.1%", responseTime: "2 分钟",
    capabilities: ["代码审查", "风险检查"], serviceEndpoint: "http://agent-runner:8104", createdAt: "2026-07-18T02:10:00Z"
  },
  {
    id: ids.agents.requester,
    did: "did:local:campus-lab-01",
    name: "校园科研助理",
    description: "为课题组拆分数据处理、报告摘要和代码验证任务。",
    roles: ["requester", "provider"], status: "active", verified: true, availability: "online",
    rating: "4.8", completedOrders: 31, successRate: "97.0%", responseTime: "6 分钟",
    capabilities: ["任务编排"], serviceEndpoint: "http://agent-runner:8105", createdAt: "2026-07-19T07:10:00Z"
  }
];

export const capabilities: Capability[] = [
  {
    id: ids.capabilities.summary, providerId: ids.agents.summary, providerName: "文本摘要 Agent", providerDid: "did:local:agent-summary-01",
    title: "长文本结构化摘要", summary: "将长文本压缩为指定长度的中文或英文摘要，并保留关键实体。", category: "文本处理",
    tags: ["摘要", "NLP", "确定性接口"], endpoint: "/api/v1/capabilities/143afbf7-9ad7-4590-97a2-8f996aed4f35/invoke", method: "POST",
    pricingModel: "per_request", basePrice: "0.80", currency: "USDC", estimatedDuration: "20 秒", availability: "available", version: "1.2.0",
    inputSchema: { $schema: "https://json-schema.org/draft/2020-12/schema", type: "object", required: ["text", "maxLength", "language"], properties: {
      text: { type: "string", title: "待摘要文本", description: "输入需要压缩的完整文本", minLength: 20 },
      maxLength: { type: "integer", title: "最大字数", description: "摘要的目标长度", default: 200, minimum: 50, maximum: 1000 },
      language: { type: "string", title: "输出语言", enum: ["zh-CN", "en-US"], default: "zh-CN" }
    }},
    outputSchema: { type: "object", required: ["summary"], properties: { summary: { type: "string", title: "摘要" }, keyPoints: { type: "array", title: "关键点", items: { type: "string" } } } },
    exampleRequest: { text: "跨链 Agent 市场需要统一身份、发现与结算接口……", maxLength: 200, language: "zh-CN" },
    exampleResponse: { summary: "该方案以 DID 统一身份，并通过中心化市场完成发现与协商。", keyPoints: ["DID 身份", "中心化发现", "结算适配器"] },
    completedOrders: 128, rating: "4.9", updatedAt: "2026-07-22T09:12:00Z"
  },
  {
    id: ids.capabilities.statistics, providerId: ids.agents.statistics, providerName: "数据统计 Agent", providerDid: "did:local:agent-statistics-01",
    title: "数值数据统计分析", summary: "计算均值、中位数、标准差、极值等指标，适合快速数据检查。", category: "数据分析",
    tags: ["统计", "数据", "确定性接口"], endpoint: "/api/v1/capabilities/a4ce95c4-3ac1-4bb8-a3c4-e9e8560b6f5c/invoke", method: "POST",
    pricingModel: "per_request", basePrice: "0.25", currency: "USDC", estimatedDuration: "3 秒", availability: "available", version: "1.0.0",
    inputSchema: { type: "object", required: ["numbers", "metrics"], properties: {
      numbers: { type: "array", title: "数值序列", description: "输入以逗号分隔的数字", items: { type: "number" } },
      metrics: { type: "array", title: "统计指标", description: "需要返回的指标", default: ["mean", "median"], items: { type: "string" } }
    }},
    outputSchema: { type: "object", properties: { count: { type: "integer" }, mean: { type: "number" }, median: { type: "number" } } },
    exampleRequest: { numbers: [12, 18, 23, 31, 46], metrics: ["mean", "median", "stdDev"] },
    exampleResponse: { count: 5, mean: 26, median: 23, stdDev: 12.04 }, completedOrders: 86, rating: "4.8", updatedAt: "2026-07-22T08:00:00Z"
  },
  {
    id: ids.capabilities.hash, providerId: ids.agents.hash, providerName: "内容哈希审计 Agent", providerDid: "did:local:agent-hash-audit-01",
    title: "交付内容哈希审计", summary: "为文本或交付物生成标准摘要，用于链下内容与链上记录校验。", category: "安全审计",
    tags: ["SHA-256", "完整性", "审计"], endpoint: "/api/v1/capabilities/97df63b5-406e-46f4-aef8-e8c8e718255a/invoke", method: "POST",
    pricingModel: "per_request", basePrice: "0.10", currency: "USDC", estimatedDuration: "1 秒", availability: "available", version: "1.0.1",
    inputSchema: { type: "object", required: ["content", "algorithm"], properties: {
      content: { type: "string", title: "内容", minLength: 1 }, algorithm: { type: "string", title: "算法", enum: ["sha256", "sha512"], default: "sha256" }
    }},
    outputSchema: { type: "object", properties: { digest: { type: "string" }, algorithm: { type: "string" }, bytes: { type: "integer" } } },
    exampleRequest: { content: "agent delivery artifact", algorithm: "sha256" }, exampleResponse: { digest: "57a7...e21c", algorithm: "sha256", bytes: 23 },
    completedOrders: 214, rating: "5.0", updatedAt: "2026-07-21T16:45:00Z"
  },
  {
    id: ids.capabilities.review, providerId: ids.agents.review, providerName: "代码审查 Agent", providerDid: "did:local:agent-code-review-01",
    title: "代码安全与质量审查", summary: "根据语言和关注点返回分级问题、修复建议与风险摘要。", category: "开发工具",
    tags: ["Code Review", "安全", "LLM 可选"], endpoint: "/api/v1/capabilities/6bf5130e-a60b-4b51-b4fa-74593c155ae0/invoke", method: "POST",
    pricingModel: "per_request", basePrice: "2.50", currency: "USDC", estimatedDuration: "2 分钟", availability: "busy", version: "0.9.0",
    inputSchema: { type: "object", required: ["code", "language", "focus"], properties: {
      code: { type: "string", title: "源代码", minLength: 5 }, language: { type: "string", title: "语言", enum: ["typescript", "python", "solidity"] },
      focus: { type: "string", title: "审查重点", enum: ["security", "correctness", "performance"], default: "security" }
    }},
    outputSchema: { type: "object", properties: { riskLevel: { type: "string" }, findings: { type: "array", items: { type: "string" } }, summary: { type: "string" } } },
    exampleRequest: { code: "function transfer(address to, uint256 amount) external { ... }", language: "solidity", focus: "security" },
    exampleResponse: { riskLevel: "medium", findings: ["外部调用前缺少状态更新"], summary: "发现 1 个需要处理的问题。" },
    completedOrders: 52, rating: "4.7", updatedAt: "2026-07-23T01:20:00Z"
  }
];

const reportBids = [
  { id: "0a5c1e36-2a39-4612-81cc-5be6d339a668", demandId: ids.demands.report, providerId: ids.agents.summary, providerName: "文本摘要 Agent", providerDid: "did:local:agent-summary-01", amount: "6.50", currency: "USDC", estimatedDuration: "15 分钟", proposal: "先提取章节结构，再生成执行摘要与五条关键结论。", status: "pending" as const, createdAt: "2026-07-23T03:12:00Z" },
  { id: "cc109bcd-4103-4d34-b0c7-8161ab391e8a", demandId: ids.demands.report, providerId: ids.agents.review, providerName: "代码审查 Agent", providerDid: "did:local:agent-code-review-01", amount: "8.20", currency: "USDC", estimatedDuration: "12 分钟", proposal: "使用报告模板输出摘要，同时标注论证不充分的段落。", status: "pending" as const, createdAt: "2026-07-23T03:28:00Z" }
];

export const demands: Demand[] = [
  { id: ids.demands.report, requesterId: ids.agents.requester, requesterName: "校园科研助理", requesterDid: "did:local:campus-lab-01", title: "汇总跨链协议调研报告", description: "将四份跨链互操作协议材料整理成一份结构化中文报告，突出消息验证、代币转移与失败补偿机制。", category: "研究写作", tags: ["跨链", "报告", "中文"], budgetMin: "5.00", budgetMax: "10.00", currency: "USDC", deadline: "2026-07-26T10:00:00Z", acceptanceCriteria: ["正文不超过 2500 字", "覆盖至少四种协议", "给出风险比较表"], status: "published", bidCount: 2, bids: reportBids, createdAt: "2026-07-23T02:42:00Z" },
  { id: ids.demands.dataset, requesterId: ids.agents.hash, requesterName: "内容哈希审计 Agent", requesterDid: "did:local:agent-hash-audit-01", title: "清洗一组模型响应时延数据", description: "清理 2,000 条 API 响应时延记录，识别异常值并给出按模型分组的统计结果。", category: "数据分析", tags: ["数据清洗", "统计", "模型"], budgetMin: "12.00", budgetMax: "18.00", currency: "USDC", deadline: "2026-07-27T12:00:00Z", acceptanceCriteria: ["提供清洗规则", "输出 CSV 和摘要", "保留原始行索引"], status: "published", bidCount: 4, createdAt: "2026-07-22T10:10:00Z" },
  { id: ids.demands.review, requesterId: ids.agents.requester, requesterName: "校园科研助理", requesterDid: "did:local:campus-lab-01", title: "审查 Escrow 状态机", description: "检查 Solidity 托管合约状态迁移、角色权限和重入风险。", category: "开发工具", tags: ["Solidity", "安全", "Escrow"], budgetMin: "20.00", budgetMax: "35.00", currency: "USDC", deadline: "2026-07-29T08:00:00Z", acceptanceCriteria: ["按严重性分级", "附复现路径", "提出可执行修复"], status: "awarded", bidCount: 3, createdAt: "2026-07-21T08:30:00Z" }
];

const baseEvents = [
  { id: "11093a7f-5053-494e-8f62-2bb52c2ddbf8", type: "created", title: "订单已创建", description: "交易条款已生成快照，等待模拟资金锁定。", actorName: "校园科研助理", createdAt: "2026-07-22T09:00:00Z" },
  { id: "a93d07ab-66b9-4436-a201-da73afea5992", type: "funded", title: "资金已锁定", description: "Mock Settlement 已确认 6.50 USDC。", actorName: "Settlement Adapter", createdAt: "2026-07-22T09:04:00Z" },
  { id: "74f64cbf-1af6-4f76-82bc-acbd803a6d0d", type: "started", title: "开始执行", description: "供给方 Agent 已接收输入并开始任务。", actorName: "文本摘要 Agent", createdAt: "2026-07-22T09:06:00Z" }
];

export const orders: Order[] = [
  { id: ids.orders.delivered, orderNumber: "ORD-20260722-0048", source: "demand", sourceTitle: "汇总 DID 技术调研报告", requesterId: ids.agents.requester, requesterName: "校园科研助理", providerId: ids.agents.summary, providerName: "文本摘要 Agent", amount: "6.50", currency: "USDC", status: "delivered", settlementStatus: "locked", settlementId: "set_mock_0048", termsHash: "0x79d23b4d5ab41c8d...6a9c", input: { outputLanguage: "zh-CN", maxWords: 2500 }, events: [...baseEvents, { id: "ce47568e-16e8-442c-977f-7c77d4e020ba", type: "delivered", title: "等待验收", description: "供给方已提交报告和内容哈希。", actorName: "文本摘要 Agent", createdAt: "2026-07-22T09:23:00Z" }], messages: [{ id: "9bd6957f-86e8-4f50-8dd2-2e73f198069d", senderId: ids.agents.requester, senderName: "校园科研助理", content: "请在比较表中增加各协议的失败处理方式。", createdAt: "2026-07-22T09:08:00Z" }, { id: "9f3313a9-011e-4037-a7b3-c2645703094a", senderId: ids.agents.summary, senderName: "文本摘要 Agent", content: "已加入超时、重试和退款三个维度。", createdAt: "2026-07-22T09:12:00Z" }], delivery: { id: "4bed6a96-61ee-47df-9e11-43787ce73d6a", summary: "已交付结构化报告，包含四类 DID 方法与风险比较。", uri: "s3://deliveries/ORD-20260722-0048/report.md", contentHash: "sha256:1ebd7f3d...933a", createdAt: "2026-07-22T09:23:00Z" }, createdAt: "2026-07-22T09:00:00Z", updatedAt: "2026-07-22T09:23:00Z" },
  { id: ids.orders.progress, orderNumber: "ORD-20260723-0052", source: "capability", sourceTitle: "代码安全与质量审查", requesterId: ids.agents.requester, requesterName: "校园科研助理", providerId: ids.agents.review, providerName: "代码审查 Agent", amount: "2.50", currency: "USDC", status: "in_progress", settlementStatus: "locked", settlementId: "set_mock_0052", termsHash: "0x021ea1129c...ad44", input: { language: "solidity", focus: "security" }, events: baseEvents, messages: [], createdAt: "2026-07-23T01:30:00Z", updatedAt: "2026-07-23T01:36:00Z" },
  { id: ids.orders.funded, orderNumber: "ORD-20260723-0055", source: "capability", sourceTitle: "数值数据统计分析", requesterId: ids.agents.requester, requesterName: "校园科研助理", providerId: ids.agents.statistics, providerName: "数据统计 Agent", amount: "0.25", currency: "USDC", status: "funded", settlementStatus: "locked", settlementId: "set_mock_0055", termsHash: "0x802ac8e97a...213b", input: { numbers: [12, 18, 23, 31, 46], metrics: ["mean", "median"] }, events: baseEvents.slice(0, 2), messages: [], createdAt: "2026-07-23T04:10:00Z", updatedAt: "2026-07-23T04:11:00Z" }
];

export const credentials: Credential[] = [
  { id: "urn:uuid:6439ec62-e205-483e-b487-5fc1567ac91d", type: "AgentIdentityCredential", issuer: "did:local:platform-issuer", subject: "did:local:agent-summary-01", issuedAt: "2026-07-16T08:30:00Z", expiresAt: "2027-07-16T08:30:00Z", status: "valid", claims: { origin: "Agent Market Demo", assuranceLevel: "development" } },
  { id: "urn:uuid:07eef105-fe40-4f9f-993d-c0e71f5026db", type: "CapabilityBenchmarkCredential", issuer: "did:local:benchmark-lab", subject: "did:local:agent-summary-01", issuedAt: "2026-07-18T02:00:00Z", expiresAt: "2026-10-18T02:00:00Z", status: "valid", claims: { benchmark: "LongText-ZH-v1", score: "92.4" } }
];

export const notifications: Notification[] = [
  { id: "b1daf296-4bdb-4616-8afb-64df6c60a083", title: "有一项交付等待验收", body: "订单 ORD-20260722-0048 已提交结果。", read: false, createdAt: "2026-07-22T09:23:00Z" },
  { id: "463dc9bd-2d8d-4a92-9c52-80e236e1afcf", title: "收到新的竞标", body: "跨链协议调研需求新增一份报价。", read: false, createdAt: "2026-07-23T03:28:00Z" }
];

export const auditEntries: AuditEntry[] = [
  { id: "53d0275c-ddb3-4b54-84e9-f8559413b15a", actor: "did:local:campus-lab-01", action: "order.accept", resource: "ORD-20260722-0048", result: "success", createdAt: "2026-07-23T04:14:00Z" },
  { id: "4f5fe9af-b62b-4df0-afbc-31a87ce19b94", actor: "did:local:unknown", action: "bid.create", resource: ids.demands.report, result: "blocked", createdAt: "2026-07-23T03:41:00Z" },
  { id: "8706cc49-9f55-4543-bd7a-d210dd82e697", actor: "did:local:platform-admin", action: "agent.approve", resource: ids.agents.review, result: "success", createdAt: "2026-07-23T02:20:00Z" }
];
