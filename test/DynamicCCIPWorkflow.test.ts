import assert from "node:assert/strict";
import { describe, it } from "node:test";

import { network } from "hardhat";
import { keccak256, stringToHex } from "viem";

const id = (value: string) => keccak256(stringToHex(value));

describe("Dynamic CCIP workflow settlement", async function () {
  const { viem } = await network.create();

  it("carries workflow and attempt identity through the official CCIP Router interface", async function () {
    const [controller, payee] = await viem.getWalletClients();
    const sourceSelector = 16_000n;
    const destinationSelector = 17_000n;
    const destinationChainId = 421_614n;
    const fee = 10_000n;
    const amount = 4_200_000n;
    const token = await viem.deployContract("MockUSDC");
    const router = await viem.deployContract("MockRealCCIPRouter", [sourceSelector, fee]);
    const receiver = await viem.deployContract("DynamicCCIPWorkflowReceiver", [router.address]);
    const sender = await viem.deployContract("DynamicCCIPWorkflowSender", [
      controller.account.address,
      controller.account.address,
      router.address,
      token.address,
      300_000n,
    ]);
    await receiver.write.configureSource([sourceSelector, sender.address, true], { account: controller.account });
    await sender.write.configureRoute([
      destinationChainId,
      destinationSelector,
      receiver.address,
      token.address,
      true,
    ], { account: controller.account });
    await token.write.mint([sender.address, amount], { account: controller.account });
    await controller.sendTransaction({ to: sender.address, value: fee });

    const workflowId = id("workflow:dynamic-ccip");
    const attemptId = id("attempt:dynamic-ccip");
    const quoted = await sender.read.quoteFee([
      workflowId,
      attemptId,
      destinationChainId,
      payee.account.address,
      amount,
    ]);
    assert.equal(quoted, fee);

    const transactionHash = await sender.write.sendSettlement([
      workflowId,
      attemptId,
      destinationChainId,
      payee.account.address,
      amount,
    ], { account: controller.account });
    const publicClient = await viem.getPublicClient();
    const receipt = await publicClient.waitForTransactionReceipt({ hash: transactionHash });
    const events = await receiver.getEvents.CCIPWorkflowSettlementReceived();
    const received = events.at(-1)!;
    assert.equal(received.args.workflowId, workflowId);
    assert.equal(received.args.attemptId, attemptId);
    assert.equal(await token.read.balanceOf([payee.account.address]), amount);
    assert.equal(await receiver.read.settlementMessageByAttempt([attemptId]), received.args.messageId);

    await sender.write.confirmSettlement([received.args.messageId!, receipt.transactionHash], { account: controller.account });
    assert.equal(await sender.read.isConfirmed([received.args.messageId!]), true);
  });

  it("moves an approved TaskEscrow reservation through CCIP using the Router messageId", async function () {
    const [controller, payer, payee, treasury] = await viem.getWalletClients();
    const sourceSelector = 18_000n;
    const destinationSelector = 19_000n;
    const destinationChainId = 845_32n;
    const fee = 12_000n;
    const amount = 3_500_000n;
    const token = await viem.deployContract("MockUSDC");
    const router = await viem.deployContract("MockRealCCIPRouter", [sourceSelector, fee]);
    const receiver = await viem.deployContract("DynamicCCIPWorkflowReceiver", [router.address]);
    const engine = await viem.deployContract("WorkflowEngine", [controller.account.address]);
    const gateway = await viem.deployContract("ResultProofGateway", [controller.account.address, controller.account.address]);
    const review = await viem.deployContract("CoordinatorReview", [controller.account.address, gateway.address]);
    const escrow = await viem.deployContract("TaskEscrow", [controller.account.address, treasury.account.address]);
    const sender = await viem.deployContract("DynamicCCIPWorkflowSender", [
      controller.account.address,
      escrow.address,
      router.address,
      token.address,
      300_000n,
    ]);
    await escrow.write.configureProtocol([engine.address, review.address, token.address], { account: controller.account });
    await receiver.write.configureSource([sourceSelector, sender.address, true], { account: controller.account });
    await sender.write.configureRoute([
      destinationChainId,
      destinationSelector,
      receiver.address,
      token.address,
      true,
    ], { account: controller.account });

    const workflowId = id("workflow:escrow-ccip");
    const nodeId = id("node:escrow-ccip");
    const attemptId = id("attempt:escrow-ccip");
    const routeKey = id("route:escrow-ccip");
    const resultHash = id("result:escrow-ccip");
    const proofHash = id("proof:escrow-ccip");
    await engine.write.createWorkflow([workflowId, payer.account.address, id("terms:escrow-ccip")], { account: controller.account });
    await engine.write.registerNode([workflowId, nodeId, id("criteria:escrow-ccip"), []], { account: controller.account });
    await engine.write.assignNode([nodeId, attemptId, routeKey, 1, payee.account.address], { account: controller.account });
    await token.write.mint([payer.account.address, amount], { account: controller.account });
    await token.write.approve([escrow.address, amount], { account: payer.account });
    await escrow.write.lockBudget([workflowId, id("terms:escrow-ccip"), token.address, amount, 0n], { account: payer.account });
    await escrow.write.reserve([workflowId, nodeId, attemptId, amount], { account: controller.account });
    await gateway.write.configureRoute([routeKey, 1, destinationChainId, receiver.address, true], { account: controller.account });
    await gateway.write.receiveResultProof([
      id("result-message:escrow-ccip"),
      destinationChainId,
      receiver.address,
      routeKey,
      1,
      workflowId,
      nodeId,
      attemptId,
      resultHash,
      proofHash,
    ], { account: controller.account });
    await review.write.recordReview([
      attemptId,
      id("criteria:escrow-ccip"),
      resultHash,
      proofHash,
      1,
      id("PASS"),
      id("review:escrow-ccip"),
    ], { account: controller.account });
    await engine.write.markSucceeded([nodeId, attemptId], { account: controller.account });

    await controller.sendTransaction({ to: sender.address, value: fee });
    const initiationTx = await escrow.write.initiateCrossChainPayment([
      attemptId,
      sender.address,
      destinationChainId,
      payee.account.address,
    ], { account: controller.account });
    const publicClient = await viem.getPublicClient();
    const receipt = await publicClient.waitForTransactionReceipt({ hash: initiationTx });
    const messageId = await escrow.read.settlementMessageIds([attemptId]);

    assert.notEqual(messageId, id("application-message-id"));
    assert.equal(await receiver.read.settlementMessageByAttempt([attemptId]), messageId);
    assert.equal(await token.read.balanceOf([payee.account.address]), amount);
    await sender.write.confirmSettlement([messageId, receipt.transactionHash], { account: controller.account });
    await escrow.write.confirmCrossChainPayment([attemptId, payee.account.address], { account: controller.account });
    assert.equal((await escrow.read.reservations([attemptId]))[3], 3);
  });
});
