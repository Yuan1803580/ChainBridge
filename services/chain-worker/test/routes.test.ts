import assert from "node:assert/strict";
import test from "node:test";

import { AsyncMockCcipRelay } from "../src/mock-relay.ts";
import { parseRouteContexts, RouteContextRegistry } from "../src/routes.ts";

const address = (digit: string) => `0x${digit.repeat(40)}`;
const routesJson = JSON.stringify([
  {
    routeId: "A_TO_B",
    routeVersion: 1,
    sourceChainId: 31337,
    destinationChainId: 31338,
    sourceRpcUrl: "http://127.0.0.1:8545",
    destinationRpcUrl: "http://127.0.0.1:8546",
    sourceGatewayAddress: address("a"),
    remoteWorkBoardAddress: address("b"),
  },
  {
    routeId: "A_TO_C",
    routeVersion: 1,
    sourceChainId: 31337,
    destinationChainId: 31339,
    sourceRpcUrl: "http://127.0.0.1:8545",
    destinationRpcUrl: "http://127.0.0.1:8547",
    sourceGatewayAddress: address("a"),
    remoteWorkBoardAddress: address("c"),
  },
  {
    routeId: "A_TO_D",
    routeVersion: 1,
    sourceChainId: 31337,
    destinationChainId: 31340,
    sourceRpcUrl: "http://127.0.0.1:8545",
    destinationRpcUrl: "http://127.0.0.1:8548",
    sourceGatewayAddress: address("a"),
    remoteWorkBoardAddress: address("d"),
  },
]);

test("route contexts key clients by routeId and routeVersion", () => {
  const registry = new RouteContextRegistry(parseRouteContexts(routesJson));
  assert.equal(registry.resolve("A_TO_B", 1).destinationChainId, 31338);
  assert.equal(registry.resolve("A_TO_C", 1).destinationChainId, 31339);
  assert.equal(registry.resolve("A_TO_D", 1).destinationChainId, 31340);
  assert.equal(registry.list().length, 3);
  assert.throws(() => registry.resolve("A_TO_D", 2), /was not found/);
});

test("route parser rejects duplicate route versions", () => {
  const value = JSON.parse(routesJson);
  value.push(value[0]);
  assert.throws(() => parseRouteContexts(JSON.stringify(value)), /Duplicate route context/);
});

test("async relay retries transport without changing businessAttemptId", async () => {
  const registry = new RouteContextRegistry(parseRouteContexts(routesJson));
  let calls = 0;
  const observedAttempts: string[] = [];
  const relay = new AsyncMockCcipRelay(registry, {
    async deliver(_route, envelope) {
      calls += 1;
      observedAttempts.push(envelope.businessAttemptId);
      if (calls === 1) throw new Error("temporary destination RPC failure");
      return { transactionHash: `0x${"1".repeat(64)}` };
    },
  }, { delayMs: 0, retryBaseMs: 0, maxRelayAttempts: 3 });
  relay.enqueue({
    messageId: "message-1",
    routeId: "A_TO_D",
    routeVersion: 1,
    businessAttemptId: "attempt-D-2",
    messageType: "DISPATCH",
    payload: {},
  });
  const deliveries = await relay.drain();
  assert.equal(calls, 2);
  assert.deepEqual(observedAttempts, ["attempt-D-2", "attempt-D-2"]);
  assert.equal(deliveries[0].relayAttempts, 2);
  assert.equal(deliveries[0].businessAttemptId, "attempt-D-2");
  assert.throws(() => relay.enqueue({
    messageId: "message-1",
    routeId: "A_TO_D",
    routeVersion: 1,
    businessAttemptId: "attempt-D-2",
    messageType: "DISPATCH",
    payload: {},
  }), /replay/);
});
