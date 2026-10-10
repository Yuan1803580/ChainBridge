import assert from "node:assert/strict";
import { describe, it } from "node:test";

import { network } from "hardhat";
import { zeroAddress, zeroHash } from "viem";

describe("CCIPSettlementReceiver", async function () {
  const { viem } = await network.create();

  const messageId =
    "0xcccccccccccccccccccccccccccccccccccccccccccccccccccccccccccccccc";
  const secondMessageId =
    "0xdddddddddddddddddddddddddddddddddddddddddddddddddddddddddddddddd";
  const settlementId =
    "0x1111111111111111111111111111111111111111111111111111111111111111";
  const orderId =
    "0xaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa";
  const sourceChainSelector = 16015286601757825753n;
  const wrongSourceChainSelector = 3478487238524512106n;
  const amount = 700_000n;

  async function deployFixture() {
    const [router, remoteSender, payer, payee, stranger] =
      await viem.getWalletClients();

    const token = await viem.deployContract("MockUSDC");
    const wrongToken = await viem.deployContract("MockUSDC");
    const receiver = await viem.deployContract("CCIPSettlementReceiver", [
      router.account.address,
      sourceChainSelector,
      remoteSender.account.address,
      token.address,
    ]);

    await token.write.mint([receiver.address, amount], {
      account: payer.account,
    });

    const payload = {
      settlementId,
      orderId,
      payer: payer.account.address,
      payee: payee.account.address,
      amount,
    };

    return {
      router,
      remoteSender,
      payer,
      payee,
      stranger,
      token,
      wrongToken,
      receiver,
      payload,
    };
  }

  it("receives a settlement and pays the payee", async function () {
    const { router, remoteSender, payee, token, receiver, payload } =
      await deployFixture();

    await receiver.write.mockReceive(
      [
        messageId,
        sourceChainSelector,
        remoteSender.account.address,
        token.address,
        payload,
      ],
      { account: router.account },
    );

    const transfer = await receiver.read.getReceivedTransfer([messageId]);

    assert.equal(await receiver.read.isMessageProcessed([messageId]), true);
    assert.equal(await token.read.balanceOf([payee.account.address]), amount);
    assert.equal(await token.read.balanceOf([receiver.address]), 0n);
    assert.equal(transfer.messageId, messageId);
    assert.equal(transfer.settlementId, settlementId);
    assert.equal(transfer.orderId, orderId);
    assert.equal(transfer.sourceChainSelector, sourceChainSelector);
    assert.equal(
      transfer.sender.toLowerCase(),
      remoteSender.account.address.toLowerCase(),
    );
    assert.equal(
      transfer.receiver.toLowerCase(),
      payee.account.address.toLowerCase(),
    );
    assert.equal(transfer.token.toLowerCase(), token.address.toLowerCase());
    assert.equal(transfer.amount, amount);
    assert.notEqual(transfer.payloadHash, zeroHash);
  });

  it("rejects calls from non-router accounts", async function () {
    const { remoteSender, stranger, token, receiver, payload } =
      await deployFixture();

    await assert.rejects(
      receiver.write.mockReceive(
        [
          messageId,
          sourceChainSelector,
          remoteSender.account.address,
          token.address,
          payload,
        ],
        { account: stranger.account },
      ),
    );
  });

  it("rejects unsupported source chains", async function () {
    const { router, remoteSender, token, receiver, payload } =
      await deployFixture();

    await assert.rejects(
      receiver.write.mockReceive(
        [
          messageId,
          wrongSourceChainSelector,
          remoteSender.account.address,
          token.address,
          payload,
        ],
        { account: router.account },
      ),
    );
  });

  it("rejects unauthorized remote senders", async function () {
    const { router, stranger, token, receiver, payload } =
      await deployFixture();

    await assert.rejects(
      receiver.write.mockReceive(
        [
          messageId,
          sourceChainSelector,
          stranger.account.address,
          token.address,
          payload,
        ],
        { account: router.account },
      ),
    );
  });

  it("rejects unsupported settlement tokens", async function () {
    const { router, remoteSender, wrongToken, receiver, payload } =
      await deployFixture();

    await assert.rejects(
      receiver.write.mockReceive(
        [
          messageId,
          sourceChainSelector,
          remoteSender.account.address,
          wrongToken.address,
          payload,
        ],
        { account: router.account },
      ),
    );
  });

  it("rejects repeated message ids", async function () {
    const { router, remoteSender, payer, payee, token, receiver, payload } =
      await deployFixture();

    await receiver.write.mockReceive(
      [
        messageId,
        sourceChainSelector,
        remoteSender.account.address,
        token.address,
        payload,
      ],
      { account: router.account },
    );

    await token.write.mint([receiver.address, amount], {
      account: payer.account,
    });

    await assert.rejects(
      receiver.write.mockReceive(
        [
          messageId,
          sourceChainSelector,
          remoteSender.account.address,
          token.address,
          {
            ...payload,
            payee: payee.account.address,
          },
        ],
        { account: router.account },
      ),
    );
  });

  it("rejects invalid payloads", async function () {
    const { router, remoteSender, token, receiver, payload } =
      await deployFixture();

    await assert.rejects(
      receiver.write.mockReceive(
        [
          messageId,
          sourceChainSelector,
          remoteSender.account.address,
          token.address,
          {
            ...payload,
            payee: zeroAddress,
          },
        ],
        { account: router.account },
      ),
    );

    await assert.rejects(
      receiver.write.mockReceive(
        [
          secondMessageId,
          sourceChainSelector,
          remoteSender.account.address,
          token.address,
          {
            ...payload,
            amount: 0n,
          },
        ],
        { account: router.account },
      ),
    );
  });
});
