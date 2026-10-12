import assert from "node:assert/strict";
import { describe, it } from "node:test";

import { network } from "hardhat";
import { keccak256, stringToHex } from "viem";

const id = (value: string) => keccak256(stringToHex(value));

describe("Dynamic local V1 contracts", async function () {
  const { viem } = await network.create();

  it("versions A-to-B/C/D routes without mutating historical versions", async function () {
    const [controller, gateway, boardB, boardC, boardD, payout, token] =
      await viem.getWalletClients();
    const registry = await viem.deployContract("ChainRouteRegistry", [
      controller.account.address,
    ]);
    const routeB = id("A_TO_B");
    const routeC = id("A_TO_C");
    const routeD = id("A_TO_D");

    await registry.write.registerRoute(
      [
        routeB,
        31337n,
        31338n,
        gateway.account.address,
        boardB.account.address,
        payout.account.address,
        token.account.address,
      ],
      { account: controller.account },
    );
    await registry.write.registerRoute(
      [
        routeC,
        31337n,
        31339n,
        gateway.account.address,
        boardC.account.address,
        payout.account.address,
        token.account.address,
      ],
      { account: controller.account },
    );
    await registry.write.registerRoute(
      [
        routeD,
        31337n,
        31340n,
        gateway.account.address,
        boardD.account.address,
        payout.account.address,
        token.account.address,
      ],
      { account: controller.account },
    );
    await registry.write.registerRoute(
      [
        routeB,
        31337n,
        31338n,
        gateway.account.address,
        boardB.account.address,
        payout.account.address,
        token.account.address,
      ],
      { account: controller.account },
    );

    assert.equal(await registry.read.latestVersion([routeB]), 2);
    assert.equal(
      (await registry.read.getRoute([routeB, 1])).destinationChainId,
      31338n,
    );
    assert.equal((await registry.read.getRoute([routeB, 2])).routeVersion, 2);
    assert.equal(
      (await registry.read.getRoute([routeC, 1])).destinationChainId,
      31339n,
    );
    assert.equal(
      (await registry.read.getRoute([routeD, 1])).destinationChainId,
      31340n,
    );

    await registry.write.setRouteActive([routeB, 1, false], {
      account: controller.account,
    });
    assert.equal((await registry.read.getRoute([routeB, 1])).active, false);
    assert.equal(
      (await registry.read.getLatestActiveRoute([routeB])).routeVersion,
      2,
    );
  });

  it("unlocks dependencies only after SETTLED and rejects stale attempts", async function () {
    const [coordinator, requester, agentB, agentC, agentD] =
      await viem.getWalletClients();
    const engine = await viem.deployContract("WorkflowEngine", [
      coordinator.account.address,
    ]);
    const workflowId = id("WF-1001");
    const n1 = id("WF-1001:N1");
    const n2 = id("WF-1001:N2");
    const attemptB = id("N1:attempt:1");
    const attemptC = id("N2:attempt:1");
    const attemptD = id("N2:attempt:2");
    const routeB = id("A_TO_B");
    const routeC = id("A_TO_C");
    const routeD = id("A_TO_D");

    await engine.write.createWorkflow(
      [workflowId, requester.account.address, id("terms")],
      { account: coordinator.account },
    );
    await engine.write.registerNode([workflowId, n1, id("criteria:N1"), []], {
      account: coordinator.account,
    });
    await engine.write.registerNode(
      [workflowId, n2, id("criteria:N2"), [n1]],
      { account: coordinator.account },
    );
    assert.equal((await engine.read.nodes([n1]))[7], 2);
    assert.equal((await engine.read.nodes([n2]))[7], 1);

    await engine.write.assignNode(
      [n1, attemptB, routeB, 1, agentB.account.address],
      { account: coordinator.account },
    );
    await engine.write.markSucceeded([n1, attemptB], {
      account: coordinator.account,
    });
    assert.equal((await engine.read.nodes([n1]))[7], 4);
    assert.equal((await engine.read.nodes([n2]))[7], 1);

    await engine.write.markSettled([n1, attemptB], {
      account: coordinator.account,
    });
    assert.equal((await engine.read.nodes([n1]))[7], 5);
    assert.equal((await engine.read.nodes([n2]))[7], 2);

    await engine.write.assignNode(
      [n2, attemptC, routeC, 1, agentC.account.address],
      { account: coordinator.account },
    );
    await engine.write.markAttemptFailed([n2, attemptC], {
      account: coordinator.account,
    });
    await engine.write.assignNode(
      [n2, attemptD, routeD, 1, agentD.account.address],
      { account: coordinator.account },
    );
    await assert.rejects(
      engine.write.markSucceeded([n2, attemptC], {
        account: coordinator.account,
      }),
    );
    await engine.write.markSucceeded([n2, attemptD], {
      account: coordinator.account,
    });
    await engine.write.markSettled([n2, attemptD], {
      account: coordinator.account,
    });
    const workflow = await engine.read.workflows([workflowId]);
    assert.equal(workflow[2], 2);
    assert.equal(workflow[3], 2);
    assert.equal(workflow[4], false);
  });

  it("releases C reservation, pays B and D, charges fee, and refunds 7.90", async function () {
    const [coordinator, payer, agentB, agentD, treasury] =
      await viem.getWalletClients();
    const token = await viem.deployContract("MockUSDC");
    const engine = await viem.deployContract("WorkflowEngine", [coordinator.account.address]);
    const gateway = await viem.deployContract("ResultProofGateway", [coordinator.account.address, coordinator.account.address]);
    const review = await viem.deployContract("CoordinatorReview", [coordinator.account.address, gateway.address]);
    const escrow = await viem.deployContract("TaskEscrow", [
      coordinator.account.address,
      treasury.account.address,
    ]);
    const workflowId = id("WF-1001");
    const n1 = id("WF-1001:N1");
    const n2 = id("WF-1001:N2");
    const attemptB = id("N1:attempt:1");
    const attemptC = id("N2:attempt:1");
    const attemptD = id("N2:attempt:2");
    const budget = 20_000_000n;
    const bReward = 4_200_000n;
    const dReward = 6_800_000n;
    const fee = 1_100_000n;
    const refund = 7_900_000n;
    const routeKey = id("A_TO_LOCAL");
    await escrow.write.configureProtocol([engine.address, review.address, token.address], { account: coordinator.account });
    await engine.write.createWorkflow([workflowId, payer.account.address, id("terms")], { account: coordinator.account });
    await engine.write.registerNode([workflowId, n1, id("criteria:N1"), []], { account: coordinator.account });
    await engine.write.registerNode([workflowId, n2, id("criteria:N2"), [n1]], { account: coordinator.account });
    await gateway.write.configureRoute([routeKey, 1, 31338n, coordinator.account.address, true], { account: coordinator.account });

    const approveAttempt = async (nodeId: `0x${string}`, attemptId: `0x${string}`, suffix: string) => {
      const resultHash = id(`result:${suffix}`);
      const proofHash = id(`proof:${suffix}`);
      await gateway.write.receiveResultProof([id(`message:${suffix}`), 31338n, coordinator.account.address, routeKey, 1, workflowId, nodeId, attemptId, resultHash, proofHash], { account: coordinator.account });
      await review.write.recordReview([attemptId, id(`criteria:${suffix}`), resultHash, proofHash, 1, id("PASS"), id(`review:${suffix}`)], { account: coordinator.account });
      await engine.write.markSucceeded([nodeId, attemptId], { account: coordinator.account });
    };

    await token.write.mint([payer.account.address, budget], {
      account: payer.account,
    });
    await token.write.approve([escrow.address, budget], {
      account: payer.account,
    });
    await escrow.write.lockBudget(
      [workflowId, id("terms"), token.address, budget, fee],
      { account: payer.account },
    );
    await engine.write.assignNode([n1, attemptB, routeKey, 1, agentB.account.address], { account: coordinator.account });
    await escrow.write.reserve([workflowId, n1, attemptB, bReward], {
      account: coordinator.account,
    });
    await approveAttempt(n1, attemptB, "B");
    await escrow.write.payReservation([attemptB, agentB.account.address], {
      account: coordinator.account,
    });
    await engine.write.markSettled([n1, attemptB], { account: coordinator.account });
    await engine.write.assignNode([n2, attemptC, routeKey, 1, agentD.account.address], { account: coordinator.account });
    await escrow.write.reserve([workflowId, n2, attemptC, dReward], {
      account: coordinator.account,
    });
    await escrow.write.releaseReservation([attemptC], {
      account: coordinator.account,
    });
    await engine.write.markAttemptFailed([n2, attemptC], { account: coordinator.account });
    await engine.write.assignNode([n2, attemptD, routeKey, 1, agentD.account.address], { account: coordinator.account });
    await escrow.write.reserve([workflowId, n2, attemptD, dReward], {
      account: coordinator.account,
    });
    await approveAttempt(n2, attemptD, "D");
    await escrow.write.payReservation([attemptD, agentD.account.address], {
      account: coordinator.account,
    });
    await engine.write.markSettled([n2, attemptD], { account: coordinator.account });
    await escrow.write.payPlatformFee([workflowId, fee], {
      account: coordinator.account,
    });
    assert.equal(await escrow.read.availableBalance([workflowId]), refund);
    await escrow.write.refundRemainder([workflowId], {
      account: coordinator.account,
    });

    assert.equal(await token.read.balanceOf([agentB.account.address]), bReward);
    assert.equal(await token.read.balanceOf([agentD.account.address]), dReward);
    assert.equal(await token.read.balanceOf([treasury.account.address]), fee);
    assert.equal(await token.read.balanceOf([payer.account.address]), refund);
    assert.equal(await token.read.balanceOf([escrow.address]), 0n);
    const account = await escrow.read.workflowAccounts([workflowId]);
    assert.equal(account[5], 11_000_000n);
    assert.equal(account[7], fee);
    assert.equal(account[8], refund);
    assert.equal(account[9], 2);
    assert.equal((await escrow.read.reservations([attemptC]))[3], 2);
  });

  it("enforces routeVersion, relay replay protection, and cancelled-attempt rejection", async function () {
    const [relay, gateway, agentC, agentD, stranger] =
      await viem.getWalletClients();
    const board = await viem.deployContract("RemoteWorkBoard", [
      31340n,
      31337n,
      relay.account.address,
    ]);
    const identities = await viem.deployContract("AgentIdentityRegistry", [relay.account.address]);
    await board.write.configureIdentityRegistry([identities.address], { account: relay.account });
    const routeD = id("A_TO_D");
    const workflowId = id("WF-1001");
    const n2 = id("WF-1001:N2");
    const attemptC = id("N2:attempt:1");
    const attemptD = id("N2:attempt:2");
    const dispatchC = id("dispatch:C");
    const dispatchD = id("dispatch:D");
    const didC = id("did:agent:C");
    const didD = id("did:agent:D");
    await identities.write.registerOrUpdate([didC, agentC.account.address, 31340n, id("provider"), id("verification"), true], { account: relay.account });
    await identities.write.registerOrUpdate([didD, agentD.account.address, 31340n, id("provider"), id("verification"), true], { account: relay.account });

    await board.write.configureRoute(
      [routeD, 1, gateway.account.address, true],
      { account: relay.account },
    );
    await assert.rejects(
      board.write.receiveAssignment(
        [[
          dispatchC,
          31337n,
          stranger.account.address,
          workflowId,
          n2,
          attemptC,
          1,
          didC,
          routeD,
          1,
          id("criteria:N2"),
          agentC.account.address,
          6_800_000n,
        ]],
        { account: relay.account },
      ),
    );
    await board.write.receiveAssignment(
      [[
        dispatchC,
        31337n,
        gateway.account.address,
        workflowId,
        n2,
        attemptC,
        1,
        didC,
        routeD,
        1,
        id("criteria:N2"),
        agentC.account.address,
        6_800_000n,
      ]],
      { account: relay.account },
    );
    await assert.rejects(
      board.write.receiveAssignment(
          [[
          dispatchC,
          31337n,
          gateway.account.address,
          workflowId,
          n2,
            attemptC,
            1,
            didC,
          routeD,
          1,
          id("criteria:N2"),
          agentC.account.address,
            6_800_000n,
          ]],
        { account: relay.account },
      ),
    );
    await board.write.cancelAttempt([attemptC], { account: relay.account });
    await assert.rejects(
      board.write.submitResult(
        [id("result:C:late"), attemptC, id("result:C"), id("proof:C")],
        { account: agentC.account },
      ),
    );

    await board.write.receiveAssignment(
      [[
        dispatchD,
        31337n,
        gateway.account.address,
        workflowId,
        n2,
        attemptD,
        2,
        didD,
        routeD,
        1,
        id("criteria:N2"),
        agentD.account.address,
        6_800_000n,
      ]],
      { account: relay.account },
    );
    await board.write.startWork([attemptD], { account: agentD.account });
    await board.write.submitResult(
      [id("result:D"), attemptD, id("payload:D"), id("proof:D")],
      { account: agentD.account },
    );
    assert.equal((await board.read.assignments([attemptD]))[8], 3);
    assert.equal(await board.read.processedMessages([id("result:D")]), true);
  });
});
