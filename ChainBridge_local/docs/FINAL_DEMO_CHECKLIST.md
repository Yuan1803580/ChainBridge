# Final Demo Checklist

Use this checklist for a review, classroom demo, or final project walkthrough.

## 1. Run Tests

From the project root:

```powershell
npx.cmd hardhat test nodejs
```

Expected result:

```text
50 passing
```

Explain that the tests cover:

- source-asset to settlement-token quote calculation, decimals conversion,
  slippage, deadlines, and quote hash checks.
- single-chain escrow lock, release, refund, and authorization.
- local mock CCIP sender/receiver behavior.
- cross-chain escrow release through the local CCIP path.
- real CCIP-shaped sender/receiver behavior through a mock router.

## 2. Run Local Cross-chain Demo

```powershell
npx.cmd hardhat run scripts/demo-crosschain-local.ts
```

Expected final output:

```text
payer refund       0.300000 USDC
payee received     0.700000 USDC
escrow balance     0.000000 USDC
sender balance     0.000000 USDC
receiver balance   0.000000 USDC
settlement status  Released
```

## 3. Explain The Five Key Points

1. Quote: A/payer gets a quote that converts the original budget asset into a
   settlement-token minimum with slippage protection.
2. Locking: A/payer approves and locks `1.0 USDC` in `MarketplaceEscrow`.
3. Acceptance: the release only happens after A accepts the task result.
4. Payment: B/payee receives `0.7 USDC` through the CCIP settlement path.
5. Refund: A/payer automatically receives the unused `0.3 USDC`.
6. Auditability: `messageId` links escrow release, sender record, and receiver
   record.

## 4. Explain The Real CCIP Status

The project has real CCIP-shaped contracts and scripts:

- `RealCCIPSettlementSender`
- `RealCCIPSettlementReceiver`
- `MarketplaceEscrow.releaseRealCrossChain`
- deploy receiver script
- deploy escrow script
- deploy sender script
- configure receiver script
- configure escrow script
- send settlement script

Real testnet deployment is not included in the demo because Arbitrum Sepolia ETH
is not currently available for destination-chain gas.

## 5. Common Questions

Why is this not deployed on real CCIP yet?

Because destination-chain testnet gas is unavailable. Faucet and bridge attempts
failed or required mainnet wallet balances. The contract code and scripts are
ready; the blocker is external funding.

Why use mock USDC?

The local demo needs deterministic, repeatable behavior without faucets. Mock
USDC proves the accounting logic: lock, release, pay, refund, and zero remaining
balances.

Why is quote local instead of a real DEX swap?

This stage validates the contract boundary first: the quote amount, slippage,
minimum settlement amount, deadline, and quote hash are enforced before funds
are locked. Real DEX, Chainlink Price Feed, Circle API, or exchange quotes can
replace the mock quote provider later.

Why is `messageId` important?

In local mock mode, it proves the sender and receiver processed the same
settlement. In real CCIP mode, it becomes the tracking ID in CCIP Explorer.

What remains after this delivery?

When Arbitrum Sepolia ETH is available, fill `.env`, deploy the receiver, deploy
the sender, configure the receiver, send one small CCIP test-token settlement,
and track the returned `messageId`.
