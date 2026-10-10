import assert from "node:assert/strict";
import { describe, it } from "node:test";

import { network } from "hardhat";
import { keccak256, stringToHex } from "viem";

const id = (value: string) => keccak256(stringToHex(value));

const reviewTypes = {
  ReviewApproval: [
    { name: "workflowId", type: "bytes32" },
    { name: "nodeId", type: "bytes32" },
    { name: "attemptId", type: "bytes32" },
    { name: "resultHash", type: "bytes32" },
    { name: "reviewPolicyHash", type: "bytes32" },
    { name: "decision", type: "uint8" },
    { name: "evidenceHash", type: "bytes32" },
    { name: "notesHash", type: "bytes32" },
    { name: "nonce", type: "bytes32" },
    { name: "deadline", type: "uint256" },
  ],
} as const;

const arbitrationTypes = {
  ArbitrationVote: [
    { name: "workflowId", type: "bytes32" },
    { name: "nodeId", type: "bytes32" },
    { name: "attemptId", type: "bytes32" },
    { name: "challengeId", type: "bytes32" },
    { name: "resultHash", type: "bytes32" },
    { name: "resolution", type: "uint8" },
    { name: "reasonHash", type: "bytes32" },
    { name: "nonce", type: "bytes32" },
    { name: "deadline", type: "uint256" },
  ],
} as const;

describe("ReviewManagerV2", async function () {
  const { viem, networkHelpers } = await network.create();

  it("uses block time to prevent settlement before an unchallenged review expires", async function () {
    const [controller, requester, reviewerOne, reviewerTwo] = await viem.getWalletClients();
    const publicClient = await viem.getPublicClient();
    const manager = await viem.deployContract("ReviewManagerV2", [controller.account.address]);
    await manager.write.configureReviewer([reviewerOne.account.address, true], { account: controller.account });
    await manager.write.configureReviewer([reviewerTwo.account.address, true], { account: controller.account });

    const workflowId = id("review-v2-workflow");
    const nodeId = id("review-v2-node");
    const attemptId = id("review-v2-attempt");
    const resultHash = id("review-v2-result");
    const policyHash = id("review-v2-policy");
    await manager.write.createReview([
      attemptId,
      workflowId,
      nodeId,
      resultHash,
      policyHash,
      requester.account.address,
      2,
      60n,
      2,
    ], { account: controller.account });

    const chainId = await publicClient.getChainId();
    const block = await publicClient.getBlock();
    const deadline = block.timestamp + 600n;
    const domain = {
      name: "AgentCrosschainReview",
      version: "2",
      chainId,
      verifyingContract: manager.address,
    } as const;
    const sign = (reviewer: typeof reviewerOne, label: string) => reviewer.signTypedData({
      account: reviewer.account,
      domain,
      primaryType: "ReviewApproval",
      types: reviewTypes,
      message: {
        workflowId,
        nodeId,
        attemptId,
        resultHash,
        reviewPolicyHash: policyHash,
        decision: 1,
        evidenceHash: id(`evidence:${label}`),
        notesHash: id(`notes:${label}`),
        nonce: id(`nonce:${label}`),
        deadline,
      },
    });
    const signatureOne = await sign(reviewerOne, "one");
    await manager.write.submitApproval([
      attemptId, 1, id("evidence:one"), id("notes:one"), id("nonce:one"), deadline, signatureOne,
    ]);
    const signatureTwo = await sign(reviewerTwo, "two");
    await manager.write.submitApproval([
      attemptId, 1, id("evidence:two"), id("notes:two"), id("nonce:two"), deadline, signatureTwo,
    ]);

    assert.equal(await manager.read.isSettlementAllowed([attemptId]), false);
    await assert.rejects(manager.write.finalizeUnchallenged([attemptId]));
    await networkHelpers.time.increase(61);
    await manager.write.finalizeUnchallenged([attemptId]);
    assert.equal(await manager.read.isSettlementAllowed([attemptId]), true);
  });

  it("approves immediately when the policy has no challenge period", async function () {
    const [controller, requester, reviewer] = await viem.getWalletClients();
    const publicClient = await viem.getPublicClient();
    const manager = await viem.deployContract("ReviewManagerV2", [controller.account.address]);
    await manager.write.configureReviewer([reviewer.account.address, true], { account: controller.account });
    const workflowId = id("immediate-workflow");
    const nodeId = id("immediate-node");
    const attemptId = id("immediate-attempt");
    const resultHash = id("immediate-result");
    const policyHash = id("immediate-policy");
    await manager.write.createReview([
      attemptId, workflowId, nodeId, resultHash, policyHash, requester.account.address, 1, 0n, 2,
    ], { account: controller.account });
    const deadline = (await publicClient.getBlock()).timestamp + 600n;
    const signature = await reviewer.signTypedData({
      account: reviewer.account,
      domain: {
        name: "AgentCrosschainReview",
        version: "2",
        chainId: await publicClient.getChainId(),
        verifyingContract: manager.address,
      },
      primaryType: "ReviewApproval",
      types: reviewTypes,
      message: {
        workflowId,
        nodeId,
        attemptId,
        resultHash,
        reviewPolicyHash: policyHash,
        decision: 1,
        evidenceHash: id("immediate-evidence"),
        notesHash: id("immediate-notes"),
        nonce: id("immediate-nonce"),
        deadline,
      },
    });
    await manager.write.submitApproval([
      attemptId, 1, id("immediate-evidence"), id("immediate-notes"), id("immediate-nonce"), deadline, signature,
    ]);
    assert.equal(await manager.read.isSettlementAllowed([attemptId]), true);
    assert.equal((await manager.read.reviews([attemptId]))[11], 4);
  });

  it("requires the configured arbitration threshold and rejects a single-signer decision", async function () {
    const [controller, requester, reviewer, arbitratorOne, arbitratorTwo] = await viem.getWalletClients();
    const publicClient = await viem.getPublicClient();
    const manager = await viem.deployContract("ReviewManagerV2", [controller.account.address]);
    await manager.write.configureReviewer([reviewer.account.address, true], { account: controller.account });
    await manager.write.configureArbitrator([arbitratorOne.account.address, true], { account: controller.account });
    await manager.write.configureArbitrator([arbitratorTwo.account.address, true], { account: controller.account });

    const workflowId = id("dispute-workflow");
    const nodeId = id("dispute-node");
    const attemptId = id("dispute-attempt");
    const resultHash = id("dispute-result");
    const policyHash = id("dispute-policy");
    const challengeId = id("dispute-challenge");
    await manager.write.createReview([
      attemptId, workflowId, nodeId, resultHash, policyHash, requester.account.address, 1, 60n, 2,
    ], { account: controller.account });
    const chainId = await publicClient.getChainId();
    const deadline = (await publicClient.getBlock()).timestamp + 600n;
    const domain = {
      name: "AgentCrosschainReview",
      version: "2",
      chainId,
      verifyingContract: manager.address,
    } as const;
    const approval = await reviewer.signTypedData({
      account: reviewer.account,
      domain,
      primaryType: "ReviewApproval",
      types: reviewTypes,
      message: {
        workflowId,
        nodeId,
        attemptId,
        resultHash,
        reviewPolicyHash: policyHash,
        decision: 1,
        evidenceHash: id("dispute-evidence"),
        notesHash: id("dispute-notes"),
        nonce: id("dispute-review-nonce"),
        deadline,
      },
    });
    await manager.write.submitApproval([
      attemptId, 1, id("dispute-evidence"), id("dispute-notes"), id("dispute-review-nonce"), deadline, approval,
    ]);
    await manager.write.challenge([attemptId, challengeId, id("challenge-evidence")], { account: requester.account });

    const vote = (arbitrator: typeof arbitratorOne, label: string) => arbitrator.signTypedData({
      account: arbitrator.account,
      domain,
      primaryType: "ArbitrationVote",
      types: arbitrationTypes,
      message: {
        workflowId,
        nodeId,
        attemptId,
        challengeId,
        resultHash,
        resolution: 2,
        reasonHash: id(`reason:${label}`),
        nonce: id(`arbitration-nonce:${label}`),
        deadline,
      },
    });
    const voteOne = await vote(arbitratorOne, "one");
    await manager.write.submitArbitrationVote([
      attemptId, challengeId, 2, id("reason:one"), id("arbitration-nonce:one"), deadline, voteOne,
    ]);
    assert.equal((await manager.read.reviews([attemptId]))[11], 3);
    assert.equal(await manager.read.isSettlementAllowed([attemptId]), false);

    const voteTwo = await vote(arbitratorTwo, "two");
    await manager.write.submitArbitrationVote([
      attemptId, challengeId, 2, id("reason:two"), id("arbitration-nonce:two"), deadline, voteTwo,
    ]);
    assert.equal((await manager.read.reviews([attemptId]))[11], 5);
    assert.equal(await manager.read.isSettlementAllowed([attemptId]), false);
  });
});
