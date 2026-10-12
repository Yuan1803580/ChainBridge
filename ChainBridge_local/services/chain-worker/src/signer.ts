import { createWalletClient, http } from "viem";
import { privateKeyToAccount } from "viem/accounts";

import type { WorkerConfig } from "./config.ts";

export type RelayerSigner = {
  account: ReturnType<typeof privateKeyToAccount>;
  walletClient: ReturnType<typeof createWalletClient>;
};

export function createRelayerSigner(config: WorkerConfig): RelayerSigner {
  if (config.signerMode === "kms") {
    throw new Error("RELAYER_SIGNER_MODE=kms requires a deployment-specific RelayerSigner adapter");
  }
  if (!config.relayerPrivateKey) throw new Error("RELAYER_PRIVATE_KEY is required for the env signer");
  const account = privateKeyToAccount(config.relayerPrivateKey);
  return {
    account,
    walletClient: createWalletClient({ account, transport: http(config.sourceRpcUrl) }),
  };
}
