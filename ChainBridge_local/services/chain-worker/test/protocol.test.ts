import assert from "node:assert/strict";
import { createHmac, generateKeyPairSync, sign } from "node:crypto";
import { mkdtempSync, rmSync, writeFileSync } from "node:fs";
import { createServer } from "node:http";
import type { AddressInfo } from "node:net";
import { tmpdir } from "node:os";
import { join } from "node:path";
import test from "node:test";

import {
  canonicalSha256,
  canonicalJson,
  agentRunnerToken,
  buildAgentTaskInput,
  formatGasCost,
  homeRpcUrl,
  loadLocalProtocolConfig,
  opaqueIdToBytes32,
  postAgentRunnerJson,
  signTaskOffer,
  verifyRuntimeEnvelope,
} from "../src/local-protocol.ts";
import { ProtocolApi } from "../src/protocol-api.ts";

async function listen(server: ReturnType<typeof createServer>): Promise<number> {
  await new Promise<void>((resolve, reject) => {
    server.once("error", reject);
    server.listen(0, "127.0.0.1", resolve);
  });
  return (server.address() as AddressInfo).port;
}

async function close(server: ReturnType<typeof createServer>): Promise<void> {
  server.closeAllConnections();
  await new Promise<void>((resolve) => server.close(() => resolve()));
}

test("Agent Runner requests support responses that arrive after a long processing delay", async () => {
  const server = createServer((_request, response) => {
    setTimeout(() => {
      response.writeHead(200, { "Content-Type": "application/json" });
      response.end(JSON.stringify({ ok: true }));
    }, 80);
  });
  const port = await listen(server);
  try {
    const response = await postAgentRunnerJson(
      `http://127.0.0.1:${port}/invoke`,
      "test-token",
      { objective: "delayed response" },
      500,
    );
    assert.equal(response.statusCode, 200);
    assert.deepEqual(response.body, { ok: true });
  } finally {
    await close(server);
  }
});

test("Agent Runner request timeout is controlled by the protocol deadline", async () => {
  const server = createServer((request) => request.resume());
  const port = await listen(server);
  try {
    await assert.rejects(
      () => postAgentRunnerJson(
        `http://127.0.0.1:${port}/invoke`,
        "test-token",
        { objective: "never responds" },
        50,
      ),
      (error: any) => error?.kind === "TIMEOUT" && /50ms/.test(error.message),
    );
  } finally {
    await close(server);
  }
});

test("dynamic protocol routing selects RPC from the matched Agent homeChainId", () => {
  const config = loadLocalProtocolConfig("C:\\workspace");
  assert.equal(homeRpcUrl(config, 31338), "http://127.0.0.1:8546");
  assert.equal(homeRpcUrl(config, 31339), "http://127.0.0.1:8547");
  assert.equal(homeRpcUrl(config, 31340), "http://127.0.0.1:8548");
  assert.throws(() => homeRpcUrl(config, 99999), /Unsupported Agent homeChainId/);
});

test("Worker Agent timeout and task-offer deadline are configurable", () => {
  const previousInvocationTimeout = process.env.AGENT_INVOCATION_TIMEOUT_SECONDS;
  const previousTaskDeadline = process.env.AGENT_TASK_DEADLINE_SECONDS;
  process.env.AGENT_INVOCATION_TIMEOUT_SECONDS = "2100";
  process.env.AGENT_TASK_DEADLINE_SECONDS = "2400";
  try {
    const config = loadLocalProtocolConfig("C:\\workspace");
    assert.equal(config.agentInvocationTimeoutMs, 2_100_000);
    assert.equal(config.agentTaskDeadlineMs, 2_400_000);
  } finally {
    if (previousInvocationTimeout === undefined) delete process.env.AGENT_INVOCATION_TIMEOUT_SECONDS;
    else process.env.AGENT_INVOCATION_TIMEOUT_SECONDS = previousInvocationTimeout;
    if (previousTaskDeadline === undefined) delete process.env.AGENT_TASK_DEADLINE_SECONDS;
    else process.env.AGENT_TASK_DEADLINE_SECONDS = previousTaskDeadline;
  }
});

test("Worker refuses Agent home-wallet private keys in every environment", () => {
  const previousEnvironment = process.env.APP_ENV;
  const previousKeys = process.env.AGENT_HOME_WALLET_PRIVATE_KEYS_JSON;
  process.env.APP_ENV = "production";
  process.env.AGENT_HOME_WALLET_PRIVATE_KEYS_JSON = JSON.stringify({
    "workflow-research-b": `0x${"11".repeat(32)}`,
  });
  try {
    assert.throws(
      () => loadLocalProtocolConfig("C:\\workspace"),
      /Worker must not load AGENT_HOME_WALLET_PRIVATE_KEYS_JSON/,
    );
  } finally {
    if (previousEnvironment === undefined) delete process.env.APP_ENV;
    else process.env.APP_ENV = previousEnvironment;
    if (previousKeys === undefined) delete process.env.AGENT_HOME_WALLET_PRIVATE_KEYS_JSON;
    else process.env.AGENT_HOME_WALLET_PRIVATE_KEYS_JSON = previousKeys;
  }
});

test("Worker selects a persisted runtime token for each external Agent", () => {
  const directory = mkdtempSync(join(tmpdir(), "agent-runtime-tokens-"));
  const store = join(directory, "tokens.json");
  writeFileSync(store, JSON.stringify({
    "external-agent-c": "external-token-c",
    "did:external:agent-d": "external-token-d",
  }));
  try {
    const config = { agentRunnerApiToken: "legacy-token", agentRuntimeTokenStorePath: store };
    assert.equal(agentRunnerToken(config, "external-agent-c", "did:external:agent-c"), "external-token-c");
    assert.equal(agentRunnerToken(config, "external-agent-d", "did:external:agent-d"), "external-token-d");
    assert.equal(agentRunnerToken(config, "legacy-agent", "did:local:legacy-agent"), "legacy-token");
  } finally {
    rmSync(directory, { recursive: true, force: true });
  }
});

test("Worker sends the node contract and settled dependency outputs to an Agent", () => {
  const input = buildAgentTaskInput(
    {
      id: "workflow-1",
      title: "Research workflow",
      objective: "Produce a verified research report.",
      inputPayload: { language: "zh-CN" },
      nodes: [
        { nodeKey: "N1", outputPayload: { paperTitle: "Prior paper" } },
      ],
    },
    {
      nodeKey: "N2",
      title: "Verify the paper",
      description: "Check source existence and metadata.",
      capability: "verification",
      dependsOn: ["N1"],
      inputPayload: { verifyAgainst: "N1 output" },
      acceptanceCriteria: {
        requiredFields: ["sourceExists", "metadataMatches"],
      },
    },
  ) as any;

  assert.equal(input.objective, "Verify the paper\nCheck source existence and metadata.");
  assert.equal(input.input.language, "zh-CN");
  assert.equal(input.input._node.capability, "verification");
  assert.deepEqual(input.input._node.acceptanceCriteria.requiredFields, [
    "sourceExists",
    "metadataMatches",
  ]);
  assert.equal(input.dependencies.N1.paperTitle, "Prior paper");
});

test("opaque database IDs have stable bytes32 snapshots", () => {
  assert.equal(
    opaqueIdToBytes32("12345678-1234-1234-1234-123456789abc"),
    "0x12345678123412341234123456789abc00000000000000000000000000000000",
  );
  assert.equal(opaqueIdToBytes32(`0x${"a".repeat(64)}`), `0x${"a".repeat(64)}`);
  assert.equal(opaqueIdToBytes32("same-value"), opaqueIdToBytes32("same-value"));
});

test("Gas receipt values fit the V1 eight-decimal ledger without rounding up", () => {
  assert.equal(formatGasCost(123_456_789_012_345n), "0.00012345");
  assert.equal(formatGasCost(10_000_000_000n), "0.00000001");
  assert.equal(formatGasCost(9_999_999_999n), "0");
});

test("task offers and Agent runtime results use deterministic signed payloads", () => {
  const offer = { workflowId: "workflow-1", attemptNumber: 1, nonce: "nonce-1234567890123456" };
  assert.equal(signTaskOffer("secret", offer), signTaskOffer("secret", { nonce: offer.nonce, attemptNumber: 1, workflowId: "workflow-1" }));

  const { privateKey, publicKey } = generateKeyPairSync("ec", { namedCurve: "secp256k1" });
  const jwk = publicKey.export({ format: "jwk" });
  const publicKeyHex = `0x04${Buffer.from(jwk.x!, "base64url").toString("hex")}${Buffer.from(jwk.y!, "base64url").toString("hex")}`;
  const output = { summary: "signed output" };
  const envelope: Record<string, unknown> = {
    agentId: "agent-b",
    capabilityId: "research",
    completedAt: "2026-08-04T00:00:00Z",
    did: "did:local:agent-b",
    outputHash: `sha256:${canonicalSha256(output).slice(2)}`,
    requestId: "offer-1",
    workflowId: "workflow-1",
    nodeId: "node-1",
    attemptId: "attempt-1",
    attemptNumber: 1,
    reservationId: "reservation-1",
    deadline: "2026-08-05T00:00:00Z",
    nonce: "nonce-1234567890123456",
    offerId: "offer-1",
    output,
  };
  const signedFields = ["agentId", "capabilityId", "completedAt", "did", "outputHash", "requestId", "workflowId", "nodeId", "attemptId", "attemptNumber", "reservationId", "deadline", "nonce", "offerId"];
  const signedPayload = Object.fromEntries(signedFields.map((field) => [field, envelope[field]]));
  envelope.signature = {
    algorithm: "ES256K",
    publicKeyHex,
    value: sign("sha256", Buffer.from(canonicalJson(signedPayload)), privateKey).toString("base64url"),
  };
  const expected = { workflowId: "workflow-1", nodeId: "node-1", attemptId: "attempt-1", attemptNumber: 1, reservationId: "reservation-1", deadline: "2026-08-05T00:00:00Z", nonce: "nonce-1234567890123456", offerId: "offer-1" };
  verifyRuntimeEnvelope(envelope, expected, "did:local:agent-b", "agent-b", publicKeyHex);
  assert.throws(() => verifyRuntimeEnvelope({ ...envelope, attemptId: "stale" }, expected, "did:local:agent-b", "agent-b", publicKeyHex), /attemptId/);
});

test("RFC 8785 JCS is stable for Chinese keys, Unicode, nested arrays, and floats", () => {
  const value = {
    "中文键": "值🌐",
    nested: {
      "é": "e\u0301",
      array: [3, 1.0, 0.000001, 1e30, { "😀": "line\ntext" }],
    },
    a: true,
    z: null,
  };
  const expected = '{"a":true,"nested":{"array":[3,1,0.000001,1e+30,{"😀":"line\\ntext"}],"é":"é"},"z":null,"中文键":"值🌐"}';

  assert.equal(canonicalJson(value), expected);
  assert.equal(canonicalSha256(value), "0x08b030c5ecd691aefc764a519589212fc3be954dd8ccf159f48699439985e3b1");
  assert.throws(() => canonicalJson({ value: Number.NaN }), /NaN|finite|JSON/i);
  assert.throws(() => canonicalJson({ value: Number.POSITIVE_INFINITY }), /Infinity|finite|JSON/i);
});

test("protocol API obtains a short-lived local admin token and authenticates job updates", async () => {
  const requests: Array<{ url: string; method: string; authorization: string | null }> = [];
  const fetcher: typeof fetch = async (input, init) => {
    const url = String(input);
    const headers = new Headers(init?.headers);
    requests.push({ url, method: init?.method || "GET", authorization: headers.get("Authorization") });
    if (url.endsWith("/api/v1/auth/dev-login")) {
      assert.deepEqual(JSON.parse(String(init?.body)), { did: "did:local:platform-admin" });
      return Response.json({ accessToken: "local-worker-token" });
    }
    if (url.includes("/api/v1/protocol-jobs?")) {
      assert.equal(headers.get("Authorization"), "Bearer local-worker-token");
      return Response.json({ items: [{ id: "job-1", jobType: "DEPLOY_ROUTE_STACK", status: "QUEUED", payload: {}, attempts: 0 }] });
    }
    if (url.includes("/api/v1/protocol-jobs/recover-stale?")) {
      assert.equal(init?.method, "POST");
      assert.ok(url.endsWith("staleAfterSeconds=1800"));
      return Response.json({ recoveredCount: 1, recoveredJobIds: ["job-stale"], staleAfterSeconds: 1800, recoveredAt: "2026-08-10T00:00:00Z" });
    }
    assert.equal(headers.get("Authorization"), "Bearer local-worker-token");
    assert.ok(headers.get("Idempotency-Key"));
    return Response.json({ id: "job-1", status: "RUNNING" });
  };
  const api = new ProtocolApi("http://127.0.0.1:8000", "did:local:platform-admin", fetcher);
  assert.equal((await api.queuedJobs())[0].jobType, "DEPLOY_ROUTE_STACK");
  await api.updateJob("job-1", { status: "RUNNING" });
  const recovered = await api.recoverStaleJobs(1800);
  assert.deepEqual(recovered.recoveredJobIds, ["job-stale"]);
  assert.equal(requests[0].authorization, null);
  assert.equal(requests[1].authorization, "Bearer local-worker-token");
  assert.equal(requests[2].method, "PATCH");
  assert.equal(requests[3].method, "POST");
});

test("protocol API registers ReviewManagerV2 with replay-protected listener HMAC", async () => {
  const secret = "runtime-registration-secret";
  const fetcher: typeof fetch = async (input, init) => {
    const url = new URL(String(input));
    const headers = new Headers(init?.headers);
    const body = String(init?.body || "");
    const signed = [
      String(init?.method || "GET").toUpperCase(),
      headers.get("X-Listener-Timestamp"),
      headers.get("X-Listener-Nonce"),
      url.pathname,
      body,
    ].join("\n");
    const expected = `sha256=${createHmac("sha256", secret).update(signed).digest("hex")}`;
    assert.equal(headers.get("X-Listener-Id"), "review-worker");
    assert.equal(headers.get("X-Listener-Signature"), expected);
    return Response.json(JSON.parse(body));
  };
  const api = new ProtocolApi("http://127.0.0.1:8000", "did:local:platform-admin", fetcher, "review-worker", secret);
  const payload = {
    chainId: 31337,
    contractAddress: `0x${"11".repeat(20)}`,
    codeHash: `0x${"22".repeat(32)}`,
    manifestHash: `0x${"33".repeat(32)}`,
  };
  assert.deepEqual(await api.registerRuntimeContract("REVIEW_MANAGER_V2", payload), payload);
});
