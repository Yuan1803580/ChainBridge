# Backend Cross-chain Workflow

Set `SETTLEMENT_MODE=external_ccip` when the marketplace is connected to real
wallet, relayer, and chain-indexer infrastructure. The API never stores payer or
platform private keys.

## Normal flow

1. A creates a direct order with task description, input, acceptance criteria,
   budget cap, deadline, delivery rules, chain accounts, settlement token, and
   dispute window.
2. B calls `POST /api/v1/orders/{id}/confirm`. The confirmation event records B's
   DID and the immutable `termsHash`.
3. A obtains an unsigned exact-output ETH-to-USDC quote from
   `POST /api/v1/orders/{id}/dex-quotes`, signs it locally, and verifies the
   resulting USDC balance.
4. A calls `/fund` with the consumed `quoteId` and confirmed Swap hash, approves
   USDC, and executes `createAndLockWithQuote` on the source escrow. The
   contract binds `baseTermsHash`, `quoteHash`, `termsHash`, minimum USDC, and
   quote deadline. The API returns `lock_pending` and exposes
   `onchainSettlementId` and `onchainOrderId`.
5. A trusted indexer verifies `FundsLocked`, then posts `funds_locked` to
   `/api/v1/integration/settlement-events`. Only then can B start work.
6. B delivers the artifact off-chain and records its URI and content hash with
   `/deliver`.
7. A calls `/accept` with `payableAmount`, optional score, and verification
   summary. The API enforces `0 < payableAmount <= budget` and computes the
   refund. The order remains `accepted` and settlement becomes `release_pending`.
8. The restricted Relayer leases a durable `release` task and executes
   `releaseRealCrossChain`. The source
   indexer posts `release_submitted` with the exact amount and CCIP message ID.
9. The destination indexer verifies `RealCrossChainSettlementReceived` and posts
   `settlement_received`. The API checks settlement ID, message ID, and amount,
   then queues a `confirm_release` relayer task while leaving the settlement
   `release_pending`.
10. The source platform signer calls `confirmCrossChainRelease` with the same
    message ID. After the source indexer posts `release_confirmed`, the API marks
    the settlement `released` and order `completed`. B may then sign a separate
    USDC-to-STC 0x transaction.

All reconciliation events have a unique `eventId`; duplicate delivery is safe.
Each Worker signs method, timestamp, nonce, path, and exact body with its own
HMAC key. Failed event reports use exponential backoff and eventually enter a
queryable/replayable dead-letter queue. Delayed CCIP messages become `stalled`
without being treated as failed.
Full task artifacts and private results remain off-chain. The database stores
only delivery metadata, hashes, payment decisions, and normalized chain events.

## Disputes

Either order participant may open a dispute. An admin resolves it through
`POST /api/v1/orders/{id}/resolve` with either:

- `outcome=refund`, which refunds the full locked budget; or
- `outcome=release` plus `payableAmount`, which pays B no more than the budget
  and refunds the remainder to A.

The provider never supplies the authoritative final payment amount.
