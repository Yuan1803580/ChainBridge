import type {
  AttemptStatus,
  Workflow,
  WorkflowEvent,
  WorkflowLedgerEntry,
  WorkflowNodeStatus,
  WorkflowStatus,
} from "./types";
import { uuid } from "./uuid";

function resolveApiBase(): string {
  const configured = (process.env.NEXT_PUBLIC_API_URL || "").replace(/\/$/, "");
  if (typeof window !== "undefined") {
    // No explicit API base → use same-origin so the deployed app works behind
    // any host/proxy (e.g. nginx /api/ -> api:8000) without a rebuild. An
    // explicit base (including http://localhost:8000 for local dev) is honored.
    if (!configured) return window.location.origin;
    return configured;
  }
  return configured || "http://127.0.0.1:8000";
}
const API_URL = resolveApiBase();

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
  resultHash?: string;
  proofHash?: string;
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
  fundingMode?: "RESERVED" | "ONCHAIN";
  fundingStatus?: string;
  fundingWallet?: string;
  lockTxHash?: string;
  onchainWorkflowId?: string;
  sourceGasDeposit?: string;
  sourceGasRefunded?: string;
  contractSyncStatus?: string;
  termsHash: string;
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
  createdAt: string;
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
  chainId: number;
  homeWallet?: string;
  capabilities: string[];
  priority: number;
  active: boolean;
  metadata: Record<string, unknown>;
  priceFloor: string;
  currency: string;
  successRateBps: number;
  trustScore: number;
  endpointLatencyMs: number;
  availability: "ONLINE" | "BUSY" | "OFFLINE";
  capacityAvailable: boolean;
  gasReady: boolean;
  credentialStatus?: "UNCONFIGURED" | "REFERENCE_ONLY" | "ACTIVE" | "REVOKED";
  credentialFingerprint?: string;
  provisioningId?: string;
  onboardingStatus?: string;
  invitationId?: string;
  agent: {
    id: string;
    did: string;
    displayName: string;
    description: string;
    status: string;
    reputationScore: string;
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
    candidates: WorkflowPlanCandidate[];
  }>;
};

export type WorkflowFundingConfig = {
  profile: "local-demo" | "managed-testnet";
  chainId: number;
  chainName: string;
  assetLabel: string;
  localTestnet: boolean;
  defaultSourceGasAmount: string;
  contracts: {
    sourceToken: string;
    taskEscrow: string;
    gasTreasury: string;
  };
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

export type RawChainWorkerHealth = {
  status: "healthy" | "degraded" | "down";
  failingStepNames?: string[];
  startedAt?: string;
  lastCycleAt?: string;
  sourceScanAt?: string;
  destinationScanAt?: string;
  relayerAt?: string;
  relayerBalanceWei?: string;
  failingSteps?: Array<{ name: string; since?: string; lastError?: string }>;
  sourceNextBlock?: number;
  destinationNextBlock?: number;
  pendingEvents?: number;
  deadLetters?: number;
  submissions?: number;
  abiMismatchCount?: number;
  lastAbiMismatchAt?: string;
  lastAbiMismatchDetail?: string;
  duplicateReplayCount?: number;
  duplicateConflictCount?: number;
  lastDuplicateConflictAt?: string;
  lastDuplicateConflictDetail?: string;
};

export type ExternalAgentOnboardingRequest = {
  ownerName: string;
  ownerEmail: string;
  organization?: string;
  agentName: string;
  agentKey: string;
  did?: string;
  endpoint?: string;
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
  roles: Array<"requester" | "provider">;
};

export type ExternalAgentOnboardingResponse = {
  admissionStatus: "PENDING" | "PENDING_PLATFORM_REVIEW" | "ACTIVE" | "ACTIVE_AWAITING_HEARTBEAT";
  onboardingToken: string;
  statusCode: string;
  agent: {
    id: string;
    did: string;
    displayName: string;
    endpoint?: string;
    roles: string[];
  };
  profile: RawAgentProfile;
  route: RawRoute;
  chainResolution: {
    sourceChainId: number;
    destinationChainId: number;
    routeKey: string;
    routeVersion: number;
    settlementAsset: string;
    payoutAsset: string;
    payoutRate: string;
  };
  next: {
    challengeEndpoint?: string;
    verifyEndpoint?: string;
    statusEndpoint?: string;
    completeEndpoint?: string;
    heartbeatEndpoint: string;
    resultSubmission: string;
  };
};

export type AgentOnboardingStatus = {
  profileId: string;
  admissionStatus: "PENDING_PROVISION" | "PROVISION_FAILED" | "REVOKED" | "PENDING_OWNERSHIP_PROOF" | "PENDING_TECHNICAL_CHECKS" | "ACTIVE_AWAITING_HEARTBEAT" | "ACTIVE";
  matchable: boolean;
  roles: Array<"requester" | "provider">;
  canRequest: boolean;
  canProvide: boolean;
  agent: {
    id: string;
    agentKey: string;
    did: string;
    name: string;
    endpoint?: string;
  };
  network: {
    chainId: number;
    homeWallet?: string;
    routeKey?: string;
    payoutAsset?: string;
  };
  verification: {
    identityVerified: boolean;
    walletVerified: boolean;
    healthCheckPassed: boolean;
    manifestCheckPassed: boolean;
    testTaskPassed: boolean;
    gasReadyVerified: boolean;
    checkedAt?: string;
  };
  steps: Array<{
    key: string;
    label: string;
    status: "complete" | "current" | "pending" | "failed";
  }>;
  updatedAt: string;
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
};

export type AgentInvitation = {
  id: string;
  routeKey: string;
  minRouteVersion: number;
  maxRouteVersion: number;
  capabilities: string[];
  roles: Array<"requester" | "provider">;
  executorType: "llm" | "http";
  executorCredentialRef?: string;
  status: "AVAILABLE" | "RESERVED" | "CONSUMED" | "USED_FAILED";
  reservedRegistrationId?: string;
  expiresAt: string;
  consumedAt?: string;
  createdAt: string;
};

export type AgentProvisioningRecord = {
  id: string;
  provisioningId: string;
  profileId: string;
  invitationId?: string;
  status: string;
  attempts: number;
  lastErrorHash?: string;
  nextAttemptAt: string;
  completedAt?: string;
  createdAt: string;
  agent: { name?: string; did?: string; agentKey?: string; onboardingStatus?: string };
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
    signal: init.signal || AbortSignal.timeout(8000),
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
        resultHash: attempt.resultHash,
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
    coordinatorName: "Coordinator Agent",
    coordinatorDid: "did:local:coordinator",
    criteriaHash: raw.termsHash,
    fundingMode: raw.fundingMode,
    fundingStatus: raw.fundingStatus,
    fundingWallet: raw.fundingWallet,
    lockTxHash: raw.lockTxHash,
    sourceGasDeposit: raw.sourceGasDeposit,
    sourceGasRefunded: raw.sourceGasRefunded,
    contractSyncStatus: raw.contractSyncStatus,
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
  health: () => apiRequest<{ status: string; database: string; timestamp: string }>("/api/v1/health"),
  workflows: async () => {
    const response = await apiRequest<{ items: RawWorkflow[]; total: number }>("/api/v1/workflows");
    return { ...response, items: response.items.map(normalizeWorkflow) };
  },
  workflow: async (id: string) => normalizeWorkflow(await apiRequest<RawWorkflow>(`/api/v1/workflows/${id}`)),
  workflowFundingConfig: () => apiRequest<WorkflowFundingConfig>("/api/v1/workflow-funding-config"),
  planWorkflow: (body: { objective: string; budget: string; platformFee?: string; inputPayload?: Record<string, unknown> }) =>
    apiRequest<WorkflowPlan>("/api/v1/workflow-plans", {
      method: "POST",
      body: JSON.stringify(body),
      idempotencyKey: uuid(),
    }),
  createWorkflow: (body: { title: string; objective: string; budget: string; platformFee?: string; inputPayload?: Record<string, unknown>; nodes?: WorkflowNodeInput[]; fundingMode?: "RESERVED" | "ONCHAIN"; payerAddress?: string }) =>
    apiRequest<RawWorkflow>("/api/v1/workflows", {
      method: "POST",
      body: JSON.stringify(body),
      idempotencyKey: uuid(),
    }),
  confirmWorkflowFunding: (id: string, body: { walletAddress: string; lockTxHash: string; sourceGasTxHash?: string; sourceGasAmount: string }) =>
    apiRequest<{ fundingStatus: string; protocolJobId: string; workflow: RawWorkflow }>(`/api/v1/workflows/${id}/funding-confirmations`, {
      method: "POST",
      body: JSON.stringify({ ...body, verificationMode: "RPC" }),
      idempotencyKey: `funding-${body.lockTxHash}`,
      signal: AbortSignal.timeout(30000),
    }),
  coordinate: (id: string, maxActions = 1) => apiRequest<{ actions: Array<{ action: string }>; workflow: RawWorkflow }>(`/api/v1/workflows/${id}/coordinate`, {
    method: "POST",
    body: JSON.stringify({ maxActions }),
    idempotencyKey: uuid(),
  }),
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
  agents: () => apiRequest<{ items: RawAgentProfile[]; total: number }>("/api/v1/workflow-agents"),
  agent: (profileId: string) => apiRequest<RawAgentProfile>(`/api/v1/workflow-agents/${profileId}`),
  workerHealth: () => apiRequest<RawChainWorkerHealth>("/api/v1/chain-worker-health"),
  invitations: () => apiRequest<{ items: AgentInvitation[]; total: number }>("/api/v1/agent-invitations"),
  createInvitation: (body: { routeKey: string; minRouteVersion: number; maxRouteVersion: number; capabilities: string[]; roles: Array<"requester" | "provider">; executorType: "llm" | "http"; executorCredentialRef?: string; expiresInHours: number }) =>
    apiRequest<AgentInvitation & { inviteCode: string }>("/api/v1/agent-invitations", {
      method: "POST",
      body: JSON.stringify(body),
      idempotencyKey: uuid(),
    }),
  provisioning: () => apiRequest<{ items: AgentProvisioningRecord[]; total: number }>("/api/v1/agent-onboarding/provisioning"),
  reconcileProvisioning: () => apiRequest<{ processed: number; completed: number; failed: number }>("/api/v1/agent-onboarding/provisioning/reconcile", { method: "POST" }),
  adminRetryProvisioning: (profileId: string) => apiRequest<{ profileId: string; provisioningId: string; status: string; attempts?: number }>(`/api/v1/agent-onboarding/registrations/${profileId}/provision-admin-retry`, { method: "POST" }),
  onboardAgent: (body: ExternalAgentOnboardingRequest) => apiRequest<ExternalAgentOnboardingResponse>("/api/v1/agent-onboarding/registrations", {
    method: "POST",
    body: JSON.stringify(body),
  }),
  onboardingChallenge: (profileId: string) => apiRequest<AgentOnboardingChallenge>(`/api/v1/agent-onboarding/registrations/${profileId}/challenge`, {
    method: "POST",
  }),
  verifyOnboarding: (profileId: string, body: { challengeId: string; identitySignature: string; walletSignature: string }) =>
    apiRequest<AgentOnboardingActionResponse>(`/api/v1/agent-onboarding/registrations/${profileId}/verify`, {
      method: "POST",
      body: JSON.stringify(body),
    }),
  onboardingStatus: (profileId: string, onboardingToken: string) =>
    apiRequest<AgentOnboardingStatus>(`/api/v1/agent-onboarding/registrations/${profileId}/status`, {
      headers: { "X-Onboarding-Token": onboardingToken },
    }),
  completeOnboarding: (profileId: string, onboardingToken: string, runtimeAccessToken: string) =>
    apiRequest<AgentOnboardingStatus & { checksPassed: boolean; evidence?: Record<string, unknown> }>(`/api/v1/agent-onboarding/registrations/${profileId}/complete`, {
      method: "POST",
      headers: { "X-Onboarding-Token": onboardingToken },
      body: JSON.stringify({ runtimeAccessToken }),
    }),
  checkOnboarding: (profileId: string, runtimeAccessToken: string) =>
    apiRequest<AgentOnboardingActionResponse>(`/api/v1/agent-onboarding/registrations/${profileId}/checks`, {
      method: "POST",
      body: JSON.stringify({ runtimeAccessToken }),
    }),
  activateOnboarding: (profileId: string, notes = "Platform checks passed.") =>
    apiRequest<AgentOnboardingActionResponse>(`/api/v1/agent-onboarding/registrations/${profileId}/activate`, {
      method: "POST",
      body: JSON.stringify({ notes }),
    }),
  messages: (status?: string) => apiRequest<{ items: RawCrossChainMessage[]; total: number }>(`/api/v1/cross-chain-messages${status ? `?status=${status}` : ""}`),
};
