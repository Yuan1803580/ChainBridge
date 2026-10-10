import { network } from "hardhat";

import {
  optionalBigInt,
  requiredAddress,
  requiredBigInt,
} from "./real-ccip-env.js";

const { viem } = await network.create();
const [deployer] = await viem.getWalletClients();

const sourceRouter = requiredAddress("SOURCE_CCIP_ROUTER");
const destinationChainSelector = requiredBigInt("DESTINATION_CHAIN_SELECTOR");
const receiver = requiredAddress("REAL_CCIP_RECEIVER_ADDRESS");
const sourceToken = requiredAddress("SOURCE_SETTLEMENT_TOKEN");
const authorizedEscrow = requiredAddress("AUTHORIZED_ESCROW");
const gasLimit = optionalBigInt("CCIP_GAS_LIMIT", 300_000n);

console.log("Deploying RealCCIPSettlementSender");
console.log(`deployer                     ${deployer.account.address}`);
console.log(`source router                ${sourceRouter}`);
console.log(`destination chain selector   ${destinationChainSelector}`);
console.log(`receiver                     ${receiver}`);
console.log(`source token                 ${sourceToken}`);
console.log(`authorized escrow            ${authorizedEscrow}`);
console.log(`receiver gas limit           ${gasLimit}`);

const sender = await viem.deployContract("RealCCIPSettlementSender", [
  sourceRouter,
  destinationChainSelector,
  receiver,
  sourceToken,
  authorizedEscrow,
  gasLimit,
]);

console.log("");
console.log(`REAL_CCIP_SENDER_ADDRESS=${sender.address}`);
console.log("");
console.log("Next step:");
console.log(
  "Set REAL_CCIP_SENDER_ADDRESS in .env, then configure the destination receiver.",
);
