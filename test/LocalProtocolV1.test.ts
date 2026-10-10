import assert from "node:assert/strict";
import { describe, it } from "node:test";

import { network } from "hardhat";
import { keccak256, stringToHex } from "viem";

const id = (value: string) => keccak256(stringToHex(value));

describe("Local protocol V1 completion contracts", async function () {
  const { viem } = await network.create();

  it("requires a relayed result proof before the coordinator can record review", async function () {
    const [controller, relay, remoteGateway] = await viem.getWalletClients();
    const gateway = await viem.deployContract("ResultProofGateway", [controller.account.address, relay.account.address]);
    const review = await viem.deployContract("CoordinatorReview", [controller.account.address, gateway.address]);
    const routeKey = id("A_TO_B");
    const attemptId = id("attempt:B:1");
    const resultHash = id("result:B:1");
    const proofHash = id("proof:B:1");

    await gateway.write.configureRoute([routeKey, 1, 31338n, remoteGateway.account.address, true], { account: controller.account });
    await assert.rejects(review.write.recordReview([
      attemptId,
      id("criteria:B"),
      resultHash,
      proofHash,
      1,
      id("REVIEW_PASSED"),
      id("review:B:1"),
    ], { account: controller.account }));

    await gateway.write.receiveResultProof([
      id("message:B:1"),
      31338n,
      remoteGateway.account.address,
      routeKey,
      1,
      id("workflow:1"),
      id("node:N1"),
      attemptId,
      resultHash,
      proofHash,
    ], { account: relay.account });
    await review.write.recordReview([
      attemptId,
      id("criteria:B"),
      resultHash,
      proofHash,
      1,
      id("REVIEW_PASSED"),
      id("review:B:1"),
    ], { account: controller.account });

    assert.equal((await gateway.read.proofs([attemptId]))[9], true);
    assert.equal((await review.read.reviews([attemptId]))[5], 1);
  });

  it("caps attempt Gas charges and refunds the unused deposit", async function () {
    const [controller, agent, relayer] = await viem.getWalletClients();
    const policy = await viem.deployContract("FeePolicy", [controller.account.address]);
    const vault = await viem.deployContract("ResultRelayGasVault", [controller.account.address, policy.address]);
    const routeKey = id("A_TO_B");
    const attemptId = id("attempt:B:gas");
    const caps = [100n, 200n, 300n, 400n, 500n, 600n, 700n] as const;
    const publicClient = await viem.getPublicClient();
    const block = await publicClient.getBlock();
    await policy.write.configurePolicy([routeKey, 1, caps, block.timestamp + 3600n], { account: controller.account });
    await vault.write.depositForAttempt([attemptId, routeKey, 1], { account: agent.account, value: 1_000n });
    await vault.write.chargeAttempt([attemptId, id("fee:result-relay"), 2, relayer.account.address, 250n], { account: controller.account });
    await assert.rejects(vault.write.chargeAttempt([attemptId, id("fee:too-high"), 2, relayer.account.address, 301n], { account: controller.account }));
    await assert.rejects(vault.write.refundAttempt([attemptId], { account: controller.account }));
    await vault.write.markAttemptTerminal([attemptId], { account: controller.account });
    await vault.write.refundAttempt([attemptId], { account: controller.account });

    const deposit = await vault.read.deposits([attemptId]);
    assert.equal(deposit[4], 250n);
    assert.equal(deposit[5], 750n);
    assert.equal(deposit[6], 2);
  });

  it("keeps settlement pending until destination payment and source confirmation", async function () {
    const [coordinator, relay, payer, agent, treasury] = await viem.getWalletClients();
    const sourceToken = await viem.deployContract("MockUSDC");
    const destinationToken = await viem.deployContract("MockUSDC");
    const engine = await viem.deployContract("WorkflowEngine", [coordinator.account.address]);
    const gateway = await viem.deployContract("ResultProofGateway", [coordinator.account.address, relay.account.address]);
    const review = await viem.deployContract("CoordinatorReview", [coordinator.account.address, gateway.address]);
    const escrow = await viem.deployContract("TaskEscrow", [coordinator.account.address, treasury.account.address]);
    const sender = await viem.deployContract("DynamicSettlementSender", [
      coordinator.account.address,
      relay.account.address,
      escrow.address,
      sourceToken.address,
    ]);
    const receiver = await viem.deployContract("DynamicSettlementReceiver", [
      coordinator.account.address,
      relay.account.address,
      destinationToken.address,
    ]);
    await sender.write.configureRoute([31338n, receiver.address, destinationToken.address, true], { account: coordinator.account });
    await receiver.write.configureSource([31337n, sender.address, true], { account: coordinator.account });

    const workflowId = id("workflow:settlement");
    const nodeId = id("node:settlement");
    const attemptId = id("attempt:settlement");
    const messageId = id("api:settlement-message");
    const budget = 20_000_000n;
    const reward = 4_200_000n;
    const routeKey = id("A_TO_B");
    const resultHash = id("result:settlement");
    const proofHash = id("proof:settlement");
    await escrow.write.configureProtocol([engine.address, review.address, sourceToken.address], { account: coordinator.account });
    await engine.write.createWorkflow([workflowId, payer.account.address, id("terms:settlement")], { account: coordinator.account });
    await engine.write.registerNode([workflowId, nodeId, id("criteria:settlement"), []], { account: coordinator.account });
    await engine.write.assignNode([nodeId, attemptId, routeKey, 1, agent.account.address], { account: coordinator.account });
    await gateway.write.configureRoute([routeKey, 1, 31338n, relay.account.address, true], { account: coordinator.account });
    await sourceToken.write.mint([payer.account.address, budget], { account: payer.account });
    await sourceToken.write.approve([escrow.address, budget], { account: payer.account });
    await escrow.write.lockBudget([workflowId, id("terms:settlement"), sourceToken.address, budget, 1_100_000n], { account: payer.account });
    await escrow.write.reserve([workflowId, nodeId, attemptId, reward], { account: coordinator.account });
    await gateway.write.receiveResultProof([id("result-message:settlement"), 31338n, relay.account.address, routeKey, 1, workflowId, nodeId, attemptId, resultHash, proofHash], { account: relay.account });
    await review.write.recordReview([attemptId, id("criteria:settlement"), resultHash, proofHash, 1, id("PASS"), id("review:settlement")], { account: coordinator.account });
    await engine.write.markSucceeded([nodeId, attemptId], { account: coordinator.account });
    await escrow.write.initiateCrossChainPaymentWithMessageId([attemptId, sender.address, 31338n, agent.account.address, messageId], { account: coordinator.account });

    assert.equal(await escrow.read.settlementMessageIds([attemptId]), messageId);
    assert.equal((await escrow.read.reservations([attemptId]))[3], 4);
    assert.equal(await sourceToken.read.balanceOf([sender.address]), reward);
    assert.equal(await destinationToken.read.balanceOf([agent.account.address]), 0n);
    await assert.rejects(escrow.write.confirmCrossChainPayment([attemptId, agent.account.address], { account: coordinator.account }));

    await sender.write.commitSettlement([messageId], { account: relay.account });
    await assert.rejects(escrow.write.cancelCrossChainPayment([attemptId], { account: coordinator.account }));
    await receiver.write.receiveSettlement([
      messageId,
      31337n,
      sender.address,
      workflowId,
      attemptId,
      destinationToken.address,
      agent.account.address,
      reward,
    ], { account: relay.account });
    assert.equal(await destinationToken.read.balanceOf([agent.account.address]), reward);
    await sender.write.confirmSettlement([messageId, id("destination:tx")], { account: relay.account });
    await escrow.write.confirmCrossChainPayment([attemptId, agent.account.address], { account: coordinator.account });

    assert.equal((await escrow.read.reservations([attemptId]))[3], 3);
    assert.equal((await escrow.read.workflowAccounts([workflowId]))[5], reward);
  });

  it("classifies failure responsibility and refunds remaining source Gas", async function () {
    const [controller, payer] = await viem.getWalletClients();
    const classifier = await viem.deployContract("FailureClassifier", [controller.account.address]);
    const treasury = await viem.deployContract("GasTreasuryA", [controller.account.address]);
    const engine = await viem.deployContract("WorkflowEngine", [controller.account.address]);
    const reason = id("RPC_FAILURE");
    await classifier.write.configure([reason, 2, 2, true, false, true], { account: controller.account });
    const rule = await classifier.read.classify([reason]);
    assert.equal(rule.responsibleParty, 2);
    assert.equal(rule.retryable, true);
    assert.equal(rule.routePaused, true);

    const workflowId = id("workflow:failed");
    const nodeId = id("node:failed");
    const attemptId = id("attempt:failed");
    await treasury.write.configureWorkflowEngine([engine.address], { account: controller.account });
    await engine.write.createWorkflow([workflowId, payer.account.address, id("terms:gas")], { account: controller.account });
    await engine.write.registerNode([workflowId, nodeId, id("criteria:gas"), []], { account: controller.account });
    await treasury.write.depositSourceGas([workflowId], { account: payer.account, value: 10_000n });
    await assert.rejects(treasury.write.refund([workflowId], { account: payer.account }));
    await engine.write.assignNode([nodeId, attemptId, id("route:gas"), 1, payer.account.address], { account: controller.account });
    await engine.write.markSucceeded([nodeId, attemptId], { account: controller.account });
    await engine.write.markSettled([nodeId, attemptId], { account: controller.account });
    await treasury.write.refund([workflowId], { account: controller.account });
    const account = await treasury.read.accounts([workflowId]);
    assert.equal(account[1], 10_000n);
    assert.equal(account[3], 10_000n);
    assert.equal(account[4], true);
  });
});
