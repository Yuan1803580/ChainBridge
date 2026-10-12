import assert from "node:assert/strict";
import { describe, it } from "node:test";

import { network } from "hardhat";

describe("MarketplaceEscrow quote-protected lock", async function () {
  const { viem } = await network.create();
  const publicClient = await viem.getPublicClient();

  const settlementId =
    "0x3333333333333333333333333333333333333333333333333333333333333333";
  const orderId =
    "0xaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa";
  const baseTermsHash =
    "0xbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbb";
  const sourceAmount = 1_000_000_000_000_000_000n; // 1 ETH-like asset
  const settlementPerSourceWad = 2_000_000_000_000_000_000_000n; // 2000 USDC-like tokens
  const budgetAmount = 2_000_000_000n;
  const minSettlementAmount = 1_980_000_000n;
  const maxSlippageBps = 100;

  async function deployFixture() {
    const [payer, payee, platformSigner] = await viem.getWalletClients();
    const token = await viem.deployContract("MockUSDC");
    const quoteProvider = await viem.deployContract("MockQuoteProvider");
    const escrow = await viem.deployContract("MarketplaceEscrow", [
      platformSigner.account.address,
      platformSigner.account.address,
      3478487238524512106n,
      payee.account.address,
    ]);
    const block = await publicClient.getBlock();
    const quoteDeadline = block.timestamp + 3600n;

    await quoteProvider.write.setRate([
      payer.account.address,
      token.address,
      18,
      6,
      settlementPerSourceWad,
    ]);

    const quote = await quoteProvider.read.quote([
      payer.account.address,
      token.address,
      sourceAmount,
      maxSlippageBps,
      quoteDeadline,
    ]);
    const termsHash = await escrow.read.buildQuoteTermsHash([
      orderId,
      baseTermsHash,
      quote.quoteHash,
    ]);

    await token.write.mint([payer.account.address, budgetAmount], {
      account: payer.account,
    });
    await token.write.approve([escrow.address, budgetAmount], {
      account: payer.account,
    });

    return {
      payer,
      payee,
      token,
      quoteProvider,
      escrow,
      quote,
      quoteDeadline,
      termsHash,
    };
  }

  it("locks settlement tokens when quote constraints are satisfied", async function () {
    const { payer, payee, token, escrow, quote, quoteDeadline, termsHash } =
      await deployFixture();

    await escrow.write.createAndLockWithQuote(
      [
        settlementId,
        orderId,
        baseTermsHash,
        quote.quoteHash,
        termsHash,
        payee.account.address,
        token.address,
        budgetAmount,
        quote.minSettlementAmount,
        quoteDeadline,
      ],
      { account: payer.account },
    );

    const settlement = await escrow.read.settlements([settlementId]);

    assert.equal(await token.read.balanceOf([escrow.address]), budgetAmount);
    assert.equal(await token.read.balanceOf([payer.account.address]), 0n);
    assert.equal(settlement[0], orderId);
    assert.equal(settlement[1], termsHash);
    assert.equal(settlement[5], budgetAmount);
    assert.equal(settlement[7], 2);
  });

  it("rejects lock amounts below the quote minimum", async function () {
    const { payer, payee, token, escrow, quote, quoteDeadline, termsHash } =
      await deployFixture();

    await assert.rejects(
      escrow.write.createAndLockWithQuote(
        [
          settlementId,
          orderId,
          baseTermsHash,
          quote.quoteHash,
          termsHash,
          payee.account.address,
          token.address,
          minSettlementAmount - 1n,
          quote.minSettlementAmount,
          quoteDeadline,
        ],
        { account: payer.account },
      ),
    );
  });

  it("rejects expired quote terms", async function () {
    const { payer, payee, token, escrow, quote, termsHash } =
      await deployFixture();
    const block = await publicClient.getBlock();

    await assert.rejects(
      escrow.write.createAndLockWithQuote(
        [
          settlementId,
          orderId,
          baseTermsHash,
          quote.quoteHash,
          termsHash,
          payee.account.address,
          token.address,
          budgetAmount,
          quote.minSettlementAmount,
          block.timestamp - 1n,
        ],
        { account: payer.account },
      ),
    );
  });

  it("rejects terms hashes that do not include the provided quote hash", async function () {
    const { payer, payee, token, quoteProvider, escrow, quote, quoteDeadline } =
      await deployFixture();
    const alteredQuoteHash = await quoteProvider.read.hashQuote([
      payer.account.address,
      token.address,
      sourceAmount,
      budgetAmount,
      minSettlementAmount - 1n,
      maxSlippageBps,
      quoteDeadline,
    ]);
    const originalTermsHash = await escrow.read.buildQuoteTermsHash([
      orderId,
      baseTermsHash,
      quote.quoteHash,
    ]);
    const alteredTermsHash = await escrow.read.buildQuoteTermsHash([
      orderId,
      baseTermsHash,
      alteredQuoteHash,
    ]);

    assert.notEqual(originalTermsHash, alteredTermsHash);
    await assert.rejects(
      escrow.write.createAndLockWithQuote(
        [
          settlementId,
          orderId,
          baseTermsHash,
          alteredQuoteHash,
          originalTermsHash,
          payee.account.address,
          token.address,
          budgetAmount,
          quote.minSettlementAmount,
          quoteDeadline,
        ],
        { account: payer.account },
      ),
    );
  });
});
