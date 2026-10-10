import { network } from "hardhat";
import { getContractAddress } from "viem";

const settlementId =
  "0x1111111111111111111111111111111111111111111111111111111111111111";
const orderId =
  "0xaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa";
const termsHash =
  "0xbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbb";
const sourceChainSelector = 16015286601757825753n;
const destinationChainSelector = 3478487238524512106n;
const budget = 1_000_000n;
const paid = 700_000n;

function formatUsdc(amount: bigint): string {
  const whole = amount / 1_000_000n;
  const fraction = (amount % 1_000_000n).toString().padStart(6, "0");
  return `${whole}.${fraction} USDC`;
}

function printAddress(label: string, address: string): void {
  console.log(`${label.padEnd(16)} ${address}`);
}

function printBalance(label: string, amount: bigint): void {
  console.log(`${label.padEnd(18)} ${formatUsdc(amount)}`);
}

const { viem } = await network.create();
const publicClient = await viem.getPublicClient();
const [deployer, payer, payee, platformSigner] = await viem.getWalletClients();

console.log("Local cross-chain settlement demo");
console.log("This uses local mock CCIP contracts, not real testnet CCIP.\n");

const token = await viem.deployContract("MockUSDC");

const nextNonce = await publicClient.getTransactionCount({
  address: deployer.account.address,
});
const expectedSenderAddress = getContractAddress({
  from: deployer.account.address,
  nonce: BigInt(nextNonce),
});
const expectedReceiverAddress = getContractAddress({
  from: deployer.account.address,
  nonce: BigInt(nextNonce) + 1n,
});
const expectedEscrowAddress = getContractAddress({
  from: deployer.account.address,
  nonce: BigInt(nextNonce) + 2n,
});

const sender = await viem.deployContract("CCIPSettlementSender", [
  sourceChainSelector,
  destinationChainSelector,
  expectedReceiverAddress,
  token.address,
  expectedEscrowAddress,
]);
const receiver = await viem.deployContract("CCIPSettlementReceiver", [
  sender.address,
  sourceChainSelector,
  sender.address,
  token.address,
]);
const escrow = await viem.deployContract("MarketplaceEscrow", [
  platformSigner.account.address,
  sender.address,
  destinationChainSelector,
  receiver.address,
]);

console.log("Participants and contracts");
printAddress("payer A", payer.account.address);
printAddress("payee B", payee.account.address);
printAddress("platform", platformSigner.account.address);
printAddress("MockUSDC", token.address);
printAddress("Escrow", escrow.address);
printAddress("CCIP Sender", sender.address);
printAddress("CCIP Receiver", receiver.address);
console.log("");

console.log("1. Mint USDC to payer");
await token.write.mint([payer.account.address, budget], {
  account: payer.account,
});
printBalance("payer balance", await token.read.balanceOf([payer.account.address]));
console.log("");

console.log("2. Approve Escrow");
await token.write.approve([escrow.address, budget], {
  account: payer.account,
});
console.log(`payer approved Escrow for ${formatUsdc(budget)}\n`);

console.log("3. Create and lock settlement");
await escrow.write.createAndLock(
  [
    settlementId,
    orderId,
    termsHash,
    payee.account.address,
    token.address,
    budget,
  ],
  { account: payer.account },
);
printBalance("escrow balance", await token.read.balanceOf([escrow.address]));
printBalance("payer balance", await token.read.balanceOf([payer.account.address]));
console.log("");

console.log("4. Release cross-chain");
const { result: messageId } = await escrow.simulate.releaseCrossChain(
  [settlementId, paid],
  { account: payer.account },
);
const releaseTx = await escrow.write.releaseCrossChain([settlementId, paid], {
  account: payer.account,
});
await publicClient.waitForTransactionReceipt({ hash: releaseTx });
console.log(`mock CCIP messageId ${messageId}`);
console.log("");

console.log("5. Verify sender/receiver records");
const sentTransfer = await sender.read.getSentTransfer([messageId]);
const receivedTransfer = await receiver.read.getReceivedTransfer([messageId]);
const storedMessageId = await escrow.read.releaseMessageIds([settlementId]);
console.log(`escrow stored messageId     ${storedMessageId}`);
console.log(`sender recorded amount      ${formatUsdc(sentTransfer.amount)}`);
console.log(`receiver recorded amount    ${formatUsdc(receivedTransfer.amount)}`);
console.log(`receiver paid to payee B    ${receivedTransfer.receiver}`);
console.log("");

console.log("6. Confirm destination receipt on source escrow");
await escrow.write.confirmCrossChainRelease([settlementId, messageId], {
  account: platformSigner.account,
});
console.log("platform reconciled the destination receiver event\n");

console.log("7. Verify final balances");
printBalance("payer refund", await token.read.balanceOf([payer.account.address]));
printBalance("payee received", await token.read.balanceOf([payee.account.address]));
printBalance("escrow balance", await token.read.balanceOf([escrow.address]));
printBalance("sender balance", await token.read.balanceOf([sender.address]));
printBalance("receiver balance", await token.read.balanceOf([receiver.address]));

const settlement = await escrow.read.settlements([settlementId]);
const statusLabels = [
  "None",
  "Created",
  "Locked",
  "ReleasePending",
  "Released",
  "RefundPending",
  "Refunded",
  "Failed",
];
console.log("");
console.log(`settlement status           ${statusLabels[Number(settlement[7])]}`);
console.log(`settlement paidAmount       ${formatUsdc(settlement[6])}`);
console.log("\nDemo complete.");
