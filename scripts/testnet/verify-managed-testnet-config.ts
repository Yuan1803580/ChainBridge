import "dotenv/config";

import { createPublicClient, getAddress, http, keccak256, parseAbi, type Address } from "viem";

const SOURCE_CHAIN_ID = 11155111;
const DESTINATION_CHAIN_ID = 421614;
const SOURCE_SELECTOR = "16015286601757825753";
const DESTINATION_SELECTOR = "3478487238524512106";

function required(name: string): string {
  const value = process.env[name]?.trim();
  if (!value) throw new Error(`${name} is required`);
  return value;
}

function rpcUrl(name: string): string {
  const value = required(name);
  const parsed = new URL(value);
  if (parsed.protocol !== "https:" || ["localhost", "127.0.0.1", "::1"].includes(parsed.hostname)) {
    throw new Error(`${name} must be a non-loopback HTTPS endpoint`);
  }
  return value;
}

function address(name: string): Address {
  return getAddress(required(name));
}

if (Number(required("SOURCE_CHAIN_ID")) !== SOURCE_CHAIN_ID) throw new Error("SOURCE_CHAIN_ID must be 11155111");
if (Number(required("DESTINATION_CHAIN_ID")) !== DESTINATION_CHAIN_ID) throw new Error("DESTINATION_CHAIN_ID must be 421614");
if (required("SOURCE_CHAIN_SELECTOR") !== SOURCE_SELECTOR) throw new Error("Ethereum Sepolia selector is incorrect");
if (required("DESTINATION_CHAIN_SELECTOR") !== DESTINATION_SELECTOR) throw new Error("Arbitrum Sepolia selector is incorrect");

const source = createPublicClient({ transport: http(rpcUrl("SOURCE_RPC_URL")) });
const destination = createPublicClient({ transport: http(rpcUrl("DESTINATION_RPC_URL")) });
const sourceRouter = address("SOURCE_CCIP_ROUTER");
const destinationRouter = address("DESTINATION_CCIP_ROUTER");
const sourceToken = address("SOURCE_SETTLEMENT_TOKEN");
const destinationToken = address("DESTINATION_SETTLEMENT_TOKEN");
const tokenAbi = parseAbi(["function symbol() view returns (string)", "function decimals() view returns (uint8)"]);

const [
  observedSourceChain,
  observedDestinationChain,
  sourceRouterCode,
  destinationRouterCode,
  sourceTokenCode,
  destinationTokenCode,
  sourceSymbol,
  destinationSymbol,
  sourceDecimals,
  destinationDecimals,
] = await Promise.all([
  source.getChainId(),
  destination.getChainId(),
  source.getCode({ address: sourceRouter }),
  destination.getCode({ address: destinationRouter }),
  source.getCode({ address: sourceToken }),
  destination.getCode({ address: destinationToken }),
  source.readContract({ address: sourceToken, abi: tokenAbi, functionName: "symbol" }),
  destination.readContract({ address: destinationToken, abi: tokenAbi, functionName: "symbol" }),
  source.readContract({ address: sourceToken, abi: tokenAbi, functionName: "decimals" }),
  destination.readContract({ address: destinationToken, abi: tokenAbi, functionName: "decimals" }),
]);

if (observedSourceChain !== SOURCE_CHAIN_ID || observedDestinationChain !== DESTINATION_CHAIN_ID) {
  throw new Error(`RPC chain mismatch: source=${observedSourceChain}, destination=${observedDestinationChain}`);
}
for (const [name, code] of Object.entries({ sourceRouterCode, destinationRouterCode, sourceTokenCode, destinationTokenCode })) {
  if (!code || code === "0x") throw new Error(`${name} has no deployed bytecode`);
}
if (!String(sourceSymbol).toLowerCase().includes("bnm") || !String(destinationSymbol).toLowerCase().includes("bnm")) {
  throw new Error(`Configured tokens are not CCIP-BnM: ${sourceSymbol}/${destinationSymbol}`);
}
if (Number(sourceDecimals) !== Number(destinationDecimals)) {
  throw new Error(`CCIP-BnM decimals differ across chains: ${sourceDecimals}/${destinationDecimals}`);
}

console.log(JSON.stringify({
  routeKey: "SEPOLIA_TO_ARBITRUM_SEPOLIA_CCIP_BNM_V1",
  routeVersion: 1,
  sourceChainId: observedSourceChain,
  destinationChainId: observedDestinationChain,
  sourceSelector: SOURCE_SELECTOR,
  destinationSelector: DESTINATION_SELECTOR,
  sourceRouter,
  destinationRouter,
  sourceToken,
  destinationToken,
  tokenSymbol: String(sourceSymbol),
  tokenDecimals: Number(sourceDecimals),
  codeHashes: {
    sourceRouter: keccak256(sourceRouterCode!),
    destinationRouter: keccak256(destinationRouterCode!),
    sourceToken: keccak256(sourceTokenCode!),
    destinationToken: keccak256(destinationTokenCode!),
  },
}, null, 2));

