# Agent ChainBridge

中文日常启动、故障排查和互联网发布说明见
[`docs/START_AND_INTERNET_DEPLOYMENT_ZH.md`](docs/START_AND_INTERNET_DEPLOYMENT_ZH.md)。

## Repository layout: two experiment branches

This repository preserves **two parallel-evolved implementations**, mirroring the two
system configurations described in the paper (§A Experiment Setting):

- **Repository root — managed testnet build.** Deployed on an Alibaba Cloud ECS
  instance in the past, settling Sepolia (11155111) → Arbitrum Sepolia (421614) via Chainlink CCIP.
  Includes the deployment assets (`infra/`, `docker-compose.*.yml`, `scripts/deploy/`)
  and external Agent onboarding. See [docs/MANAGED_TESTNET_V4.md](docs/MANAGED_TESTNET_V4.md).
- **[`ChainBridge_local/`](ChainBridge_local/README.md) — local experiment build.**
  Four isolated Hardhat chains (31337–31340) with no testnet funds, no hosted model
  API and no real assets required. All paper experiments were produced here; raw
  results live in [`ChainBridge_local/experiments/results/`](ChainBridge_local/experiments)
  with [`summarize_experiments.py`](ChainBridge_local/experiments/summarize_experiments.py).

The two trees forked while bugs were being fixed in parallel during experimentation,
so neither supersedes the other — **use the root tree for deployment, and
`ChainBridge_local/` for reproducing the local experiments.**

## Local V1 Quick Start

The first seven delivery steps are runnable on four independent local chains.
`projects.zip` contributes only the approved chain IDs (`31337` through `31340`);
the workflow DAG, Coordinator, attempts, retry rules, contracts and relay are new
implementations in this repository.

```powershell
powershell -ExecutionPolicy Bypass -File scripts\setup-local.ps1
pnpm local:v1
```

Open `http://127.0.0.1:3000`. `pnpm local:v1:accept` runs the integrated API,
Worker and four-chain acceptance. Stop the stack with `pnpm local:v1:stop` and
run the complete regression suite with `pnpm local:v1:verify`.

See [Local Dynamic Multi-chain V1](docs/LOCAL_V1.md) for the chain topology,
seven-step implementation map, commands, acceptance evidence and testnet boundary.
See [Fixed Version 2026-08-01](docs/FIXED_VERSION_20260801.md) for the security
fix matrix, dynamic CCIP boundary, Gas accounting rules and verification results.
See [Agent deployment, onboarding and DeepSeek parameters](docs/Agent部署接入与DeepSeek参数.md)
for the Runner configuration, identity and wallet proofs, signed heartbeat,
activation checks, Gas responsibility and CCIP testnet prerequisites.
See [Managed Testnet V4](docs/MANAGED_TESTNET_V4.md) for the real Agent,
DeepSeek, zero-RPC Sidecar, route predeployment, CCIP-BnM and acceptance runbook.
See [Risk-based node review](docs/risk-based-review.md) for LOW/MEDIUM/HIGH review
policies, sampled independent approvals, challenge periods and settlement gates.
See [Review V2 and Agent-owned signing](docs/REVIEW_V2_AND_AGENT_SIGNING.md) for
EIP-712 approvals, post-result randomness, the on-chain challenge/arbitration
gate, and the rule that Agent wallet keys remain inside Agent Runner.

For an isolated backend acceptance run without starting Next.js:

```powershell
$env:LOCAL_RUNTIME_DIR=".runtime\acceptance"
$env:LOCAL_DATABASE_PATH=".runtime\acceptance\acceptance.db"
$env:LOCAL_SKIP_WEB="true"
pnpm local:v1:accept
```

## Dynamic Workflow Console

This delivery adds a visual V1 control plane on top of the original marketplace
baseline. It keeps the existing DID, order, Escrow, 0x, CCIP-shaped contract and
Worker modules, while adding a runnable interface for:

- Agent-first matching followed by `homeChainId` route selection.
- Stable workflow nodes with replaceable attempts and immutable criteria hashes.
- B success, C review failure, D retry success and stale-attempt rejection.
- Route onboarding/version snapshots, cross-chain event history and fee ownership.
- Browser-wallet funding for new workflows, with exact `TaskEscrow.lockBudget`
  receipt verification and an explicitly separate model-reservation path.
- A richer Agent information platform with chain accounts, heartbeats, SLA,
  attestations, trust signals, gas readiness and attempt history.

To run only the visual console from the repository root:

```powershell
$env:NEXT_PUBLIC_ENABLE_MOCK_FALLBACK="true"
pnpm platform:dev
```

Then open `http://127.0.0.1:3000`. The workflow console is an explicit local
four-chain proof model. It does not claim that public-testnet CCIP or production
contract security has been completed.

The browser-wallet funding option requires the complete `pnpm local:v1:start`
stack. It writes real transactions to local Chain A, but the asset is MockUSDC,
not public-network USDC. The front end reads public contract addresses from the
API and never receives a payer private key.

Agent Cross-chain Shop is a prototype for agent discovery, task collaboration,
escrow settlement, and cross-chain payment. The marketplace app handles agent
matching and task flow. The contracts demonstrate how payer funds can be locked,
released after acceptance, and paid to a payee through a CCIP-style cross-chain
settlement path.

## Current Delivery Status

The project is ready for local review and demonstration:

- The local app prototype supports agent discovery, task/order flow, identities,
  deliveries, reviews, disputes, and audit-oriented metadata.
- `MarketplaceEscrow` locks payer funds, releases accepted payments, refunds the
  remainder, and prevents duplicate release.
- `MockQuoteProvider` defines a local quote and slippage-protection layer for
  converting an arbitrary source-asset budget into the settlement-token amount.
- Local mock CCIP contracts run a full settlement flow without testnet funds.
- Real CCIP-shaped contracts use Chainlink CCIP interfaces and are covered by
  local tests through a mock router.
- Dynamic workflow routes can select `LOCAL_MOCK` or `CHAINLINK_CCIP`
  settlement while preserving workflow, node, attempt, payee, asset and amount.
- Escrow funding proofs are checked against the exact contract, method, token,
  amount, workflow terms and event before a workflow can run.
- Workflow and attempt Gas deposits use transaction receipts for charging and
  cannot be refunded before their corresponding terminal state.
- `ReviewManagerV2` verifies EIP-712 reviewer and arbitrator signatures, enforces
  the challenge deadline with chain time, and blocks settlement until final approval.
- Agent home-chain result transactions are signed by the zero-RPC Agent Signer
  Sidecar under per-Agent policy. Runner and Protocol Worker independently
  verify the raw transaction, and only Worker may broadcast it.
- The API returns validated unsigned 0x funding/payout transactions and disables
  fixed-rate quotes in external settlement mode.
- A standalone viem worker persists chain cursors in SQLite, reconciles source
  and destination events, and leases release transactions to a dedicated
  relayer wallet.
- Real deployment and send scripts are prepared, but real testnet deployment is
  intentionally paused until source and destination testnet gas are available.

The older `MockSettlementAdapter` is only historical prototype context. The
current contract delivery focuses on:

```text
MarketplaceEscrow
MockQuoteProvider
CCIPSettlementSender / CCIPSettlementReceiver
RealCCIPSettlementSender / RealCCIPSettlementReceiver
```

## Short Review Commands

Run these commands from the repository root:

```powershell
powershell -ExecutionPolicy Bypass -File scripts\local\invoke-hardhat-isolated.ps1 -Command test
pnpm local:v1:accept
```

Expected test result:

```text
65 passing
```

Expected integrated V1 result:

```text
LOCAL_PROTOCOL_WORKER_ACCEPTED
B=8.4_BTK C=0_CTK D=27.2_DTK fee=1.1_USDC refund=7.9_USDC sourceGasRefund=<remaining ETH>
```

The demo also prints a mock CCIP `messageId`. In the real CCIP version, that ID
is used to track cross-chain message delivery in CCIP Explorer.

## Business Flow

1. Agent A creates a task/order through the marketplace.
2. Agent B accepts the task.
3. A obtains a quote from the quote layer for source asset to settlement token.
4. A locks enough settlement token to satisfy the quote minimum after slippage.
5. B completes and delivers the work.
6. A accepts the result.
7. Escrow releases the accepted amount to B through the CCIP settlement path.
8. Escrow refunds the unused budget to A.
9. The platform reconciles the destination receiver event and submits the
   source escrow confirmation.
10. The source confirmation event marks the release and order complete.
11. Sender and Receiver record the same `messageId` for auditability.

## Contract Layers

Local mock layer:

- `MockQuoteProvider` calculates a local source-asset to settlement-token quote,
  including decimals conversion, maximum slippage, quote deadline, and quote
  hash. It does not execute swaps.
- `MarketplaceEscrow` locks and releases funds.
- `CCIPSettlementSender` simulates sending a cross-chain settlement message.
- `CCIPSettlementReceiver` simulates destination-chain receipt and pays B.
- This layer is fully runnable without Sepolia ETH, Arbitrum Sepolia ETH, LINK,
  CCIP-BnM, or real USDC.

Real CCIP-shaped layer:

- `RealCCIPSettlementSender` builds `Client.EVM2AnyMessage`, quotes native CCIP
  fees with `IRouterClient.getFee`, and sends with `ccipSend`.
- `RealCCIPSettlementReceiver` inherits Chainlink `CCIPReceiver`, validates the
  source chain, sender, token, amount, and message replay, then pays the payee.
- Tests use `MockRealCCIPRouter`, so development is not blocked by faucets.

Real testnet deployment layer:

- `scripts/deploy-real-ccip-receiver.ts`
- `scripts/deploy-real-marketplace-escrow.ts`
- `scripts/deploy-real-ccip-sender.ts`
- `scripts/configure-real-ccip-receiver.ts`
- `scripts/configure-real-marketplace-escrow.ts`
- `scripts/send-real-ccip-settlement.ts`

These scripts are prepared but should only be run after testnet gas and test
tokens are available.

## Real CCIP Deployment Status

The intended first real route is Ethereum Sepolia to Arbitrum Sepolia. Real
deployment is paused until supported RPCs, funded restricted keys, LINK/native
fees and settlement tokens are available. Dynamic CCIP settlement is implemented
and tested against local Router interfaces, but this repository does not claim a
public-testnet delivery receipt.

When funds are available, use the official Chainlink CCIP Explorer/Directory to
confirm current router addresses, chain selectors, and supported token addresses:

```text
https://ccip.chain.link/
```

Then follow:

- [Real CCIP Integration](docs/REAL_CCIP_INTEGRATION.md)
- [Cross-chain Local Demo](docs/CROSSCHAIN_LOCAL_DEMO.md)
- [Delivery Overview](docs/DELIVERY_OVERVIEW.md)

## Project Layout

```text
apps/api                 FastAPI marketplace and identity APIs
apps/web                 Next.js web app
services/agent-runner    Demo agent runtimes
services/chain-worker    Durable chain listener and restricted relayer
packages/sdk/python      Agent SDK and webhook helpers
packages/schemas         Shared JSON schemas
contracts/src            Escrow, mock CCIP, real CCIP-shaped contracts
contracts/interfaces     Contract interface drafts
scripts                  Local demo, deployment, and operations scripts
docs                     Architecture, demo, and delivery documentation
infra                    Docker Compose support
```

## DEX And Worker Commands

After configuring `.env` from `.env.example`, the non-visual execution paths
are:

```powershell
npm run worker:start
npm run wallet:fund-via-0x
npm run contracts:deploy:mock-stc
npm run contracts:seed:stc-pool
npm run wallet:swap-payout
npm run testnet:verify-acceptance
```

Wallet scripts read A/B keys only from their invoking process. The API never
receives those keys, and the chain worker uses a separate restricted relayer
key.

For the final live run, create `docs/testnet-acceptance.json` from
`docs/testnet-acceptance.example.json`. The V4 acceptance command verifies the
canonical Agent signature, Worker intent HMAC, raw transaction sender and
calldata, CCIP send/receive/confirmation events, historical CCIP-BnM balance
delta, confirmation depth, deployment bytecode/manifest hashes, and final API
attempt state. It fails when any real-chain evidence is missing or inconsistent.

Start the cross-chain Compose profile with explicit environment substitution:

```powershell
docker compose --env-file .env -f infra/docker-compose.yml --profile crosschain up --build
```

The API receives quote/listener configuration only. The Worker receives the
dedicated HMAC and Relayer keys; deployment and wallet keys are not injected
into either service by Compose.

## Local App

For the broader marketplace app, use:

```powershell
powershell -ExecutionPolicy Bypass -File scripts\setup-local.ps1
powershell -ExecutionPolicy Bypass -File scripts\start-local.ps1
```

Main local URLs:

- Web: `http://127.0.0.1:3000`
- API docs: `http://127.0.0.1:8000/api/docs`
- Agent Runtime docs: `http://127.0.0.1:8010/docs`

Stop services:

```powershell
powershell -ExecutionPolicy Bypass -File scripts\stop-local.ps1
```
(Note: The code is from vibe coding. The code will be continuously revised and improved in the future. Stay tuned.)
