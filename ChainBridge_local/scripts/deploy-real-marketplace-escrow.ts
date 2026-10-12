import { network } from "hardhat";

import {
  requiredAddress,
  requiredBigInt,
} from "./real-ccip-env.js";

const { viem } = await network.create();
const [deployer] = await viem.getWalletClients();

const platformSigner = requiredAddress("REAL_PLATFORM_SIGNER");
const mockCcipSender = requiredAddress("MOCK_CCIP_SENDER_FOR_ESCROW");
const destinationChainSelector = requiredBigInt("DESTINATION_CHAIN_SELECTOR");
const destinationReceiver = requiredAddress("REAL_CCIP_RECEIVER_ADDRESS");

console.log("Deploying MarketplaceEscrow for real CCIP path");
console.log(`deployer                     ${deployer.account.address}`);
console.log(`platform signer              ${platformSigner}`);
console.log(`mock sender placeholder       ${mockCcipSender}`);
console.log(`destination chain selector    ${destinationChainSelector}`);
console.log(`destination receiver          ${destinationReceiver}`);

const escrow = await viem.deployContract("MarketplaceEscrow", [
  platformSigner,
  mockCcipSender,
  destinationChainSelector,
  destinationReceiver,
]);

console.log("");
console.log(`REAL_MARKETPLACE_ESCROW_ADDRESS=${escrow.address}`);
console.log("");
console.log("Next step:");
console.log(
  "Set REAL_MARKETPLACE_ESCROW_ADDRESS and AUTHORIZED_ESCROW to this address, then deploy RealCCIPSettlementSender.",
);

