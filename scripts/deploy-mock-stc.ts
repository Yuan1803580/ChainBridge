import { network } from "hardhat";

const { viem } = await network.create();
const publicClient = await viem.getPublicClient();
const [deployer] = await viem.getWalletClients();
const stc = await viem.deployContract("MockSTC");
const initialSupply = BigInt(process.env.STC_INITIAL_SUPPLY ?? "1000000000000000000000000");
const mintHash = await stc.write.mint([deployer.account.address, initialSupply], {
  account: deployer.account,
});
await publicClient.waitForTransactionReceipt({ hash: mintHash });

console.log(`deployer                      ${deployer.account.address}`);
console.log(`DESTINATION_STC_TOKEN=${stc.address}`);
console.log(`initial STC supply            ${initialSupply}`);
console.log(`mint tx                       ${mintHash}`);
