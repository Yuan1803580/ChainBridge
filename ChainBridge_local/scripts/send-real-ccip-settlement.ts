import { network } from "hardhat";

import {
  optionalEnv,
  requiredAddress,
  requiredBigInt,
  requiredEnv,
} from "./real-ccip-env.js";

const { viem } = await network.create();
if (process.env.ALLOW_DIRECT_CCIP_TEST !== "true") {
  throw new Error(
    "Direct Sender calls bypass Escrow. Set ALLOW_DIRECT_CCIP_TEST=true only for an isolated CCIP transport test.",
  );
}
const publicClient = await viem.getPublicClient();
const [payer] = await viem.getWalletClients();

const senderAddress = requiredAddress("REAL_CCIP_SENDER_ADDRESS");
const destinationChainSelector = requiredBigInt("DESTINATION_CHAIN_SELECTOR");
const receiverAddress = requiredAddress("REAL_CCIP_RECEIVER_ADDRESS");
const tokenAddress = requiredAddress("SOURCE_SETTLEMENT_TOKEN");
const settlementId = requiredEnv("SETTLEMENT_ID") as `0x${string}`;
const orderId = requiredEnv("ORDER_ID") as `0x${string}`;
const payeeAddress = requiredAddress("PAYEE_ADDRESS");
const amount = requiredBigInt("SETTLEMENT_AMOUNT");
const ccipExplorerUrl = optionalEnv("CCIP_EXPLORER_URL") ?? "https://ccip.chain.link/";

if (!settlementId.startsWith("0x") || settlementId.length !== 66) {
  throw new Error("SETTLEMENT_ID must be a bytes32 hex string");
}
if (!orderId.startsWith("0x") || orderId.length !== 66) {
  throw new Error("ORDER_ID must be a bytes32 hex string");
}
if (amount <= 0n) {
  throw new Error("SETTLEMENT_AMOUNT must be greater than 0");
}

const token = await viem.getContractAt("MockUSDC", tokenAddress);
const sender = await viem.getContractAt("RealCCIPSettlementSender", senderAddress);

const payload = {
  settlementId,
  orderId,
  payer: payer.account.address,
  payee: payeeAddress,
  amount,
};

console.log("Sending real CCIP settlement");
console.log(`payer                         ${payer.account.address}`);
console.log(`payee                         ${payeeAddress}`);
console.log(`sender                        ${senderAddress}`);
console.log(`receiver                      ${receiverAddress}`);
console.log(`source token                  ${tokenAddress}`);
console.log(`destination chain selector    ${destinationChainSelector}`);
console.log(`settlement amount             ${amount}`);

const fee = await sender.read.quoteFee([
  destinationChainSelector,
  receiverAddress,
  tokenAddress,
  amount,
]);

console.log(`native CCIP fee quote         ${fee}`);
console.log("");

console.log("Approving sender to spend source token...");
const approveTx = await token.write.approve([senderAddress, amount], {
  account: payer.account,
});
await publicClient.waitForTransactionReceipt({ hash: approveTx });
console.log(`approve tx                    ${approveTx}`);

console.log("Sending CCIP message...");
const { result: messageId } = await sender.simulate.sendSettlementPayNative(
  [destinationChainSelector, receiverAddress, tokenAddress, payload],
  { account: payer.account, value: fee },
);
const sendTx = await sender.write.sendSettlementPayNative(
  [destinationChainSelector, receiverAddress, tokenAddress, payload],
  { account: payer.account, value: fee },
);
await publicClient.waitForTransactionReceipt({ hash: sendTx });

console.log("");
console.log(`messageId                     ${messageId}`);
console.log(`send tx                       ${sendTx}`);
console.log(`CCIP Explorer                 ${ccipExplorerUrl}`);
console.log("");
console.log("Use the messageId above to track delivery status in CCIP Explorer.");
