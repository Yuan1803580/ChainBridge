import path from "node:path";

import { JsonRpcProvider } from "ethers";

import { balanceSnapshot } from "./contracts.js";
import { redact, writeJsonAtomic } from "./utils.js";

async function transactionEvidence(provider, hash) {
  if (!hash) return null;
  const [transaction, receipt] = await Promise.all([
    provider.getTransaction(hash),
    provider.getTransactionReceipt(hash),
  ]);
  return {
    hash,
    from: transaction?.from,
    to: transaction?.to,
    value: transaction?.value?.toString(),
    blockNumber: receipt?.blockNumber,
    status: receipt?.status,
    gasUsed: receipt?.gasUsed?.toString(),
  };
}

export async function exportEvidence(config, publicConfig, state, workflow) {
  const provider = new JsonRpcProvider(publicConfig.rpcUrl, publicConfig.chainId, { staticNetwork: true });
  const hashes = state.funding || {};
  const finalBalances = await balanceSnapshot(publicConfig, state.walletAddress);
  const report = redact({
    schemaVersion: "local-requester-evidence/v1",
    runId: state.runId,
    generatedAt: new Date().toISOString(),
    securityBoundary: {
      platformReceivedPrivateKey: false,
      walletStorage: "encrypted-json-keystore",
      localExperimentOnly: true,
    },
    trustedDeployment: state.trustedDeployment,
    publicConfig,
    walletAddress: state.walletAddress,
    conversationId: state.conversationId,
    workflowId: workflow.id,
    onchainWorkflowId: workflow.onchainWorkflowId,
    termsHash: workflow.termsHash,
    fundingStatus: workflow.fundingStatus,
    workflowStatus: workflow.status,
    faucet: state.faucet,
    funding: {
      ...hashes,
      transactions: {
        approve: await transactionEvidence(provider, hashes.approveTxHash),
        lock: await transactionEvidence(provider, hashes.lockTxHash),
        sourceGas: await transactionEvidence(provider, hashes.sourceGasTxHash),
      },
    },
    balances: {
      beforeFaucet: state.balances?.beforeFaucet,
      afterFaucet: state.balances?.afterFaucet,
      beforeFunding: state.balances?.beforeFunding,
      afterFunding: state.balances?.afterFunding,
      final: finalBalances,
    },
    workflow: {
      paidAmount: workflow.paidAmount,
      refundedAmount: workflow.refundedAmount,
      sourceGasDeposit: workflow.sourceGasDeposit,
      sourceGasRefunded: workflow.sourceGasRefunded,
      ledgerSummary: workflow.ledgerSummary,
      nodes: workflow.nodes,
      events: workflow.events,
      ledger: workflow.ledger,
    },
  });
  const file = path.join(config.outputDirectory, `requester-evidence-${state.runId}.json`);
  await writeJsonAtomic(file, report);
  return { file, report };
}
