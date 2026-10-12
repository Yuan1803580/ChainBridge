import test from "node:test";
import assert from "node:assert/strict";

import { canonicalHash, canonicalJson } from "../src/utils.js";
import { validateDag, validatePlan, verifyTermsHash } from "../src/planning.js";
import { isResumableRun, retryDelay } from "../src/run-state.js";

const node = {
  nodeKey: "N1",
  title: "Research",
  description: "Research a topic",
  capability: "research",
  dependsOn: [],
  acceptanceCriteria: {},
  inputPayload: {},
  reward: "8.00",
  riskLevel: "LOW",
  reviewPolicyVersion: "RISK_REVIEW_V2",
  requiredApprovals: 0,
  challengePeriodSeconds: 0,
  samplingRateBps: 0,
  reviewBudget: "0",
  maxAttempts: 3,
};

const conversation = {
  draft: {
    title: "Research workflow",
    objective: "Research and verify a workflow topic.",
    budget: "10.00",
    platformFee: "1.00",
    currency: "USDC",
    sourceChainId: 31337,
    inputPayload: {},
    nodes: [node],
  },
  plan: {
    nodes: [{ ...node, riskAssessment: { score: 10, riskLevel: "LOW", reasons: [], assignmentMode: "AUTO" } }],
  },
};

test("canonical JSON is independent of object insertion order", () => {
  assert.equal(canonicalJson({ b: 2, a: 1 }), canonicalJson({ a: 1, b: 2 }));
  assert.equal(canonicalHash({ b: 2, a: 1 }), canonicalHash({ a: 1, b: 2 }));
});

test("DAG validation rejects cycles", () => {
  assert.throws(() => validateDag([
    { nodeKey: "N1", dependsOn: ["N2"] },
    { nodeKey: "N2", dependsOn: ["N1"] },
  ]), /cyclic/);
});

test("plan validation enforces budget conservation", () => {
  assert.equal(validatePlan(conversation).remaining, 1_000_000n);
  const invalid = structuredClone(conversation);
  invalid.draft.nodes[0].reward = "10.00";
  assert.throws(() => validatePlan(invalid), /exceed/);
});

test("termsHash is independently recomputed", () => {
  const workflow = { termsHash: canonicalHash({
    ...conversation.draft,
    nodes: [{ ...node, riskAssessment: conversation.plan.nodes[0].riskAssessment }],
  }) };
  assert.equal(verifyTermsHash(conversation, workflow), workflow.termsHash);
});

test("non-terminal requester runs resume without a fixed cycle limit", () => {
  assert.equal(isResumableRun({ status: "RUNNING", cycle: 5000 }), true);
  assert.equal(isResumableRun({ status: "WAITING_RETRY", cycle: 5000 }), true);
  assert.equal(isResumableRun({ status: "COMPLETED" }), false);
});

test("requester retry delay is bounded", () => {
  assert.equal(retryDelay(1, 2000, 30000), 2000);
  assert.equal(retryDelay(5, 2000, 30000), 30000);
  assert.equal(retryDelay(100, 2000, 30000), 30000);
});
