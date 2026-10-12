# Implementation Contract

This document defines the current implementation boundary for the agent
marketplace and settlement prototype.

## Product Boundary

The marketplace remains a centralized discovery and workflow platform with DID
authentication. The settlement layer now has implemented local smart-contract
coverage and real CCIP-shaped contracts.

The historical `MockSettlementAdapter` belongs to the early marketplace
prototype. It is not the current contract delivery. The current settlement
delivery is centered on:

- `MarketplaceEscrow`
- `MockQuoteProvider`
- `CCIPSettlementSender`
- `CCIPSettlementReceiver`
- `RealCCIPSettlementSender`
- `RealCCIPSettlementReceiver`

The integrated dynamic local protocol additionally uses `WorkflowEngine`,
`TaskEscrow`, `ChainRouteRegistry`, `RemoteWorkBoard`, `ResultProofGateway`,
`CoordinatorReview`, `DynamicSettlementSender`, `DynamicSettlementReceiver`,
`MockPayoutToken`, `LocalPayoutSwap`, `FeePolicy`, `ResultRelayGasVault`,
`GasTreasuryA`, and `FailureClassifier`.

## Dynamic Local V1 Boundary

The dynamic V1 uses Chain A `31337` as the task and settlement chain. Execution
chains are B `31338`, C `31339`, and D `31340`. These IDs are the only design
input taken from `projects.zip`; its DAG is not part of this implementation.

The default graph is N1 `research` followed by N2 `verification`. Dependencies
unlock only after every predecessor reaches `SETTLED`; `SUCCEEDED` is not enough.
A stable `nodeId` may have multiple attempts. Each retry creates a new
`attemptId`, snapshots `routeId + routeVersion`, and rejects results from stale
attempts. Coordinator matching selects an Agent by capability before resolving
that Agent's home-chain route. Every first dispatch persists a Top-K candidate
ranking under `MATCH_V1`. Only the selected candidate receives an attempt and a
budget reservation. Standby candidates are revalidated against current Agent
activity, capacity, Gas readiness, quote validity, node budget, and the
snapshotted route before a retry is dispatched.

An empty candidate pool moves the node to `WAITING_FOR_AGENT`; it does not fail
the workflow. A later directory registration or profile update can make the
node dispatchable again. Every directory refresh creates an immutable matching
round so the selection remains auditable.

The execution chains are existing local RPCs, not chains created on demand.
What is dynamic is the route selection and deployment: Coordinator first picks
an Agent, reads the Agent's `homeChainId`, then the Worker deploys or reuses that
exact route version on B, C, or D. `homeWallet`, `homeChainId`, `routeId`, and
`routeVersion` are immutable attempt snapshots.

Every route also snapshots `settlementAsset`, `payoutAsset`, payout token,
decimals, rate, and slippage. A route enables destination conversion only when
both `payoutTokenAddress` and `payoutSwapAddress` exist. Otherwise it retains
the legacy direct-USDC payout. Settlement freezes an exact payout quote,
minimum amount, and ten-minute deadline. A destination swap failure reverts the
entire receive transaction and leaves the attempt in `SETTLEMENT_PENDING`.

The dynamic API surface is:

- `GET/POST /api/v1/workflows`
- `GET /api/v1/workflows/{workflowId}`
- `POST /api/v1/workflows/{workflowId}/coordinate`
- `POST /api/v1/workflows/{workflowId}/nodes/{nodeId}/attempts/{attemptId}/results`
- `POST /api/v1/workflows/{workflowId}/funding-confirmations`
- `POST /api/v1/workflows/{workflowId}/nodes/{nodeId}/attempts/{attemptId}/result-proof-confirmations`
- `POST /api/v1/workflows/{workflowId}/nodes/{nodeId}/attempts/{attemptId}/settlement-confirmations`
- `POST /api/v1/workflows/{workflowId}/finalization-confirmations`
- `GET/PATCH /api/v1/chain-routes`
- `GET/POST /api/v1/route-deployments`
- `GET/PATCH /api/v1/protocol-jobs`
- `GET/POST /api/v1/workflow-agents`
- `PATCH /api/v1/workflow-agents/{profileId}`
- `GET /api/v1/cross-chain-messages`

All dynamic mutations require DID bearer authorization and `Idempotency-Key`.
Money remains a decimal string. General business-money fields accept eight
fractional digits; payout amount and rate fields accept EVM-scale decimal
strings without floating-point conversion. Business Token entries and
execution/relay Gas entries are separate balanced ledger assets. `PAYOUT`
records the USDC settlement leg while `PAYOUT_SWAP` records the destination
BTK/CTK/DTK leg, so different assets are never summed as one currency.

Workflow Agent profile mutations require the `admin` role. Candidate rankings
include score breakdown, quote, home chain, route version, selection state, and
exclusion reason. Agent-owned immutable-review failures create an
`AGENT_RESULT_REJECTED` failure incident and release the attempt reservation.
Agent credentials are represented only by `credentialStatus`, `credentialRef`,
and `credentialFingerprint`; API keys are never stored or returned. A local
seed account may supply a public `homeWallet`, but no private key is persisted.

## Roles

- `requester`: publishes demands, accepts bids, places orders, funds escrow, and
  accepts delivery.
- `provider`: publishes capabilities, bids, starts work, and submits delivery.
- `admin`: reviews agents, handles disputes, and views audit logs.
- `platformSigner`: can trigger controlled settlement actions where the contract
  allows platform-assisted release or refund.

## Identity Flow

1. The requester calls `POST /api/v1/auth/challenges` with a DID.
2. The agent signs the canonical challenge with its operational key.
3. The API verifies DID resolution, nonce, audience, and expiry.
4. The API returns a short-lived bearer token.

Development agents use a local DID resolver with secp256k1 keys. This boundary
allows a future `did:ethr` implementation without changing the marketplace
service model.

## Marketplace Workflow

The business workflow is:

1. A/requester creates or selects a task.
2. B/provider accepts or is selected for the task.
3. A obtains a source-asset to settlement-token quote.
4. A locks enough settlement token in Escrow to satisfy the quote minimum.
5. B completes and delivers the task result.
6. A accepts the result.
7. Escrow releases the accepted amount to B through the settlement path.
8. Escrow refunds the unused amount to A.

Direct orders require the selected provider to confirm the immutable task
terms before funding. Bid-based orders treat the provider's signed bid as that
confirmation. The normal order states are:

```text
awaiting_confirmation -> awaiting_funding -> funded -> in_progress -> delivered -> accepted -> completed
```

Exceptional states remain:

```text
cancelled, disputed, refund_pending, refunded, settlement_failed
```

## Contract Boundary

`MarketplaceEscrow` provides:

- `createAndLock`: creates a settlement and locks ERC20 funds.
- `createAndLockWithQuote`: creates a settlement only if the quote has not
  expired, the locked settlement-token amount is at least the quote minimum, and
  the terms hash includes the quote hash.
- `buildQuoteTermsHash`: derives the quote-aware terms hash from order terms and
  quote hash.
- `release`: releases funds on the same chain and refunds the remainder.
- `releaseCrossChain`: releases funds through the local CCIP sender path.
- `quoteRealCrossChainFee`: quotes native fee for the real CCIP sender path.
- `releaseRealCrossChain`: submits a real CCIP-shaped release through
  `RealCCIPSettlementSender` and pays the native CCIP fee from `msg.value`.
- cross-chain releases remain `ReleasePending` until `platformSigner` confirms
  the exact message ID after observing the destination receiver event.
- `refund`: refunds the full locked budget.
- replay protection through settlement status transitions.
- payer/platform authorization checks.

`MockQuoteProvider` provides the current local quote boundary:

- pair rates for arbitrary source asset to settlement token.
- decimals-aware conversion, such as 18-decimal ETH-like amounts to 6-decimal
  USDC-like amounts.
- max slippage in basis points.
- quote deadline checks.
- deterministic quote hash generation for order terms.
- no swap execution and no real external price source.

`CCIPSettlementSender` and `CCIPSettlementReceiver` provide the local mock CCIP
path used by the demo.

`DynamicSettlementReceiver`, `MockPayoutToken`, and `LocalPayoutSwap` provide
the integrated destination payout path. The Receiver validates source chain,
sender, token, message replay, payout token, minimum amount, and deadline. The
allowlisted swap supports differing token decimals and fixed local rates. Its
liquidity is test liquidity and is not a price oracle or production DEX.

`RealCCIPSettlementSender` and `RealCCIPSettlementReceiver` provide the real
CCIP-shaped path:

- source-side fee quote through `IRouterClient.getFee`.
- source-side send through `IRouterClient.ccipSend`.
- destination-side receive through Chainlink `CCIPReceiver`.
- validation of chain selector, remote sender, token, amount, and message replay.

## Real Deployment Boundary

### DEX and chain worker

- `POST /api/v1/orders/{orderId}/dex-quotes` obtains a validated, unsigned 0x
  transaction. Funding is exact-output USDC; payout conversion is exact-input
  USDC and remains provider-signed.
- External settlement mode disables the fixed mock quote path.
- Internal settlement events and relayer task leases use timestamped HMAC
  authentication with per-Worker identities and nonce replay protection, not
  an administrator bearer token.
- Destination receipt keeps an order in `accepted` / `release_pending` and
  queues `confirm_release`. Only the source `CrossChainReleaseConfirmed` event
  completes the order.
- The standalone `services/chain-worker` persists cursors, pending events, and
  broadcast transaction hashes in SQLite. It rewinds a configurable block
  depth and relies on deterministic event IDs for replay safety.
- `settlement_failed` is never inferred from elapsed time alone; delayed CCIP
  delivery remains pending for operator investigation.

The real testnet deployment scripts are implemented:

- `scripts/deploy-real-ccip-receiver.ts`
- `scripts/deploy-real-marketplace-escrow.ts`
- `scripts/deploy-real-ccip-sender.ts`
- `scripts/configure-real-ccip-receiver.ts`
- `scripts/configure-real-marketplace-escrow.ts`
- `scripts/send-real-ccip-settlement.ts`

They are not executed in the current delivery because Arbitrum Sepolia ETH is
not available for destination-chain deployment gas. This is an external testnet
funding blocker, not a missing contract path.

## Acceptance Criteria

The contract delivery is considered locally accepted when:

- the isolated Hardhat suite reports `60 passing`;
- FastAPI reports `31 passed`, Worker reports `15 passed`, and both TypeScript
  projects pass type checking;
- `pnpm local:v1:accept` reports `LOCAL_PROTOCOL_WORKER_ACCEPTED` with B
  `8.4 BTK`, C `0 CTK`, D `27.2 DTK`; B/D swap-pool inputs `4.2/6.8 USDC`;
  platform fee `1.1 USDC`; token refund `7.9 USDC`; and source Gas refund
  `0.01 ETH`;
- `npx.cmd hardhat run scripts/demo-crosschain-local.ts` shows:
  - payer refund: `0.300000 USDC`
  - payee received: `0.700000 USDC`
  - escrow balance: `0.000000 USDC`
  - sender balance: `0.000000 USDC`
  - receiver balance: `0.000000 USDC`
  - settlement status: `Released`

## Security Notes

- Do not reuse any private key that appears in screenshots, chat logs, commits,
  or documentation.
- Real deployment must use fresh test wallets.
- Router, chain selector, and token addresses must be confirmed from Chainlink
  CCIP Explorer on the deployment day.
- The marketplace service must not store production wallet private keys.
