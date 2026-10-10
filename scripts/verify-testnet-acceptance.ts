import "dotenv/config";

import { createHmac, createPublicKey, timingSafeEqual, verify as verifySignature } from "node:crypto";
import { readFileSync } from "node:fs";
import { resolve } from "node:path";
import {
  createPublicClient,
  decodeEventLog,
  decodeFunctionData,
  getAddress,
  http,
  isAddress,
  isHash,
  keccak256,
  parseAbi,
  parseTransaction,
  recoverTransactionAddress,
  stringToHex,
  type Address,
  type Hash,
  type Hex,
} from "viem";

import { requiredEnv } from "./real-ccip-env.ts";

type AcceptanceRecord = {
  schemaVersion: 4;
  workflowApiId: string;
  nodeApiId: string;
  attemptApiId: string;
  agentEvidencePath: string;
  deploymentManifestPath: string;
  ccipMessageId: Hash;
  sourceSettlementTxHash: Hash;
  destinationSettlementTxHash: Hash;
  sourceConfirmationTxHash: Hash;
};

type AgentTransactionEvidence = {
  schemaVersion: 1;
  attemptId: string;
  canonical: {
    payload: Record<string, unknown> & {
      runtimeId: string;
      did: string;
      workflowId: string;
      attemptId: Hash;
      resultMessageId: Hash;
      resultHash: Hash;
      proofHash: Hash;
      routeKey: string;
      routeVersion: number;
    };
    attestation: { did: string; algorithm: string; publicKeyHex: Hex; signature: string };
  };
  transactions: Array<{
    requestId: string;
    purpose: "submit_result" | "replace" | "cancel";
    intent: Record<string, unknown>;
    workerIntentSignature: string;
    assignedNonce: number;
    signer: Address;
    rawTransaction: `0x02${string}`;
    transactionHash: Hash;
  }>;
  finalTransactionHash: Hash;
};

type DeploymentManifest = {
  routeId: string;
  routeKey: string;
  routeVersion: number;
  homeChainId: number;
  contracts: Record<string, Address>;
  transactionHashes: Record<string, Hash>;
  codeHashes: Record<string, Hash>;
  manifestHash: Hash;
};

const ROUTE_KEY = "SEPOLIA_TO_ARBITRUM_SEPOLIA_CCIP_BNM_V1";
const SOURCE_CHAIN_ID = 11155111;
const DESTINATION_CHAIN_ID = 421614;
const recordPath = resolve(process.env.ACCEPTANCE_RECORD || "docs/testnet-acceptance.json");
const record = JSON.parse(readFileSync(recordPath, "utf8")) as AcceptanceRecord;

function stable(value: unknown): string {
  if (Array.isArray(value)) return `[${value.map(stable).join(",")}]`;
  if (value && typeof value === "object") {
    return `{${Object.entries(value as Record<string, unknown>)
      .sort(([left], [right]) => left.localeCompare(right))
      .map(([key, item]) => `${JSON.stringify(key)}:${stable(item)}`)
      .join(",")}}`;
  }
  return JSON.stringify(value);
}

function loadJson<T>(path: string): T {
  return JSON.parse(readFileSync(resolve(path), "utf8")) as T;
}

function expectHash(value: unknown, name: string): asserts value is Hash {
  if (typeof value !== "string" || !isHash(value)) throw new Error(`${name} must be a bytes32 hash`);
}

function equalAddress(actual: unknown, expected: unknown, name: string): void {
  if (typeof actual !== "string" || typeof expected !== "string" || !isAddress(actual) || !isAddress(expected) || getAddress(actual) !== getAddress(expected)) {
    throw new Error(`${name} address does not match`);
  }
}

function verifyCanonicalSignature(evidence: AgentTransactionEvidence): void {
  const { payload, attestation } = evidence.canonical;
  if (attestation.algorithm !== "ES256K" || attestation.did !== payload.did || !/^0x04[0-9a-fA-F]{128}$/.test(attestation.publicKeyHex)) {
    throw new Error("Canonical result attestation identity is invalid");
  }
  const publicKey = attestation.publicKeyHex.slice(4);
  const key = createPublicKey({
    key: {
      kty: "EC",
      crv: "secp256k1",
      x: Buffer.from(publicKey.slice(0, 64), "hex").toString("base64url"),
      y: Buffer.from(publicKey.slice(64), "hex").toString("base64url"),
    },
    format: "jwk",
  });
  if (!verifySignature("sha256", Buffer.from(stable(payload)), key, Buffer.from(attestation.signature, "base64url"))) {
    throw new Error("Canonical result ES256K signature is invalid");
  }
}

function verifyWorkerIntent(intent: Record<string, unknown>, signature: string): void {
  if ("nonce" in intent) throw new Error("Worker intent must not contain the final transaction nonce");
  const expected = `sha256=${createHmac("sha256", requiredEnv("WORKER_INTENT_HMAC_SECRET")).update(stable(intent)).digest("hex")}`;
  const left = Buffer.from(signature);
  const right = Buffer.from(expected);
  if (left.length !== right.length || !timingSafeEqual(left, right)) throw new Error("Worker intent HMAC is invalid");
}

if (record.schemaVersion !== 4 || !record.workflowApiId || !record.nodeApiId || !record.attemptApiId) {
  throw new Error("Acceptance record must use schemaVersion 4 and identify the workflow, node, and attempt");
}
for (const [name, value] of Object.entries({
  ccipMessageId: record.ccipMessageId,
  sourceSettlementTxHash: record.sourceSettlementTxHash,
  destinationSettlementTxHash: record.destinationSettlementTxHash,
  sourceConfirmationTxHash: record.sourceConfirmationTxHash,
})) expectHash(value, name);

const evidence = loadJson<AgentTransactionEvidence>(record.agentEvidencePath);
const manifest = loadJson<DeploymentManifest>(record.deploymentManifestPath);
if (evidence.schemaVersion !== 1 || evidence.attemptId !== record.attemptApiId) throw new Error("Agent evidence does not belong to the accepted attempt");
if (manifest.routeKey !== ROUTE_KEY || manifest.routeVersion !== 1 || manifest.homeChainId !== DESTINATION_CHAIN_ID) {
  throw new Error("Deployment manifest is not the pinned CCIP-BnM route");
}
const calculatedManifestHash = keccak256(stringToHex(stable({
  routeId: manifest.routeId,
  routeKey: manifest.routeKey,
  routeVersion: manifest.routeVersion,
  homeChainId: manifest.homeChainId,
  contracts: manifest.contracts,
  transactionHashes: manifest.transactionHashes,
  codeHashes: manifest.codeHashes,
})));
if (calculatedManifestHash.toLowerCase() !== manifest.manifestHash.toLowerCase()) throw new Error("Deployment manifest hash is invalid");

verifyCanonicalSignature(evidence);
const canonical = evidence.canonical.payload;
if (canonical.routeKey !== ROUTE_KEY || canonical.routeVersion !== 1 || canonical.workflowId !== record.workflowApiId) {
  throw new Error("Canonical result route or workflow binding is invalid");
}

const transactionEvidence = evidence.transactions.find((item) => item.transactionHash.toLowerCase() === evidence.finalTransactionHash.toLowerCase());
if (!transactionEvidence || transactionEvidence.purpose === "cancel") throw new Error("Final Agent transaction evidence is missing or cancelled");
verifyWorkerIntent(transactionEvidence.intent, transactionEvidence.workerIntentSignature);
const recoveredAgent = await recoverTransactionAddress({ serializedTransaction: transactionEvidence.rawTransaction });
const parsedAgentTransaction = parseTransaction(transactionEvidence.rawTransaction);
const intent = transactionEvidence.intent;
equalAddress(recoveredAgent, transactionEvidence.signer, "Recovered Agent");
equalAddress(parsedAgentTransaction.to, intent.to, "Agent transaction target");
if (
  Number(parsedAgentTransaction.chainId) !== DESTINATION_CHAIN_ID
  || parsedAgentTransaction.nonce !== transactionEvidence.assignedNonce
  || (parsedAgentTransaction.data || "0x").toLowerCase() !== String(intent.data).toLowerCase()
  || parsedAgentTransaction.gas !== BigInt(Number(intent.gasLimit))
  || parsedAgentTransaction.maxFeePerGas !== BigInt(String(intent.maxFeePerGas))
  || parsedAgentTransaction.maxPriorityFeePerGas !== BigInt(String(intent.maxPriorityFeePerGas))
  || keccak256(transactionEvidence.rawTransaction).toLowerCase() !== transactionEvidence.transactionHash.toLowerCase()
) throw new Error("Raw Agent transaction does not match its immutable Worker intent");

const submitResultAbi = parseAbi(["function submitResult(bytes32 resultMessageId, bytes32 attemptId, bytes32 resultHash, bytes32 proofHash)"]);
const decodedResult = decodeFunctionData({ abi: submitResultAbi, data: String(intent.data) as Hex });
if (decodedResult.functionName !== "submitResult") throw new Error("Agent transaction is not submitResult");
const [resultMessageId, attemptId, resultHash, proofHash] = decodedResult.args;
if (
  String(resultMessageId).toLowerCase() !== canonical.resultMessageId.toLowerCase()
  || String(attemptId).toLowerCase() !== canonical.attemptId.toLowerCase()
  || String(resultHash).toLowerCase() !== canonical.resultHash.toLowerCase()
  || String(proofHash).toLowerCase() !== canonical.proofHash.toLowerCase()
) throw new Error("submitResult calldata does not match the canonical result");
equalAddress(intent.to, manifest.contracts.remoteWorkBoard, "RemoteWorkBoard");

const sourceClient = createPublicClient({ transport: http(requiredEnv("SOURCE_RPC_URL")) });
const destinationClient = createPublicClient({ transport: http(requiredEnv("DESTINATION_RPC_URL")) });
const [sourceChainId, destinationChainId] = await Promise.all([sourceClient.getChainId(), destinationClient.getChainId()]);
if (sourceChainId !== SOURCE_CHAIN_ID || destinationChainId !== DESTINATION_CHAIN_ID) {
  throw new Error(`RPC chain IDs must be ${SOURCE_CHAIN_ID} and ${DESTINATION_CHAIN_ID}`);
}

const [agentReceipt, sourceReceipt, destinationReceipt, confirmationReceipt] = await Promise.all([
  destinationClient.getTransactionReceipt({ hash: transactionEvidence.transactionHash }),
  sourceClient.getTransactionReceipt({ hash: record.sourceSettlementTxHash }),
  destinationClient.getTransactionReceipt({ hash: record.destinationSettlementTxHash }),
  sourceClient.getTransactionReceipt({ hash: record.sourceConfirmationTxHash }),
]);
for (const [name, receipt] of Object.entries({ agentReceipt, sourceReceipt, destinationReceipt, confirmationReceipt })) {
  if (receipt.status !== "success") throw new Error(`${name} reverted`);
}

const senderAbi = parseAbi([
  "event CCIPWorkflowSettlementSent(bytes32 indexed messageId, bytes32 indexed workflowId, bytes32 indexed attemptId, uint64 destinationChainId, address payee, uint256 amount, uint256 fee)",
  "event CCIPWorkflowSettlementConfirmed(bytes32 indexed messageId, bytes32 indexed destinationTxHash)",
  "function isConfirmed(bytes32 messageId) view returns (bool)",
]);
const receiverAbi = parseAbi([
  "event CCIPWorkflowSettlementReceived(bytes32 indexed messageId, bytes32 indexed workflowId, bytes32 indexed attemptId, uint64 sourceChainSelector, address payee, address token, uint256 amount)",
]);
const sentLog = sourceReceipt.logs
  .filter((log) => log.address.toLowerCase() === manifest.contracts.dynamicCCIPWorkflowSenderA.toLowerCase())
  .map((log) => { try { return decodeEventLog({ abi: senderAbi, data: log.data, topics: log.topics }); } catch { return undefined; } })
  .find((item) => item?.eventName === "CCIPWorkflowSettlementSent") as any;
const receivedLog = destinationReceipt.logs
  .filter((log) => log.address.toLowerCase() === manifest.contracts.dynamicCCIPWorkflowReceiver.toLowerCase())
  .map((log) => { try { return decodeEventLog({ abi: receiverAbi, data: log.data, topics: log.topics }); } catch { return undefined; } })
  .find((item) => item?.eventName === "CCIPWorkflowSettlementReceived") as any;
const confirmedLog = confirmationReceipt.logs
  .filter((log) => log.address.toLowerCase() === manifest.contracts.dynamicCCIPWorkflowSenderA.toLowerCase())
  .map((log) => { try { return decodeEventLog({ abi: senderAbi, data: log.data, topics: log.topics }); } catch { return undefined; } })
  .find((item) => item?.eventName === "CCIPWorkflowSettlementConfirmed") as any;
if (!sentLog || !receivedLog || !confirmedLog) throw new Error("CCIP send, receive, or source confirmation event is missing");

const sent = sentLog.args as Record<string, any>;
const received = receivedLog.args as Record<string, any>;
const confirmed = confirmedLog.args as Record<string, any>;
for (const values of [sent, received, confirmed]) {
  if (String(values.messageId).toLowerCase() !== record.ccipMessageId.toLowerCase()) throw new Error("CCIP event messageId mismatch");
}
if (
  String(sent.workflowId).toLowerCase() !== String(received.workflowId).toLowerCase()
  || String(sent.attemptId).toLowerCase() !== canonical.attemptId.toLowerCase()
  || String(received.attemptId).toLowerCase() !== canonical.attemptId.toLowerCase()
  || BigInt(sent.amount) !== BigInt(received.amount)
  || BigInt(sent.destinationChainId) !== BigInt(DESTINATION_CHAIN_ID)
  || String(confirmed.destinationTxHash).toLowerCase() !== record.destinationSettlementTxHash.toLowerCase()
) throw new Error("CCIP settlement event fields are inconsistent");
equalAddress(sent.payee, recoveredAgent, "CCIP payee");
equalAddress(received.payee, recoveredAgent, "CCIP recipient");
equalAddress(received.token, manifest.contracts.destinationToken, "Destination CCIP-BnM");

const erc20Abi = parseAbi(["function balanceOf(address account) view returns (uint256)"]);
const beforeBlock = destinationReceipt.blockNumber > 0n ? destinationReceipt.blockNumber - 1n : 0n;
const [balanceBefore, balanceAfter, sourceConfirmed] = await Promise.all([
  destinationClient.readContract({ address: manifest.contracts.destinationToken, abi: erc20Abi, functionName: "balanceOf", args: [recoveredAgent], blockNumber: beforeBlock } as any) as Promise<bigint>,
  destinationClient.readContract({ address: manifest.contracts.destinationToken, abi: erc20Abi, functionName: "balanceOf", args: [recoveredAgent], blockNumber: destinationReceipt.blockNumber } as any) as Promise<bigint>,
  sourceClient.readContract({ address: manifest.contracts.dynamicCCIPWorkflowSenderA, abi: senderAbi, functionName: "isConfirmed", args: [record.ccipMessageId] } as any) as Promise<boolean>,
]);
if (balanceAfter - balanceBefore !== BigInt(received.amount)) throw new Error("Destination CCIP-BnM balance delta does not match the received amount");
if (!sourceConfirmed) throw new Error("Source sender has not confirmed the CCIP settlement");

const sourceConfirmations = BigInt(Number(process.env.SOURCE_CONFIRMATIONS || 3));
const destinationConfirmations = BigInt(Number(process.env.DESTINATION_CONFIRMATIONS || 5));
const [sourceHead, destinationHead] = await Promise.all([sourceClient.getBlockNumber(), destinationClient.getBlockNumber()]);
if (sourceHead < confirmationReceipt.blockNumber + sourceConfirmations - 1n) throw new Error("Source confirmation depth has not been reached");
if (destinationHead < destinationReceipt.blockNumber + destinationConfirmations - 1n) throw new Error("Destination confirmation depth has not been reached");

for (const [name, address, client, expectedHash] of [
  ["RemoteWorkBoard", manifest.contracts.remoteWorkBoard, destinationClient, manifest.codeHashes.remoteWorkBoard],
  ["CCIP receiver", manifest.contracts.dynamicCCIPWorkflowReceiver, destinationClient, manifest.codeHashes.dynamicCCIPWorkflowReceiver],
  ["CCIP sender", manifest.contracts.dynamicCCIPWorkflowSenderA, sourceClient, manifest.codeHashes.dynamicCCIPWorkflowSenderA],
] as const) {
  const code = await client.getCode({ address });
  if (!code || code === "0x" || keccak256(code).toLowerCase() !== expectedHash.toLowerCase()) throw new Error(`${name} bytecode hash does not match the deployment manifest`);
}

const apiUrl = requiredEnv("API_URL").replace(/\/$/, "");
const [workflowResponse, deploymentsResponse, agentsResponse] = await Promise.all([
  fetch(`${apiUrl}/api/v1/workflows/${record.workflowApiId}`),
  fetch(`${apiUrl}/api/v1/route-deployments`),
  fetch(`${apiUrl}/api/v1/workflow-agents`),
]);
if (!workflowResponse.ok || !deploymentsResponse.ok || !agentsResponse.ok) throw new Error("Platform API acceptance lookup failed");
const workflow = await workflowResponse.json() as Record<string, any>;
const node = (workflow.nodes as Record<string, any>[]).find((item) => item.id === record.nodeApiId);
const attempt = (node?.attempts as Record<string, any>[] | undefined)?.find((item) => item.id === record.attemptApiId);
if (!attempt || attempt.status !== "SETTLED" || String(attempt.settlementMessageId).toLowerCase() !== record.ccipMessageId.toLowerCase()) {
  throw new Error("Platform attempt is not settled with the accepted CCIP message");
}
if (attempt.destinationTxHash && String(attempt.destinationTxHash).toLowerCase() !== record.destinationSettlementTxHash.toLowerCase()) throw new Error("Platform destination transaction evidence differs");
if (attempt.sourceConfirmTxHash && String(attempt.sourceConfirmTxHash).toLowerCase() !== record.sourceConfirmationTxHash.toLowerCase()) throw new Error("Platform source confirmation evidence differs");
const deployments = await deploymentsResponse.json() as { items: Array<Record<string, any>> };
const deployment = deployments.items.find((item) => item.routeId === manifest.routeId && item.routeVersion === manifest.routeVersion);
if (!deployment || String(deployment.manifestHash).toLowerCase() !== manifest.manifestHash.toLowerCase()) throw new Error("API route deployment manifest hash differs");
const profiles = await agentsResponse.json() as { items: Array<Record<string, any>> };
const profile = profiles.items.find((item) => item.agentKey === attempt.agentKey);
const registeredKey = String(profile?.agent?.operationalPublicKey || "").replace(/^es256k:/i, "0x");
if (registeredKey && registeredKey.toLowerCase() !== evidence.canonical.attestation.publicKeyHex.toLowerCase()) throw new Error("Canonical signer does not match the registered Agent identity key");

console.log(`Agent transaction          ${transactionEvidence.transactionHash}`);
console.log(`Agent sender               ${recoveredAgent}`);
console.log(`CCIP message ID            ${record.ccipMessageId}`);
console.log(`Destination BnM delta      ${balanceAfter - balanceBefore}`);
console.log(`Destination receipt        ${record.destinationSettlementTxHash} block=${destinationReceipt.blockNumber}`);
console.log(`Source confirmation        ${record.sourceConfirmationTxHash} block=${confirmationReceipt.blockNumber}`);
console.log(`Deployment manifest hash   ${manifest.manifestHash}`);
console.log("V4 managed testnet acceptance verified");
