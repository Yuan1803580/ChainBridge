import { readFile } from "node:fs/promises";
import path from "node:path";

const apiBase = (process.env.API_URL || "http://127.0.0.1:8000").replace(/\/$/, "") + "/api/v1";
const demoTitle = "Local V1 B-success C-failure D-retry workflow";

type Json = Record<string, any>;

async function call(pathname: string, options: RequestInit = {}, token?: string, idempotencyKey?: string): Promise<Json> {
  const headers = new Headers(options.headers);
  headers.set("Accept", "application/json");
  if (options.body) headers.set("Content-Type", "application/json");
  if (token) headers.set("Authorization", `Bearer ${token}`);
  if (idempotencyKey) headers.set("Idempotency-Key", idempotencyKey);
  const response = await fetch(`${apiBase}${pathname}`, { ...options, headers });
  const body = await response.json().catch(() => ({}));
  if (!response.ok) throw new Error(`${options.method || "GET"} ${pathname} failed (${response.status}): ${JSON.stringify(body)}`);
  return body;
}

async function login(slug: string): Promise<string> {
  const session = await call("/auth/dev-login", {
    method: "POST",
    body: JSON.stringify({ did: `did:local:${slug}` }),
  });
  return session.accessToken;
}

function node(workflow: Json, nodeKey: string): Json {
  const value = workflow.nodes.find((item: Json) => item.nodeKey === nodeKey);
  if (!value) throw new Error(`Node ${nodeKey} was not returned by the API`);
  return value;
}

async function acceptanceTransactions(): Promise<Record<string, string>> {
  try {
    const reportPath = path.resolve(process.cwd(), ".runtime/local-v1/acceptance-report.json");
    const report = JSON.parse(await readFile(reportPath, "utf8"));
    const result = (fragment: string) => report.gasLedger.find((entry: Json) => entry.action.includes(fragment))?.transactionHash;
    return { B: result(":start") || "", C: result("N2:C-review-fail") || "", D: result("N2:D-review-pass") || "" };
  } catch {
    return {};
  }
}

function transaction(value: string | undefined, digit: string): string {
  return value && /^0x[0-9a-fA-F]{64}$/.test(value) ? value : `0x${digit.repeat(64)}`;
}

async function main() {
  await call("/health");
  const existing = await call("/workflows");
  const completed = existing.items.find((item: Json) =>
    item.title === demoTitle
    && item.status === "COMPLETED"
    && item.nodes.some((workflowNode: Json) => workflowNode.matchRounds?.length > 0),
  );
  if (completed) {
    console.log(`API_V1_DEMO_ALREADY_COMPLETE workflow=${completed.referenceCode} id=${completed.id}`);
    return;
  }

  const requester = await login("campus-requester");
const workerB = await login("workflow-research-b");
const workerC = await login("workflow-verification-c");
const workerD = await login("workflow-verification-d");
  const tx = await acceptanceTransactions();
  const runId = Date.now().toString(36);
  const mutate = (pathname: string, body: Json, token: string, key: string) => call(pathname, {
    method: "POST",
    body: JSON.stringify(body),
  }, token, `local-v1-${runId}-${key}`);

  let workflow = await mutate("/workflows", {
    title: demoTitle,
    objective: "Execute N1 on Chain B, reject N2 on Chain C, retry N2 on Chain D, and settle the balanced refund.",
    budget: "20.00",
    platformFee: "1.10",
    inputPayload: { source: "local-v1-acceptance-report", approvedChainIds: [31337, 31338, 31339, 31340] },
  }, requester, "create");
  const workflowId = workflow.id;
  const coordinate = async (key: string) => {
    const response = await mutate(`/workflows/${workflowId}/coordinate`, { maxActions: 1 }, requester, key);
    workflow = response.workflow;
    return response;
  };
  const submit = (nodeKey: string, attemptId: string, token: string, key: string, body: Json) => mutate(
    `/workflows/${workflowId}/nodes/${nodeKey}/attempts/${attemptId}/results`, body, token, key,
  );

  await coordinate("dispatch-b");
  const attemptB = node(workflow, "N1").currentAttempt;
  await submit("N1", attemptB.id, workerB, "result-b", {
    output: { summary: "Structured local evidence", score: 92 },
    proofHash: `0x${"b".repeat(64)}`,
    gasCost: "0.00011",
    transactionHash: transaction(tx.B, "1"),
  });
  await coordinate("review-b");
  if (node(workflow, "N2").status !== "BLOCKED") throw new Error("N2 unlocked before N1 reached SETTLED");
  await coordinate("settle-b");
  await coordinate("dispatch-c");

  const attemptC = node(workflow, "N2").currentAttempt;
  await submit("N2", attemptC.id, workerC, "result-c", {
    output: { verified: false, report: "The immutable verification rule is not satisfied" },
    proofHash: `0x${"c".repeat(64)}`,
    gasCost: "0.00012",
    transactionHash: transaction(tx.C, "2"),
  });
  await coordinate("review-c");
  await coordinate("dispatch-d");

  const attemptD = node(workflow, "N2").currentAttempt;
  if (attemptD.id === attemptC.id || attemptD.agentKey !== "Agent-D1") throw new Error("C failure did not create a new D attempt");
  const stale = await submit("N2", attemptC.id, workerC, "late-c", {
    output: { verified: true, report: "Late result must be rejected" },
    proofHash: `0x${"c".repeat(64)}`,
  });
  if (stale.accepted !== false || stale.code !== "STALE_ATTEMPT") throw new Error("Late C attempt was not rejected");

  await submit("N2", attemptD.id, workerD, "result-d", {
    output: { verified: true, report: "Audited final report" },
    proofHash: `0x${"d".repeat(64)}`,
    gasCost: "0.00013",
    transactionHash: transaction(tx.D, "3"),
  });
  await coordinate("review-d");
  await coordinate("settle-d");
  await coordinate("complete");

  const final = await call(`/workflows/${workflowId}`);
  if (final.status !== "COMPLETED" || final.paidAmount !== "11" || final.refundedAmount !== "7.9") {
    throw new Error(`Unexpected final settlement: ${JSON.stringify({ status: final.status, paid: final.paidAmount, refund: final.refundedAmount })}`);
  }
  if (!final.ledgerSummary.balanced) throw new Error("Workflow ledger is not balanced");
  const n2Candidates = node(final, "N2").matchRounds[0]?.candidates || [];
  if (
    n2Candidates.length !== 2
    || n2Candidates[0].agentKey !== "Agent-C1"
    || n2Candidates[0].status !== "FAILED"
    || n2Candidates[1].agentKey !== "Agent-D1"
    || n2Candidates[1].status !== "SETTLED"
  ) {
    throw new Error(`Unexpected N2 candidate audit trail: ${JSON.stringify(n2Candidates)}`);
  }
  console.log(`API_V1_DEMO_COMPLETED workflow=${final.referenceCode} id=${final.id}`);
  console.log("candidates=C1:rank1:FAILED,D1:rank2:SETTLED agents=B:4.20,C:0,D:6.80 fee=1.10 refund=7.90 staleAttemptRejected=true");
}

main().catch((error) => {
  console.error(error instanceof Error ? error.stack : error);
  process.exitCode = 1;
});
