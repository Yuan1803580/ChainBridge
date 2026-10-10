import assert from "node:assert/strict";
import { createHmac, generateKeyPairSync, sign } from "node:crypto";
import test from "node:test";

import {
  buildRecoveryIntent,
  canonicalSha256,
  formatGasCost,
  homeRpcUrl,
  loadLocalProtocolConfig,
  opaqueIdToBytes32,
  replacementFee,
  routeRpcUrl,
  signTaskOffer,
  verifyRuntimeEnvelope,
} from "../src/local-protocol.ts";
import { ProtocolApi } from "../src/protocol-api.ts";

test("dynamic protocol routing selects RPC from the matched Agent homeChainId", () => {
  const config = loadLocalProtocolConfig("C:\\workspace");
  assert.equal(homeRpcUrl(config, 31338), "http://127.0.0.1:8546");
  assert.equal(homeRpcUrl(config, 31339), "http://127.0.0.1:8547");
  assert.equal(homeRpcUrl(config, 31340), "http://127.0.0.1:8548");
  assert.throws(() => homeRpcUrl(config, 99999), /Unsupported Agent homeChainId/);
});

test("testnet routes resolve stored RPC secret references inside the Worker", () => {
  const config = loadLocalProtocolConfig("C:\\workspace");
  const managedConfig = {
    ...config,
    workerProfile: "testnet-ccip" as const,
    homeRpcUrls: { 421614: "https://arbitrum-sepolia.example.test" },
  };
  assert.equal(
    routeRpcUrl(managedConfig, 421614, "secretref:DESTINATION_RPC_URL"),
    "https://arbitrum-sepolia.example.test",
  );
  assert.equal(routeRpcUrl(config, 31338, "http://127.0.0.1:8546"), "http://127.0.0.1:8546");
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

test("replacement and cancel intents preserve nonce ownership and raise fees by at least 12.5 percent", () => {
  const original = {
    intentId: `0x${"11".repeat(32)}`,
    requestId: "attempt-0001:submit-result:v1",
    runtimeId: "runtime-openclaw-01",
    did: "did:agent:openclaw-research-01:local",
    purpose: "submit_result",
    chainId: 421614,
    to: `0x${"22".repeat(20)}`,
    data: `0x${"33".repeat(132)}`,
    value: "0",
    gasLimit: 180000,
    maxFeePerGas: "1001",
    maxPriorityFeePerGas: "101",
    observedPendingNonce: 7,
    observedBlockNumber: 100,
    observedAt: "2026-08-05T00:00:00Z",
    routeKey: "SEPOLIA_TO_ARBITRUM_SEPOLIA_CCIP_BNM_V1",
    routeVersion: 1,
    policyVersion: "agent-wallet-v1",
    expiresAt: "2026-08-05T00:01:00Z",
  };
  const now = new Date("2026-08-05T01:00:00Z");
  const replacement = buildRecoveryIntent(original, "replace", `0x${"44".repeat(20)}`, 8, 101, "PENDING", now);
  assert.equal(replacement.purpose, "replace");
  assert.equal(replacement.to, original.to);
  assert.equal(replacement.data, original.data);
  assert.equal(replacement.gasLimit, original.gasLimit);
  assert.equal(replacement.replacesRequestId, original.requestId);
  assert.equal(replacement.maxFeePerGas, replacementFee(1001n).toString());
  assert.equal("nonce" in replacement, false);

  const cancel = buildRecoveryIntent(original, "cancel", `0x${"44".repeat(20)}`, 8, 101, "DROPPED", now);
  assert.equal(cancel.to, `0x${"44".repeat(20)}`);
  assert.equal(cancel.data, "0x");
  assert.equal(cancel.value, "0");
  assert.equal(cancel.gasLimit, 21000);
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
  const stable = (value: unknown): string => {
    if (Array.isArray(value)) return `[${value.map(stable).join(",")}]`;
    if (value && typeof value === "object") {
      return `{${Object.entries(value as Record<string, unknown>).sort(([a], [b]) => a.localeCompare(b)).map(([key, item]) => `${JSON.stringify(key)}:${stable(item)}`).join(",")}}`;
    }
    return JSON.stringify(value);
  };
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
    value: sign("sha256", Buffer.from(stable(signedPayload)), privateKey).toString("base64url"),
  };
  const expected = { workflowId: "workflow-1", nodeId: "node-1", attemptId: "attempt-1", attemptNumber: 1, reservationId: "reservation-1", deadline: "2026-08-05T00:00:00Z", nonce: "nonce-1234567890123456", offerId: "offer-1" };
  verifyRuntimeEnvelope(envelope, expected, "did:local:agent-b", "agent-b", publicKeyHex);
  assert.throws(() => verifyRuntimeEnvelope({ ...envelope, attemptId: "stale" }, expected, "did:local:agent-b", "agent-b", publicKeyHex), /attemptId/);
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
    assert.equal(headers.get("Authorization"), "Bearer local-worker-token");
    assert.ok(headers.get("Idempotency-Key"));
    return Response.json({ id: "job-1", status: "RUNNING" });
  };
  const api = new ProtocolApi("http://127.0.0.1:8000", "did:local:platform-admin", fetcher);
  assert.equal((await api.queuedJobs())[0].jobType, "DEPLOY_ROUTE_STACK");
  await api.updateJob("job-1", { status: "RUNNING" });
  assert.equal(requests[0].authorization, null);
  assert.equal(requests[1].authorization, "Bearer local-worker-token");
  assert.equal(requests[2].method, "PATCH");
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
