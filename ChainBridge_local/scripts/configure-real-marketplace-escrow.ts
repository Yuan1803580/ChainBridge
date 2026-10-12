import { network } from "hardhat";

import {
  requiredAddress,
  requiredBigInt,
} from "./real-ccip-env.js";

const { viem } = await network.create();
const [deployer] = await viem.getWalletClients();

const escrowAddress = requiredAddress("REAL_MARKETPLACE_ESCROW_ADDRESS");
const senderAddress = requiredAddress("REAL_CCIP_SENDER_ADDRESS");
const destinationChainSelector = requiredBigInt("DESTINATION_CHAIN_SELECTOR");
const receiverAddress = requiredAddress("REAL_CCIP_RECEIVER_ADDRESS");

console.log("Configuring MarketplaceEscrow real CCIP path");
console.log(`deployer                     ${deployer.account.address}`);
console.log(`escrow                       ${escrowAddress}`);
console.log(`real sender                  ${senderAddress}`);
console.log(`destination chain selector    ${destinationChainSelector}`);
console.log(`destination receiver          ${receiverAddress}`);

const escrow = await viem.getContractAt("MarketplaceEscrow", escrowAddress);

const txHash = await escrow.write.setRealCcipConfig([
  senderAddress,
  destinationChainSelector,
  receiverAddress,
], { account: deployer.account });
const publicClient = await viem.getPublicClient();
await publicClient.waitForTransactionReceipt({ hash: txHash });

console.log("");
console.log(`configured tx                ${txHash}`);
console.log(`real sender                  ${await escrow.read.realCcipSender()}`);
