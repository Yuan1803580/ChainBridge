import type {
  AttemptStatus,
  Workflow,
  WorkflowEvent,
  WorkflowLedgerEntry,
  WorkflowNodeStatus,
  WorkflowStatus,
} from "./types";

const API_URL = (process.env.NEXT_PUBLIC_API_URL || "http://127.0.0.1:8000").replace(/\/$/, "");

export type RawAttempt = {
  id: string;
  attemptNumber: number;
  status: string;
  agentKey: string;
  agentId: string;
  chainId: number;
  homeWallet?: string;
  routeId: string;
  routeVersion: number;
  matchCandidateId?: string;
  resultPayload?: unknown;
  resultHash?: string;
  proofHash?: string;
  reportedScore?: string;
  matchingTrustScore?: string;
  matchingTrustSource?: string;
  qualityScore?: string;
  qualityStatus?: "NOT_CALCULATED" | "PROVISIONAL" | "FINAL";
  qualityBreakdown?: {
    formula: string;
    weights: Record<string, string>;
    components: {
      completion: { score: string; contribution: string; passedChecks: number; totalChecks: number };
      review: { score: string; contribution: string; decision: "PASS" | "FAIL" };
      matchingTrust: { score: string; contribution: string; source: string };
    };
    reportedScore: { value?: string | null; trusted: false; includedInQuality: false };
  };
  qualityPolicyVersion?: string;
  qualityEvidenceHash?: string;
  qualityCalculatedAt?: string;
  reviewReason?: string;
  failureReasonCode?: string;
  failureResponsibleParty?: string;
  resultProofStatus?: string;
  resultProofTxHash?: string;
  coordinatorReviewHash?: string;
  reviewStatus?: string;
  reviewPolicyHash?: string;
  reviewSampled?: boolean;
  challengeExpiresAt?: string;
  reviewApprovals?: Array<{ id: string; reviewerDid: string; decision: "PASS" | "FAIL"; evidenceHash: string }>;
  reviewChallenges?: Array<{ id: string; status: string; evidenceHash: string; resolution?: string }>;
  settlementStatus?: string;
  settlementMessageId?: string;
  destinationTxHash?: string;
  sourceConfirmTxHash?: string;
  settlementAsset: string;
  payoutAsset: string;
  payoutTokenAddress?: string;
  payoutDecimals: number;
  payoutRate: string;
  payoutSlippageBps: number;
  payoutQuoteAmount?: string;
  payoutMinAmount?: string;
  payoutQuoteExpiresAt?: string;
  payoutStatus: string;
  payoutTxHash?: string;
  submittedAt?: string;
  reviewedAt?: string;
  settledAt?: string;
  createdAt: string;
  updatedAt?: string;
};

export type RawMatchCandidate = {
  id: string;
  agentProfileId: string;
  agentKey: string;
  rank: number;
  score: string;
  scoreBreakdown: Record<string, string>;
  quoteAmount: string;
  currency: string;
  chainId: number;
  homeWallet?: string;
  routeId: string;
  routeVersion: number;
  routeKey?: string;
  status: "AVAILABLE" | "SELECTED" | "FAILED" | "UNAVAILABLE" | "SETTLED" | "NOT_NEEDED";
  exclusionReason?: string;
  agent?: { id: string; displayName: string };
};

export type RawMatchRound = {
  id: string;
  roundNumber: number;
  policyVersion: string;
  status: "ACTIVE" | "EXHAUSTED" | "COMPLETED";
  candidates: RawMatchCandidate[];
};

export type RawFailureIncident = {
  id: string;
  reasonCode: string;
  responsibleParty: "AGENT" | "PLATFORM" | "ROUTE" | "USER";
  retryable: boolean;
  agentFault: boolean;
  gasTreatment: string;
  createdAt: string;
};

export type RawReservation = {
  id: string;
  attemptId: string;
  amount: string;
  currency: string;
  status: "RESERVED" | "RELEASED" | "PAID";
  createdAt: string;
};

export type RawWorkflowNode = {
  id: string;
  nodeKey: string;
  title: string;
  description: string;
  capability: string;
  status: string;
  dependsOn: string[];
  acceptanceCriteria: Record<string, unknown>;
  criteriaHash: string;
  reward: string;
  riskLevel: "LOW" | "MEDIUM" | "HIGH";
  riskAssignmentMode: "AUTO" | "MANUAL";
  riskScore: number;
  riskReasons: string[];
  reviewPolicyVersion: string;
  reviewPolicyHash: string;
  requiredApprovals: number;
  challengePeriodSeconds: number;
  samplingRateBps: number;
  reviewBudget: string;
  attempts: RawAttempt[];
  reservations: RawReservation[];
  matchRounds?: RawMatchRound[];
  failureIncidents?: RawFailureIncident[];
  waitingDiagnostics?: {
    capability: string;
    waitingReason: string;
    registeredAgentCount: number;
    matchableAgentCount: number;
    matchableAgents: Array<{ agentKey: string; chainId: number; routeVersion: number }>;
    exclusionReasons: Record<string, number>;
    attemptsUsed: number;
    maxAttempts: number;
    remainingAttempts: number;
    autoRecoveryEligible: boolean;
  };
  createdAt: string;
};

export type RawWorkflow = {
  id: string;
  referenceCode: string;
  requesterId: string;
  title: string;
  objective: string;
  status: string;
  currency: string;
  budget: string;
  platformFee: string;
  paidAmount: string;
  refundedAmount: string;
  sourceChainId: number;
  onchainWorkflowId?: string;
  fundingMode?: "RESERVED" | "ONCHAIN";
  fundingStatus?: string;
  fundingWallet?: string;
  approveTxHash?: string;
  lockTxHash?: string;
  sourceGasTxHash?: string;
  deploymentId?: string;
  sourceGasDeposit?: string;
  sourceGasRefunded?: string;
  contractSyncStatus?: string;
  termsHash: string;
  inputPayload?: Record<string, unknown>;
  nodes: RawWorkflowNode[];
  events: Array<{
    id: string;
    sequence: number;
    eventType: string;
    nodeId?: string;
    attemptId?: string;
    actor: string;
    payload: Record<string, unknown>;
    createdAt: string;
  }>;
  ledger: Array<{
    id: string;
    sequence: number;
    entryType: string;
    assetKind: "TOKEN" | "GAS";
    assetSymbol: string;
    amount: string;
    fromAccount: string;
    toAccount: string;
    chainId: number;
    nodeId?: string;
    attemptId?: string;
    transactionHash?: string;
    createdAt: string;
  }>;
  ledgerSummary: { balanced: boolean; entryCount: number };
  qualitySummary?: {
    policyVersion: string;
    qualityStatus: "PROVISIONAL" | "FINAL";
    formula: string;
    qualityScore: string;
    completionRate: string;
    reviewPassRate: string;
    averageMatchingTrust: string;
    averageFinalAttemptQuality?: string;
    totalNodes: number;
    terminalNodes: number;
    settledNodes: number;
    reviewedAttempts: number;
    finalScoredAttempts: number;
  };
  createdAt: string;
  updatedAt: string;
};

export type LocalProtocolConfig = {
  mode: "LOCAL_EXPERIMENT";
  chainId: number;
  chainName: string;
  rpcUrl: string;
  nativeCurrency: { name: string; symbol: string; decimals: number };
  sourceToken: string;
  taskEscrow: string;
  gasTreasury: string;
  sourceGasAmount: string;
  deploymentId: string;
  confirmationBlocks: number;
};

export type RequesterWallet = {
  status: "READY" | "MISSING";
  mode?: "LOCAL_EXPERIMENT";
  address?: string;
  chainId: number;
  chainName?: string;
  sourceToken?: string;
  taskEscrow?: string;
  gasTreasury?: string;
  deploymentId?: string;
  signerReady: boolean;
  balances?: {
    native: string;
    nativeWei: string;
    token: string;
    tokenUnits: string;
    tokenDecimals: number;
    blockNumber: number;
  };
  startup?: { status: "PENDING" | "READY" | "DEGRADED"; checkedAt?: string | null; error?: string | null };
};

export type RequesterRunState = {
  conversationId: string;
  status: "RUNNING" | "WAITING_RETRY" | "COMPLETED" | "CANCELLED";
  cycle: number;
  workflowId?: string;
  workflowStatus?: string | null;
  lastAction?: string | null;
  error?: string | null;
  retryCount?: number;
  nextRetryAt?: string | null;
  startedAt: string;
  updatedAt: string;
  completedAt?: string;
};

export type CoordinatorWorkflowSummary = {
  workflowId: string;
  referenceCode: string;
  status: string;
  completed: boolean;
  nodes: Array<{
    nodeKey: string;
    title: string;
    status: string;
    attemptId?: string;
    agentKey?: string;
    output?: unknown;
  }>;
  finalOutput?: unknown;
  funds: { budget: string; paidAmount: string; platformFee: string; refundedAmount: string };
};

export type WorkflowRecoveryHealth = {
  enabled: boolean;
  running: boolean;
  intervalSeconds: number;
  lastScanAt?: string | null;
  nextScanAt?: string | null;
  scanCount: number;
  lastError?: string | null;
};

export type AgentChatResponse = {
  sessionId: string;
  agentId: string;
  message: string;
  provider: string;
  model: string;
  usage: Record<string, unknown>;
  sources: Array<{ path: string; heading: string }>;
  toolInvocations?: Array<{
    name: string;
    arguments: Record<string, unknown>;
    argumentsHash?: string;
    durationMs?: number;
    ok: boolean;
    error?: string;
  }>;
  updatedAt: string;
};

export type RawRoute = {
  id: string;
  routeKey: string;
  routeVersion: number;
  sourceChainId: number;
  destinationChainId: number;
  status: "ACTIVE" | "PAUSED" | "RETIRED";
  rpcUrl: string;
  sourceGatewayAddress?: string;
  remoteWorkBoardAddress?: string;
  payoutReceiverAddress?: string;
  tokenAddress?: string;
  settlementAsset: string;
  payoutAsset: string;
  payoutTokenAddress?: string;
  payoutSwapAddress?: string;
  payoutDecimals: number;
  payoutRate: string;
  payoutSlippageBps: number;
  confirmationBlocks: number;
  deploymentStatus?: string;
  deploymentManifestHash?: string;
  updatedAt: string;
};

export type RawAgentProfile = {
  id: string;
  agentId: string;
  agentKey: string;
  agentType: "EXECUTOR" | "COORDINATOR";
  chainId: number;
  homeWallet?: string;
  capabilities: string[];
  priority: number;
  active: boolean;
  admissionStatus: "PENDING_OWNERSHIP" | "PENDING_PLATFORM_REVIEW" | "APPROVED" | "REJECTED";
  chainStatus: "PENDING_ROUTE" | "READY" | "REJECTED";
  matchable?: boolean;
  operationalStatus?: string;
  metadata: Record<string, unknown>;
  priceFloor: string;
  currency: string;
  successRateBps: number;
  trustScore: number;
  endpointLatencyMs: number;
  availability: "ONLINE" | "BUSY" | "OFFLINE";
  capacityAvailable: boolean;
  gasReady: boolean;
  lastHeartbeatAt?: string;
  quoteExpiresAt?: string;
  credentialStatus?: "UNCONFIGURED" | "REFERENCE_ONLY" | "ACTIVE" | "REVOKED";
  credentialFingerprint?: string;
  createdAt?: string;
  updatedAt?: string;
  agent: {
    id: string;
    did: string;
    displayName: string;
    description: string;
    status: string;
    reputationScore: string;
    endpoint?: string;
    createdAt?: string;
    updatedAt?: string;
  };
};

export type AgentAccountSnapshot = {
  profileId: string;
  chainId: number;
  wallet: string;
  checkedAt: string;
  gasBalanceWei?: string;
  gasBalanceError?: string;
  gasSymbol: string;
  payoutAsset: string;
  payoutTokenAddress?: string;
  payoutDecimals: number;
  payoutBalanceRaw?: string;
  payoutBalance?: string;
  payoutBalanceError?: string;
  latestPayout?: {
    workflowId?: string;
    workflowReference?: string;
    nodeId?: string;
    nodeKey?: string;
    attemptId: string;
    amount?: string;
    asset: string;
    settlementAmount?: string;
    settlementAsset?: string;
    transactionHash?: string;
    settledAt: string;
  };
};

export type WorkflowPlanCandidate = {
  rank: number;
  agentProfileId: string;
  agentKey: string;
  homeChainId: number;
  homeWallet?: string;
  routeId: string;
  routeKey: string;
  routeVersion: number;
  deploymentStatus: string;
  score: string;
  scoreBreakdown: Record<string, string>;
  quoteAmount: string;
  credentialStatus: string;
};

export type WorkflowNodeInput = {
  nodeKey: string;
  title: string;
  description?: string;
  capability: string;
  dependsOn?: string[];
  acceptanceCriteria?: Record<string, unknown>;
  inputPayload?: Record<string, unknown>;
  reward: string;
  riskLevel?: "LOW" | "MEDIUM" | "HIGH";
  reviewPolicyVersion?: string;
  requiredApprovals?: number;
  challengePeriodSeconds?: number;
  samplingRateBps?: number;
  reviewBudget?: string;
  maxAttempts?: number;
};

export type WorkflowPlan = {
  plannerVersion: string;
  decisionId: string;
  decisionHash: string;
  funding: { mode: string; status: string; currency: string };
  validation?: {
    status: "VALID" | "VALID_WITH_WARNINGS";
    validatorVersion: string;
    warnings: Array<{ code: string; nodeKey: string; message: string }>;
  };
  nodes: Array<{
    nodeKey: string;
    title: string;
    description: string;
    capability: string;
    dependsOn: string[];
    reward: string;
    riskLevel: "LOW" | "MEDIUM" | "HIGH";
    reviewPolicyVersion: string;
    requiredApprovals: number;
    challengePeriodSeconds: number;
    samplingRateBps: number;
    reviewBudget: string;
    riskAssessment: { score: number; riskLevel: "LOW" | "MEDIUM" | "HIGH"; reasons: string[]; assignmentMode: "AUTO" | "MANUAL" };
    criteriaHash: string;
    proposalRationale?: string;
    marketQuote?: {
      status: "MATCHABLE" | "BELOW_MARKET_PRICE" | "PENDING_AGENT";
      suggestedReward: string;
      lowestAvailableQuote?: string;
      matchableCandidateCount: number;
    };
    candidates: WorkflowPlanCandidate[];
  }>;
};

export type CoordinatorConversation = {
  conversationId: string;
  status: "DRAFT" | "WORKFLOW_CREATED";
  provider: string;
  coordinator?: {
    agentType: "COORDINATOR";
    agentKey: string;
    did: string;
    profileId?: string;
  };
  assistantMessage: string;
  plan: WorkflowPlan;
  draft: {
    title: string;
    objective: string;
    budget: string;
    platformFee: string;
    currency: string;
    sourceChainId: number;
    inputPayload: Record<string, unknown>;
    nodes: WorkflowNodeInput[];
  };
  marketContext: {
    currency: string;
    availableCapabilities: Array<{
      capability: string;
      lowestQuote: string;
      eligibleProviders: number;
      liveProviders: number;
    }>;
  };
  workflowId?: string;
};

export type RawCrossChainMessage = {
  id: string;
  messageId: string;
  workflowId: string;
  nodeId: string;
  attemptId: string;
  messageType: "DISPATCH" | "RESULT" | "RESULT_PROOF" | "SETTLEMENT";
  sourceChainId: number;
  destinationChainId: number;
  routeId: string;
  routeVersion: number;
  status: string;
  transactionHash?: string;
  relayAttempts: number;
  lastError?: string;
  createdAt: string;
  deliveredAt?: string;
};

export type ExternalAgentOnboardingRequest = {
  agentType: "EXECUTOR" | "COORDINATOR";
  ownerName: string;
  ownerEmail: string;
  organization?: string;
  agentName: string;
  agentKey: string;
  did?: string;
  endpoint: string;
  publicKey: string;
  llmProvider: string;
  llmModel: string;
  systemPrompt: string;
  capabilities: string[];
  preferredChainId?: number;
  payoutAsset: string;
  settlementAsset: string;
  homeWallet?: string;
  priceFloor: string;
  currency: string;
};

export type ExternalAgentOnboardingResponse = {
  admissionStatus: "PENDING_OWNERSHIP" | "PENDING_PLATFORM_REVIEW" | "APPROVED" | "REJECTED";
  chainStatus: "PENDING_ROUTE" | "READY" | "REJECTED";
  matchable: boolean;
  agent: {
    id: string;
    did: string;
    displayName: string;
    endpoint?: string;
    roles: string[];
  };
  profile: RawAgentProfile;
  route: RawRoute | null;
  chainResolution: {
    sourceChainId: number;
    destinationChainId: number;
    routeKey: string | null;
    routeVersion: number | null;
    settlementAsset: string;
    payoutAsset: string;
    payoutRate: string | null;
  };
  next: {
    challengeEndpoint?: string;
    verifyEndpoint?: string;
    heartbeatEndpoint: string;
    resultSubmission: string;
  };
};

export type AgentOnboardingChallenge = {
  challengeId: string;
  profileId: string;
  nonce: string;
  message: string;
  expiresAt: string;
};

export type AgentOnboardingActionResponse = {
  admissionStatus?: string;
  checksPassed?: boolean;
  accepted?: boolean;
  matchable?: boolean;
  profileId?: string;
  profile?: RawAgentProfile;
  evidence?: Record<string, unknown>;
  chainStatus?: string;
  operationalStatus?: string;
};

type RequestOptions = RequestInit & { idempotencyKey?: string };

async function apiRequest<T>(path: string, options: RequestOptions = {}): Promise<T> {
  const { idempotencyKey, ...init } = options;
  const headers = new Headers(init.headers);
  headers.set("Accept", "application/json");
  if (init.body) headers.set("Content-Type", "application/json");
  if (idempotencyKey) headers.set("Idempotency-Key", idempotencyKey);
  if (typeof window !== "undefined") {
    const token = window.localStorage.getItem("agentMarketToken");
    if (token) headers.set("Authorization", `Bearer ${token}`);
  }
  const response = await fetch(`${API_URL}${path}`, {
    ...init,
    headers,
    cache: "no-store",
    signal: init.signal || AbortSignal.timeout(30_000),
  });
  const body = await response.json().catch(() => null) as T | { error?: { message?: string } } | null;
  if (!response.ok) {
    const message = body && typeof body === "object" && "error" in body
      ? body.error?.message
      : undefined;
    throw new Error(message || `API request failed (${response.status})`);
  }
  return body as T;
}

async function coordinatorRequest<T>(profileId: string, path: string, init: RequestInit): Promise<T> {
  if (!profileId) throw new Error("请选择一个可用的 Coordinator Agent");
  const headers = new Headers(init.headers);
  headers.set("Accept", "application/json");
  headers.set("Content-Type", "application/json");
  headers.set("X-Coordinator-Profile-Id", profileId);
  const response = await fetch(`/api/coordinator${path}`, {
    ...init,
    headers,
    cache: "no-store",
    signal: init.signal || AbortSignal.timeout(180_000),
  });
  const body = await response.json().catch(() => null) as T | { detail?: string } | null;
  if (!response.ok) {
    const detail = body && typeof body === "object" && "detail" in body ? body.detail : undefined;
    throw new Error(typeof detail === "string" ? detail : `Coordinator request failed (${response.status})`);
  }
  return body as T;
}

async function requesterRequest<T>(path: string, init: RequestInit = {}): Promise<T> {
  const headers = new Headers(init.headers);
  headers.set("Accept", "application/json");
  if (init.body) headers.set("Content-Type", "application/json");
  const response = await fetch(`/api/requester${path}`, {
    ...init,
    headers,
    cache: "no-store",
    signal: init.signal || AbortSignal.timeout(path.endsWith("/fund") ? 300_000 : 180_000),
  });
  const body = await response.json().catch(() => null) as T | {
    detail?: string;
    error?: { message?: string };
  } | null;
  if (!response.ok) {
    const message = body && typeof body === "object"
      ? ("error" in body ? body.error?.message : "detail" in body ? body.detail : undefined)
      : undefined;
    throw new Error(typeof message === "string" ? message : `Requester request failed (${response.status})`);
  }
  return body as T;
}

const workflowStatus = (value: string): WorkflowStatus => {
  if (value === "COMPLETED") return "COMPLETED";
  if (["FAILED", "FAILED_REFUNDING", "FAILED_REFUNDED", "DISPUTED"].includes(value)) return "FAILED";
  if (value === "AWAITING_FUNDING") return "DRAFT";
  if (value === "DRAFT") return "DRAFT";
  if (value === "FUNDED") return "FUNDED";
  return "RUNNING";
};

const nodeStatus = (value: string): WorkflowNodeStatus => {
  if (value === "BLOCKED") return "BLOCKED";
  if (["READY", "RETRYING"].includes(value)) return "READY";
  if (["WAITING_FOR_AGENT", "WAITING_FOR_ROUTE_DEPLOYMENT"].includes(value)) return "WAITING";
  if (value === "ASSIGNED") return "ASSIGNING";
  if (value === "EXECUTING") return "EXECUTING";
  if (["RESULT_PROOF_PENDING", "REVIEWING", "REVIEW_PENDING", "CHALLENGE_PERIOD", "SUCCEEDED", "SETTLEMENT_PENDING"].includes(value)) return "IN_REVIEW";
  if (value === "SETTLED") return "SETTLED";
  return "FAILED";
};

const attemptStatus = (value: string): AttemptStatus => {
  const mapping: Record<string, AttemptStatus> = {
    CREATED: "OFFERED",
    DISPATCHED: "OFFERED",
    EXECUTING: "EXECUTING",
    RESULT_SUBMITTED: "PROOF_RECEIVED",
    RESULT_PROOF_PENDING: "EXECUTING",
    INDEPENDENT_REVIEW_PENDING: "PROOF_RECEIVED",
    APPROVALS_PENDING: "PROOF_RECEIVED",
    CHALLENGE_PERIOD: "PROOF_RECEIVED",
    DISPUTED: "REJECTED",
    REVIEW_FAILED: "REJECTED",
    REVIEW_PASSED: "APPROVED",
    SETTLEMENT_PENDING: "APPROVED",
    SETTLED: "SETTLED",
    EXPIRED: "EXPIRED",
  };
  return mapping[value] || "REJECTED";
};

const eventKind = (eventType: string): WorkflowEvent["kind"] => {
  if (eventType.includes("ROUTE") || eventType.includes("DISPATCH")) return "route";
  if (eventType.includes("RESULT")) return "proof";
  if (eventType.includes("REVIEW")) return "review";
  if (eventType.includes("SETTLED") || eventType.includes("BUDGET") || eventType.includes("COMPLETED")) return "settlement";
  if (eventType.includes("STALE") || eventType.includes("FAILED") || eventType.includes("EXHAUSTED")) return "security";
  return "workflow";
};

const eventTitle: Record<string, string> = {
  WORKFLOW_CREATED: "工作流已创建",
  BUDGET_LOCKED: "预算已锁定",
  NODE_DISPATCHED: "节点已跨链派发",
  RESULT_SUBMITTED: "结果证明已返回",
  REVIEW_PASSED: "不可变验收通过",
  REVIEW_FAILED: "不可变验收失败",
  NODE_SETTLED: "节点支付已确认",
  NODE_UNLOCKED: "后继节点已解锁",
  STALE_ATTEMPT_REJECTED: "迟到尝试已拒绝",
  AGENT_CANDIDATES_RANKED: "候选 Agent 排名已保存",
  AGENT_POOL_EXHAUSTED: "候选 Agent 池已耗尽",
  WAITING_FOR_AGENT: "正在等待新的合格 Agent",
  WORKFLOW_COMPLETED: "工作流完成并退款",
};

function ledgerCategory(entryType: string): WorkflowLedgerEntry["category"] {
  if (entryType === "PAYOUT") return "AGENT_REWARD";
  if (entryType === "PLATFORM_FEE") return "PLATFORM_FEE";
  if (entryType === "REFUND") return "REFUND";
  if (entryType.includes("GAS")) return "RETURN_GAS";
  return "RESERVATION";
}

export function normalizeWorkflow(raw: RawWorkflow): Workflow {
  const coordinator = raw.inputPayload?._coordinator;
  const coordinatorIdentity = coordinator && typeof coordinator === "object"
    ? coordinator as Record<string, unknown>
    : {};
  const activeReservations = raw.nodes.flatMap((node) => node.reservations)
    .filter((item) => item.status === "RESERVED")
    .reduce((sum, item) => sum + Number(item.amount), 0);
  const gasEntries = raw.ledger.filter((entry) => entry.assetKind === "GAS");
  const gasSpent = gasEntries.reduce((sum, entry) => sum + Number(entry.amount), 0);
  const eventNodeKey = new Map(raw.nodes.map((node) => [node.id, node.nodeKey]));

  const nodes = raw.nodes.map((node, index) => ({
    id: node.id,
    sequence: index + 1,
    title: `${node.nodeKey} · ${node.title}`,
    description: node.description,
    criteriaHash: node.criteriaHash,
    dependencies: node.dependsOn,
    status: nodeStatus(node.status),
    budget: node.reward,
    capability: node.capability,
    riskLevel: node.riskLevel,
    riskAssignmentMode: node.riskAssignmentMode,
    riskScore: node.riskScore,
    riskReasons: node.riskReasons,
    reviewPolicyVersion: node.reviewPolicyVersion,
    reviewPolicyHash: node.reviewPolicyHash,
    requiredApprovals: node.requiredApprovals,
    challengePeriodSeconds: node.challengePeriodSeconds,
    samplingRateBps: node.samplingRateBps,
    reviewBudget: node.reviewBudget,
    waitingDiagnostics: node.waitingDiagnostics,
    matchRounds: (node.matchRounds || []).map((round) => ({
      id: round.id,
      number: round.roundNumber,
      policyVersion: round.policyVersion,
      status: round.status,
      candidates: round.candidates.map((candidate) => ({
        id: candidate.id,
        rank: candidate.rank,
        agentId: candidate.agent?.id || candidate.agentProfileId,
        agentName: candidate.agent?.displayName || candidate.agentKey,
        agentKey: candidate.agentKey,
        homeChainId: candidate.chainId,
        homeWallet: candidate.homeWallet,
        routeId: candidate.routeKey || candidate.routeId,
        routeVersion: candidate.routeVersion,
        score: candidate.score,
        scoreBreakdown: candidate.scoreBreakdown,
        quoteAmount: candidate.quoteAmount,
        currency: candidate.currency,
        status: candidate.status,
        exclusionReason: candidate.exclusionReason,
      })),
    })),
    failureIncidents: (node.failureIncidents || []).map((incident) => ({
      id: incident.id,
      reasonCode: incident.reasonCode,
      responsibleParty: incident.responsibleParty,
      retryable: incident.retryable,
      agentFault: incident.agentFault,
      gasTreatment: incident.gasTreatment,
      createdAt: incident.createdAt,
    })),
    attempts: node.attempts.map((attempt) => {
      const reservation = node.reservations.find((item) => item.attemptId === attempt.id);
      return {
        id: attempt.id,
        number: attempt.attemptNumber,
        agentId: attempt.agentId,
        agentName: attempt.agentKey,
        agentDid: `did:local:${attempt.agentKey.toLowerCase()}`,
        homeChainId: attempt.chainId,
        homeWallet: attempt.homeWallet,
        homeChainName: `Chain ${String.fromCharCode(65 + attempt.chainId - 31337)}`,
        routeId: attempt.routeId,
        routeVersion: attempt.routeVersion,
        status: attemptStatus(attempt.status),
        reservation: {
          id: reservation?.id || `reservation-${attempt.id}`,
          amount: reservation?.amount || node.reward,
          returnGasDeposit: "按实际 Gas 账本",
          status: reservation?.status === "PAID" ? "SETTLED" as const : reservation?.status || "RESERVED" as const,
        },
        startedAt: attempt.createdAt,
        finishedAt: attempt.settledAt || attempt.reviewedAt || attempt.submittedAt,
        output: attempt.resultPayload,
        resultHash: attempt.resultHash,
        reportedScore: attempt.reportedScore,
        matchingTrustScore: attempt.matchingTrustScore,
        matchingTrustSource: attempt.matchingTrustSource,
        qualityScore: attempt.qualityScore,
        qualityStatus: attempt.qualityStatus,
        qualityBreakdown: attempt.qualityBreakdown,
        qualityPolicyVersion: attempt.qualityPolicyVersion,
        qualityEvidenceHash: attempt.qualityEvidenceHash,
        qualityCalculatedAt: attempt.qualityCalculatedAt,
        resultProofStatus: attempt.resultProofStatus,
        resultProofTxHash: attempt.resultProofTxHash,
        coordinatorReviewHash: attempt.coordinatorReviewHash,
        reviewStatus: attempt.reviewStatus,
        reviewPolicyHash: attempt.reviewPolicyHash,
        reviewSampled: attempt.reviewSampled,
        challengeExpiresAt: attempt.challengeExpiresAt,
        reviewApprovals: attempt.reviewApprovals,
        reviewChallenges: attempt.reviewChallenges,
        settlementStatus: attempt.settlementStatus,
        settlementMessageId: attempt.settlementMessageId,
        destinationTxHash: attempt.destinationTxHash,
        sourceConfirmTxHash: attempt.sourceConfirmTxHash,
        settlementAsset: attempt.settlementAsset,
        payoutAsset: attempt.payoutAsset,
        payoutTokenAddress: attempt.payoutTokenAddress,
        payoutDecimals: attempt.payoutDecimals,
        payoutRate: attempt.payoutRate,
        payoutSlippageBps: attempt.payoutSlippageBps,
        payoutQuoteAmount: attempt.payoutQuoteAmount,
        payoutMinAmount: attempt.payoutMinAmount,
        payoutQuoteExpiresAt: attempt.payoutQuoteExpiresAt,
        payoutStatus: attempt.payoutStatus,
        payoutTxHash: attempt.payoutTxHash,
        review: attempt.reviewReason ? {
          decision: attempt.status === "REVIEW_FAILED" ? "FAIL" as const : "PASS" as const,
          reasonCode: attempt.status,
          summary: attempt.reviewReason,
          decidedAt: attempt.reviewedAt || attempt.updatedAt || attempt.createdAt,
        } : undefined,
      };
    }),
  }));

  const events: WorkflowEvent[] = raw.events.map((event) => {
    const payload = event.payload || {};
    const chainId = typeof payload.chainId === "number" ? payload.chainId : undefined;
    const destination = chainId ? String.fromCharCode(65 + chainId - 31337) : undefined;
    return {
      id: event.id,
      step: event.sequence,
      kind: eventKind(event.eventType),
      title: eventTitle[event.eventType] || event.eventType.replaceAll("_", " "),
      detail: JSON.stringify(payload),
      status: "complete",
      occurredAt: event.createdAt,
      chainLabel: destination ? `A → ${destination}` : "A",
      nodeId: event.nodeId,
      attemptId: event.attemptId,
      messageId: typeof payload.messageId === "string" ? payload.messageId : undefined,
    };
  });

  const ledger: WorkflowLedgerEntry[] = raw.ledger.map((entry) => {
    const incoming = ["PAYOUT", "PAYOUT_SWAP", "REFUND", "RELEASE_RESERVATION"].includes(entry.entryType);
    const responsibleParty = entry.assetKind === "GAS" ? "AGENT"
      : entry.entryType === "PLATFORM_FEE" ? "PLATFORM"
        : entry.entryType === "REFUND" ? "SYSTEM" : "USER";
    return {
      id: entry.id,
      category: ledgerCategory(entry.entryType),
      description: `${entry.entryType} · ${entry.fromAccount} → ${entry.toAccount}`,
      debit: incoming ? "0" : entry.amount,
      credit: incoming ? entry.amount : "0",
      responsibleParty,
      assetSymbol: entry.assetSymbol,
      chainId: entry.chainId,
      transactionHash: entry.transactionHash,
      nodeId: entry.nodeId,
      attemptId: entry.attemptId,
      occurredAt: entry.createdAt,
    };
  });

  return {
    id: raw.id,
    displayId: raw.referenceCode,
    title: raw.title,
    objective: raw.objective,
    status: workflowStatus(raw.status),
    taskChainId: raw.sourceChainId,
    coordinatorName: typeof coordinatorIdentity.agentKey === "string" ? coordinatorIdentity.agentKey : "Coordinator Agent",
    coordinatorDid: typeof coordinatorIdentity.did === "string" ? coordinatorIdentity.did : "did:local:coordinator",
    coordinatorProfileId: typeof coordinatorIdentity.profileId === "string" ? coordinatorIdentity.profileId : undefined,
    criteriaHash: raw.termsHash,
    fundingMode: raw.fundingMode,
    fundingStatus: raw.fundingStatus,
    fundingWallet: raw.fundingWallet,
    lockTxHash: raw.lockTxHash,
    sourceGasDeposit: raw.sourceGasDeposit,
    sourceGasRefunded: raw.sourceGasRefunded,
    contractSyncStatus: raw.contractSyncStatus,
    qualitySummary: raw.qualitySummary,
    budget: {
      total: raw.budget,
      reserved: String(activeReservations),
      paid: raw.paidAmount,
      platformFee: raw.platformFee,
      returnGasSpent: gasEntries.length ? `${gasSpent} ${gasEntries[0].assetSymbol}` : "0 ETH",
      refundable: raw.refundedAmount || String(Math.max(0, Number(raw.budget) - Number(raw.paidAmount) - Number(raw.platformFee) - activeReservations)),
      currency: raw.currency,
    },
    nodes,
    events,
    ledger,
    createdAt: raw.createdAt,
    updatedAt: raw.updatedAt,
  };
}

export const dynamicApi = {
  baseUrl: API_URL,
  health: () => apiRequest<{ status: string; database: string; timestamp: string; workflowRecovery?: WorkflowRecoveryHealth }>("/api/v1/health"),
  workflows: async () => {
    const response = await apiRequest<{ items: RawWorkflow[]; total: number }>("/api/v1/workflows?view=summary&limit=100");
    return { ...response, items: response.items.map(normalizeWorkflow) };
  },
  workflow: async (id: string) => normalizeWorkflow(await apiRequest<RawWorkflow>(`/api/v1/workflows/${id}`)),
  planWorkflow: (body: { objective: string; budget: string; platformFee?: string; inputPayload?: Record<string, unknown> }) =>
    apiRequest<WorkflowPlan>("/api/v1/workflow-plans", {
      method: "POST",
      body: JSON.stringify(body),
      idempotencyKey: crypto.randomUUID(),
    }),
  createWorkflow: (body: { title: string; objective: string; budget: string; platformFee?: string; inputPayload?: Record<string, unknown>; nodes?: WorkflowNodeInput[]; fundingMode?: "RESERVED" | "ONCHAIN"; payerAddress?: string }) =>
    apiRequest<RawWorkflow>("/api/v1/workflows", {
      method: "POST",
      body: JSON.stringify(body),
      idempotencyKey: crypto.randomUUID(),
    }),
  localProtocolConfig: () => apiRequest<LocalProtocolConfig>("/api/v1/local-protocol/public-config"),
  localFaucet: (body: { requestId: string; walletAddress: string; tokenAmount: string; nativeAmount: string }) => apiRequest<{ tokenTransactionHash: string; nativeTransactionHash?: string }>("/api/v1/local-protocol/faucet", {
    method: "POST",
    body: JSON.stringify(body),
  }),
  confirmFunding: (workflowId: string, body: { walletAddress: string; termsHash: string; approveTxHash: string; lockTxHash: string; sourceGasTxHash?: string; sourceGasAmount: string; deploymentId: string; verificationMode: "RPC" }) => apiRequest<{ workflow: RawWorkflow }>(`/api/v1/workflows/${workflowId}/funding-confirmations`, {
    method: "POST",
    body: JSON.stringify(body),
    idempotencyKey: crypto.randomUUID(),
  }),
  coordinate: (id: string, maxActions = 1) => apiRequest<{ actions: Array<{ action: string }>; workflow: RawWorkflow }>(`/api/v1/workflows/${id}/coordinate`, {
    method: "POST",
    body: JSON.stringify({ maxActions }),
    idempotencyKey: crypto.randomUUID(),
  }),
  resume: async (id: string, maxActions = 4) => {
    const response = await apiRequest<{ resumed: boolean; actions: Array<{ action: string }>; workflow: RawWorkflow }>(`/api/v1/workflows/${id}/resume`, {
      method: "POST",
      body: JSON.stringify({ maxActions }),
      idempotencyKey: crypto.randomUUID(),
    });
    return { ...response, workflow: normalizeWorkflow(response.workflow) };
  },
  approveReview: (workflowId: string, nodeId: string, attemptId: string, body: { decision: "PASS" | "FAIL"; evidenceHash: string; notes?: string }) =>
    apiRequest<{ action: string; workflow: RawWorkflow }>(`/api/v1/workflows/${workflowId}/nodes/${nodeId}/attempts/${attemptId}/review-approvals`, {
      method: "POST",
      body: JSON.stringify(body),
    }),
  challengeReview: (workflowId: string, nodeId: string, attemptId: string, body: { reason: string; evidenceHash: string }) =>
    apiRequest<{ action: string; challengeId: string; workflow: RawWorkflow }>(`/api/v1/workflows/${workflowId}/nodes/${nodeId}/attempts/${attemptId}/review-challenges`, {
      method: "POST",
      body: JSON.stringify(body),
    }),
  resolveReviewChallenge: (workflowId: string, nodeId: string, attemptId: string, challengeId: string, body: { resolution: "UPHOLD_PASS" | "REJECT_RESULT"; reason: string }) =>
    apiRequest<{ action: string; workflow: RawWorkflow }>(`/api/v1/workflows/${workflowId}/nodes/${nodeId}/attempts/${attemptId}/review-challenges/${challengeId}/resolve`, {
      method: "POST",
      body: JSON.stringify(body),
    }),
  routes: () => apiRequest<{ items: RawRoute[]; total: number }>("/api/v1/chain-routes"),
  agents: (agentType?: "EXECUTOR" | "COORDINATOR") => apiRequest<{ items: RawAgentProfile[]; total: number }>(
    `/api/v1/workflow-agents${agentType ? `?agentType=${agentType}` : ""}`,
  ),
  agentAccount: (profileId: string) => apiRequest<AgentAccountSnapshot>(`/api/v1/workflow-agents/${profileId}/account`),
  syncAgentCapabilities: (profileId: string) => apiRequest<{ items: unknown[]; total: number }>(`/api/v1/workflow-agents/${profileId}/capabilities/sync`, { method: "POST" }),
  createAgentChatSession: (profileId: string, title?: string) => apiRequest<{ sessionId: string; title?: string }>(`/api/v1/workflow-agents/${profileId}/chat/sessions`, {
    method: "POST",
    body: JSON.stringify({ title }),
  }),
  chatWithAgent: (profileId: string, sessionId: string, message: string, signal?: AbortSignal) => apiRequest<AgentChatResponse>(`/api/v1/workflow-agents/${profileId}/chat`, {
    method: "POST",
    body: JSON.stringify({ sessionId, message }),
    signal,
  }),
  onboardAgent: (body: ExternalAgentOnboardingRequest) => apiRequest<ExternalAgentOnboardingResponse>("/api/v1/agent-onboarding/registrations", {
    method: "POST",
    body: JSON.stringify(body),
  }),
  onboardingRegistration: (profileId: string) =>
    apiRequest<RawAgentProfile>(`/api/v1/agent-onboarding/registrations/${profileId}`),
  onboardingRegistrations: (filters: { admissionStatus?: string; chainStatus?: string } = {}) => {
    const query = new URLSearchParams();
    if (filters.admissionStatus) query.set("admissionStatus", filters.admissionStatus);
    if (filters.chainStatus) query.set("chainStatus", filters.chainStatus);
    return apiRequest<{ items: RawAgentProfile[]; total: number }>(`/api/v1/agent-onboarding/registrations${query.size ? `?${query}` : ""}`);
  },
  onboardingChallenge: (profileId: string) => apiRequest<AgentOnboardingChallenge>(`/api/v1/agent-onboarding/registrations/${profileId}/challenge`, {
    method: "POST",
  }),
  verifyOnboarding: (profileId: string, body: { challengeId: string; identitySignature: string; walletSignature?: string }) =>
    apiRequest<AgentOnboardingActionResponse>(`/api/v1/agent-onboarding/registrations/${profileId}/verify`, {
      method: "POST",
      body: JSON.stringify(body),
    }),
  checkOnboarding: (profileId: string, runtimeAccessToken: string) =>
    apiRequest<AgentOnboardingActionResponse>(`/api/v1/agent-onboarding/registrations/${profileId}/checks`, {
      method: "POST",
      body: JSON.stringify({ runtimeAccessToken }),
      signal: AbortSignal.timeout(240_000),
    }),
  recheckOnboarding: (profileId: string) =>
    apiRequest<AgentOnboardingActionResponse>(`/api/v1/agent-onboarding/registrations/${profileId}/checks`, {
      method: "POST",
      body: JSON.stringify({}),
      signal: AbortSignal.timeout(240_000),
    }),
  activateOnboarding: (profileId: string, notes = "Platform checks passed.") =>
    apiRequest<AgentOnboardingActionResponse>(`/api/v1/agent-onboarding/registrations/${profileId}/activate`, {
      method: "POST",
      body: JSON.stringify({ notes }),
    }),
  rejectOnboarding: (profileId: string, reason: string) =>
    apiRequest<AgentOnboardingActionResponse>(`/api/v1/agent-onboarding/registrations/${profileId}/reject`, {
      method: "POST",
      body: JSON.stringify({ reason }),
    }),
  messages: (status?: string) => apiRequest<{ items: RawCrossChainMessage[]; total: number }>(`/api/v1/cross-chain-messages${status ? `?status=${status}` : ""}`),
};

export const coordinatorApi = {
  plan: (profileId: string, body: {
    message: string;
    budget: string;
    platformFee?: string;
    currency?: string;
    inputPayload?: Record<string, unknown>;
  }) => coordinatorRequest<CoordinatorConversation>(profileId, "/chat", {
    method: "POST",
    body: JSON.stringify(body),
  }),
  refine: (profileId: string, conversationId: string, body: {
    message: string;
    budget?: string;
    platformFee?: string;
    currency?: string;
    inputPayload?: Record<string, unknown>;
  }) => coordinatorRequest<CoordinatorConversation>(profileId, `/conversations/${conversationId}/refine`, {
    method: "POST",
    body: JSON.stringify(body),
  }),
  createWorkflow: (profileId: string, conversationId: string, body: {
    fundingMode: "RESERVED" | "ONCHAIN";
    payerAddress?: string;
  }) => coordinatorRequest<{ conversation: CoordinatorConversation; workflow: RawWorkflow }>(profileId,
    `/conversations/${conversationId}/workflows`,
    { method: "POST", body: JSON.stringify(body) },
  ),
  summary: (profileId: string, conversationId: string) => coordinatorRequest<CoordinatorWorkflowSummary>(profileId,
    `/conversations/${conversationId}/summary`,
    { method: "GET" },
  ),
  summaryByWorkflow: (profileId: string, workflowId: string) => coordinatorRequest<CoordinatorWorkflowSummary>(profileId,
    `/workflows/${workflowId}/summary`,
    { method: "GET" },
  ),
};

export const requesterApi = {
  wallet: () => requesterRequest<RequesterWallet>("/wallet", { method: "GET" }),
  faucet: (body: { tokenTarget?: string; nativeTarget?: string } = {}) => requesterRequest<{
    funded: boolean;
    balances: RequesterWallet["balances"];
  }>("/wallet/faucet", { method: "POST", body: JSON.stringify(body) }),
  fund: (coordinatorProfileId: string, conversationId: string) => requesterRequest<{
    reused: boolean;
    wallet: RequesterWallet;
    workflow: RawWorkflow;
    funding: {
      approveTxHash?: string;
      lockTxHash?: string;
      sourceGasTxHash?: string;
      sourceGasAmount?: string;
    };
  }>(`/workflows/${conversationId}/fund`, {
    method: "POST",
    body: JSON.stringify({ coordinatorProfileId }),
  }),
  run: (coordinatorProfileId: string, conversationId: string) => requesterRequest<RequesterRunState>(
    `/workflows/${conversationId}/run`,
    { method: "POST", body: JSON.stringify({ coordinatorProfileId }) },
  ),
  resume: async (workflowId: string, maxActions = 4) => {
    const response = await requesterRequest<{ resumed: boolean; actions: Array<{ action: string }>; workflow: RawWorkflow }>(
      `/platform-workflows/${workflowId}/resume`,
      { method: "POST", body: JSON.stringify({ maxActions }) },
    );
    return { ...response, workflow: normalizeWorkflow(response.workflow) };
  },
  status: (conversationId: string) => requesterRequest<{
    run: RequesterRunState | null;
    conversation: CoordinatorConversation;
    workflow: RawWorkflow | null;
  }>(`/workflows/${conversationId}/status`, { method: "GET" }),
  summary: (conversationId: string) => requesterRequest<CoordinatorWorkflowSummary>(
    `/workflows/${conversationId}/summary`,
    { method: "GET" },
  ),
};
