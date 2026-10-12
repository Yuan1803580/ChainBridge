import assert from "node:assert/strict";
import { mkdirSync, readFileSync, writeFileSync } from "node:fs";
import { resolve } from "node:path";

import {
  createPublicClient,
  createWalletClient,
  defineChain,
  formatUnits,
  http,
  keccak256,
  stringToHex,
  type Abi,
  type Address,
  type Hash,
  type Hex,
} from "viem";

import { AsyncMockCcipRelay, type RelayEnvelope } from "../services/chain-worker/src/mock-relay.ts";
import { RouteContextRegistry, type RouteContext } from "../services/chain-worker/src/routes.ts";

type Artifact = { abi: Abi; bytecode: Hex };
type ChainRuntime = {
  name: "A" | "B" | "C" | "D";
  chainId: number;
  rpcUrl: string;
  publicClient: ReturnType<typeof createPublicClient>;
  walletClient: ReturnType<typeof createWalletClient>;
  accounts: Address[];
};
type GasRecord = {
  chain: string;
  chainId: number;
  responsibility: string;
  action: string;
  transactionHash: Hash;
  gasUsed: string;
  gasCostWei: string;
};

const projectRoot = resolve(import.meta.dirname, "..");
const artifact = (contractName: string): Artifact => JSON.parse(readFileSync(
  resolve(projectRoot, `hardhat-artifacts/contracts/src/${contractName}.sol/${contractName}.json`),
  "utf8",
)) as Artifact;
const id = (value: string): Hash => keccak256(stringToHex(value));
const amount = (value: string): bigint => BigInt(value.replace(".", "").padEnd(value.includes(".") ? value.indexOf(".") + 7 : value.length + 6, "0"));

const artifacts = {
  token: artifact("MockUSDC"),
  engine: artifact("WorkflowEngine"),
  escrow: artifact("TaskEscrow"),
  registry: artifact("ChainRouteRegistry"),
  board: artifact("RemoteWorkBoard"),
};

const chainDefinitions = [
  { name: "A" as const, chainId: 31337, rpcUrl: "http://127.0.0.1:8545" },
  { name: "B" as const, chainId: 31338, rpcUrl: "http://127.0.0.1:8546" },
  { name: "C" as const, chainId: 31339, rpcUrl: "http://127.0.0.1:8547" },
  { name: "D" as const, chainId: 31340, rpcUrl: "http://127.0.0.1:8548" },
];

const chains = new Map<number, ChainRuntime>();
for (const definition of chainDefinitions) {
  const chain = defineChain({
    id: definition.chainId,
    name: `Local Chain ${definition.name}`,
    nativeCurrency: { name: "Local ETH", symbol: "ETH", decimals: 18 },
    rpcUrls: { default: { http: [definition.rpcUrl] } },
  });
  const publicClient = createPublicClient({ chain, transport: http(definition.rpcUrl) });
  const walletClient = createWalletClient({ chain, transport: http(definition.rpcUrl) });
  const observedChainId = await publicClient.getChainId();
  assert.equal(observedChainId, definition.chainId, `${definition.name} RPC chainId mismatch`);
  const accounts = await walletClient.getAddresses();
  assert.ok(accounts.length >= 6, `${definition.name} requires six unlocked local accounts`);
  chains.set(definition.chainId, { ...definition, publicClient, walletClient, accounts });
}

const chainA = chains.get(31337)!;
const coordinator = chainA.accounts[0];
const requester = chainA.accounts[1];
const agentB = chainA.accounts[2];
const agentC = chainA.accounts[3];
const agentD = chainA.accounts[4];
const treasury = chainA.accounts[5];
const gasRecords: GasRecord[] = [];

async function receipt(
  chain: ChainRuntime,
  transactionHash: Hash,
  responsibility: string,
  action: string,
) {
  const value = await chain.publicClient.waitForTransactionReceipt({ hash: transactionHash });
  assert.equal(value.status, "success", `${chain.name}:${action} reverted`);
  const gasPrice = value.effectiveGasPrice ?? 0n;
  gasRecords.push({
    chain: chain.name,
    chainId: chain.chainId,
    responsibility,
    action,
    transactionHash,
    gasUsed: value.gasUsed.toString(),
    gasCostWei: (value.gasUsed * gasPrice).toString(),
  });
  return value;
}

async function deploy(
  chain: ChainRuntime,
  contract: Artifact,
  args: readonly unknown[],
  label: string,
): Promise<Address> {
  const hash = await chain.walletClient.deployContract({
    account: chain.accounts[0],
    abi: contract.abi,
    bytecode: contract.bytecode,
    args,
  });
  const value = await receipt(chain, hash, "platform_deployer", `deploy:${label}`);
  assert.ok(value.contractAddress, `${label} deployment did not return an address`);
  return value.contractAddress;
}

async function write(
  chain: ChainRuntime,
  request: {
    address: Address;
    abi: Abi;
    functionName: string;
    args?: readonly unknown[];
    account: Address;
  },
  responsibility: string,
  action: string,
): Promise<Hash> {
  const hash = await chain.walletClient.writeContract(request as never);
  await receipt(chain, hash, responsibility, action);
  return hash;
}

const token = await deploy(chainA, artifacts.token, [], "MockUSDC");
const engine = await deploy(chainA, artifacts.engine, [coordinator], "WorkflowEngine");
const escrow = await deploy(chainA, artifacts.escrow, [coordinator, treasury], "TaskEscrow");
const registry = await deploy(chainA, artifacts.registry, [coordinator], "ChainRouteRegistry");

const boardAddresses = new Map<number, Address>();
for (const destinationChainId of [31338, 31339, 31340]) {
  const chain = chains.get(destinationChainId)!;
  const board = await deploy(chain, artifacts.board, [BigInt(destinationChainId), 31337n, chain.accounts[0]], `RemoteWorkBoard-${chain.name}`);
  boardAddresses.set(destinationChainId, board);
}

const routeSpecs = [
  { routeId: "A_TO_B", routeKey: id("A_TO_B"), destinationChainId: 31338, agent: agentB },
  { routeId: "A_TO_C", routeKey: id("A_TO_C"), destinationChainId: 31339, agent: agentC },
  { routeId: "A_TO_D", routeKey: id("A_TO_D"), destinationChainId: 31340, agent: agentD },
];
const routeContexts: RouteContext[] = [];
for (const route of routeSpecs) {
  const destination = chains.get(route.destinationChainId)!;
  const board = boardAddresses.get(route.destinationChainId)!;
  await write(chainA, {
    address: registry,
    abi: artifacts.registry.abi,
    functionName: "registerRoute",
    args: [route.routeKey, 31337n, BigInt(route.destinationChainId), engine, board, route.agent, token],
    account: coordinator,
  }, "platform_coordinator", `route:${route.routeId}:register`);
  await write(destination, {
    address: board,
    abi: artifacts.board.abi,
    functionName: "configureRoute",
    args: [route.routeKey, 1, engine, true],
    account: destination.accounts[0],
  }, "mock_ccip_relay", `route:${route.routeId}:configure`);
  routeContexts.push({
    routeId: route.routeId,
    routeVersion: 1,
    sourceChainId: 31337,
    destinationChainId: route.destinationChainId,
    sourceRpcUrl: chainA.rpcUrl,
    destinationRpcUrl: destination.rpcUrl,
    sourceGatewayAddress: engine,
    remoteWorkBoardAddress: board,
    active: true,
  });
}

const routeRegistry = new RouteContextRegistry(routeContexts);
const relay = new AsyncMockCcipRelay(routeRegistry, {
  async deliver(route: RouteContext, envelope: RelayEnvelope) {
    assert.equal(envelope.messageType, "DISPATCH");
    const chain = chains.get(route.destinationChainId)!;
    const payload = envelope.payload as {
      workflowId: Hash;
      nodeId: Hash;
      attemptId: Hash;
      routeKey: Hash;
      criteriaHash: Hash;
      agent: Address;
      reward: bigint;
    };
    const transactionHash = await write(chain, {
      address: route.remoteWorkBoardAddress,
      abi: artifacts.board.abi,
      functionName: "receiveAssignment",
      args: [
        envelope.messageId as Hash,
        31337n,
        route.sourceGatewayAddress,
        payload.workflowId,
        payload.nodeId,
        payload.attemptId,
        payload.routeKey,
        route.routeVersion,
        payload.criteriaHash,
        payload.agent,
        payload.reward,
      ],
      account: chain.accounts[0],
    }, "mock_ccip_relay", `deliver:${envelope.businessAttemptId}`);
    return { transactionHash };
  },
}, { delayMs: 20, retryBaseMs: 10, maxRelayAttempts: 3 });

const workflowId = id("WF-1001");
const nodeN1 = id("WF-1001:N1");
const nodeN2 = id("WF-1001:N2");
const attemptB = id("WF-1001:N1:attempt:1");
const attemptC = id("WF-1001:N2:attempt:1");
const attemptD = id("WF-1001:N2:attempt:2");
const criteriaN1 = id("required:summary;minScore:80");
const criteriaN2 = id("required:verified,report;verified:true");
const termsHash = id("WF-1001:budget=20.00;fee=1.10;N1=4.20;N2=6.80");
const budget = 20_000_000n;
const rewardB = 4_200_000n;
const rewardN2 = 6_800_000n;
const platformFee = 1_100_000n;
const expectedRefund = 7_900_000n;

await write(chainA, {
  address: token,
  abi: artifacts.token.abi,
  functionName: "mint",
  args: [requester, budget],
  account: coordinator,
}, "platform_faucet", "mint:requester");
await write(chainA, {
  address: token,
  abi: artifacts.token.abi,
  functionName: "approve",
  args: [escrow, budget],
  account: requester,
}, "requester", "approve:escrow");
await write(chainA, {
  address: escrow,
  abi: artifacts.escrow.abi,
  functionName: "lockBudget",
  args: [workflowId, termsHash, token, budget, platformFee],
  account: requester,
}, "requester", "budget:lock");
await write(chainA, {
  address: engine,
  abi: artifacts.engine.abi,
  functionName: "createWorkflow",
  args: [workflowId, requester, termsHash],
  account: coordinator,
}, "platform_coordinator", "workflow:create");
await write(chainA, {
  address: engine,
  abi: artifacts.engine.abi,
  functionName: "registerNode",
  args: [workflowId, nodeN1, criteriaN1, []],
  account: coordinator,
}, "platform_coordinator", "node:N1:register");
await write(chainA, {
  address: engine,
  abi: artifacts.engine.abi,
  functionName: "registerNode",
  args: [workflowId, nodeN2, criteriaN2, [nodeN1]],
  account: coordinator,
}, "platform_coordinator", "node:N2:register");

async function assign(
  nodeId: Hash,
  attemptId: Hash,
  routeId: string,
  routeKey: Hash,
  agent: Address,
  reward: bigint,
  criteriaHash: Hash,
) {
  await write(chainA, {
    address: engine,
    abi: artifacts.engine.abi,
    functionName: "assignNode",
    args: [nodeId, attemptId, routeKey, 1, agent],
    account: coordinator,
  }, "platform_coordinator", `${routeId}:assign`);
  await write(chainA, {
    address: escrow,
    abi: artifacts.escrow.abi,
    functionName: "reserve",
    args: [workflowId, nodeId, attemptId, reward],
    account: coordinator,
  }, "platform_coordinator", `${routeId}:reserve`);
  const messageId = id(`dispatch:${attemptId}`);
  relay.enqueue({
    messageId,
    routeId,
    routeVersion: 1,
    businessAttemptId: attemptId,
    messageType: "DISPATCH",
    payload: { workflowId, nodeId, attemptId, routeKey, criteriaHash, agent, reward },
  });
  const deliveries = await relay.drain();
  assert.equal(deliveries.length, 1);
  assert.equal(deliveries[0].businessAttemptId, attemptId);
  return messageId;
}

async function remoteResult(
  destinationChainId: number,
  nodeId: Hash,
  attemptId: Hash,
  agent: Address,
  resultHash: Hash,
  proofHash: Hash,
) {
  const chain = chains.get(destinationChainId)!;
  const board = boardAddresses.get(destinationChainId)!;
  await write(chain, {
    address: board,
    abi: artifacts.board.abi,
    functionName: "startWork",
    args: [attemptId],
    account: agent,
  }, `agent:${destinationChainId}`, `${attemptId}:start`);
  const resultMessageId = id(`result:${attemptId}`);
  await write(chain, {
    address: board,
    abi: artifacts.board.abi,
    functionName: "submitResult",
    args: [resultMessageId, attemptId, resultHash, proofHash],
    account: agent,
  }, `agent:${destinationChainId}`, `${attemptId}:result`);
  assert.equal(await chain.publicClient.readContract({
    address: board,
    abi: artifacts.board.abi,
    functionName: "processedMessages",
    args: [resultMessageId],
  }), true);
  return resultMessageId;
}

const dispatchB = await assign(nodeN1, attemptB, "A_TO_B", id("A_TO_B"), agentB, rewardB, criteriaN1);
const resultMessageB = await remoteResult(31338, nodeN1, attemptB, agentB, id("B:structured-evidence:score=92"), id("B:proof"));
await write(chainA, {
  address: engine,
  abi: artifacts.engine.abi,
  functionName: "markSucceeded",
  args: [nodeN1, attemptB],
  account: coordinator,
}, "platform_coordinator", "N1:review-pass");
const n2BeforeSettlement = await chainA.publicClient.readContract({
  address: engine,
  abi: artifacts.engine.abi,
  functionName: "nodes",
  args: [nodeN2],
}) as readonly unknown[];
assert.equal(n2BeforeSettlement[7], 1, "N2 unlocked before N1 settlement");
await write(chainA, {
  address: escrow,
  abi: artifacts.escrow.abi,
  functionName: "payReservation",
  args: [attemptB, agentB],
  account: coordinator,
}, "platform_coordinator", "N1:payout-B");
await write(chainA, {
  address: engine,
  abi: artifacts.engine.abi,
  functionName: "markSettled",
  args: [nodeN1, attemptB],
  account: coordinator,
}, "platform_coordinator", "N1:settle");

const dispatchC = await assign(nodeN2, attemptC, "A_TO_C", id("A_TO_C"), agentC, rewardN2, criteriaN2);
const resultMessageC = await remoteResult(31339, nodeN2, attemptC, agentC, id("C:verified=false;report=valid-proof"), id("C:valid-proof"));
const cReview = { proofValid: true, verified: false, passedImmutableCriteria: false };
assert.equal(cReview.proofValid, true);
assert.equal(cReview.passedImmutableCriteria, false);
await write(chainA, {
  address: engine,
  abi: artifacts.engine.abi,
  functionName: "markAttemptFailed",
  args: [nodeN2, attemptC],
  account: coordinator,
}, "platform_coordinator", "N2:C-review-fail");
await write(chainA, {
  address: escrow,
  abi: artifacts.escrow.abi,
  functionName: "releaseReservation",
  args: [attemptC],
  account: coordinator,
}, "platform_coordinator", "N2:C-release-reservation");

const dispatchD = await assign(nodeN2, attemptD, "A_TO_D", id("A_TO_D"), agentD, rewardN2, criteriaN2);
let staleAttemptRejected = false;
try {
  await chainA.walletClient.writeContract({
    address: engine,
    abi: artifacts.engine.abi,
    functionName: "markSucceeded",
    args: [nodeN2, attemptC],
    account: coordinator,
  });
} catch {
  staleAttemptRejected = true;
}
assert.equal(staleAttemptRejected, true, "Late C attempt was not rejected");

const resultMessageD = await remoteResult(31340, nodeN2, attemptD, agentD, id("D:verified=true;report=audited"), id("D:proof"));
await write(chainA, {
  address: engine,
  abi: artifacts.engine.abi,
  functionName: "markSucceeded",
  args: [nodeN2, attemptD],
  account: coordinator,
}, "platform_coordinator", "N2:D-review-pass");
await write(chainA, {
  address: escrow,
  abi: artifacts.escrow.abi,
  functionName: "payReservation",
  args: [attemptD, agentD],
  account: coordinator,
}, "platform_coordinator", "N2:payout-D");
await write(chainA, {
  address: engine,
  abi: artifacts.engine.abi,
  functionName: "markSettled",
  args: [nodeN2, attemptD],
  account: coordinator,
}, "platform_coordinator", "N2:settle");
await write(chainA, {
  address: escrow,
  abi: artifacts.escrow.abi,
  functionName: "payPlatformFee",
  args: [workflowId, platformFee],
  account: coordinator,
}, "platform_coordinator", "platform-fee");
await write(chainA, {
  address: escrow,
  abi: artifacts.escrow.abi,
  functionName: "refundRemainder",
  args: [workflowId],
  account: coordinator,
}, "platform_coordinator", "requester-refund");

const balanceOf = (account: Address) => chainA.publicClient.readContract({
  address: token,
  abi: artifacts.token.abi,
  functionName: "balanceOf",
  args: [account],
}) as Promise<bigint>;
const balances = {
  requester: await balanceOf(requester),
  agentB: await balanceOf(agentB),
  agentC: await balanceOf(agentC),
  agentD: await balanceOf(agentD),
  treasury: await balanceOf(treasury),
  escrow: await balanceOf(escrow),
};
assert.deepEqual(balances, {
  requester: expectedRefund,
  agentB: rewardB,
  agentC: 0n,
  agentD: rewardN2,
  treasury: platformFee,
  escrow: 0n,
});

const workflow = await chainA.publicClient.readContract({
  address: engine,
  abi: artifacts.engine.abi,
  functionName: "workflows",
  args: [workflowId],
}) as readonly unknown[];
const escrowAccount = await chainA.publicClient.readContract({
  address: escrow,
  abi: artifacts.escrow.abi,
  functionName: "workflowAccounts",
  args: [workflowId],
}) as readonly unknown[];
const cReservation = await chainA.publicClient.readContract({
  address: escrow,
  abi: artifacts.escrow.abi,
  functionName: "reservations",
  args: [attemptC],
}) as readonly unknown[];
assert.equal(workflow[2], 2);
assert.equal(workflow[3], 2);
assert.equal(workflow[4], false);
assert.equal(escrowAccount[5], 11_000_000n);
assert.equal(escrowAccount[7], platformFee);
assert.equal(escrowAccount[8], expectedRefund);
assert.equal(cReservation[3], 2);
assert.ok(gasRecords.some((item) => item.responsibility === "mock_ccip_relay"));
assert.ok(gasRecords.some((item) => item.responsibility === "requester"));
assert.ok(gasRecords.some((item) => item.responsibility.startsWith("agent:")));

const eventCounts: Record<string, number> = {};
for (const chain of chains.values()) {
  const logs = await chain.publicClient.getLogs({ fromBlock: 0n, toBlock: "latest" });
  eventCounts[chain.name] = logs.length;
  assert.ok(logs.length > 0, `Chain ${chain.name} emitted no events`);
}

const report = {
  accepted: true,
  generatedAt: new Date().toISOString(),
  chains: chainDefinitions.map(({ name, chainId, rpcUrl }) => ({ name, chainId, rpcUrl })),
  contracts: {
    chainA: { token, workflowEngine: engine, taskEscrow: escrow, chainRouteRegistry: registry },
    chainB: { remoteWorkBoard: boardAddresses.get(31338) },
    chainC: { remoteWorkBoard: boardAddresses.get(31339) },
    chainD: { remoteWorkBoard: boardAddresses.get(31340) },
  },
  workflow: {
    referenceCode: "WF-1001",
    workflowId,
    termsHash,
    nodeStates: { N1: "SETTLED", N2: "SETTLED" },
    attempts: [
      { node: "N1", attemptId: attemptB, agent: "Agent-B1", chainId: 31338, outcome: "SETTLED" },
      { node: "N2", attemptId: attemptC, agent: "Agent-C1", chainId: 31339, outcome: "REVIEW_FAILED" },
      { node: "N2", attemptId: attemptD, agent: "Agent-D1", chainId: 31340, outcome: "SETTLED" },
    ],
    staleAttemptRejected,
  },
  routes: routeContexts.map((route) => ({
    routeId: route.routeId,
    routeVersion: route.routeVersion,
    sourceChainId: route.sourceChainId,
    destinationChainId: route.destinationChainId,
  })),
  messages: {
    dispatch: [dispatchB, dispatchC, dispatchD],
    result: [resultMessageB, resultMessageC, resultMessageD],
    relayDeliveries: relay.delivered(),
  },
  tokenLedger: {
    locked: formatUnits(budget, 6),
    agentBPayout: formatUnits(balances.agentB, 6),
    agentCPayout: formatUnits(balances.agentC, 6),
    agentDPayout: formatUnits(balances.agentD, 6),
    platformFee: formatUnits(balances.treasury, 6),
    requesterRefund: formatUnits(balances.requester, 6),
    escrowRemainder: formatUnits(balances.escrow, 6),
    balanced: balances.agentB + balances.agentC + balances.agentD + balances.treasury + balances.requester === budget,
  },
  gasLedger: gasRecords,
  eventCounts,
};
assert.equal(report.tokenLedger.balanced, true);
const runtimeDir = resolve(projectRoot, ".runtime", "local-v1");
mkdirSync(runtimeDir, { recursive: true });
const reportPath = resolve(runtimeDir, "acceptance-report.json");
writeFileSync(reportPath, `${JSON.stringify(report, null, 2)}\n`, "utf8");

console.log("LOCAL_V1_ACCEPTED");
console.log(`chains=${chainDefinitions.map((chain) => `${chain.name}:${chain.chainId}`).join(",")}`);
console.log("payouts=B:4.20,C:0,D:6.80 fee=1.10 refund=7.90 escrow=0");
console.log(`staleAttemptRejected=${staleAttemptRejected} relayDeliveries=${relay.delivered().length}`);
console.log(`report=${reportPath}`);
