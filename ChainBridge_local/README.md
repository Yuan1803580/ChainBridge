# Agent ChainBridge

A locally reproducible multi-chain Agent workflow platform. A Coordinator Agent
turns a natural-language request into a DAG, the platform Core validates budget
and risk and matches each node to an Agent by capability, and node results are
signed, relayed cross-chain and settled through an Escrow.

Everything runs on four local EVM chains. **No testnet funds, no hosted model API
and no real assets are required.**

---

## What is real and what is a stand-in

This distinction matters when reading any output the system produces.

**Real code paths:**

- Agent registration, DID identity, signed challenges, ownership verification
- Signed heartbeats with a 60-second TTL that gates whether an Agent is matchable
- Capability-based matching, retry on a replacement Agent, stale-attempt rejection
- Escrow funding and release, cross-chain task/result/settlement messages,
  destination payout conversion
- EIP-712 review approvals (`ReviewManagerV2`) enforced against chain time

**Stand-ins:**

- **Inference.** Node outputs come from your local model. If none is configured,
  a deterministic stub returns hardcoded templates. See
  [Local model inference](#local-model-inference).
- **Cross-chain transport.** The contracts import the real Chainlink CCIP
  interfaces, but the router is `MockRealCCIPRouter`. There is no real router
  address and no public-network delivery. See
  [About CCIP](#about-ccip).
- **Testnet deployment.** Scripts exist but are paused pending gas and tokens.

---

## Requirements

| Item | Requirement |
|---|---|
| OS | Windows (all scripts are PowerShell) |
| Python | **Native Windows Python 3** from python.org with the `py` launcher enabled. WSL and Anaconda are not supported |
| Package manager | `pnpm` (`corepack enable pnpm` or a standalone install) |
| Node | Managed by pnpm; verified on v22 |
| Model | Optional local model server. Without one the stack runs on a stub |

---

## Quick start

```powershell
powershell -ExecutionPolicy Bypass -File scripts\setup-local.ps1
Remove-Item -Recurse -Force .runtime\local-experiment
pnpm local:experiment:start
pnpm local:agents:connect
pnpm local:stack:check
```

Startup takes two to three minutes because four chains are launched.

- `local:experiment:start` launches the chains, API, web console, three Agent
  Runners, the Coordinator Agent and the requester wallet service.
- `local:agents:connect` registers every Agent with the platform: identity
  challenge, signature verification, activation and the signed heartbeat loop.
  **An Agent cannot be matched until this step has run.**
- `local:stack:check` is a read-only health report. It prints
  `RESULT: all local Agents are connected and matchable.` when ready.

Startup is not atomic, but a failed start cleans up after itself: the launcher
stops every process it created during that run and removes the partial state file,
so it never leaves orphan processes holding ports for the next attempt.

Open `http://127.0.0.1:3100`. Stop the stack with `pnpm local:experiment:stop`.

> **Why the runtime directory is deleted first.** Restarting resets the local
> chains, but `agent-market.db` keeps the previous workflow records. A new funding
> transaction then reproduces the exact same EIP-155 transaction hash, and the
> platform rejects it with
> `409 A funding transaction hash has already been used by another workflow`.
> Rule of thumb: **if the chains reset, clear the runtime directory.**

---

## Services and ports

| Service | Port | Notes |
|---|---:|---|
| Web console | 3100 | `http://127.0.0.1:3100` |
| Platform API | 8200 | Docs at `/api/docs` |
| Coordinator Agent | 8300 | Docs at `/docs` |
| Requester wallet service | 8400 | Signs funding transactions |
| Agent Runner B | 8111 | `workflow-research-b`, home chain 31338 |
| Agent Runner C | 8112 | `workflow-verification-c`, home chain 31339 |
| Agent Runner D | 8113 | `workflow-verification-d`, home chain 31340 |
| Chain A / B / C / D | 8545–8548 | chainIds 31337–31340 |

---

## Agents and capabilities

The market exposes two capabilities today:

| Agent | Capability | Home chain |
|---|---|---:|
| `workflow-research-b` | `research` | 31338 |
| `workflow-verification-c` | `verification` | 31339 |
| `workflow-verification-d` | `verification` | 31340 |

`workflow-research-b` is conversational and receives project documentation
retrieved from `AGENT_KNOWLEDGE_PATHS` (the `docs` directory by default) injected
as `input.projectEvidence` before its handler runs.

Runner C and D are deliberately equivalent in capability so the platform has a
genuine replacement target when an attempt fails.

---

## Local model inference

The Coordinator and the three Runners resolve their provider from the same
variables, so one block configures the whole stack:

```powershell
$env:LLM_PROVIDER      = "deepseek"
$env:DEEPSEEK_BASE_URL = "http://127.0.0.1:11434/v1"
$env:DEEPSEEK_API_KEY  = "ollama"
$env:DEEPSEEK_MODEL    = "qwen2.5:7b"
```

The provider speaks the standard OpenAI `/chat/completions` protocol, so any
OpenAI-compatible server works — Ollama, vLLM, LM Studio or a self-hosted
endpoint. Only `DEEPSEEK_BASE_URL` and `DEEPSEEK_MODEL` need to change.

Two requirements on the server:

1. It must expose an OpenAI-compatible `/v1/chat/completions` route.
2. It should honour `response_format: {"type":"json_object"}`. Structured node
   calls rely on it. If the server does not, node output validation fails rather
   than degrades.

Without these variables the stack falls back to a **deterministic stub**.
`local:stack:check` always reports which mode is active:

| `llmMode` | Meaning | Conclusions it supports |
|---|---|---|
| `mock` | Node outputs are hardcoded templates | **Protocol layer only** |
| `real` | Node outputs come from your local model | Protocol layer plus inference |
| `mixed` | Some components use a stub, others the model | Check `llm-mode.json` |

Evidence exported by `pnpm experiment:real-agents` carries an `llmMode` field, so
every result states on its own whether its node outputs came from a real model or
a stub.

---

## Architecture: who decides what

```
user → Coordinator Agent (8300)      translates a request into a DAG proposal
     → Platform Core (apps/api, 8200) sole authority: budget, risk, review,
                                      matching, routing, settlement
     → Agent Runners (8111-8113)      execute nodes, sign results with ES256K
     → Four local chains (8545-8548)  Escrow, cross-chain messaging, payout
```

The permission boundary is deliberate. The model may only propose task fields such
as `nodeKey`, `capability`, `dependsOn`, `acceptanceCriteria` and
`suggestedReward`. It **cannot** decide the risk level, review policy, which Agent
to pick, the wallet, the chain, the route or the settlement state — all of that
belongs to the platform Core.

Nothing is created and no funds move until the user confirms the proposed DAG.

---

## About CCIP

The repository depends on `@chainlink/contracts-ccip` and its contracts import the
real interfaces:

```solidity
import {CCIPReceiver} from "@chainlink/contracts-ccip/contracts/applications/CCIPReceiver.sol";
import {IRouterClient} from "@chainlink/contracts-ccip/contracts/interfaces/IRouterClient.sol";
```

`RealCCIPSettlementSender` builds `Client.EVM2AnyMessage`, quotes fees with
`IRouterClient.getFee` and sends with `ccipSend`. `DynamicCCIPWorkflowSender` and
`DynamicCCIPWorkflowReceiver` follow the same shape.

**This is a CCIP-shaped local implementation, not a CCIP integration.** Tests run
against `contracts/src/mocks/MockRealCCIPRouter.sol`, no real router address is
configured anywhere in the repository, and the default local experiment wires the
local relay instead. The deployment scripts in `scripts/` target Ethereum Sepolia
and Arbitrum Sepolia but are intentionally paused until gas, LINK fees and
settlement tokens are available. **No public-testnet delivery receipt is claimed.**

---

## Project layout

```text
apps/api                 Platform API and Coordinator Core (FastAPI)
apps/web                 Web console (Next.js)
services/coordinator-agent   User-facing Coordinator Agent
services/agent-runner    Agent execution runtime, three isolated processes
services/agent-signer    Zero-RPC Agent Signer Sidecar (optional topology)
services/chain-worker    Chain listener and restricted relayer
tools/local-requester-client  Requester wallet and workflow driver
contracts/src            33 Solidity contracts, including mocks
contracts/interfaces     Contract interface drafts
scripts/local            Start, stop, onboarding and health-check scripts
experiments              Experiment runners and exported evidence
docs                     Architecture, delivery and experiment documentation
infra                    Docker Compose support
```

---

## Common commands

| Command | Purpose |
|---|---|
| `pnpm local:experiment:start` / `:stop` | Start or stop the full stack |
| `pnpm local:agents:connect` | Register Agents with the platform |
| `pnpm local:stack:check` | Read-only health report |
| `pnpm local:v1` | Local V1 platform only (web on 3000) |
| `pnpm experiment:real-agents` | Run the real-Agent experiment suite |
| `pnpm experiment:summary` | Summarise exported experiment data |
| `pnpm requester -- <command>` | Requester wallet: `wallet:create`, `faucet`, `fund`, `run` |
| `pnpm local:mock-agent-c:stop` | Stop Runner C to inject a real failure |

---

## Troubleshooting

| Symptom | Cause | Fix |
|---|---|---|
| `port 8200 is already owned by PID …` | Orphan process from a previous run | `pnpm local:experiment:stop`, or end that PID |
| `409 A funding transaction hash has already been used` | Chains reset but the ledger did not | Delete `.runtime\local-experiment` and restart |
| Agent is not matchable | `local:agents:connect` not run, or heartbeat older than 60 s | Re-run `local:agents:connect` |
| Coordinator stays `mock` | Variables never reached the service process | Set them in the **same terminal** before start; or set `COORDINATOR_LLM_PROVIDER` explicitly |
| Code changes have no effect | Services run old code | **Any code change requires a restart** |
| Requester reports insufficient balance | Balances are zero after a chain reset | `pnpm requester -- faucet --token 100 --native 1` |
| `ELIFECYCLE Command failed with exit code 1` | Not a crash | The check script returns 1 when it finds a problem; read the lines above |
| `did not become ready at …/health` while the service log says `Uvicorn running` | A machine-wide `HTTP_PROXY` swallowed the readiness probes. PowerShell 5.1 `Invoke-RestMethod` reads `HTTP_PROXY` but ignores `NO_PROXY`, so probes to `127.0.0.1` go to the proxy and never arrive | The launcher clears the proxy variables for its own run. If you export them yourself, unset them before starting |
| `local:experiment:stop` leaves processes running | `platform.json` / `experiment.json` are missing, so stop has no PIDs to kill | End the PIDs manually, see below |

To tell whether a listener is an orphan, compare the `runtimeId` returned by its
`/health` against `.runtime\local-experiment\platform.json`. A mismatch means it
belongs to an earlier run.

If stop cannot find the processes, release the ports directly:

```powershell
foreach ($p in 3100,8200,8300,8400,8111,8112,8113,8545,8546,8547,8548) {
    Get-NetTCPConnection -State Listen -LocalPort $p -ErrorAction SilentlyContinue |
        Select-Object -ExpandProperty OwningProcess -Unique |
        ForEach-Object { Stop-Process -Id $_ -Force -ErrorAction SilentlyContinue }
}
Remove-Item -Recurse -Force .runtime\local-experiment
```

---

## Known limitations

1. **Inference defaults to a stub.** A `mock` run validates the protocol path
   only, not Agent reasoning ability.
2. **Two capabilities only**: `research` and `verification`. A model may choose
   any node count (1–20) but must use a capability that exists in the market.
3. **No real CCIP.** Local mock router only; see [About CCIP](#about-ccip).
4. **Chains reset on every restart**, so historical workflows are not preserved.
5. **Windows only.** Every orchestration script is PowerShell.

---

## Security notes

The launcher scripts contain private keys for the Agent home wallets
(`0x5de4…`, `0x7c85…`, `0x47e1…`). **These are the publicly documented Hardhat
default test accounts.** They have no value outside the local `31337`–`31340`
chains and are committed so the project reproduces from a clean clone. Never reuse
them on a public network and never swap in a key that holds real funds. Automated
secret scanners will flag them; that is expected.

`.gitignore` covers `.runtime/`, `.venv/`, `node_modules/`, `.env` and its
variants, `*keystore*.json`, `*.db`, `data/`, `hardhat-artifacts/`,
`hardhat-cache/`, `*.bak`, `*.zip` and `.workbuddy/`. Keep model endpoints and any
API keys in user environment variables.

To check before pushing to a public remote:

```powershell
git grep -nI "sk-[A-Za-z0-9]\{16,\}"    # must print nothing
```

---

## Documentation

| Document | Contents |
|---|---|
| [docs/START_AND_INTERNET_DEPLOYMENT_ZH.md](docs/START_AND_INTERNET_DEPLOYMENT_ZH.md) | Chinese guide: daily startup, troubleshooting, publishing |
| [docs/LOCAL_EXPERIMENT.md](docs/LOCAL_EXPERIMENT.md) | Full local experiment: wallets, Runner isolation, settlement, evidence |
| [docs/REAL_LLM_ONBOARDING_ZH.md](docs/REAL_LLM_ONBOARDING_ZH.md) | Model configuration matrix and disclosure requirements |
| [docs/LOCAL_V1.md](docs/LOCAL_V1.md) | Local V1 topology and implementation map |
| [docs/risk-based-review.md](docs/risk-based-review.md) | LOW/MEDIUM/HIGH review policies and settlement gates |
| [docs/REVIEW_V2_AND_AGENT_SIGNING.md](docs/REVIEW_V2_AND_AGENT_SIGNING.md) | EIP-712 approvals and Agent-owned signing |
| [docs/REAL_CCIP_INTEGRATION.md](docs/REAL_CCIP_INTEGRATION.md) | Steps for a real CCIP integration |
| [docs/EXPERIMENT_V2_RUNBOOK_ZH.md](docs/EXPERIMENT_V2_RUNBOOK_ZH.md) | Controlled multi-chain experiment V2 runbook |

---

(Note: The code is from vibe coding. The code will be continuously revised and improved in the future. Stay tuned.)
