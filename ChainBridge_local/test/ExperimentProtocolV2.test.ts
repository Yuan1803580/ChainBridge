import assert from "node:assert/strict";
import { describe, it } from "node:test";

import { network } from "hardhat";
import { decodeAbiParameters, keccak256, stringToHex, type Hex } from "viem";

const id = (value: string) => keccak256(stringToHex(value));

const envelopeParameter = {
  type: "tuple",
  components: [
    { name: "messageType", type: "uint8" },
    { name: "messageId", type: "bytes32" },
    { name: "sourceChainId", type: "uint64" },
    { name: "targetChainId", type: "uint64" },
    { name: "sourceSender", type: "address" },
    { name: "targetReceiver", type: "address" },
    { name: "workflowId", type: "bytes32" },
    { name: "nodeId", type: "bytes32" },
    { name: "attemptId", type: "bytes32" },
    { name: "attemptNumber", type: "uint32" },
    { name: "routeKey", type: "bytes32" },
    { name: "routeVersion", type: "uint32" },
    { name: "nonce", type: "uint256" },
    { name: "payloadHash", type: "bytes32" },
  ],
} as const;

async function experimentMessage(publicClient: any, contract: any, hash: Hex) {
  const receipt = await publicClient.waitForTransactionReceipt({ hash });
  const events = await publicClient.getContractEvents({
    address: contract.address,
    abi: contract.abi,
    eventName: "ExperimentMessageSent",
    fromBlock: receipt.blockNumber,
    toBlock: receipt.blockNumber,
    strict: true,
  });
  assert.equal(events.length, 1);
  const args = events[0].args as { encodedEnvelope: Hex; payload: Hex };
  const [envelope] = decodeAbiParameters([envelopeParameter], args.encodedEnvelope);
  return { envelope, payload: args.payload };
}

async function tokenBalanceSum(token: any, addresses: readonly Hex[]) {
  let total = 0n;
  for (const address of addresses) total += await token.read.balanceOf([address]);
  return total;
}

describe("Experiment protocol V2", async function () {
  const { viem } = await network.create();

  it("uses real AMM reserves, price impact, deadlines, and slippage checks", async function () {
    const [provider, trader] = await viem.getWalletClients();
    const token0 = await viem.deployContract("MockUSDC");
    const token1 = await viem.deployContract("MockUSDC");
    const amm = await viem.deployContract("ExperimentConstantProductAMM", [token0.address, token1.address]);
    const publicClient = await viem.getPublicClient();
    const now = (await publicClient.getBlock()).timestamp;

    await token0.write.mint([provider.account.address, 1_000_000_000n]);
    await token1.write.mint([provider.account.address, 2_000_000_000n]);
    await token0.write.approve([amm.address, 1_000_000_000n], { account: provider.account });
    await token1.write.approve([amm.address, 2_000_000_000n], { account: provider.account });
    await amm.write.addLiquidity([1_000_000_000n, 2_000_000_000n, 1n, now + 3_600n], { account: provider.account });

    const smallQuote = await amm.read.quoteExactInput([token0.address, 1_000_000n]);
    const largeQuote = await amm.read.quoteExactInput([token0.address, 100_000_000n]);
    assert.ok(largeQuote < smallQuote * 100n, "larger swaps must suffer price impact");

    await token0.write.mint([trader.account.address, 10_000_000n]);
    await token0.write.approve([amm.address, 10_000_000n], { account: trader.account });
    await assert.rejects(amm.write.swapExactInput([
      token0.address,
      10_000_000n,
      await amm.read.quoteExactInput([token0.address, 10_000_000n]) + 1n,
      now + 3_600n,
      trader.account.address,
    ], { account: trader.account }));
    await assert.rejects(amm.write.swapExactInput([
      token0.address,
      1_000_000n,
      1n,
      now - 1n,
      trader.account.address,
    ], { account: trader.account }));

    const amountOut = await amm.read.quoteExactInput([token0.address, 10_000_000n]);
    await amm.write.swapExactInput([
      token0.address,
      10_000_000n,
      amountOut,
      now + 3_600n,
      trader.account.address,
    ], { account: trader.account });
    assert.equal(await token1.read.balanceOf([trader.account.address]), amountOut);
    assert.equal(await amm.read.reserve0(), 1_010_000_000n);
    assert.equal(await amm.read.reserve1(), 2_000_000_000n - amountOut);
  });

  it("settles only after event relay, keeps failed swaps in the Vault, and preserves all balances", async function () {
    const [controller, relay, payer, agent, treasury, liquidityProvider] = await viem.getWalletClients();
    const publicClient = await viem.getPublicClient();
    const chainA = BigInt(await publicClient.getChainId());
    const chainB = chainA + 1n;
    const routeKey = id("EXPERIMENT_A_TO_B");
    const routeVersion = 1;

    const sourceToken = await viem.deployContract("MockUSDC");
    const sourceBridge = await viem.deployContract("MockUSDC");
    const destinationBridge = await viem.deployContract("MockUSDC");
    const payoutToken = await viem.deployContract("MockUSDC");
    const workflow = await viem.deployContract("WorkflowEngine", [controller.account.address]);
    const gateway = await viem.deployContract("ResultProofGateway", [controller.account.address, relay.account.address]);
    await gateway.write.configureWorkflowEngine([workflow.address], { account: controller.account });
    const review = await viem.deployContract("CoordinatorReview", [controller.account.address, gateway.address]);
    const sourceAMM = await viem.deployContract("ExperimentConstantProductAMM", [sourceToken.address, sourceBridge.address]);
    const sourceVault = await viem.deployContract("ExperimentSourceVault", [
      controller.account.address,
      relay.account.address,
      sourceBridge.address,
    ]);
    const escrow = await viem.deployContract("ExperimentEscrow", [
      controller.account.address,
      treasury.account.address,
      sourceToken.address,
      sourceBridge.address,
      workflow.address,
      review.address,
      sourceAMM.address,
      sourceVault.address,
    ]);
    await sourceVault.write.configureEscrow([escrow.address], { account: controller.account });
    await workflow.write.configureSettlementVerifier([sourceVault.address], { account: controller.account });

    const identityRegistry = await viem.deployContract("AgentIdentityRegistry", [relay.account.address]);
    const workBoard = await viem.deployContract("RemoteWorkBoard", [chainB, chainA, relay.account.address]);
    await workBoard.write.configureIdentityRegistry([identityRegistry.address], { account: relay.account });
    const dispatch = await viem.deployContract("ExperimentDispatchSender", [controller.account.address, workflow.address]);
    const destinationAMM = await viem.deployContract("ExperimentConstantProductAMM", [destinationBridge.address, payoutToken.address]);
    const destinationVault = await viem.deployContract("ExperimentDestinationVault", [
      controller.account.address,
      relay.account.address,
      chainB,
      destinationBridge.address,
      payoutToken.address,
      destinationAMM.address,
      workBoard.address,
    ]);

    await dispatch.write.configureRoute([routeKey, routeVersion, chainB, workBoard.address, true], { account: controller.account });
    await workBoard.write.configureRoute([routeKey, routeVersion, dispatch.address, true], { account: relay.account });
    await workBoard.write.configureResultReceiver([routeKey, routeVersion, gateway.address], { account: relay.account });
    await gateway.write.configureRoute([routeKey, routeVersion, chainB, workBoard.address, true], { account: controller.account });
    await sourceVault.write.configureRoute([routeKey, routeVersion, chainB, destinationVault.address, true], { account: controller.account });
    await destinationVault.write.configureSource([routeKey, routeVersion, chainA, sourceVault.address, true], { account: controller.account });

    const agentDidHash = id("did:experiment:agent-b");
    await identityRegistry.write.registerOrUpdate([
      agentDidHash,
      agent.account.address,
      chainB,
      id("role:executor"),
      id("capability:analysis"),
      true,
    ], { account: relay.account });

    const now = (await publicClient.getBlock()).timestamp;
    const sourceLiquidity = 1_000_000_000n;
    const destinationBridgeLiquidity = 1_000_000_000n;
    const payoutLiquidity = 2_000_000_000n;
    const destinationVaultLiquidity = 500_000_000n;
    await sourceToken.write.mint([liquidityProvider.account.address, sourceLiquidity]);
    await sourceBridge.write.mint([liquidityProvider.account.address, sourceLiquidity]);
    await sourceToken.write.approve([sourceAMM.address, sourceLiquidity], { account: liquidityProvider.account });
    await sourceBridge.write.approve([sourceAMM.address, sourceLiquidity], { account: liquidityProvider.account });
    await sourceAMM.write.addLiquidity([sourceLiquidity, sourceLiquidity, 1n, now + 3_600n], { account: liquidityProvider.account });

    await destinationBridge.write.mint([liquidityProvider.account.address, destinationBridgeLiquidity]);
    await payoutToken.write.mint([liquidityProvider.account.address, payoutLiquidity]);
    await destinationBridge.write.approve([destinationAMM.address, destinationBridgeLiquidity], { account: liquidityProvider.account });
    await payoutToken.write.approve([destinationAMM.address, payoutLiquidity], { account: liquidityProvider.account });
    await destinationAMM.write.addLiquidity([
      destinationBridgeLiquidity,
      payoutLiquidity,
      1n,
      now + 3_600n,
    ], { account: liquidityProvider.account });
    await destinationBridge.write.mint([destinationVault.address, destinationVaultLiquidity]);

    const workflowId = id("experiment:workflow:1");
    const nodeId = id("experiment:node:1");
    const attemptId = id("experiment:attempt:1");
    const criteriaHash = id("experiment:criteria:1");
    const resultHash = id("experiment:result:1");
    const proofHash = id("experiment:proof:1");
    const budget = 20_000_000n;
    const sourceSpend = 10_000_000n;
    const platformFee = 1_000_000n;
    await workflow.write.createWorkflow([workflowId, payer.account.address, id("experiment:terms:1")], { account: controller.account });
    await workflow.write.registerNode([workflowId, nodeId, criteriaHash, []], { account: controller.account });
    await workflow.write.assignNode([nodeId, attemptId, routeKey, routeVersion, agent.account.address], { account: controller.account });
    await sourceToken.write.mint([payer.account.address, budget]);
    await sourceToken.write.approve([escrow.address, budget], { account: payer.account });
    await escrow.write.lockBudget([workflowId, budget, platformFee], { account: payer.account });
    await escrow.write.reserve([workflowId, nodeId, attemptId, sourceSpend], { account: controller.account });

    const assignmentHash = await dispatch.write.sendAssignment([
      nodeId,
      attemptId,
      1,
      agentDidHash,
      sourceSpend,
    ], { account: controller.account });
    const assignmentMessage = await experimentMessage(publicClient, dispatch, assignmentHash);
    await workBoard.write.receiveAssignmentV2([
      assignmentMessage.envelope,
      assignmentMessage.payload,
    ], { account: relay.account });
    await assert.rejects(workBoard.write.receiveAssignmentV2([
      assignmentMessage.envelope,
      assignmentMessage.payload,
    ], { account: relay.account }));

    await workBoard.write.startWork([attemptId], { account: agent.account });
    const resultTxHash = await workBoard.write.submitResultV2([attemptId, resultHash, proofHash], { account: agent.account });
    const resultMessage = await experimentMessage(publicClient, workBoard, resultTxHash);
    await gateway.write.receiveResultProofV2([resultMessage.envelope, resultMessage.payload], { account: relay.account });
    await review.write.recordReview([
      attemptId,
      criteriaHash,
      resultHash,
      proofHash,
      1,
      id("PASS"),
      id("experiment:review:1"),
    ], { account: controller.account });
    await workflow.write.markSucceeded([nodeId, attemptId], { account: controller.account });

    const expectedBridge = await sourceAMM.read.quoteExactInput([sourceToken.address, sourceSpend]);
    const impossiblePayout = await destinationAMM.read.quoteExactInput([destinationBridge.address, expectedBridge]) + 1n;
    const settlementTxHash = await escrow.write.prepareSettlement([{
      attemptId,
      attemptNumber: 1,
      minBridgeAmount: expectedBridge,
      sourceSwapDeadline: now + 3_600n,
      payee: agent.account.address,
      payoutToken: payoutToken.address,
      minPayoutAmount: impossiblePayout,
      payoutDeadline: now + 3_600n,
    }], { account: controller.account });
    const settlementMessage = await experimentMessage(publicClient, sourceVault, settlementTxHash);
    const settlementMessageId = settlementMessage.envelope.messageId;
    await workflow.write.markSettlementPending([nodeId, attemptId, settlementMessageId], { account: controller.account });
    await assert.rejects(workflow.write.markSettled([nodeId, attemptId], { account: controller.account }));

    await destinationVault.write.receiveSettlementV2([
      settlementMessage.envelope,
      settlementMessage.payload,
    ], { account: relay.account });
    await assert.rejects(destinationVault.write.receiveSettlementV2([
      settlementMessage.envelope,
      settlementMessage.payload,
    ], { account: relay.account }));
    assert.equal(await destinationVault.read.payoutStatus([settlementMessageId]), 1);
    const vaultBalanceBeforeFailedSwap = await destinationBridge.read.balanceOf([destinationVault.address]);
    await assert.rejects(destinationVault.write.executePayout([settlementMessageId], { account: relay.account }));
    assert.equal(await destinationVault.read.payoutStatus([settlementMessageId]), 1);
    assert.equal(await destinationBridge.read.balanceOf([destinationVault.address]), vaultBalanceBeforeFailedSwap);
    assert.equal(await destinationVault.read.reservedBridge(), expectedBridge);

    const recoverablePayout = await destinationAMM.read.quoteExactInput([destinationBridge.address, expectedBridge]);
    await destinationVault.write.updatePayoutTerms([
      settlementMessageId,
      recoverablePayout,
      now + 7_200n,
    ], { account: controller.account });
    const payoutTxHash = await destinationVault.write.executePayout([settlementMessageId], { account: relay.account });
    const confirmationMessage = await experimentMessage(publicClient, destinationVault, payoutTxHash);
    assert.equal(await destinationVault.read.payoutStatus([settlementMessageId]), 2);
    assert.equal(await destinationVault.read.reservedBridge(), 0n);
    assert.equal(await payoutToken.read.balanceOf([agent.account.address]), recoverablePayout);
    await assert.rejects(destinationVault.write.executePayout([settlementMessageId], { account: relay.account }));

    await sourceVault.write.receiveConfirmationV2([
      confirmationMessage.envelope,
      confirmationMessage.payload,
    ], { account: relay.account });
    await assert.rejects(sourceVault.write.receiveConfirmationV2([
      confirmationMessage.envelope,
      confirmationMessage.payload,
    ], { account: relay.account }));
    assert.equal(await sourceVault.read.settlementStatus([settlementMessageId]), 2);
    assert.equal(await sourceVault.read.isAttemptConfirmed([attemptId]), true);
    await workflow.write.markSettled([nodeId, attemptId], { account: controller.account });
    assert.equal((await workflow.read.nodes([nodeId]))[7], 5);
    assert.equal((await workflow.read.workflows([workflowId]))[4], false);

    await escrow.write.payPlatformFee([workflowId, platformFee], { account: controller.account });
    await assert.rejects(escrow.write.payPlatformFee([workflowId, 1n], { account: controller.account }));
    await escrow.write.refundRemainder([workflowId], { account: controller.account });
    assert.equal(await sourceToken.read.balanceOf([payer.account.address]), budget - sourceSpend - platformFee);
    assert.equal(await sourceToken.read.balanceOf([treasury.account.address]), platformFee);
    assert.equal(await sourceVault.read.lockedBacking(), expectedBridge);
    assert.equal(
      destinationVaultLiquidity - await destinationBridge.read.balanceOf([destinationVault.address]),
      expectedBridge,
      "source backing must equal destination liquidity released",
    );

    assert.equal(await tokenBalanceSum(sourceToken, [
      payer.account.address,
      treasury.account.address,
      escrow.address,
      sourceAMM.address,
      liquidityProvider.account.address,
    ]), await sourceToken.read.totalSupply());
    assert.equal(await tokenBalanceSum(sourceBridge, [
      sourceAMM.address,
      sourceVault.address,
      liquidityProvider.account.address,
    ]), await sourceBridge.read.totalSupply());
    assert.equal(await tokenBalanceSum(destinationBridge, [
      destinationAMM.address,
      destinationVault.address,
      liquidityProvider.account.address,
    ]), await destinationBridge.read.totalSupply());
    assert.equal(await tokenBalanceSum(payoutToken, [
      destinationAMM.address,
      agent.account.address,
      liquidityProvider.account.address,
    ]), await payoutToken.read.totalSupply());
  });

  it("rejects a delayed older attempt after a newer assignment has arrived", async function () {
    const [controller, relay, agentOne, agentTwo, requester] = await viem.getWalletClients();
    const publicClient = await viem.getPublicClient();
    const chainA = BigInt(await publicClient.getChainId());
    const chainB = chainA + 1n;
    const routeKey = id("EXPERIMENT_RETRY_ROUTE");
    const workflow = await viem.deployContract("WorkflowEngine", [controller.account.address]);
    const dispatch = await viem.deployContract("ExperimentDispatchSender", [controller.account.address, workflow.address]);
    const registry = await viem.deployContract("AgentIdentityRegistry", [relay.account.address]);
    const board = await viem.deployContract("RemoteWorkBoard", [chainB, chainA, relay.account.address]);
    await board.write.configureIdentityRegistry([registry.address], { account: relay.account });
    await board.write.configureRoute([routeKey, 1, dispatch.address, true], { account: relay.account });
    await dispatch.write.configureRoute([routeKey, 1, chainB, board.address, true], { account: controller.account });
    for (const [name, wallet] of [["one", agentOne], ["two", agentTwo]] as const) {
      await registry.write.registerOrUpdate([
        id(`did:retry:${name}`),
        wallet.account.address,
        chainB,
        id("role:executor"),
        id("capability:retry"),
        true,
      ], { account: relay.account });
    }

    const workflowId = id("experiment:workflow:retry");
    const nodeId = id("experiment:node:retry");
    const attemptOne = id("experiment:attempt:retry:1");
    const attemptTwo = id("experiment:attempt:retry:2");
    await workflow.write.createWorkflow([workflowId, requester.account.address, id("experiment:terms:retry")], { account: controller.account });
    await workflow.write.registerNode([workflowId, nodeId, id("experiment:criteria:retry"), []], { account: controller.account });
    await workflow.write.assignNode([nodeId, attemptOne, routeKey, 1, agentOne.account.address], { account: controller.account });
    const attemptOneTx = await dispatch.write.sendAssignment([
      nodeId,
      attemptOne,
      1,
      id("did:retry:one"),
      1_000_000n,
    ], { account: controller.account });
    const attemptOneMessage = await experimentMessage(publicClient, dispatch, attemptOneTx);
    await workflow.write.markAttemptFailed([nodeId, attemptOne], { account: controller.account });
    await workflow.write.assignNode([nodeId, attemptTwo, routeKey, 1, agentTwo.account.address], { account: controller.account });
    const attemptTwoTx = await dispatch.write.sendAssignment([
      nodeId,
      attemptTwo,
      2,
      id("did:retry:two"),
      1_000_000n,
    ], { account: controller.account });
    const attemptTwoMessage = await experimentMessage(publicClient, dispatch, attemptTwoTx);

    await board.write.receiveAssignmentV2([
      attemptTwoMessage.envelope,
      attemptTwoMessage.payload,
    ], { account: relay.account });
    await assert.rejects(board.write.receiveAssignmentV2([
      attemptOneMessage.envelope,
      attemptOneMessage.payload,
    ], { account: relay.account }));
    assert.equal(await board.read.currentAttemptByNode([nodeId]), attemptTwo);
    assert.equal(await board.read.latestAttemptNumberByNode([nodeId]), 2);
  });

  it("does not create a settlement for a failed coordinator review", async function () {
    const [controller, relay, payer, agent, treasury] = await viem.getWalletClients();
    const token = await viem.deployContract("MockUSDC");
    const bridge = await viem.deployContract("MockUSDC");
    const workflow = await viem.deployContract("WorkflowEngine", [controller.account.address]);
    const gateway = await viem.deployContract("ResultProofGateway", [controller.account.address, relay.account.address]);
    await gateway.write.configureWorkflowEngine([workflow.address], { account: controller.account });
    const review = await viem.deployContract("CoordinatorReview", [controller.account.address, gateway.address]);
    const amm = await viem.deployContract("ExperimentConstantProductAMM", [token.address, bridge.address]);
    const sourceVault = await viem.deployContract("ExperimentSourceVault", [controller.account.address, relay.account.address, bridge.address]);
    const escrow = await viem.deployContract("ExperimentEscrow", [
      controller.account.address,
      treasury.account.address,
      token.address,
      bridge.address,
      workflow.address,
      review.address,
      amm.address,
      sourceVault.address,
    ]);
    await sourceVault.write.configureEscrow([escrow.address], { account: controller.account });
    const workflowId = id("failed-review:workflow");
    const nodeId = id("failed-review:node");
    const attemptId = id("failed-review:attempt");
    const routeKey = id("failed-review:route");
    const resultHash = id("failed-review:result");
    const proofHash = id("failed-review:proof");
    await workflow.write.createWorkflow([workflowId, payer.account.address, id("failed-review:terms")], { account: controller.account });
    await workflow.write.registerNode([workflowId, nodeId, id("failed-review:criteria"), []], { account: controller.account });
    await workflow.write.assignNode([nodeId, attemptId, routeKey, 1, agent.account.address], { account: controller.account });
    await token.write.mint([payer.account.address, 10_000_000n]);
    await token.write.approve([escrow.address, 10_000_000n], { account: payer.account });
    await escrow.write.lockBudget([workflowId, 10_000_000n, 0n], { account: payer.account });
    await escrow.write.reserve([workflowId, nodeId, attemptId, 5_000_000n], { account: controller.account });
    await gateway.write.configureRoute([routeKey, 1, 31_338n, relay.account.address, true], { account: controller.account });
    await gateway.write.receiveResultProof([
      id("failed-review:message"),
      31_338n,
      relay.account.address,
      routeKey,
      1,
      workflowId,
      nodeId,
      attemptId,
      resultHash,
      proofHash,
    ], { account: relay.account });
    await review.write.recordReview([
      attemptId,
      id("failed-review:criteria"),
      resultHash,
      proofHash,
      2,
      id("QUALITY_FAILED"),
      id("failed-review:review"),
    ], { account: controller.account });
    await assert.rejects(escrow.write.prepareSettlement([{
      attemptId,
      attemptNumber: 1,
      minBridgeAmount: 1n,
      sourceSwapDeadline: 9_999_999_999n,
      payee: agent.account.address,
      payoutToken: token.address,
      minPayoutAmount: 1n,
      payoutDeadline: 9_999_999_999n,
    }], { account: controller.account }));
    assert.equal((await escrow.read.reservations([attemptId]))[3], 1);
    assert.equal(await escrow.read.settlementMessageIds([attemptId]), `0x${"00".repeat(32)}`);
  });
});
