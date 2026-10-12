import assert from "node:assert/strict";
import { describe, it } from "node:test";

import { network } from "hardhat";

describe("MockQuoteProvider", async function () {
  const { viem } = await network.create();
  const publicClient = await viem.getPublicClient();

  const sourceAmount = 1_000_000_000_000_000_000n; // 1 ETH-like asset
  const settlementPerSourceWad = 2_000_000_000_000_000_000_000n; // 2000 USDC-like tokens
  const quotedSettlementAmount = 2_000_000_000n; // 2000 USDC with 6 decimals
  const minSettlementAmount = 1_980_000_000n; // 1% slippage
  const maxSlippageBps = 100;

  async function deployFixture() {
    const [sourceAsset] = await viem.getWalletClients();
    const settlementToken = await viem.deployContract("MockUSDC");
    const quoteProvider = await viem.deployContract("MockQuoteProvider");
    const block = await publicClient.getBlock();
    const quoteDeadline = block.timestamp + 3600n;

    await quoteProvider.write.setRate([
      sourceAsset.account.address,
      settlementToken.address,
      18,
      6,
      settlementPerSourceWad,
    ]);

    return { sourceAsset, settlementToken, quoteProvider, quoteDeadline };
  }

  it("quotes an ETH-like source asset into a USDC-like settlement token", async function () {
    const { sourceAsset, settlementToken, quoteProvider, quoteDeadline } =
      await deployFixture();

    const quote = await quoteProvider.read.quote([
      sourceAsset.account.address,
      settlementToken.address,
      sourceAmount,
      maxSlippageBps,
      quoteDeadline,
    ]);
    const quoteHash = await quoteProvider.read.hashQuote([
      sourceAsset.account.address,
      settlementToken.address,
      sourceAmount,
      quotedSettlementAmount,
      minSettlementAmount,
      maxSlippageBps,
      quoteDeadline,
    ]);

    assert.equal(quote.sourceAsset.toLowerCase(), sourceAsset.account.address);
    assert.equal(quote.settlementToken.toLowerCase(), settlementToken.address);
    assert.equal(quote.sourceAmount, sourceAmount);
    assert.equal(quote.quotedSettlementAmount, quotedSettlementAmount);
    assert.equal(quote.minSettlementAmount, minSettlementAmount);
    assert.equal(quote.maxSlippageBps, maxSlippageBps);
    assert.equal(quote.quoteDeadline, quoteDeadline);
    assert.equal(quote.quoteHash, quoteHash);
  });

  it("calculates slippage in basis points", async function () {
    const { sourceAsset, settlementToken, quoteProvider, quoteDeadline } =
      await deployFixture();

    const quote = await quoteProvider.read.quote([
      sourceAsset.account.address,
      settlementToken.address,
      sourceAmount,
      250,
      quoteDeadline,
    ]);

    assert.equal(quote.quotedSettlementAmount, quotedSettlementAmount);
    assert.equal(quote.minSettlementAmount, 1_950_000_000n);
  });

  it("rejects missing rates, invalid slippage, and expired quotes", async function () {
    const { sourceAsset, settlementToken, quoteProvider } = await deployFixture();
    const [, unknownSourceAsset] = await viem.getWalletClients();
    const block = await publicClient.getBlock();

    await assert.rejects(
      quoteProvider.read.quote([
        unknownSourceAsset.account.address,
        settlementToken.address,
        sourceAmount,
        maxSlippageBps,
        block.timestamp + 3600n,
      ]),
    );
    await assert.rejects(
      quoteProvider.read.quote([
        sourceAsset.account.address,
        settlementToken.address,
        sourceAmount,
        10_001,
        block.timestamp + 3600n,
      ]),
    );
    await assert.rejects(
      quoteProvider.read.quote([
        sourceAsset.account.address,
        settlementToken.address,
        sourceAmount,
        maxSlippageBps,
        block.timestamp - 1n,
      ]),
    );
  });
});
