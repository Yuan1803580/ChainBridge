# Agent Runner

This service hosts the development and workflow agents used by the platform. In
development, `DEV_IDENTITY_SEED` deterministically derives one in-memory secp256k1
operational key per agent, keeping DIDs stable across restarts without writing keys to
disk. Production requires every key through `AGENT_RUNTIME_PRIVATE_KEYS_JSON` and
refuses to start if any is missing. Private keys are never returned by the API.

The development formula is `SHA256("{DEV_IDENTITY_SEED}:{slug}")`, reduced into the
secp256k1 scalar range. With `DEV_IDENTITY_SEED=agent-market-demo`, keys match the API
seed records. DIDs default to `did:local:{slug}`; `DEV_DID_PREFIX` can override the
prefix for a separate development resolver.

## Run

```powershell
python -m pip install -r requirements.txt
$env:DEV_IDENTITY_SEED="replace-with-a-local-development-value"
uvicorn app.main:app --host 127.0.0.1 --port 8010
```

The runtime exposes:

- `GET /health` and `GET /agents`
- `GET /agents/{slug}/health`
- `GET /agents/{slug}/manifest`
- `GET /agents/{slug}/schemas/input`
- `GET /agents/{slug}/schemas/output`
- `POST /agents/{slug}/invoke`
- `POST /agents/{slug}/identity-signatures`
- `POST /agents/{slug}/wallet-transactions/sign`
- `POST /agents/{slug}/task-offers`
- `POST /agents/{slug}/platform-registration`
- `GET /agents/{slug}/platform-registration`
- `POST /agents/{slug}/chat/sessions`
- `GET /agents/{slug}/chat/sessions/{sessionId}`
- `DELETE /agents/{slug}/chat/sessions/{sessionId}`
- `POST /agents/{slug}/chat`

Set `AGENT_RUNNER_API_TOKEN` to require `Authorization: Bearer <token>` for invoke
requests. For injected identities, set `AGENT_RUNTIME_PRIVATE_KEYS_JSON` to a JSON
object mapping agent slugs to 32-byte secp256k1 private keys encoded as hex. Both
identity variables must be supplied by local environment configuration or a secret
manager and must not be committed. `DEV_IDENTITY_SEED` is rejected as a production
fallback.

Every successful invocation returns a result envelope containing the output hash,
operational public key, and an ECDSA secp256k1 signature over canonical JSON fields.
Consumers can independently validate the result provenance.

## Conversations and project evidence

Research-capable Agents (`workflow-research-b`) expose a `/chat` endpoint that
stores conversations in the Runner-owned SQLite database configured by
`AGENT_CHAT_DB_PATH`.

Before the structured handler runs, every research node receives relevant
Markdown sections retrieved from the paths in `AGENT_KNOWLEDGE_PATHS` (the
project `docs` directory by default). The evidence is injected into the handler
input as `input.projectEvidence`, so a model can ground its answer in real
project documentation instead of guessing.

Conversation and invocation endpoints require the same Bearer token as other
protected Runner operations.

## Home-chain transaction signing

Agent wallet private keys belong only to Agent Runner. Configure
`AGENT_HOME_WALLET_PRIVATE_KEYS_JSON` together with
`AGENT_WALLET_POLICIES_JSON`; production also requires
`AGENT_WALLET_NONCE_STORE_PATH`. Before signing, Runner verifies the target
chain, Agent home wallet, contract runtime bytecode, method selector,
`attemptId`, calldata, value, Gas and Gas-price limits, and a persistent request
nonce. The Protocol Worker is not allowed to load Agent wallet keys and only
broadcasts the returned raw signed transaction.

After an external Agent is activated, `/platform-registration` starts its signed
heartbeat loop. Configure `AGENT_PLATFORM_REGISTRATIONS_JSON` to restore those
registrations automatically after a production Runner restart. See
`docs/Agent部署接入与DeepSeek参数.md` for the complete deployment parameter list.
