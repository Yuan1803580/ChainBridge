import { parseUnits } from "ethers";

import { canonicalHash } from "./utils.js";

export function validateDag(nodes) {
  const keys = nodes.map((node) => node.nodeKey);
  if (new Set(keys).size !== keys.length) throw new Error("DAG contains duplicate nodeKey values");
  const known = new Set(keys);
  const indegree = new Map(keys.map((key) => [key, 0]));
  const children = new Map(keys.map((key) => [key, []]));
  for (const node of nodes) {
    for (const dependency of node.dependsOn || []) {
      if (!known.has(dependency)) throw new Error(`Node ${node.nodeKey} has unknown dependency ${dependency}`);
      if (dependency === node.nodeKey) throw new Error(`Node ${node.nodeKey} depends on itself`);
      indegree.set(node.nodeKey, indegree.get(node.nodeKey) + 1);
      children.get(dependency).push(node.nodeKey);
    }
  }
  const queue = keys.filter((key) => indegree.get(key) === 0);
  let visited = 0;
  while (queue.length) {
    const key = queue.shift();
    visited += 1;
    for (const child of children.get(key)) {
      indegree.set(child, indegree.get(child) - 1);
      if (indegree.get(child) === 0) queue.push(child);
    }
  }
  if (visited !== nodes.length) throw new Error("Workflow plan is cyclic");
  return true;
}

export function validatePlan(conversation) {
  const { draft, plan } = conversation;
  if (!draft || !plan || !Array.isArray(draft.nodes)) throw new Error("Coordinator returned an incomplete plan");
  validateDag(draft.nodes);
  const decimals = 6;
  const budget = parseUnits(draft.budget, decimals);
  const platformFee = parseUnits(draft.platformFee, decimals);
  const nodeTotal = draft.nodes.reduce(
    (sum, node) => sum + parseUnits(node.reward, decimals) + parseUnits(node.reviewBudget || "0", decimals),
    0n,
  );
  if (nodeTotal + platformFee > budget) throw new Error("Node rewards, review budgets and platform fee exceed total budget");
  return { budget, platformFee, nodeTotal, remaining: budget - nodeTotal - platformFee };
}

export function termsSnapshot(conversation) {
  const { draft, plan } = conversation;
  const allowed = new Set([
    "nodeKey", "title", "description", "capability", "dependsOn", "acceptanceCriteria",
    "inputPayload", "reward", "riskLevel", "reviewPolicyVersion", "requiredApprovals",
    "challengePeriodSeconds", "samplingRateBps", "reviewBudget", "maxAttempts",
  ]);
  return {
    title: draft.title,
    objective: draft.objective,
    budget: draft.budget,
    platformFee: draft.platformFee,
    currency: draft.currency,
    sourceChainId: draft.sourceChainId,
    inputPayload: draft.inputPayload,
    nodes: plan.nodes.map((node) => ({
      ...Object.fromEntries(Object.entries(node).filter(([key]) => allowed.has(key))),
      riskAssessment: node.riskAssessment,
    })),
  };
}

export function verifyTermsHash(conversation, workflow) {
  const calculated = canonicalHash(termsSnapshot(conversation));
  if (calculated.toLowerCase() !== workflow.termsHash.toLowerCase()) {
    throw new Error(`termsHash mismatch: local=${calculated}, platform=${workflow.termsHash}`);
  }
  return calculated;
}

export function planSummary(conversation) {
  const totals = validatePlan(conversation);
  return {
    title: conversation.draft.title,
    budget: conversation.draft.budget,
    platformFee: conversation.draft.platformFee,
    nodeBudget: conversation.draft.nodes.reduce((sum, node) => sum + Number(node.reward), 0).toFixed(2),
    unallocated: Number(totals.remaining) / 1_000_000,
    nodes: conversation.draft.nodes.map((node) => {
      const planned = conversation.plan.nodes.find((item) => item.nodeKey === node.nodeKey);
      return {
        nodeKey: node.nodeKey,
        title: node.title,
        dependsOn: node.dependsOn,
        capability: node.capability,
        reward: node.reward,
        riskLevel: planned?.riskAssessment?.riskLevel || node.riskLevel,
        candidates: planned?.candidates?.length || 0,
      };
    }),
  };
}
