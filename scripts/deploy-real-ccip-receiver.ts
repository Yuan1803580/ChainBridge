import { network } from "hardhat";

import {
  requiredAddress,
  requiredBigInt,
} from "./real-ccip-env.js";

const { viem } = await network.create();
const [deployer] = await viem.getWalletClients();

const destinationRouter = requiredAddress("DESTINATION_CCIP_ROUTER");
const sourceChainSelector = requiredBigInt("SOURCE_CHAIN_SELECTOR");
const destinationToken = requiredAddress("DESTINATION_SETTLEMENT_TOKEN");

console.log("Deploying RealCCIPSettlementReceiver");
console.log(`deployer                  ${deployer.account.address}`);
console.log(`destination router        ${destinationRouter}`);
console.log(`allowed source selector   ${sourceChainSelector}`);
console.log(`destination token         ${destinationToken}`);

const receiver = await viem.deployContract("RealCCIPSettlementReceiver", [
  destinationRouter,
  sourceChainSelector,
  destinationToken,
]);

console.log("");
console.log(`REAL_CCIP_RECEIVER_ADDRESS=${receiver.address}`);
console.log("");
console.log("Next step:");
console.log(
  "Set REAL_CCIP_RECEIVER_ADDRESS in .env, then deploy RealCCIPSettlementSender on sourceTestnet.",
);

