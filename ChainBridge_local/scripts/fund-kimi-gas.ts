import { mkdir, readFile, rename, writeFile } from "node:fs/promises";
import { dirname, isAbsolute, resolve } from "node:path";

import {
  createPublicClient,
  createWalletClient,
  formatEther,
  getAddress,
  http,
  parseEther,
  type Hex,
} from "viem";
import { privateKeyToAccount } from "viem/accounts";

import { CONTROLLER_PRIVATE_KEY } from "./experiment-v2-deploy-lib.ts";

const projectRoot = resolve(import.meta.dirname, "..");
const configuredRuntime = process.env.LOCAL_RUNTIME_DIR ?? ".runtime/local-experiment";
const runtimeDir = isAbsolute(configuredRuntime)
  ? configuredRuntime
  : resolve(projectRoot, configuredRuntime);
const onboardingPath = resolve(runtimeDir, "runner-kimi-onboarding.json");
const evidencePath = resolve(runtimeDir, "evidence", "kimi-gas-funding.json");
const rpcUrl = process.env.LOCAL_CHAIN_B_RPC_URL
  ?? process.env.EXPERIMENT_V2_CHAIN_B_RPC
  ?? "http://127.0.0.1:8546";
const expectedChainId = 31338;
const targetBalance = parseEther(process.env.KIMI_GAS_TARGET_ETH ?? "1");

type OnboardingState = {
  agentKey: string;
  homeWallet: string;
  chainId: number;
  profileId: string;
};

async function writeJsonAtomic(path: string, value: unknown): Promise<void> {
  await mkdir(dirname(path), { recursive: true });
  const temporaryPath = `${path}.${process.pid}.tmp`;
  await writeFile(temporaryPath, `${JSON.stringify(value, null, 2)}\n`, "utf8");
  await rename(temporaryPath, path);
}

async function main(): Promise<void> {
  const raw = (await readFile(onboardingPath, "utf8")).replace(/^\uFEFF/, "");
  const onboarding = JSON.parse(raw) as OnboardingState;
  if (onboarding.agentKey !== "workflow-research-kimi-external-v2") {
    throw new Error("The onboarding state does not belong to workflow-research-kimi-external-v2");
  }
  if (Number(onboarding.chainId) !== expectedChainId) {
    throw new Error(`Kimi onboarding chainId ${onboarding.chainId} does not match chain B (${expectedChainId})`);
  }
  const recipient = getAddress(onboarding.homeWallet);
  const account = privateKeyToAccount(CONTROLLER_PRIVATE_KEY);
  const publicClient = createPublicClient({ transport: http(rpcUrl) });
  const observedChainId = await publicClient.getChainId();
  if (observedChainId !== expectedChainId) {
    throw new Error(`Chain B RPC reports chainId ${observedChainId}, expected ${expectedChainId}`);
  }

  const balanceBefore = await publicClient.getBalance({ address: recipient });
  let amountTransferred = 0n;
  let transactionHash: Hex | null = null;
  let blockNumber: bigint | null = null;
  let gasUsed: bigint | null = null;
  let status = "ALREADY_FUNDED";

  if (balanceBefore < targetBalance) {
    amountTransferred = targetBalance - balanceBefore;
    const walletClient = createWalletClient({ account, transport: http(rpcUrl) });
    transactionHash = await walletClient.sendTransaction({
      account,
      to: recipient,
      value: amountTransferred,
    });
    const receipt = await publicClient.waitForTransactionReceipt({ hash: transactionHash });
    if (receipt.status !== "success") {
      throw new Error(`Kimi Gas funding transaction ${transactionHash} failed`);
    }
    blockNumber = receipt.blockNumber;
    gasUsed = receipt.gasUsed;
    status = "SUCCESS";
  }

  const balanceAfter = await publicClient.getBalance({ address: recipient });
  if (balanceAfter < targetBalance) {
    throw new Error(`Kimi wallet balance ${balanceAfter} is below target ${targetBalance} after funding`);
  }
  const evidence = {
    schemaVersion: 1,
    chainId: observedChainId,
    rpcUrl,
    funder: account.address,
    recipient,
    targetBalanceWei: targetBalance.toString(),
    targetBalanceEth: formatEther(targetBalance),
    balanceBefore: balanceBefore.toString(),
    amountTransferred: amountTransferred.toString(),
    balanceAfter: balanceAfter.toString(),
    transactionHash,
    blockNumber: blockNumber?.toString() ?? null,
    gasUsed: gasUsed?.toString() ?? null,
    status,
    timestamp: new Date().toISOString(),
  };
  await writeJsonAtomic(evidencePath, evidence);
  process.stdout.write(`${JSON.stringify(evidence)}\n`);
}

main().catch((error: unknown) => {
  const message = error instanceof Error ? error.message : String(error);
  process.stderr.write(`Kimi Gas funding failed: ${message}\n`);
  process.exitCode = 1;
});
