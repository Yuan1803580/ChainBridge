import assert from "node:assert/strict";
import { describe, it } from "node:test";

import { network } from "hardhat";
import {
  encodeAbiParameters,
  zeroAddress,
  zeroHash,
} from "viem";

describe("RealCCIPSettlement contracts", async function () {
  const { viem } = await network.create();
  const publicClient = await viem.getPublicClient();

  const settlementId =
    "0x1111111111111111111111111111111111111111111111111111111111111111";
  const orderId =
    "0xaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa";
  const sourceChainSelector = 16015286601757825753n;
  const destinationChainSelector = 3478487238524512106n;
  const wrongChainSelector = 12532609583862916517n;
  const amount = 700_000n;
  const fee = 123n;
  const gasLimit = 300_000n;

  async function deployFixture() {
    const [deployer, payer, payee, authorizedEscrow, stranger] =
      await viem.getWalletClients();

    const token = await viem.deployContract("MockUSDC");
    const wrongToken = await viem.deployContract("MockUSDC");
    const router = await viem.deployContract("MockRealCCIPRouter", [
      sourceChainSelector,
      fee,
    ]);

    const receiver = await viem.deployContract("RealCCIPSettlementReceiver", [
      router.address,
      sourceChainSelector,
      token.address,
    ]);
    const sender = await viem.deployContract("RealCCIPSettlementSender", [
      router.address,
      destinationChainSelector,
      receiver.address,
      token.address,
      authorizedEscrow.account.address,
      gasLimit,
    ]);
    await receiver.write.setAllowedRemoteSender([sender.address], {
      account: deployer.account,
    });

    const payload = {
      settlementId,
      orderId,
      payer: payer.account.address,
      payee: payee.account.address,
      amount,
    };

    return {
      deployer,
      payer,
      payee,
      authorizedEscrow,
      stranger,
      token,
      wrongToken,
      router,
      sender,
      receiver,
      payload,
    };
  }

  it("deploys the receiver before the remote sender is configured", async function () {
    const [deployer, stranger] = await viem.getWalletClients();
    const token = await viem.deployContract("MockUSDC");
    const router = await viem.deployContract("MockRealCCIPRouter", [
      sourceChainSelector,
      fee,
    ]);

    const receiver = await viem.deployContract("RealCCIPSettlementReceiver", [
      router.address,
      sourceChainSelector,
      token.address,
    ]);

    assert.equal(
      (await receiver.read.owner()).toLowerCase(),
      deployer.account.address.toLowerCase(),
    );
    assert.equal(await receiver.read.allowedRemoteSender(), zeroAddress);

    await assert.rejects(
      receiver.write.setAllowedRemoteSender([stranger.account.address], {
        account: stranger.account,
      }),
    );

    await receiver.write.setAllowedRemoteSender([stranger.account.address], {
      account: deployer.account,
    });

    assert.equal(
      (await receiver.read.allowedRemoteSender()).toLowerCase(),
      stranger.account.address.toLowerCase(),
    );
  });

  it("sends a real-shaped CCIP message and pays the destination payee", async function () {
    const { payer, payee, token, router, sender, receiver, payload } =
      await deployFixture();

    await token.write.mint([payer.account.address, amount], {
      account: payer.account,
    });
    await token.write.approve([sender.address, amount], {
      account: payer.account,
    });

    assert.equal(
      await sender.read.quoteFee([
        destinationChainSelector,
        receiver.address,
        token.address,
        amount,
      ]),
      fee,
    );

    const { result: messageId } =
      await sender.simulate.sendSettlementPayNative(
        [destinationChainSelector, receiver.address, token.address, payload],
        { account: payer.account, value: fee },
      );

    await sender.write.sendSettlementPayNative(
      [destinationChainSelector, receiver.address, token.address, payload],
      { account: payer.account, value: fee },
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
    assert.equal(await publicClient.getBalance({ address: router.address }), fee);

    assert.equal(sentTransfer.messageId, messageId);
    assert.equal(sentTransfer.settlementId, settlementId);
    assert.equal(sentTransfer.orderId, orderId);
    assert.equal(sentTransfer.destinationChainSelector, destinationChainSelector);
    assert.equal(sentTransfer.receiver.toLowerCase(), receiver.address.toLowerCase());
    assert.equal(sentTransfer.token.toLowerCase(), token.address.toLowerCase());
    assert.equal(sentTransfer.amount, amount);
    assert.equal(sentTransfer.feeToken, zeroAddress);
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
    assert.equal(receivedTransfer.amount, amount);
  });

  it("allows the authorized escrow to send while preserving the original payer", async function () {
    const {
      payer,
      payee,
      authorizedEscrow,
      token,
      sender,
      receiver,
      payload,
    } = await deployFixture();

    await token.write.mint([authorizedEscrow.account.address, amount], {
      account: authorizedEscrow.account,
    });
    await token.write.approve([sender.address, amount], {
      account: authorizedEscrow.account,
    });

    const { result: messageId } =
      await sender.simulate.sendSettlementFromEscrowPayNative(
        [destinationChainSelector, receiver.address, token.address, payload],
        { account: authorizedEscrow.account, value: fee },
      );

    await sender.write.sendSettlementFromEscrowPayNative(
      [destinationChainSelector, receiver.address, token.address, payload],
      { account: authorizedEscrow.account, value: fee },
    );

    const receivedTransfer = await receiver.read.getReceivedTransfer([
      messageId,
    ]);

    assert.equal(await token.read.balanceOf([payee.account.address]), amount);
    assert.equal(
      receivedTransfer.receiver.toLowerCase(),
      payee.account.address.toLowerCase(),
    );
    assert.equal(payload.payer.toLowerCase(), payer.account.address.toLowerCase());
  });

  it("rejects invalid send routes, payloads, and native fee amounts", async function () {
    const {
      payer,
      stranger,
      token,
      wrongToken,
      sender,
      receiver,
      payload,
    } = await deployFixture();

    await token.write.mint([payer.account.address, amount], {
      account: payer.account,
    });
    await token.write.approve([sender.address, amount], {
      account: payer.account,
    });

    await assert.rejects(
      sender.write.sendSettlementPayNative(
        [wrongChainSelector, receiver.address, token.address, payload],
        { account: payer.account, value: fee },
      ),
    );
    await assert.rejects(
      sender.write.sendSettlementPayNative(
        [destinationChainSelector, stranger.account.address, token.address, payload],
        { account: payer.account, value: fee },
      ),
    );
    await assert.rejects(
      sender.write.sendSettlementPayNative(
        [destinationChainSelector, receiver.address, wrongToken.address, payload],
        { account: payer.account, value: fee },
      ),
    );
    await assert.rejects(
      sender.write.sendSettlementPayNative(
        [
          destinationChainSelector,
          receiver.address,
          token.address,
          { ...payload, payee: zeroAddress },
        ],
        { account: payer.account, value: fee },
      ),
    );
    await assert.rejects(
      sender.write.sendSettlementPayNative(
        [destinationChainSelector, receiver.address, token.address, payload],
        { account: payer.account, value: fee - 1n },
      ),
    );
  });

  it("rejects escrow sends from non-escrow accounts", async function () {
    const { payer, token, sender, receiver, payload } = await deployFixture();

    await assert.rejects(
      sender.write.sendSettlementFromEscrowPayNative(
        [destinationChainSelector, receiver.address, token.address, payload],
        { account: payer.account, value: fee },
      ),
    );
  });

  it("rejects receiver messages from unsupported source chains", async function () {
    const { payer, token, router, sender, receiver, payload } =
      await deployFixture();

    await token.write.mint([payer.account.address, amount], {
      account: payer.account,
    });
    await token.write.approve([sender.address, amount], {
      account: payer.account,
    });
    await router.write.setSourceChainSelector([wrongChainSelector]);

    await assert.rejects(
      sender.write.sendSettlementPayNative(
        [destinationChainSelector, receiver.address, token.address, payload],
        { account: payer.account, value: fee },
      ),
    );
  });

  it("rejects receiver messages from unauthorized remote senders", async function () {
    const { deployer, payer, payee, token, router, sender, stranger, payload } =
      await deployFixture();

    const receiver = await viem.deployContract("RealCCIPSettlementReceiver", [
      router.address,
      sourceChainSelector,
      token.address,
    ]);
    await receiver.write.setAllowedRemoteSender([stranger.account.address], {
      account: deployer.account,
    });

    await token.write.mint([payer.account.address, amount], {
      account: payer.account,
    });
    await token.write.approve([sender.address, amount], {
      account: payer.account,
    });

    await assert.rejects(
      sender.write.sendSettlementPayNative(
        [
          destinationChainSelector,
          receiver.address,
          token.address,
          { ...payload, payee: payee.account.address },
        ],
        { account: payer.account, value: fee },
      ),
    );
  });

  it("rejects receiver messages before the remote sender is configured", async function () {
    const [payee] = await viem.getWalletClients();
    const token = await viem.deployContract("MockUSDC");
    const router = await viem.deployContract("MockRealCCIPRouter", [
      sourceChainSelector,
      fee,
    ]);
    const receiver = await viem.deployContract("RealCCIPSettlementReceiver", [
      router.address,
      sourceChainSelector,
      token.address,
    ]);
    const messageId =
      "0x8888888888888888888888888888888888888888888888888888888888888888";
    const payloadData = encodeAbiParameters(
      [
        {
          type: "tuple",
          components: [
            { name: "settlementId", type: "bytes32" },
            { name: "orderId", type: "bytes32" },
            { name: "payer", type: "address" },
            { name: "payee", type: "address" },
            { name: "amount", type: "uint256" },
          ],
        },
      ],
      [
        {
          settlementId,
          orderId,
          payer: payee.account.address,
          payee: payee.account.address,
          amount,
        },
      ],
    );
    const encodedSender = encodeAbiParameters(
      [{ type: "address" }],
      [payee.account.address],
    );
    const message = {
      messageId,
      sourceChainSelector,
      sender: encodedSender,
      data: payloadData,
      destTokenAmounts: [{ token: token.address, amount }],
    };

    await token.write.mint([receiver.address, amount], {
      account: payee.account,
    });

    await assert.rejects(router.write.deliverMessage([receiver.address, message]));
  });

  it("rejects repeated receiver message ids", async function () {
    const { payee, token, router, sender, receiver } = await deployFixture();
    const messageId =
      "0x9999999999999999999999999999999999999999999999999999999999999999";
    const payloadData = encodeAbiParameters(
      [
        {
          type: "tuple",
          components: [
            { name: "settlementId", type: "bytes32" },
            { name: "orderId", type: "bytes32" },
            { name: "payer", type: "address" },
            { name: "payee", type: "address" },
            { name: "amount", type: "uint256" },
          ],
        },
      ],
      [
        {
          settlementId,
          orderId,
          payer: sender.address,
          payee: payee.account.address,
          amount,
        },
      ],
    );
    const encodedSender = encodeAbiParameters(
      [{ type: "address" }],
      [sender.address],
    );
    const message = {
      messageId,
      sourceChainSelector,
      sender: encodedSender,
      data: payloadData,
      destTokenAmounts: [{ token: token.address, amount }],
    };

    await token.write.mint([receiver.address, amount], {
      account: payee.account,
    });

    await router.write.deliverMessage([receiver.address, message]);

    assert.equal(await receiver.read.isMessageProcessed([messageId]), true);
    assert.equal(await token.read.balanceOf([payee.account.address]), amount);

    await assert.rejects(router.write.deliverMessage([receiver.address, message]));
  });
});
