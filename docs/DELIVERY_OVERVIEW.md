# Delivery Overview

This document summarizes the current reviewable state of the cross-chain agent
settlement prototype.

## What Is Delivered

The current delivery proves the full local settlement flow:

1. A/payer obtains a local quote from source asset to settlement token.
2. A/payer locks enough settlement token into `MarketplaceEscrow` to satisfy the
   quote minimum.
3. A accepts B/payee's task result.
4. Escrow releases `0.7 USDC` through the local CCIP sender/receiver path.
5. B/payee receives `0.7 USDC`.
6. A/payer receives the unused `0.3 USDC` refund.
7. Escrow, sender, and receiver end with zero token balance.
8. A mock CCIP `messageId` links the sender and receiver records.

The expected local test result is:

```text
50 passing
```

## Three Implementation Layers

Local mock CCIP:

- Runs fully on local Hardhat.
- Requires no testnet gas, LINK, CCIP test token, or real USDC.
- Includes `MockQuoteProvider` for local quote, slippage, deadline, and quote
  hash tests.
- Demonstrates the complete business and accounting flow.

Real CCIP-shaped contracts:

- Use Chainlink CCIP interfaces and message structs.
- Are tested locally with `MockRealCCIPRouter`.
- Validate route, sender, token, amount, and replay protection.

Real testnet scripts:

- Receiver deployment script is ready.
- Escrow deployment script is ready.
- Sender deployment script is ready.
- Receiver configuration script is ready.
- Escrow real CCIP configuration script is ready.
- Real CCIP send script is ready.

## Demo Steps

From the project root:

```powershell
npx.cmd hardhat test nodejs
npx.cmd hardhat run scripts/demo-crosschain-local.ts
```

Explain the demo output as:

- `payer refund 0.300000 USDC`: A got back the unused budget.
- `payee received 0.700000 USDC`: B received the accepted payment.
- `escrow/sender/receiver balance 0`: no settlement funds remain stuck.
- `settlement status Released`: the order is settled.
- `messageId`: local stand-in for the real CCIP tracking ID.

Explain the quote layer as:

- The current quote module is local mock logic, not a DEX swap.
- It converts arbitrary source-asset budgets into settlement-token amounts.
- It protects the lock step with max slippage, a minimum settlement amount, a
  deadline, and a quote hash included in the order terms.

## Why Real CCIP Is Not Deployed Yet

The real deployment target is Ethereum Sepolia to Arbitrum Sepolia. The project
currently cannot complete that deployment because the destination wallet does
not have Arbitrum Sepolia ETH for deployment gas. Faucet and bridge attempts
failed or required mainnet wallet balances.

This does not block code review:

- Real CCIP-shaped contracts are implemented.
- Deployment scripts are implemented.
- Send script is implemented.
- Local tests validate the critical behavior without external faucets.

## Real CCIP Checklist

When testnet assets become available:

1. Fill `.env` with fresh test-wallet RPC and key values.
2. Confirm latest routers, chain selectors, and supported token addresses in
   Chainlink CCIP Explorer.
3. Deploy `RealCCIPSettlementReceiver` on destination testnet.
4. Deploy `MarketplaceEscrow` on source testnet.
5. Deploy `RealCCIPSettlementSender` on source testnet with Escrow authorized.
6. Configure the receiver with the sender address.
7. Configure Escrow with the sender and receiver route.
8. Fund the source wallet with CCIP-BnM or another supported test token.
9. Keep the Chain Worker running so its restricted Relayer calls Escrow
   `releaseRealCrossChain` with native fee.
10. Track the printed `messageId` in CCIP Explorer.

Do not use a wallet private key that has appeared in screenshots, chat, commits,
or documentation.
