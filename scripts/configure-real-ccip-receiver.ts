import { network } from "hardhat";

import { requiredAddress } from "./real-ccip-env.js";

const { viem } = await network.create();
const [deployer] = await viem.getWalletClients();

const receiverAddress = requiredAddress("REAL_CCIP_RECEIVER_ADDRESS");
const senderAddress = requiredAddress("REAL_CCIP_SENDER_ADDRESS");

console.log("Configuring RealCCIPSettlementReceiver");
console.log(`deployer        ${deployer.account.address}`);
console.log(`receiver        ${receiverAddress}`);
console.log(`allowed sender  ${senderAddress}`);

const receiver = await viem.getContractAt(
  "RealCCIPSettlementReceiver",
  receiverAddress,
);

const txHash = await receiver.write.setAllowedRemoteSender([senderAddress], {
  account: deployer.account,
});
const publicClient = await viem.getPublicClient();
await publicClient.waitForTransactionReceipt({ hash: txHash });

console.log("");
console.log(`configured tx   ${txHash}`);
console.log(
  `allowed sender  ${await receiver.read.allowedRemoteSender()}`,
);

