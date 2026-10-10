import assert from "node:assert/strict";
import { describe, it } from "node:test";

import { network } from "hardhat";
import { zeroHash } from "viem";

describe("MarketplaceEscrow real CCIP release", async function () {
  const { viem } = await network.create();
  const publicClient = await viem.getPublicClient();

  const settlementId =
    "0x1111111111111111111111111111111111111111111111111111111111111111";
  const orderId =
    "0xaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa";
  const baseTermsHash =
    "0xbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbb";
  const quoteHash =
    "0xcccccccccccccccccccccccccccccccccccccccccccccccccccccccccccccccc";
  const sourceChainSelector = 16015286601757825753n;
  const destinationChainSelector = 3478487238524512106n;
  const budget = 1_000_000n;
  const paid = 700_000n;
  const refund = 300_000n;
  const fee = 123n;
  const gasLimit = 300_000n;

  async function deployFixture(configureReceiver = true) {
    const [deployer, payer, payee, platformSigner, stranger] =
      await viem.getWalletClients();

    const token = await viem.deployContract("MockUSDC");
    const mockSender = await viem.deployContract("CCIPSettlementSender", [
      sourceChainSelector,
      destinationChainSelector,
      deployer.account.address,
      token.address,
      deployer.account.address,
    ]);
    const router = await viem.deployContract("MockRealCCIPRouter", [
      sourceChainSelector,
      fee,
    ]);
    const receiver = await viem.deployContract("RealCCIPSettlementReceiver", [
      router.address,
      sourceChainSelector,
      token.address,
    ]);
    const escrow = await viem.deployContract("MarketplaceEscrow", [
      platformSigner.account.address,
      mockSender.address,
      destinationChainSelector,
      deployer.account.address,
    ]);
    const sender = await viem.deployContract("RealCCIPSettlementSender", [
      router.address,
      destinationChainSelector,
      receiver.address,
      token.address,
      escrow.address,
      gasLimit,
    ]);

    if (configureReceiver) {
      await receiver.write.setAllowedRemoteSender([sender.address], {
        account: deployer.account,
      });
    }
    await escrow.write.setRealCcipConfig(
      [sender.address, destinationChainSelector, receiver.address],
      { account: platformSigner.account },
    );

    await token.write.mint([payer.account.address, budget], {
      account: payer.account,
    });
    await token.write.approve([escrow.address, budget], {
      account: payer.account,
    });

    return {
      payer,
      payee,
      platformSigner,
      stranger,
      token,
      router,
      receiver,
      escrow,
      sender,
    };
  }

  async function createSettlement(
    fixture: Awaited<ReturnType<typeof deployFixture>>,
  ) {
    const termsHash = await fixture.escrow.read.buildQuoteTermsHash([
      orderId,
      baseTermsHash,
      quoteHash,
    ]);
    const block = await publicClient.getBlock();
    await fixture.escrow.write.createAndLockWithQuote(
      [
        settlementId,
        orderId,
        baseTermsHash,
        quoteHash,
        termsHash,
        fixture.payee.account.address,
        fixture.token.address,
        budget,
        budget,
        block.timestamp + 3600n,
      ],
      { account: fixture.payer.account },
    );
  }

  it("releases through the real CCIP sender path with native fee", async function () {
    const fixture = await deployFixture();
    const { payer, payee, platformSigner, token, router, receiver, escrow, sender } = fixture;
    await createSettlement(fixture);

    assert.equal(
      await escrow.read.quoteRealCrossChainFee([settlementId, paid]),
      fee,
    );

    const { result: messageId } = await escrow.simulate.releaseRealCrossChain(
      [settlementId, paid],
      { account: payer.account, value: fee },
    );

    await escrow.write.releaseRealCrossChain([settlementId, paid], {
      account: payer.account,
      value: fee,
    });

    const settlement = await escrow.read.settlements([settlementId]);
    const sentTransfer = await sender.read.getSentTransfer([messageId]);
    const receivedTransfer = await receiver.read.getReceivedTransfer([
      messageId,
    ]);

    assert.equal(await escrow.read.releaseMessageIds([settlementId]), messageId);
    assert.equal(await sender.read.isMessageSent([messageId]), true);
    assert.equal(await receiver.read.isMessageProcessed([messageId]), true);
    assert.equal(await token.read.balanceOf([payer.account.address]), refund);
    assert.equal(await token.read.balanceOf([payee.account.address]), paid);
    assert.equal(await token.read.balanceOf([escrow.address]), 0n);
    assert.equal(await token.read.balanceOf([sender.address]), 0n);
    assert.equal(await token.read.balanceOf([receiver.address]), 0n);
    assert.equal(await publicClient.getBalance({ address: router.address }), fee);
    assert.equal(settlement[6], paid);
    assert.equal(settlement[7], 3);

    await escrow.write.confirmCrossChainRelease([settlementId, messageId], {
      account: platformSigner.account,
    });
    assert.equal((await escrow.read.settlements([settlementId]))[7], 4);

    assert.equal(sentTransfer.messageId, messageId);
    assert.equal(sentTransfer.settlementId, settlementId);
    assert.equal(sentTransfer.orderId, orderId);
    assert.equal(sentTransfer.destinationChainSelector, destinationChainSelector);
    assert.equal(sentTransfer.receiver.toLowerCase(), receiver.address.toLowerCase());
    assert.equal(sentTransfer.token.toLowerCase(), token.address.toLowerCase());
    assert.equal(sentTransfer.amount, paid);
    assert.equal(sentTransfer.fee, fee);
    assert.notEqual(sentTransfer.payloadHash, zeroHash);

    assert.equal(receivedTransfer.messageId, messageId);
    assert.equal(receivedTransfer.settlementId, settlementId);
    assert.equal(receivedTransfer.orderId, orderId);
    assert.equal(receivedTransfer.sourceChainSelector, sourceChainSelector);
    assert.equal(receivedTransfer.sender.toLowerCase(), sender.address.toLowerCase());
    assert.equal(
      receivedTransfer.receiver.toLowerCase(),
      payee.account.address.toLowerCase(),
    );
    assert.equal(receivedTransfer.token.toLowerCase(), token.address.toLowerCase());
    assert.equal(receivedTransfer.amount, paid);
  });

  it("allows the platform signer to release through the real CCIP path", async function () {
    const fixture = await deployFixture();
    const { payee, platformSigner, token, escrow } = fixture;
    await createSettlement(fixture);

    await escrow.write.releaseRealCrossChain([settlementId, paid], {
      account: platformSigner.account,
      value: fee,
    });

    assert.equal(await token.read.balanceOf([payee.account.address]), paid);
    assert.notEqual(await escrow.read.releaseMessageIds([settlementId]), zeroHash);
  });

  it("rejects unauthorized, invalid, and underfunded real CCIP releases", async function () {
    const fixture = await deployFixture();
    const { payer, stranger, escrow } = fixture;
    await createSettlement(fixture);

    await assert.rejects(
      escrow.write.releaseRealCrossChain([settlementId, paid], {
        account: stranger.account,
        value: fee,
      }),
    );
    await assert.rejects(
      escrow.write.releaseRealCrossChain([settlementId, 0n], {
        account: payer.account,
        value: fee,
      }),
    );
    await assert.rejects(
      escrow.write.releaseRealCrossChain([settlementId, budget + 1n], {
        account: payer.account,
        value: fee,
      }),
    );
    await assert.rejects(
      escrow.write.releaseRealCrossChain([settlementId, paid], {
        account: payer.account,
        value: fee - 1n,
      }),
    );
  });

  it("rejects repeated real CCIP releases", async function () {
    const fixture = await deployFixture();
    const { payer, escrow } = fixture;
    await createSettlement(fixture);

    await escrow.write.releaseRealCrossChain([settlementId, paid], {
      account: payer.account,
      value: fee,
    });

    await assert.rejects(
      escrow.write.releaseRealCrossChain([settlementId, paid], {
        account: payer.account,
        value: fee,
      }),
    );
  });

  it("reverts if the real receiver has not allowed the sender", async function () {
    const fixture = await deployFixture(false);
    const { payer, token, escrow } = fixture;
    await createSettlement(fixture);

    await assert.rejects(
      escrow.write.releaseRealCrossChain([settlementId, paid], {
        account: payer.account,
        value: fee,
      }),
    );

    const settlement = await escrow.read.settlements([settlementId]);
    assert.equal(settlement[7], 2);
    assert.equal(await token.read.balanceOf([escrow.address]), budget);
  });
});
