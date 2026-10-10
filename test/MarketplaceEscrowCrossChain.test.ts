import assert from "node:assert/strict";
import { describe, it } from "node:test";

import { network } from "hardhat";
import { getContractAddress, zeroHash } from "viem";

describe("MarketplaceEscrow cross-chain release", async function () {
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

  async function deployFixture() {
    const [deployer, payer, payee, platformSigner, stranger] =
      await viem.getWalletClients();

    const token = await viem.deployContract("MockUSDC");
    const nextNonce = await publicClient.getTransactionCount({
      address: deployer.account.address,
    });
    const expectedSenderAddress = getContractAddress({
      from: deployer.account.address,
      nonce: BigInt(nextNonce),
    });
    const expectedReceiverAddress = getContractAddress({
      from: deployer.account.address,
      nonce: BigInt(nextNonce) + 1n,
    });
    const expectedEscrowAddress = getContractAddress({
      from: deployer.account.address,
      nonce: BigInt(nextNonce) + 2n,
    });

    const sender = await viem.deployContract("CCIPSettlementSender", [
      sourceChainSelector,
      destinationChainSelector,
      expectedReceiverAddress,
      token.address,
      expectedEscrowAddress,
    ]);
    const receiver = await viem.deployContract("CCIPSettlementReceiver", [
      sender.address,
      sourceChainSelector,
      sender.address,
      token.address,
    ]);
    const escrow = await viem.deployContract("MarketplaceEscrow", [
      platformSigner.account.address,
      sender.address,
      destinationChainSelector,
      receiver.address,
    ]);

    assert.equal(sender.address.toLowerCase(), expectedSenderAddress.toLowerCase());
    assert.equal(
      receiver.address.toLowerCase(),
      expectedReceiverAddress.toLowerCase(),
    );
    assert.equal(escrow.address.toLowerCase(), expectedEscrowAddress.toLowerCase());

    await token.write.mint([payer.account.address, budget], {
      account: payer.account,
    });
    await token.write.approve([escrow.address, budget], {
      account: payer.account,
    });

    return { payer, payee, platformSigner, stranger, token, sender, receiver, escrow };
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

  it("releases through the local CCIP sender and receiver path", async function () {
    const fixture = await deployFixture();
    const { payer, payee, platformSigner, token, sender, receiver, escrow } = fixture;
    await createSettlement(fixture);

    const { result: messageId } = await escrow.simulate.releaseCrossChain(
      [settlementId, paid],
      { account: payer.account },
    );

    await escrow.write.releaseCrossChain([settlementId, paid], {
      account: payer.account,
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

  it("allows the platform signer to release cross-chain", async function () {
    const fixture = await deployFixture();
    const { payee, platformSigner, token, escrow } = fixture;
    await createSettlement(fixture);

    await escrow.write.releaseCrossChain([settlementId, paid], {
      account: platformSigner.account,
    });

    assert.equal(await token.read.balanceOf([payee.account.address]), paid);
    assert.notEqual(await escrow.read.releaseMessageIds([settlementId]), zeroHash);
  });

  it("rejects unauthorized and invalid cross-chain releases", async function () {
    const fixture = await deployFixture();
    const { payer, stranger, escrow } = fixture;
    await createSettlement(fixture);

    await assert.rejects(
      escrow.write.releaseCrossChain([settlementId, paid], {
        account: stranger.account,
      }),
    );
    await assert.rejects(
      escrow.write.releaseCrossChain([settlementId, 0n], {
        account: payer.account,
      }),
    );
    await assert.rejects(
      escrow.write.releaseCrossChain([settlementId, budget + 1n], {
        account: payer.account,
      }),
    );
  });

  it("rejects repeated cross-chain releases", async function () {
    const fixture = await deployFixture();
    const { payer, escrow } = fixture;
    await createSettlement(fixture);

    await escrow.write.releaseCrossChain([settlementId, paid], {
      account: payer.account,
    });

    await assert.rejects(
      escrow.write.releaseCrossChain([settlementId, paid], {
        account: payer.account,
      }),
    );
  });

  it("only confirms the recorded message through the platform signer", async function () {
    const fixture = await deployFixture();
    const { payer, platformSigner, stranger, escrow } = fixture;
    await createSettlement(fixture);

    const { result: messageId } = await escrow.simulate.releaseCrossChain(
      [settlementId, paid],
      { account: payer.account },
    );
    await escrow.write.releaseCrossChain([settlementId, paid], {
      account: payer.account,
    });

    await assert.rejects(
      escrow.write.confirmCrossChainRelease([settlementId, messageId], {
        account: stranger.account,
      }),
    );
    await assert.rejects(
      escrow.write.confirmCrossChainRelease([settlementId, zeroHash], {
        account: platformSigner.account,
      }),
    );
  });
});
