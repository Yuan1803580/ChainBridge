import { network } from "hardhat";
import { getContractAddress, parseUnits } from "viem";

const sourceChainSelector = 16015286601757825753n;
const destinationChainSelector = 3478487238524512106n;
const settlementPerSourceWad = 2_000n * 10n ** 18n;
const mintAmount = parseUnits("1000", 6);

function printEnv(key: string, value: string | bigint): void {
  console.log(`${key}=${value}`);
}

const { viem } = await network.create();
const publicClient = await viem.getPublicClient();
const wallets = await viem.getWalletClients();
const [deployer, payer, payee, platformSigner] = wallets;

console.log("Deploying local wallet settlement demo contracts");
console.log("Run this with a persistent local node:");
console.log("npx.cmd hardhat run --network localhost --no-compile scripts/deploy-wallet-local.ts\n");

const token = await viem.deployContract("MockUSDC");
const quoteProvider = await viem.deployContract("MockQuoteProvider");

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

for (const wallet of wallets.slice(0, 10)) {
  await token.write.mint([wallet.account.address, mintAmount], {
    account: deployer.account,
  });
  await quoteProvider.write.setRate(
    [wallet.account.address, token.address, 18, 6, settlementPerSourceWad],
    { account: deployer.account },
  );
}

console.log("Contracts");
console.log(`MockUSDC             ${token.address}`);
console.log(`MockQuoteProvider    ${quoteProvider.address}`);
console.log(`CCIPSettlementSender ${sender.address}`);
console.log(`CCIPSettlementRecv   ${receiver.address}`);
console.log(`MarketplaceEscrow    ${escrow.address}`);
console.log("");
console.log("Demo accounts");
console.log(`payer A              ${payer.account.address}`);
console.log(`payee B              ${payee.account.address}`);
console.log(`platformSigner       ${platformSigner.account.address}`);
console.log("");
console.log("Copy into apps/web/.env.local");
printEnv("NEXT_PUBLIC_LOCAL_CHAIN_ID", 31337n);
printEnv("NEXT_PUBLIC_MOCK_USDC_ADDRESS", token.address);
printEnv("NEXT_PUBLIC_MOCK_QUOTE_PROVIDER_ADDRESS", quoteProvider.address);
printEnv("NEXT_PUBLIC_MARKETPLACE_ESCROW_ADDRESS", escrow.address);
printEnv("NEXT_PUBLIC_LOCAL_PAYEE_ADDRESS", payee.account.address);
console.log("");
console.log("Each of the first 10 Hardhat accounts received 1000 MockUSDC.");
