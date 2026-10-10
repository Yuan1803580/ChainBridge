# Local Cross-chain Settlement Demo

This demo uses a local Hardhat network to simulate the complete
Escrow + CCIP Sender + CCIP Receiver settlement path. It does not connect to
real testnets and does not require testnet ETH, LINK, CCIP-BnM, or a real
Chainlink Router.

## Run Commands

Recommended:

```powershell
pnpm contracts:compile
pnpm contracts:test
pnpm contracts:demo
```

If `pnpm` is not available in PowerShell:

```powershell
npx.cmd hardhat compile
npx.cmd hardhat test nodejs
npx.cmd hardhat run scripts/demo-crosschain-local.ts
```

## What The Demo Does

The script deploys:

- `MockUSDC`
- `CCIPSettlementSender`
- `CCIPSettlementReceiver`
- `MarketplaceEscrow`

Then it runs the settlement flow:

1. Mint `1.0 USDC` to A/payer.
2. A approves Escrow to spend the USDC.
3. A creates a settlement and locks `1.0 USDC` in Escrow.
4. A calls `releaseCrossChain` to pay `0.7 USDC` to B/payee.
5. Escrow refunds the remaining `0.3 USDC` to A.
6. Sender records the mock CCIP outgoing message.
7. Receiver records the mock CCIP incoming message and pays B.

## Expected Result

Final balances should be:

- payer A: `0.300000 USDC`
- payee B: `0.700000 USDC`
- Escrow: `0.000000 USDC`
- CCIP Sender: `0.000000 USDC`
- CCIP Receiver: `0.000000 USDC`

The script also prints a `messageId`. In the real CCIP version, this ID is used
to track cross-chain message status. In the local demo, it proves Sender and
Receiver processed the same settlement.

## Current Boundary

This is a local mock CCIP implementation, not a real cross-chain transfer:

- `CCIPSettlementSender` directly transfers mock USDC to the Receiver and calls
  `mockReceive`.
- `CCIPSettlementReceiver` simulates the destination-side handling that would
  happen after a real CCIP message arrives.
- `MarketplaceEscrow` already has the cross-chain release entrypoint
  `releaseCrossChain`.
- The real CCIP-shaped contracts and deployment scripts are documented in
  [Real CCIP Integration](REAL_CCIP_INTEGRATION.md).

