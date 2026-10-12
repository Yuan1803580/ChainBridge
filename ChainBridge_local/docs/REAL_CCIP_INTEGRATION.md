# Real CCIP Integration

This document describes the real CCIP contract path added next to the existing local mock path.

## Current status

The project now has two CCIP layers:

- Local mock demo layer: `CCIPSettlementSender` and `CCIPSettlementReceiver`.
- Real CCIP-shaped layer: `RealCCIPSettlementSender` and `RealCCIPSettlementReceiver`.

The real layer uses Chainlink CCIP interfaces:

- `IRouterClient.getFee`
- `IRouterClient.ccipSend`
- `Client.EVM2AnyMessage`
- `CCIPReceiver.ccipReceive`
- `_ccipReceive(Client.Any2EVMMessage memory message)`

It is still locally testable through `MockRealCCIPRouter`, so development is not blocked by Sepolia or Arbitrum Sepolia faucet availability.

The real testnet deployment scripts are prepared, but they should not be run
until the deployer wallet has source-chain gas and destination-chain gas.

## Message flow

1. Source-side caller approves `RealCCIPSettlementSender` to spend the settlement token.
2. `RealCCIPSettlementSender` builds `Client.EVM2AnyMessage`.
3. The message includes:
   - `receiver = abi.encode(destinationReceiver)`
   - `data = abi.encode(SettlementPayload)`
   - `tokenAmounts = [{ token, amount }]`
   - `feeToken = address(0)` for native fee payment
4. Sender calls `router.getFee(destinationChainSelector, message)`.
5. Sender transfers token from caller to itself, approves the router, and calls `router.ccipSend{value: fee}`.
6. Destination CCIP Router calls `RealCCIPSettlementReceiver.ccipReceive`.
7. Receiver validates source chain, remote sender, token, amount, and message replay.
8. Receiver records the message and pays the destination payee.

## Deployment order

The real sender, receiver, and escrow reference each other:

- Sender must know the destination receiver address.
- Receiver must know the allowed remote sender address.
- Sender must authorize the source escrow address.
- Escrow must know the final sender and receiver route.

To avoid relying on nonce-based address prediction across two chains, deploy in
five explicit steps:

1. Deploy `RealCCIPSettlementReceiver` on the destination chain with no remote
   sender configured yet.
2. Deploy `MarketplaceEscrow` on the source chain using the receiver address
   from step 1. Its real CCIP sender can be configured after sender deployment.
3. Deploy `RealCCIPSettlementSender` on the source chain with
   `AUTHORIZED_ESCROW` set to the escrow address from step 2.
4. Configure the destination receiver with the sender address from step 3.
5. Configure the source escrow with the sender and receiver route.

The receiver owner is the deployer account. Only that owner can call
`setAllowedRemoteSender(address)`.
The escrow real CCIP config is controlled by `platformSigner`.

## Faucet problem workaround

If testnet funds cannot be claimed, continue with local validation:

```powershell
pnpm contracts:compile
pnpm contracts:test
```

or:

```powershell
npx.cmd hardhat compile
npx.cmd hardhat test nodejs
```

The real CCIP tests use `MockRealCCIPRouter`; they do not require:

- Sepolia ETH
- Arbitrum Sepolia ETH
- LINK
- CCIP-BnM
- real USDC

## Assets needed for real testnet deployment

For Ethereum Sepolia -> Arbitrum Sepolia, prepare:

- Source chain native gas: Sepolia ETH.
- Destination chain native gas: Arbitrum Sepolia ETH.
- Source chain CCIP fee: native Sepolia ETH for the current implementation.
- Source chain transferable token: start with CCIP-BnM or CCIP-LnM before trying real USDC.

Do not use a wallet whose private key has been shared in screenshots, chat, commits, or documentation.

## Environment variables

Copy these values into `.env` before running deployment scripts:

```text
ZERO_EX_API_KEY=
ZERO_EX_ALLOWED_TARGETS=
ZERO_EX_ALLOWED_ALLOWANCE_TARGETS=
ZERO_EX_ALLOWED_SELECTORS=
CHAIN_WORKER_ID=ccip-worker-1
CHAIN_WORKER_HMAC_SECRET=
LISTENER_HMAC_KEYS=ccip-worker-1:replace-with-the-same-worker-secret
RELAYER_PRIVATE_KEY=
SOURCE_RPC_URL=
DESTINATION_RPC_URL=
DEPLOYER_PRIVATE_KEY=
SOURCE_CCIP_ROUTER=
DESTINATION_CCIP_ROUTER=
SOURCE_CHAIN_SELECTOR=
DESTINATION_CHAIN_SELECTOR=
SOURCE_SETTLEMENT_TOKEN=
DESTINATION_SETTLEMENT_TOKEN=
REAL_CCIP_RECEIVER_ADDRESS=
REAL_CCIP_SENDER_ADDRESS=
REAL_MARKETPLACE_ESCROW_ADDRESS=
AUTHORIZED_ESCROW=
REAL_PLATFORM_SIGNER=
MOCK_CCIP_SENDER_FOR_ESCROW=
CCIP_GAS_LIMIT=300000
SETTLEMENT_ID=0x1111111111111111111111111111111111111111111111111111111111111111
ORDER_ID=0xaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa
PAYEE_ADDRESS=
SETTLEMENT_AMOUNT=100000
CCIP_EXPLORER_URL=https://ccip.chain.link/
```

`AUTHORIZED_ESCROW` is required for real sender deployment. Set it to
`REAL_MARKETPLACE_ESCROW_ADDRESS`.

`MOCK_CCIP_SENDER_FOR_ESCROW` is only a constructor placeholder for the existing
local mock path in `MarketplaceEscrow`. For a real-only testnet escrow, it can be
any nonzero address controlled by the deployer, but do not call the mock
`releaseCrossChain` path on that deployment.

`SETTLEMENT_AMOUNT` uses the token's smallest unit. For a 6-decimal token,
`100000` means `0.1` token, and `1000000` means `1` token.

Before deploying, collect the latest values from Chainlink CCIP Explorer or
Directory:

- Source router address.
- Destination router address.
- Source chain selector.
- Destination chain selector.
- Supported token address on the source chain.
- Supported token address on the destination chain.
- CCIP Explorer URL for message tracking.

Do not hardcode these values in source files. Confirm them on the deployment day:

```text
https://ccip.chain.link/
```

## Deployment commands

Compile and test locally first:

```powershell
npx.cmd hardhat compile
npx.cmd hardhat test nodejs
```

Deploy the receiver on the destination testnet:

```powershell
npx.cmd hardhat run --network destinationTestnet scripts/deploy-real-ccip-receiver.ts
```

Copy the printed `REAL_CCIP_RECEIVER_ADDRESS=...` into `.env`, then deploy the
source escrow:

```powershell
npx.cmd hardhat run --network sourceTestnet scripts/deploy-real-marketplace-escrow.ts
```

Copy the printed `REAL_MARKETPLACE_ESCROW_ADDRESS=...` into `.env`, set
`AUTHORIZED_ESCROW` to the same address, then deploy the sender on the source
testnet:

```powershell
npx.cmd hardhat run --network sourceTestnet scripts/deploy-real-ccip-sender.ts
```

Copy the printed `REAL_CCIP_SENDER_ADDRESS=...` into `.env`, then configure the
destination receiver:

```powershell
npx.cmd hardhat run --network destinationTestnet scripts/configure-real-ccip-receiver.ts
```

Finally configure the source escrow to use the real sender and receiver:

```powershell
npx.cmd hardhat run --network sourceTestnet scripts/configure-real-marketplace-escrow.ts
```

Equivalent package scripts are available:

```powershell
pnpm contracts:deploy:real-receiver
pnpm contracts:deploy:real-escrow
pnpm contracts:deploy:real-sender
pnpm contracts:configure:real-receiver
pnpm contracts:configure:real-escrow
```

## Real Escrow Release

`MarketplaceEscrow.releaseRealCrossChain(...)` is payable because the real CCIP
send must pay a native source-chain fee. Before calling it:

1. Call `quoteRealCrossChainFee(settlementId, paidAmount)`.
2. Pass at least that amount as `msg.value`.
3. Escrow refunds unused settlement tokens to the payer.
4. Escrow forwards the accepted token amount and native fee to
   `RealCCIPSettlementSender`.
5. Escrow stores the returned `messageId`.

The source escrow marks a submitted CCIP release as `ReleasePending`. After the
platform indexer verifies `RealCrossChainSettlementReceived` on the destination
receiver, the platform signer calls `confirmCrossChainRelease(settlementId,
messageId)`. The marketplace API similarly remains `accepted` until its
authenticated destination `settlement_received` event is validated and the
source `CrossChainReleaseConfirmed` event is subsequently reconciled as
`release_confirmed`.

## First real CCIP settlement send

The normal marketplace path is the quote-protected funding CLI plus Chain
Worker/Relayer. `scripts/send-real-ccip-settlement.ts` bypasses Escrow and is
only a router diagnostic; it refuses to run unless
`ALLOW_DIRECT_CCIP_TEST=true`. Never enable it in a production deployment.

Run this only after:

- The receiver has been deployed on the destination testnet.
- The sender has been deployed on the source testnet.
- The receiver has been configured with `REAL_CCIP_SENDER_ADDRESS`.
- The escrow has been configured with `REAL_CCIP_SENDER_ADDRESS`.
- The source wallet has source native gas for the transaction and CCIP fee.
- The source wallet has the selected source settlement token.

Fill these `.env` values:

```text
REAL_CCIP_SENDER_ADDRESS=
DESTINATION_CHAIN_SELECTOR=
REAL_CCIP_RECEIVER_ADDRESS=
SOURCE_SETTLEMENT_TOKEN=
SETTLEMENT_ID=
ORDER_ID=
PAYEE_ADDRESS=
SETTLEMENT_AMOUNT=
CCIP_EXPLORER_URL=https://ccip.chain.link/
```

Then send one small settlement from the source testnet:

```powershell
npx.cmd hardhat run --network sourceTestnet scripts/send-real-ccip-settlement.ts
```

or:

```powershell
pnpm contracts:send:real-ccip
```

The script will:

1. Quote the native CCIP fee with `quoteFee(...)`.
2. Approve `RealCCIPSettlementSender` to spend `SETTLEMENT_AMOUNT`.
3. Call `sendSettlementPayNative(...)`.
4. Print the source transaction hash and CCIP `messageId`.

Use the printed `messageId` in CCIP Explorer to track cross-chain delivery.

For the actual order flow, keep the Worker running instead:

```powershell
npm run worker:start
```

After all eight transactions complete, fill
`docs/testnet-acceptance.json` from the provided example and run:

```powershell
npm run testnet:verify-acceptance
```

This verifies source/destination receipts, final API state, and payer, payee,
and Escrow token balances.

## Pre-deployment checklist

Before running the deployment commands, verify:

- `.env` has valid `SOURCE_RPC_URL` and `DESTINATION_RPC_URL`.
- `.env` uses a fresh test-wallet `DEPLOYER_PRIVATE_KEY`.
- The deployer has source-chain native gas.
- The deployer has destination-chain native gas.
- The source wallet has the transferable source token, preferably CCIP-BnM or CCIP-LnM.
- The selected token is supported for the selected CCIP lane.
- The sender call will pay CCIP fees with native source-chain gas.
- The receiver has been configured with the final sender address before any real CCIP message is sent.
- The escrow has been configured with the final sender and receiver route.

## Next real-testnet step

After the scripts are deployed and configured:

1. Fund the source caller with the supported source token.
2. Approve `RealCCIPSettlementSender` to spend a small amount.
3. Call `quoteFee(...)` to estimate the native CCIP fee.
4. Send one small `sendSettlementPayNative(...)` transfer.
5. Track the returned `messageId` in CCIP Explorer.

These manual actions are now wrapped by `scripts/send-real-ccip-settlement.ts`.
