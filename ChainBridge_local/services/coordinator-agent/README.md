# Coordinator Agent

This local service is the user-facing planning layer. A deterministic mock,
DeepSeek, or OpenAI-compatible provider proposes a business-level DAG. The
platform Coordinator Core then validates the graph and budget, recalculates
risk and review policy, queries matchable Agents and routes, and returns the
authoritative plan. Explicit user confirmation is required before workflow or
funding state is created. The service never receives a user or Agent wallet
private key.

The LLM may propose only node keys, dependencies, capabilities, acceptance
criteria, task payloads, suggested rewards, retry limits, and explanations. It
cannot choose risk levels, reviewers, Agents, wallets, chains, routes,
settlement eligibility, or payment state. Those fields remain under
Coordinator Core control.

Endpoints:

- `GET /health`
- `GET /manifest`
- `POST /identity-signatures`
- `POST /test-plan`
- `POST /platform-registration`
- `POST /chat`
- `GET /conversations/{conversationId}`
- `POST /conversations/{conversationId}/refine`
- `POST /conversations/{conversationId}/workflows`
- `POST /conversations/{conversationId}/coordinate`
- `POST /conversations/{conversationId}/failure-advice`
- `GET /conversations/{conversationId}/summary`

`POST /chat` creates a validated draft only. `refine` replaces an unconfirmed
draft after another user turn. `failure-advice` is advisory and cannot mutate
workflow or money state. `summary` returns the latest authoritative workflow
and node outputs after confirmation.

The Web `/workflows/new` flow calls this service through a same-origin Next.js
server proxy that first resolves an approved, online `COORDINATOR` profile in
the platform Agent directory. The Coordinator token remains server-side. Plan preview,
multi-turn refinement, and workflow confirmation all use the same conversation
draft so the displayed DAG is the DAG submitted for authoritative creation.

Use `COORDINATOR_LLM_PROVIDER=mock` for reproducible local experiments. Set it
to `deepseek` and provide `DEEPSEEK_API_KEY` only when testing real model
behavior.

For an OpenAI-compatible provider, configure:

```text
COORDINATOR_LLM_PROVIDER=openai_compatible
COORDINATOR_OPENAI_BASE_URL=https://provider.example/v1
COORDINATOR_OPENAI_API_KEY=...
COORDINATOR_OPENAI_MODEL=provider-model
COORDINATOR_OPENAI_API_MODE=responses
```

`COORDINATOR_OPENAI_API_MODE` supports `responses` and `chat_completions`.
The local experiment launcher can load these values from the file named by
`COORDINATOR_LLM_ENV_FILE`. A file containing `LLM_BASE_URL`, `LLM_API_KEY`,
`LLM_MODEL`, and `LLM_PROVIDER` is mapped to the Coordinator-specific variables
without printing the secret values. Explicit Coordinator variables take
precedence over values loaded from the file.

Coordinator Agents use a stable secp256k1 DID identity and signed heartbeat,
but do not register an execution-chain wallet, payout route, or Gas balance.
Set `COORDINATOR_PLATFORM_REGISTRATION_PATH` to persist the selected platform
profile across service restarts. Production must provide
`COORDINATOR_AGENT_PRIVATE_KEY`; development can derive a stable identity from
`DEV_IDENTITY_SEED`.
