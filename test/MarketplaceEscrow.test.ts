import assert from "node:assert/strict";
import { describe, it } from "node:test";

import { network } from "hardhat";
import { zeroAddress } from "viem";

describe("MarketplaceEscrow", async function () {
  const { viem } = await network.create();

  const settlementId =
    "0x1111111111111111111111111111111111111111111111111111111111111111";
  const secondSettlementId =
    "0x2222222222222222222222222222222222222222222222222222222222222222";
  const orderId =
    "0xaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa";
  const termsHash =
    "0xbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbb";
  const budget = 1_000_000n;
  const paid = 700_000n;
  const refund = 300_000n;

  async function deployFixture() {
    const [payer, payee, platformSigner, stranger] =
      await viem.getWalletClients();

    const token = await viem.deployContract("MockUSDC");
    const escrow = await viem.deployContract("MarketplaceEscrow", [
      platformSigner.account.address,
      platformSigner.account.address,
      3478487238524512106n,
      payee.account.address,
    ]);

    await token.write.mint([payer.account.address, budget], {
      account: payer.account,
    });
    await token.write.approve([escrow.address, budget], {
      account: payer.account,
    });

    return { payer, payee, platformSigner, stranger, token, escrow };
  }

  it("locks funds and records settlement details", async function () {
    const { payer, payee, token, escrow } = await deployFixture();

    await escrow.write.createAndLock(
      [
        settlementId,
        orderId,
        termsHash,
        payee.account.address,
        token.address,
        budget,
      ],
      { account: payer.account },
    );

    const escrowBalance = await token.read.balanceOf([escrow.address]);
    const payerBalance = await token.read.balanceOf([payer.account.address]);
    const settlement = await escrow.read.settlements([settlementId]);

    assert.equal(escrowBalance, budget);
    assert.equal(payerBalance, 0n);
    assert.equal(settlement[0], orderId);
    assert.equal(settlement[1], termsHash);
    assert.equal(settlement[2].toLowerCase(), payer.account.address);
    assert.equal(settlement[3].toLowerCase(), payee.account.address);
    assert.equal(settlement[4].toLowerCase(), token.address);
    assert.equal(settlement[5], budget);
    assert.equal(settlement[6], 0n);
    assert.equal(settlement[7], 2);
  });

  it("releases a partial payment and refunds the remainder", async function () {
    const { payer, payee, token, escrow } = await deployFixture();

    await escrow.write.createAndLock(
      [
        settlementId,
        orderId,
        termsHash,
        payee.account.address,
        token.address,
        budget,
      ],
      { account: payer.account },
    );

    await escrow.write.release([settlementId, paid], {
      account: payer.account,
    });

    const escrowBalance = await token.read.balanceOf([escrow.address]);
    const payerBalance = await token.read.balanceOf([payer.account.address]);
    const payeeBalance = await token.read.balanceOf([payee.account.address]);
    const settlement = await escrow.read.settlements([settlementId]);

    assert.equal(payeeBalance, paid);
    assert.equal(payerBalance, refund);
    assert.equal(escrowBalance, 0n);
    assert.equal(settlement[6], paid);
    assert.equal(settlement[7], 4);
  });

  it("allows the platform signer to release funds", async function () {
    const { payer, payee, platformSigner, token, escrow } =
      await deployFixture();

    await escrow.write.createAndLock(
      [
        settlementId,
        orderId,
        termsHash,
        payee.account.address,
        token.address,
        budget,
      ],
      { account: payer.account },
    );

    await escrow.write.release([settlementId, paid], {
      account: platformSigner.account,
    });

    assert.equal(await token.read.balanceOf([payee.account.address]), paid);
    assert.equal(await token.read.balanceOf([payer.account.address]), refund);
  });

  it("refunds the full budget", async function () {
    const { payer, payee, token, escrow } = await deployFixture();

    await escrow.write.createAndLock(
      [
        settlementId,
        orderId,
        termsHash,
        payee.account.address,
        token.address,
        budget,
      ],
      { account: payer.account },
    );

    await escrow.write.refund([settlementId], { account: payer.account });

    const settlement = await escrow.read.settlements([settlementId]);

    assert.equal(await token.read.balanceOf([payer.account.address]), budget);
    assert.equal(await token.read.balanceOf([payee.account.address]), 0n);
    assert.equal(await token.read.balanceOf([escrow.address]), 0n);
    assert.equal(settlement[7], 6);
  });

  it("rejects unauthorized release and refund attempts", async function () {
    const { payer, payee, stranger, token, escrow } = await deployFixture();

    await escrow.write.createAndLock(
      [
        settlementId,
        orderId,
        termsHash,
        payee.account.address,
        token.address,
        budget,
      ],
      { account: payer.account },
    );

    await assert.rejects(
      escrow.write.release([settlementId, paid], {
        account: stranger.account,
      }),
    );
    await assert.rejects(
      escrow.write.refund([settlementId], {
        account: stranger.account,
      }),
    );
  });

  it("rejects invalid amounts and duplicate settlement ids", async function () {
    const { payer, payee, token, escrow } = await deployFixture();

    await assert.rejects(
      escrow.write.createAndLock(
        [
          settlementId,
          orderId,
          termsHash,
          payee.account.address,
          token.address,
          0n,
        ],
        { account: payer.account },
      ),
    );
    await assert.rejects(
      escrow.write.createAndLock(
        [settlementId, orderId, termsHash, zeroAddress, token.address, budget],
        { account: payer.account },
      ),
    );
    await assert.rejects(
      escrow.write.createAndLock(
        [
          settlementId,
          orderId,
          termsHash,
          payee.account.address,
          zeroAddress,
          budget,
        ],
        { account: payer.account },
      ),
    );

    await escrow.write.createAndLock(
      [
        secondSettlementId,
        orderId,
        termsHash,
        payee.account.address,
        token.address,
        budget,
      ],
      { account: payer.account },
    );

    await assert.rejects(
      escrow.write.createAndLock(
        [
          secondSettlementId,
          orderId,
          termsHash,
          payee.account.address,
          token.address,
          budget,
        ],
        { account: payer.account },
      ),
    );
    await assert.rejects(
      escrow.write.release([secondSettlementId, budget + 1n], {
        account: payer.account,
      }),
    );
  });

  it("rejects repeated releases", async function () {
    const { payer, payee, token, escrow } = await deployFixture();

    await escrow.write.createAndLock(
      [
        settlementId,
        orderId,
        termsHash,
        payee.account.address,
        token.address,
        budget,
      ],
      { account: payer.account },
    );

    await escrow.write.release([settlementId, paid], {
      account: payer.account,
    });

    await assert.rejects(
      escrow.write.release([settlementId, paid], {
        account: payer.account,
      }),
    );
  });
});
