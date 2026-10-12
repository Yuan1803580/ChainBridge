export type AgentRole = "requester" | "provider" | "admin";
export type AgentStatus = "active" | "pending" | "suspended";
export type DemandStatus = "draft" | "published" | "awarded" | "closed" | "cancelled";
export type BidStatus = "pending" | "accepted" | "rejected" | "withdrawn";
export type OrderStatus =
  | "awaiting_funding"
  | "funded"
  | "in_progress"
  | "delivered"
  | "accepted"
  | "completed"
  | "cancelled"
  | "disputed"
  | "refund_pending"
  | "refunded";

export type JsonSchemaProperty = {
  type: "string" | "number" | "integer" | "boolean" | "array";
  title?: string;
  description?: string;
  default?: string | number | boolean | string[];
  enum?: Array<string | number>;
  minLength?: number;
  minimum?: number;
  maximum?: number;
  items?: { type: "string" | "number" | "integer" };
};

export type JsonSchema = {
  $schema?: string;
  type: "object";
  title?: string;
  required?: string[];
  properties: Record<string, JsonSchemaProperty>;
};

export interface Agent {
  id: string;
  did: string;
  name: string;
  description: string;
  roles: AgentRole[];
  status: AgentStatus;
  verified: boolean;
  availability: "online" | "busy" | "offline";
  rating: string;
  completedOrders: number;
  successRate: string;
  responseTime: string;
  capabilities: string[];
  serviceEndpoint: string;
  createdAt: string;
}

export interface Credential {
  id: string;
  type: string;
  issuer: string;
  subject: string;
  issuedAt: string;
  expiresAt: string;
  status: "valid" | "revoked" | "expired";
  claims: Record<string, string>;
}

export interface Capability {
  id: string;
  providerId: string;
  providerName: string;
  providerDid: string;
  title: string;
  summary: string;
  category: string;
  tags: string[];
  endpoint: string;
  method: "POST";
  pricingModel: "per_request" | "per_token" | "fixed";
  basePrice: string;
  currency: string;
  estimatedDuration: string;
  availability: "available" | "busy" | "offline";
  version: string;
  inputSchema: JsonSchema;
  outputSchema: JsonSchema;
  exampleRequest: Record<string, unknown>;
  exampleResponse: Record<string, unknown>;
  completedOrders: number;
  rating: string;
  updatedAt: string;
}

export interface Bid {
  id: string;
  demandId: string;
  providerId: string;
  providerName: string;
  providerDid: string;
  amount: string;
  currency: string;
  estimatedDuration: string;
  proposal: string;
  status: BidStatus;
  createdAt: string;
}

export interface Demand {
  id: string;
  requesterId: string;
  requesterName: string;
  requesterDid: string;
  title: string;
  description: string;
  category: string;
  tags: string[];
  budgetMin: string;
  budgetMax: string;
  currency: string;
  deadline: string;
  acceptanceCriteria: string[];
  status: DemandStatus;
  bidCount: number;
  bids?: Bid[];
  createdAt: string;
}

export interface OrderEvent {
  id: string;
  type: string;
  title: string;
  description: string;
  actorName: string;
  createdAt: string;
}

export interface OrderMessage {
  id: string;
  senderId: string;
  senderName: string;
  content: string;
  createdAt: string;
}

export interface Delivery {
  id: string;
  summary: string;
  uri: string;
  contentHash: string;
  createdAt: string;
}

export interface SettlementQuote {
  sourceAsset: string;
  settlementToken: string;
  sourceAmount: string;
  quotedSettlementAmount: string;
  minSettlementAmount: string;
  maxSlippageBps: number;
  quoteDeadline: string;
  quoteHash: string;
  rate: string;
  termsHash: string;
}

export interface SettlementInfo {
  id: string;
  status: Order["settlementStatus"];
  amount: string;
  currency: string;
  payerAccount?: string;
  payeeAccount?: string;
  assetId?: string;
  sourceChain?: string;
  destinationChain?: string;
  transactionHash?: string;
  ccipMessageId?: string;
}

export interface Order {
  id: string;
  orderNumber: string;
  source: "demand" | "capability";
  sourceTitle: string;
  requesterId: string;
  requesterName: string;
  providerId: string;
  providerName: string;
  amount: string;
  currency: string;
  status: OrderStatus;
  settlementStatus: "created" | "locked" | "release_pending" | "released" | "refund_pending" | "refunded" | "failed";
  settlementId: string;
  settlement?: SettlementInfo;
  settlementQuote?: SettlementQuote;
  termsHash: string;
  input: Record<string, unknown>;
  events: OrderEvent[];
  messages: OrderMessage[];
  delivery?: Delivery;
  createdAt: string;
  updatedAt: string;
}

export interface Notification {
  id: string;
  title: string;
  body: string;
  read: boolean;
  createdAt: string;
}

export interface AuditEntry {
  id: string;
  actor: string;
  action: string;
  resource: string;
  result: "success" | "blocked";
  createdAt: string;
}

export interface ApiResult<T> {
  data: T;
  source: "api" | "mock";
}

export interface DevIdentity {
  id: string;
  did: string;
  displayName: string;
  roles: AgentRole[];
}

export type WorkflowStatus = "DRAFT" | "FUNDED" | "RUNNING" | "COMPLETED" | "FAILED";
export type WorkflowNodeStatus = "BLOCKED" | "READY" | "WAITING" | "ASSIGNING" | "EXECUTING" | "IN_REVIEW" | "SETTLED" | "FAILED";
export type AttemptStatus = "OFFERED" | "EXECUTING" | "PROOF_RECEIVED" | "REJECTED" | "APPROVED" | "SETTLED" | "EXPIRED";
export type RouteStatus = "DISCOVERED" | "ONBOARDING" | "CONFIGURED" | "AVAILABLE" | "PAUSED";

export interface ChainSummary {
  chainId: number;
  name: string;
  shortName: string;
  role: "task" | "execution";
  rpcStatus: "healthy" | "degraded" | "offline";
  blockNumber: number;
  blockTime: string;
  color: string;
}

export interface ChainRoute {
  id: string;
  version: number;
  sourceChainId: number;
  destinationChainId: number;
  destinationName: string;
  status: RouteStatus;
  protocol: "LOCAL_CCIP" | "CHAINLINK_CCIP";
  settlementToken: string;
  payoutToken?: string;
  payoutTokenAddress?: string;
  payoutSwapAddress?: string;
  payoutDecimals?: number;
  payoutRate?: string;
  payoutSlippageBps?: number;
  remoteGateway: string;
  confirmations: number;
  medianLatency: string;
  lastCheckedAt: string;
  checks: Array<{ label: string; status: "pass" | "pending" | "fail" }>;
  inFlightMessages: number;
  deliveredMessages: number;
}

export interface AgentChainAccount {
  chainId: number;
  chainName: string;
  homeWallet: string;
  acceptedAsset: string;
  routeId: string;
  routeStatus: RouteStatus;
  gasBalance: string;
  gasStatus: "sufficient" | "low" | "empty";
  lastVerifiedAt: string;
}

export interface AgentDirectoryProfile {
  agentId: string;
  chainAccount: AgentChainAccount;
  heartbeatAt: string;
  endpointLatencyMs: number;
  uptime30d: string;
  priceFloor: string;
  currency: string;
  settledAttempts: number;
  rejectedAttempts: number;
  disputeRate: string;
  trustScore: number;
  specializations: string[];
  languages: string[];
  executionPolicy: string;
  sla: { responseSeconds: number; executionMinutes: number; concurrency: number };
  attestations: Array<{ name: string; issuer: string; status: "valid" | "expiring"; expiresAt: string }>;
}

export interface AttemptReservation {
  id: string;
  amount: string;
  returnGasDeposit: string;
  status: "RESERVED" | "RELEASED" | "SETTLED";
}

export interface WorkflowAttempt {
  id: string;
  number: number;
  agentId: string;
  agentName: string;
  agentDid: string;
  homeChainId: number;
  homeWallet?: string;
  homeChainName: string;
  routeId: string;
  routeVersion: number;
  status: AttemptStatus;
  reservation: AttemptReservation;
  startedAt: string;
  finishedAt?: string;
  output?: unknown;
  resultHash?: string;
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
  resultUri?: string;
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
  settlementAsset?: string;
  payoutAsset?: string;
  payoutTokenAddress?: string;
  payoutDecimals?: number;
  payoutRate?: string;
  payoutSlippageBps?: number;
  payoutQuoteAmount?: string;
  payoutMinAmount?: string;
  payoutQuoteExpiresAt?: string;
  payoutStatus?: string;
  payoutTxHash?: string;
  review?: {
    decision: "PASS" | "FAIL";
    reasonCode: string;
    summary: string;
    decidedAt: string;
  };
}

export interface WorkflowAgentCandidate {
  id: string;
  rank: number;
  agentId: string;
  agentName: string;
  agentKey: string;
  homeChainId: number;
  homeWallet?: string;
  routeId: string;
  routeVersion: number;
  score: string;
  scoreBreakdown: Record<string, string>;
  quoteAmount: string;
  currency: string;
  status: "AVAILABLE" | "SELECTED" | "FAILED" | "UNAVAILABLE" | "SETTLED" | "NOT_NEEDED";
  exclusionReason?: string;
}

export interface WorkflowAgentMatchRound {
  id: string;
  number: number;
  policyVersion: string;
  status: "ACTIVE" | "EXHAUSTED" | "COMPLETED";
  candidates: WorkflowAgentCandidate[];
}

export interface WorkflowFailureIncident {
  id: string;
  reasonCode: string;
  responsibleParty: "AGENT" | "PLATFORM" | "ROUTE" | "USER" | "DISPUTED";
  retryable: boolean;
  agentFault: boolean;
  gasTreatment: string;
  createdAt: string;
}

export interface WorkflowWaitingDiagnostics {
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
}

export interface WorkflowNode {
  id: string;
  sequence: number;
  title: string;
  description: string;
  criteriaHash: string;
  dependencies: string[];
  status: WorkflowNodeStatus;
  budget: string;
  capability: string;
  riskLevel?: "LOW" | "MEDIUM" | "HIGH";
  riskAssignmentMode?: "AUTO" | "MANUAL";
  riskScore?: number;
  riskReasons?: string[];
  reviewPolicyVersion?: string;
  reviewPolicyHash?: string;
  requiredApprovals?: number;
  challengePeriodSeconds?: number;
  samplingRateBps?: number;
  reviewBudget?: string;
  attempts: WorkflowAttempt[];
  matchRounds?: WorkflowAgentMatchRound[];
  failureIncidents?: WorkflowFailureIncident[];
  waitingDiagnostics?: WorkflowWaitingDiagnostics;
}

export interface WorkflowBudget {
  total: string;
  reserved: string;
  paid: string;
  platformFee: string;
  returnGasSpent: string;
  refundable: string;
  currency: string;
}

export interface WorkflowEvent {
  id: string;
  step: number;
  kind: "workflow" | "route" | "message" | "proof" | "review" | "settlement" | "security";
  title: string;
  detail: string;
  status: "complete" | "active" | "blocked";
  occurredAt: string;
  chainLabel?: string;
  nodeId?: string;
  attemptId?: string;
  txHash?: string;
  messageId?: string;
}

export interface WorkflowLedgerEntry {
  id: string;
  category: "RESERVATION" | "AGENT_REWARD" | "PLATFORM_FEE" | "SOURCE_GAS" | "RETURN_GAS" | "REFUND";
  description: string;
  debit: string;
  credit: string;
  responsibleParty: "USER" | "PLATFORM" | "AGENT" | "SYSTEM";
  assetSymbol?: string;
  chainId?: number;
  transactionHash?: string;
  nodeId?: string;
  attemptId?: string;
  occurredAt: string;
}

export interface Workflow {
  id: string;
  displayId: string;
  title: string;
  objective: string;
  status: WorkflowStatus;
  taskChainId: number;
  coordinatorName: string;
  coordinatorDid: string;
  coordinatorProfileId?: string;
  criteriaHash: string;
  fundingMode?: "RESERVED" | "ONCHAIN";
  fundingStatus?: string;
  fundingWallet?: string;
  lockTxHash?: string;
  sourceGasDeposit?: string;
  sourceGasRefunded?: string;
  contractSyncStatus?: string;
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
  budget: WorkflowBudget;
  nodes: WorkflowNode[];
  events: WorkflowEvent[];
  ledger: WorkflowLedgerEntry[];
  createdAt: string;
  updatedAt: string;
}

export interface ServiceHealth {
  id: string;
  name: string;
  kind: "api" | "coordinator" | "worker" | "relayer" | "scanner" | "agent";
  status: "healthy" | "degraded" | "offline";
  scope: string;
  latencyMs: number;
  queueDepth: number;
  lastHeartbeatAt: string;
}
