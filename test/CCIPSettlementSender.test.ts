import assert from "node:assert/strict";
import { describe, it } from "node:test";

import { network } from "hardhat";
import { getContractAddress, zeroAddress, zeroHash } from "viem";

describe("CCIPSettlementSender", async function () {
  const { viem } = await network.create();
  const publicClient = await viem.getPublicClient();

  const settlementId =
    "0x1111111111111111111111111111111111111111111111111111111111111111";
  const orderId =
    "0xaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa";
  const sourceChainSelector = 16015286601757825753n;
  const destinationChainSelector = 3478487238524512106n;
  const wrongDestinationChainSelector = 12532609583862916517n;
  const amount = 700_000n;

  async function deployFixture() {
    const [deployer, payer, payee, stranger] = await viem.getWalletClients();

    const token = await viem.deployContract("MockUSDC");
    const wrongToken = await viem.deployContract("MockUSDC");
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

    const sender = await viem.deployContract("CCIPSettlementSender", [
      sourceChainSelector,
      destinationChainSelector,
      expectedReceiverAddress,
      token.address,
      stranger.account.address,
    ]);
    const receiver = await viem.deployContract("CCIPSettlementReceiver", [
      sender.address,
      sourceChainSelector,
      sender.address,
      token.address,
    ]);

    assert.equal(sender.address.toLowerCase(), expectedSenderAddress.toLowerCase());
    assert.equal(
      receiver.address.toLowerCase(),
      expectedReceiverAddress.toLowerCase(),
    );

    const payload = {
      settlementId,
      orderId,
      payer: payer.account.address,
      payee: payee.account.address,
      amount,
    };

    return { payer, payee, stranger, token, wrongToken, sender, receiver, payload };
  }

  it("sends a settlement and delivers funds through the receiver", async function () {
    const { payer, payee, token, sender, receiver, payload } =
      await deployFixture();

    await token.write.mint([payer.account.address, amount], {
      account: payer.account,
    });
    await token.write.approve([sender.address, amount], {
      account: payer.account,
    });

    const { result: messageId } = await sender.simulate.sendSettlement(
      [destinationChainSelector, receiver.address, token.address, payload],
      { account: payer.account },
    );

    await sender.write.sendSettlement(
      [destinationChainSelector, receiver.address, token.address, payload],
      { account: payer.account },
    );

    const sentTransfer = await sender.read.getSentTransfer([messageId]);
    const receivedTransfer = await receiver.read.getReceivedTransfer([
      messageId,
    ]);

    assert.equal(await sender.read.isMessageSent([messageId]), true);
    assert.equal(await receiver.read.isMessageProcessed([messageId]), true);
    assert.equal(await token.read.balanceOf([payee.account.address]), amount);
    assert.equal(await token.read.balanceOf([payer.account.address]), 0n);
    assert.equal(await token.read.balanceOf([sender.address]), 0n);
    assert.equal(await token.read.balanceOf([receiver.address]), 0n);

    assert.equal(sentTransfer.messageId, messageId);
    assert.equal(sentTransfer.settlementId, settlementId);
    assert.equal(sentTransfer.orderId, orderId);
    assert.equal(sentTransfer.destinationChainSelector, destinationChainSelector);
    assert.equal(sentTransfer.receiver.toLowerCase(), receiver.address.toLowerCase());
    assert.equal(sentTransfer.token.toLowerCase(), token.address.toLowerCase());
    assert.equal(sentTransfer.amount, amount);
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
    assert.equal(receivedTransfer.amount, amount);
  });

  it("quotes zero local mock fees for valid routes", async function () {
    const { token, sender, receiver } = await deployFixture();

    assert.equal(
      await sender.read.quoteFee([
        destinationChainSelector,
        receiver.address,
        token.address,
        amount,
      ]),
      0n,
    );
  });

  it("rejects unsupported destination chains", async function () {
    const { payer, token, sender, receiver, payload } = await deployFixture();

    await assert.rejects(
      sender.write.sendSettlement(
        [wrongDestinationChainSelector, receiver.address, token.address, payload],
        { account: payer.account },
      ),
    );
  });

  it("rejects unauthorized receivers", async function () {
    const { payer, stranger, token, sender, payload } = await deployFixture();

    await assert.rejects(
      sender.write.sendSettlement(
        [
          destinationChainSelector,
          stranger.account.address,
          token.address,
          payload,
        ],
        { account: payer.account },
      ),
    );
  });

  it("rejects unsupported settlement tokens", async function () {
    const { payer, wrongToken, sender, receiver, payload } =
      await deployFixture();

    await assert.rejects(
      sender.write.sendSettlement(
        [destinationChainSelector, receiver.address, wrongToken.address, payload],
        { account: payer.account },
      ),
    );
  });

  it("rejects invalid payloads", async function () {
    const { payer, token, sender, receiver, payload } = await deployFixture();

    await assert.rejects(
      sender.write.sendSettlement(
        [
          destinationChainSelector,
          receiver.address,
          token.address,
          {
            ...payload,
            payee: zeroAddress,
          },
        ],
        { account: payer.account },
      ),
    );
    await assert.rejects(
      sender.write.sendSettlement(
        [
          destinationChainSelector,
          receiver.address,
          token.address,
          {
            ...payload,
            amount: 0n,
          },
        ],
        { account: payer.account },
      ),
    );
  });

  it("rejects unapproved token transfers", async function () {
    const { payer, token, sender, receiver, payload } = await deployFixture();

    await token.write.mint([payer.account.address, amount], {
      account: payer.account,
    });

    await assert.rejects(
      sender.write.sendSettlement(
        [destinationChainSelector, receiver.address, token.address, payload],
        { account: payer.account },
      ),
    );
  });

  it("rejects insufficient token balances", async function () {
    const { payer, token, sender, receiver, payload } = await deployFixture();

    await token.write.approve([sender.address, amount], {
      account: payer.account,
    });

    await assert.rejects(
      sender.write.sendSettlement(
        [destinationChainSelector, receiver.address, token.address, payload],
        { account: payer.account },
      ),
    );
  });

  it("rejects escrow sends from non-escrow accounts", async function () {
    const { payer, token, sender, receiver, payload } = await deployFixture();

    await assert.rejects(
      sender.write.sendSettlementFromEscrow(
        [destinationChainSelector, receiver.address, token.address, payload],
        { account: payer.account },
      ),
    );
  });
});
