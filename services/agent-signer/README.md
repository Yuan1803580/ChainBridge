# Agent Signer Sidecar

The Agent Signer Sidecar is the only managed-platform process that loads an
Agent identity private key or Agent EVM wallet private key. It has no RPC client
and never broadcasts transactions. Runner requests are authenticated per Agent;
Worker observations and status updates are independently authenticated with an
HMAC.

## Configuration

Copy `config.example.json` to an ACL-protected location outside the repository.
Set `AGENT_SIGNER_CONFIG_FILE` to that path. Each entry binds one credential to
one `runtimeId`, DID, wallet, route, scope set, contract allowlist, and fee
budget.

The value in `credentialSha256` is the lowercase SHA-256 digest of the random
bearer credential stored for the Runner through `signerCredentialRef`. Store
the raw credential in an ACL-protected file when possible; do not put it in the
Sidecar config.

`identityKeyFile` must contain an unencrypted PEM secp256k1 private key.
`walletKeyFile` must contain one 32-byte EVM private key. The Sidecar verifies
that it derives the configured `homeWallet` before signing.

`allowedContracts` must contain the deployed `RemoteWorkBoard` address for the
configured route. The Sidecar reloads the config file before each authenticated
request, so this address can be added after the route deployment becomes
`READY` without restarting the process. An empty list cannot sign a result
transaction.

Required environment:

```text
APP_ENV=production
AGENT_SIGNER_CONFIG_FILE=C:/secure/agent-signer/config.json
AGENT_SIGNER_DATABASE_URL=sqlite:///C:/secure/agent-signer/state.db
SIDECAR_STORAGE_KEY=<Fernet key>
WORKER_INTENT_HMAC_SECRET=<Worker-only intent/status HMAC secret>
```

Generate `SIDECAR_STORAGE_KEY` with a cryptographically secure 32-byte random
value encoded as URL-safe base64. It encrypts persisted raw transactions; it is
not an Agent wallet key.

## Self-Service Provisioning

Self-service onboarding adds a loopback-only `POST /v1/provision` endpoint. It
is disabled unless both `SIDECAR_PROVISIONING_TOKEN` and `SIDECAR_KEY_DIR` are
configured. Use a dedicated random token; do not reuse an Agent bearer token.

The endpoint atomically creates an ES256K identity key, an EVM testnet wallet,
and a per-Agent bearer credential below `SIDECAR_KEY_DIR/<runtimeId>`. It updates
the structured Sidecar config under a cross-process lock. A retry with the same
`runtimeId` and fields returns the same public identity; conflicting fields are
rejected. Private keys and the raw bearer value are never returned.

The calling Agent process needs the same `SIDECAR_PROVISIONING_TOKEN` in its own
local environment while it runs the onboarding script. The managed launcher
removes that token before starting API, Runner, Web, and Worker.

## API

- `POST /v1/provision` (loopback and provisioning token only)
- `POST /v1/sign/identity-message`
- `POST /v1/sign/runtime-result` (Runner invocation attestation)
- `POST /v1/sign/canonical-result`
- `POST /v1/sign/eip191`
- `POST /v1/sign/transaction`
- `POST /v1/transactions/{requestId}/broadcast-confirmation`
- `POST /v1/transactions/{requestId}/chain-status`
- `GET /health`

The transaction endpoint accepts only Worker-HMAC-authenticated EIP-1559
intents. It checks route, contract, ABI fields, canonical-result binding, Gas and
fee caps, and atomically allocates the nonce. Replacement and cancel requests
must reference a locally known unconfirmed request and preserve its nonce.

## Database

Run the Sidecar migration chain independently:

```powershell
Set-Location services/agent-signer
..\..\.venv\Scripts\python.exe -m alembic -c alembic.ini upgrade head
```

The database stores encrypted raw transactions, canonical-result bindings,
nonce reservations, and Worker-signed chain status evidence. It does not store
plaintext private keys or bearer credentials.
