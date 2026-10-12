import assert from "node:assert/strict";
import { describe, it } from "node:test";

import { network } from "hardhat";
import { keccak256, stringToHex } from "viem";

const id = (value: string) => keccak256(stringToHex(value));

describe("Local destination payout conversion", async function () {
  const { viem } = await network.create();

  it("converts six-decimal settlement USDC into a chain-local 18-decimal token", async function () {
    const [controller, relay, sourceSender, agent] = await viem.getWalletClients();
    const settlementToken = await viem.deployContract("MockUSDC");
    const payoutToken = await viem.deployContract("MockPayoutToken", [
      "Chain B Token",
      "BTK",
      18,
      controller.account.address,
    ]);
    const swap = await viem.deployContract("LocalPayoutSwap", [
      controller.account.address,
      settlementToken.address,
      payoutToken.address,
      6,
      18,
      2_000_000_000_000_000_000n,
    ]);
    const receiver = await viem.deployContract("DynamicSettlementReceiver", [
      controller.account.address,
      relay.account.address,
      settlementToken.address,
    ]);
    await receiver.write.configureSource([31337n, sourceSender.account.address, true], { account: controller.account });
    await receiver.write.configurePayout([swap.address, payoutToken.address, true], { account: controller.account });
    await swap.write.configureReceiver([receiver.address], { account: controller.account });
    await payoutToken.write.mint([swap.address, 1_000_000n * 10n ** 18n], { account: controller.account });

    const settlementAmount = 4_200_000n;
    const quotedPayout = 8_400_000_000_000_000_000n;
    assert.equal(await swap.read.quoteExactInput([settlementAmount]), quotedPayout);
    const deadline = (await (await viem.getPublicClient()).getBlock()).timestamp + 300n;
    const messageId = id("payout:B:1");
    await receiver.write.receiveSettlementAndSwap([
      messageId,
      31337n,
      sourceSender.account.address,
      id("workflow:B"),
      id("attempt:B"),
      settlementToken.address,
      agent.account.address,
      settlementAmount,
      payoutToken.address,
      8_316_000_000_000_000_000n,
      deadline,
    ], { account: relay.account });

    assert.equal(await settlementToken.read.balanceOf([agent.account.address]), 0n);
    assert.equal(await settlementToken.read.balanceOf([swap.address]), settlementAmount);
    assert.equal(await payoutToken.read.balanceOf([agent.account.address]), quotedPayout);
    const record = await receiver.read.payoutRecords([messageId]);
    assert.equal(record[0].toLowerCase(), agent.account.address.toLowerCase());
    assert.equal(record[1].toLowerCase(), payoutToken.address.toLowerCase());
    assert.equal(record[2], settlementAmount);
    assert.equal(record[3], quotedPayout);

    await assert.rejects(receiver.write.receiveSettlementAndSwap([
      messageId,
      31337n,
      sourceSender.account.address,
      id("workflow:B"),
      id("attempt:B"),
      settlementToken.address,
      agent.account.address,
      settlementAmount,
      payoutToken.address,
      8_316_000_000_000_000_000n,
      deadline,
    ], { account: relay.account }));
  });

  it("reverts the whole delivery when the minimum payout cannot be met", async function () {
    const [controller, relay, sourceSender, agent] = await viem.getWalletClients();
    const settlementToken = await viem.deployContract("MockUSDC");
    const payoutToken = await viem.deployContract("MockPayoutToken", [
      "Chain D Token",
      "DTK",
      6,
      controller.account.address,
    ]);
    const swap = await viem.deployContract("LocalPayoutSwap", [
      controller.account.address,
      settlementToken.address,
      payoutToken.address,
      6,
      6,
      4_000_000_000_000_000_000n,
    ]);
    const receiver = await viem.deployContract("DynamicSettlementReceiver", [
      controller.account.address,
      relay.account.address,
      settlementToken.address,
    ]);
    await receiver.write.configureSource([31337n, sourceSender.account.address, true], { account: controller.account });
    await receiver.write.configurePayout([swap.address, payoutToken.address, true], { account: controller.account });
    await swap.write.configureReceiver([receiver.address], { account: controller.account });
    await payoutToken.write.mint([swap.address, 1_000_000_000_000n], { account: controller.account });
    const publicClient = await viem.getPublicClient();
    const deadline = (await publicClient.getBlock()).timestamp + 300n;
    const messageId = id("payout:D:slippage");

    await assert.rejects(receiver.write.receiveSettlementAndSwap([
      messageId,
      31337n,
      sourceSender.account.address,
      id("workflow:D"),
      id("attempt:D"),
      settlementToken.address,
      agent.account.address,
      6_800_000n,
      payoutToken.address,
      27_200_001n,
      deadline,
    ], { account: relay.account }));
    assert.equal(await receiver.read.processedMessages([messageId]), false);
    assert.equal(await payoutToken.read.balanceOf([agent.account.address]), 0n);
    assert.equal(await settlementToken.read.balanceOf([receiver.address]), 0n);
  });
});
