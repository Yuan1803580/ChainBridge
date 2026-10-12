import { Contract, Interface, JsonRpcProvider, NonceManager, ZeroAddress, formatEther, formatUnits, getAddress, parseEther, parseUnits } from "ethers";

import { readJson } from "./utils.js";

const TOKEN_ABI = [
  "function decimals() view returns (uint8)",
  "function balanceOf(address) view returns (uint256)",
  "function allowance(address,address) view returns (uint256)",
  "function approve(address,uint256) returns (bool)",
  "event Approval(address indexed owner,address indexed spender,uint256 value)",
  "event Transfer(address indexed from,address indexed to,uint256 value)",
];

const ESCROW_ABI = [
  "function lockBudget(bytes32 workflowId,bytes32 termsHash,address token,uint256 budget,uint256 platformFeeLimit)",
  "event WorkflowBudgetLocked(bytes32 indexed workflowId,address indexed payer,address indexed token,uint256 budget,uint256 platformFeeLimit)",
];

const GAS_ABI = [
  "function depositSourceGas(bytes32 workflowId) payable",
];

function sameAddress(left, right) {
  return getAddress(left) === getAddress(right);
}

export async function chainContext(publicConfig) {
  const provider = new JsonRpcProvider(publicConfig.rpcUrl, publicConfig.chainId, { staticNetwork: true });
  const network = await provider.getNetwork();
  if (Number(network.chainId) !== publicConfig.chainId) throw new Error("RPC chainId does not match Platform public config");
  return { provider, confirmationBlocks: publicConfig.confirmationBlocks || 1 };
}

export async function assertTrustedDeployment(publicConfig, trustedFile) {
  const trusted = await readJson(trustedFile);
  const checks = [
    ["mode", publicConfig.mode, trusted.mode],
    ["chainId", Number(publicConfig.chainId), Number(trusted.chainId)],
    ["deploymentId", publicConfig.deploymentId, trusted.deploymentId],
  ];
  for (const [name, observed, expected] of checks) {
    if (observed !== expected) throw new Error(`Untrusted ${name}: expected ${expected}, received ${observed}`);
  }
  for (const name of ["sourceToken", "taskEscrow", "gasTreasury"]) {
    if (!sameAddress(publicConfig[name], trusted[name])) {
      throw new Error(`Untrusted ${name}: expected ${trusted[name]}, received ${publicConfig[name]}`);
    }
  }
  const { provider } = await chainContext(publicConfig);
  for (const name of ["sourceToken", "taskEscrow", "gasTreasury"]) {
    if (await provider.getCode(publicConfig[name]) === "0x") throw new Error(`No contract code at trusted ${name}`);
  }
  return trusted;
}

async function successfulReceipt(provider, hash, confirmations) {
  const receipt = await provider.waitForTransaction(hash, confirmations, 120000);
  if (!receipt || receipt.status !== 1) throw new Error(`Transaction failed: ${hash}`);
  if (await receipt.confirmations() < confirmations) throw new Error(`Transaction has insufficient confirmations: ${hash}`);
  return receipt;
}

export async function balanceSnapshot(publicConfig, address) {
  const { provider } = await chainContext(publicConfig);
  const token = new Contract(publicConfig.sourceToken, TOKEN_ABI, provider);
  const decimals = Number(await token.decimals());
  const [native, userToken, escrowToken, gasTreasuryNative] = await Promise.all([
    provider.getBalance(address),
    token.balanceOf(address),
    token.balanceOf(publicConfig.taskEscrow),
    provider.getBalance(publicConfig.gasTreasury),
  ]);
  return {
    blockNumber: await provider.getBlockNumber(),
    walletAddress: getAddress(address),
    nativeWei: native.toString(),
    native: formatEther(native),
    tokenUnits: userToken.toString(),
    token: formatUnits(userToken, decimals),
    escrowTokenUnits: escrowToken.toString(),
    escrowToken: formatUnits(escrowToken, decimals),
    gasTreasuryWei: gasTreasuryNative.toString(),
    gasTreasuryNative: formatEther(gasTreasuryNative),
    tokenDecimals: decimals,
  };
}

export async function verifyFaucetEvidence(publicConfig, address, response, before) {
  const { provider, confirmationBlocks } = await chainContext(publicConfig);
  const tokenInterface = new Interface(TOKEN_ABI);
  const tokenReceipt = await successfulReceipt(provider, response.tokenTransactionHash, confirmationBlocks);
  const minted = tokenReceipt.logs.some((log) => {
    if (!sameAddress(log.address, publicConfig.sourceToken)) return false;
    try {
      const parsed = tokenInterface.parseLog(log);
      return parsed?.name === "Transfer" && sameAddress(parsed.args.from, ZeroAddress) && sameAddress(parsed.args.to, address);
    } catch {
      return false;
    }
  });
  if (!minted) throw new Error("Faucet token transaction has no mint Transfer event for this wallet");
  if (response.nativeTransactionHash) {
    await successfulReceipt(provider, response.nativeTransactionHash, confirmationBlocks);
    const transaction = await provider.getTransaction(response.nativeTransactionHash);
    if (!transaction || !sameAddress(transaction.to, address)) throw new Error("Faucet native transaction recipient mismatch");
  }
  const after = await balanceSnapshot(publicConfig, address);
  if (BigInt(after.tokenUnits) <= BigInt(before.tokenUnits)) throw new Error("Faucet token balance did not increase");
  return { before, after, receipts: { token: tokenReceipt.hash, native: response.nativeTransactionHash || null } };
}

export async function executeFunding(publicConfig, signer, workflow, existing = {}, onProgress = async () => {}) {
  const { provider, confirmationBlocks } = await chainContext(publicConfig);
  const connected = new NonceManager(signer.connect(provider));
  const signerAddress = await connected.getAddress();
  if (!sameAddress(signerAddress, workflow.fundingWallet)) throw new Error("Signer does not own workflow fundingWallet");
  const token = new Contract(publicConfig.sourceToken, TOKEN_ABI, connected);
  const escrow = new Contract(publicConfig.taskEscrow, ESCROW_ABI, connected);
  const gasTreasury = new Contract(publicConfig.gasTreasury, GAS_ABI, connected);
  const decimals = Number(await token.decimals());
  const budget = parseUnits(workflow.budget, decimals);
  const platformFee = parseUnits(workflow.platformFee, decimals);
  if (await token.balanceOf(signerAddress) < budget) throw new Error("Requester wallet has insufficient Test USDC");

  let approveTxHash = existing.approveTxHash;
  let approvalReceipt;
  if (approveTxHash) {
    approvalReceipt = await successfulReceipt(provider, approveTxHash, confirmationBlocks);
  } else {
    const approval = await token.approve(publicConfig.taskEscrow, budget);
    approveTxHash = approval.hash;
    await onProgress({ approveTxHash });
    approvalReceipt = await approval.wait(confirmationBlocks);
  }
  if (!approvalReceipt || approvalReceipt.status !== 1) throw new Error("Exact-budget approval failed");
  if (!existing.lockTxHash && await token.allowance(signerAddress, publicConfig.taskEscrow) !== budget) {
    throw new Error("TaskEscrow allowance is not equal to the exact workflow budget");
  }

  let lockTxHash = existing.lockTxHash;
  let lockReceipt;
  if (lockTxHash) {
    lockReceipt = await successfulReceipt(provider, lockTxHash, confirmationBlocks);
  } else {
    const lock = await escrow.lockBudget(
      workflow.onchainWorkflowId,
      workflow.termsHash,
      publicConfig.sourceToken,
      budget,
      platformFee,
    );
    lockTxHash = lock.hash;
    await onProgress({ approveTxHash, lockTxHash });
    lockReceipt = await lock.wait(confirmationBlocks);
  }
  if (!lockReceipt || lockReceipt.status !== 1) throw new Error("TaskEscrow.lockBudget failed");

  const sourceGas = parseEther(publicConfig.sourceGasAmount);
  let sourceGasTxHash = existing.sourceGasTxHash;
  let gasReceipt;
  if (sourceGasTxHash) {
    gasReceipt = await successfulReceipt(provider, sourceGasTxHash, confirmationBlocks);
  } else {
    const gasDeposit = await gasTreasury.depositSourceGas(workflow.onchainWorkflowId, { value: sourceGas });
    sourceGasTxHash = gasDeposit.hash;
    await onProgress({ approveTxHash, lockTxHash, sourceGasTxHash });
    gasReceipt = await gasDeposit.wait(confirmationBlocks);
  }
  if (!gasReceipt || gasReceipt.status !== 1) throw new Error("GasTreasuryA.depositSourceGas failed");

  const interfaces = {
    approval: new Interface(TOKEN_ABI),
    lock: new Interface(ESCROW_ABI),
    gas: new Interface(GAS_ABI),
  };
  const transactions = await Promise.all([
    provider.getTransaction(approveTxHash),
    provider.getTransaction(lockTxHash),
    provider.getTransaction(sourceGasTxHash),
  ]);
  const parsed = [
    interfaces.approval.parseTransaction({ data: transactions[0].data, value: transactions[0].value }),
    interfaces.lock.parseTransaction({ data: transactions[1].data, value: transactions[1].value }),
    interfaces.gas.parseTransaction({ data: transactions[2].data, value: transactions[2].value }),
  ];
  if (parsed[0]?.name !== "approve" || parsed[1]?.name !== "lockBudget" || parsed[2]?.name !== "depositSourceGas") {
    throw new Error("A funding transaction called an unexpected contract method");
  }
  return {
    approveTxHash,
    lockTxHash,
    sourceGasTxHash,
    sourceGasAmount: publicConfig.sourceGasAmount,
    confirmationBlocks,
    blockNumbers: {
      approval: approvalReceipt.blockNumber,
      lock: lockReceipt.blockNumber,
      sourceGas: gasReceipt.blockNumber,
    },
  };
}

export const contractAbis = { TOKEN_ABI, ESCROW_ABI, GAS_ABI };
