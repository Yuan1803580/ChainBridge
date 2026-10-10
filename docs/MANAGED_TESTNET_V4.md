# Managed Testnet V4 Runbook

This runbook brings up the managed DeepSeek Runner, zero-RPC Agent Signer
Sidecar, Worker-only transaction broadcaster, and the Ethereum Sepolia to
Arbitrum Sepolia CCIP-BnM route.

It does not claim a live testnet result until `pnpm testnet:verify-acceptance`
passes against real receipts and saved evidence.

## 1. Fixed Network Boundary

```text
Route: SEPOLIA_TO_ARBITRUM_SEPOLIA_CCIP_BNM_V1 version 1
Source: Ethereum Sepolia (11155111)
Destination/home: Arbitrum Sepolia (421614)
Transport: CHAINLINK_CCIP
Settlement: ccip_direct
Asset: CCIP-BNM on both chains
```

On the deployment day, obtain Router, selector, and CCIP-BnM token addresses
from the official Chainlink CCIP Directory/Explorer. Run the configuration
verifier before any deployment. It checks both RPC chain IDs, code at every
configured address, token symbol/decimals, and emits code hashes. Never replace
CCIP-BnM with MockUSDC in this profile.

Acquire CCIP-BnM through the current official Chainlink test-token faucet or
faucet contract documented for Ethereum Sepolia. Verify the token address
against the same Directory entry used by this deployment. The source payer
needs CCIP-BnM; the Agent receives minted CCIP-BnM on Arbitrum Sepolia.

## 2. Accounts and Funds

Use separate testnet-only accounts:

- Protocol relayer/deployer: Sepolia ETH and Arbitrum Sepolia ETH.
- Requester/payer: Sepolia ETH and enough source CCIP-BnM for the workflow.
- Agent `homeWallet`: Arbitrum Sepolia ETH for `submitResult` Gas.
- Agent identity: a distinct secp256k1 identity key.

The protocol relayer key remains in `services/chain-worker/src/signer.ts`'s
local-file path. It is not the Agent wallet and is not supported by
`rustok-wallet` for contract deployment.

## 3. Local Secrets

Keep all values in machine-local secret configuration. Do not paste them into
chat, commit them, or place them in `.env.example`.

Required service secrets:

```text
APP_SECRET
PLATFORM_ADMIN_DID
PLATFORM_ADMIN_PUBLIC_KEY
AGENT_RUNNER_API_TOKEN
RUNNER_CONTROL_HMAC_SECRET
WORKER_INTENT_HMAC_SECRET
PROTOCOL_WORKER_API_TOKEN
LISTENER_HMAC_KEYS
PROTOCOL_WORKER_LISTENER_SECRET
AGENT_SIGNER_CONFIG_FILE
SIDECAR_STORAGE_KEY
PROTOCOL_RELAYER_PRIVATE_KEY_FILE
SOURCE_RPC_URL
DESTINATION_RPC_URL
SOURCE_CCIP_ROUTER
DESTINATION_CCIP_ROUTER
SOURCE_CHAIN_SELECTOR
DESTINATION_CHAIN_SELECTOR
SOURCE_SETTLEMENT_TOKEN
DESTINATION_SETTLEMENT_TOKEN
CCIP_BNM_DECIMALS
```

To enable one-command Agent admission, also configure:

```text
MANAGED_SELF_SERVICE_ONBOARDING=true
MANAGED_SELF_SERVICE_CAPABILITIES=research
MANAGED_SELF_SERVICE_EXECUTOR_CREDENTIAL_REF=env:DEEPSEEK_API_KEY
SIDECAR_KEY_DIR=C:/secure/agent-signer/agents
SIDECAR_PROVISIONING_TOKEN=<dedicated random local provisioning token>
```

The provisioning token is inherited only by Sidecar. The Agent process that
runs `onboard-agent.ps1` also needs it in its own local environment. API,
Runner, Web, Worker, chat, and logs must never receive it.

Use either `DEEPSEEK_API_KEY` for the platform default or an ACL-protected
`file:` reference per invitation. The start script injects the platform key into
Runner only and removes it before starting API, Web, and Worker.

Use one random Sidecar bearer credential per Agent. Put its SHA-256 digest in
the Sidecar config and place the raw value in the file referenced by that
Agent's `signerCredentialRef`. Never share one credential across Agents.

## 4. Prepare the Sidecar

For manual admission, start with an ACL-protected config based on
`services/agent-signer/config.example.json`. For self-service admission, the
config may start with an empty `agents` array; Sidecar's loopback
`POST /v1/provision` creates the per-Agent keys and credential below
`SIDECAR_KEY_DIR`, then atomically updates the config after route deployment.

The identity key file and wallet key file are loaded only by Sidecar. Runner,
API, Worker, and Web must not receive those file paths.

## 5. Start Managed Services

From the repository root:

```powershell
pnpm testnet:managed:verify-config
pnpm testnet:managed:start
```

The start command synchronizes Python dependencies, then runs the independent
API, Runner, and Sidecar Alembic chains before launching Signer, Runner, API,
Web, and Worker. It writes process state and logs below
`.runtime/managed-testnet` unless `MANAGED_RUNTIME_DIR` overrides it.

Open `http://127.0.0.1:3000/admin` and confirm all four services are healthy.
The Sidecar health response must include `rpcConnected: false`.

Stop with:

```powershell
pnpm testnet:managed:stop
```

## 6. Obtain an Administrator JWT

Production disables `dev-login`. Request a DID challenge:

```http
POST {api}/api/v1/auth/challenges
Content-Type: application/json

{"did":"{PLATFORM_ADMIN_DID}","audience":"agent-market"}
```

Sign the exact returned `message` with the administrator secp256k1 key using
ECDSA-SHA256 and encode the DER signature as unpadded base64url. Exchange it:

```http
POST {api}/api/v1/auth/verify
Content-Type: application/json

{
  "challengeId":"{challengeId}",
  "did":"{PLATFORM_ADMIN_DID}",
  "audience":"agent-market",
  "signature":"{base64urlDerSignature}"
}
```

Keep the returned short-lived `accessToken` only in memory. Paste it into the
managed admin page when using that UI, or set `PLATFORM_ADMIN_API_TOKEN` only in
the shell that runs the route command.

## 7. Create and Predeploy the Route

With `API_URL` and the short-lived administrator token set:

```powershell
pnpm testnet:managed:create-route
```

The command creates the fixed route and queues `DEPLOY_ROUTE_STACK`. Worker is
the only broadcaster and deploys the source stack, `RemoteWorkBoard`, identity
registry, CCIP sender, and CCIP receiver. Poll:

```http
GET {api}/api/v1/route-deployments
```

Continue only when the matching deployment is `READY`. Its manifest is saved as
`.runtime/managed-testnet/route-{deploymentId}.json` and contains transaction
hashes, contract addresses, code hashes, and `manifestHash`.

Manual entries must add `contracts.remoteWorkBoard` from that manifest to
`allowedContracts`. Self-service provisioning receives this same deployed
address from the public route record. The Sidecar reloads its local policy file
on the next request; no RPC lookup is used.

## 8. Self-Service Onboard OpenClaw

After enabling the policy above, send OpenClaw only the public skill URL:

```text
http://localhost:3000/skills/agent-market-onboarding/SKILL.md
```

OpenClaw runs the linked `onboard-agent.ps1` with operator name/email, Agent
name, and a unique Agent key. Sidecar creates the identity and testnet wallet;
API issues a short-lived invitation bound to that exact Agent key, DID, Runtime
ID, wallet, and identity public-key fingerprint. Capabilities, route, and the
DeepSeek credential reference come only from server policy. Do not provide an
invitation, Runner token, wallet key, or DeepSeek key.

Managed `/complete` uses the API process's server-side
`RUNNER_CHECK_API_TOKEN`, and Runner restores Sidecar-signed heartbeats from its
own database after restart. The script returns the public wallet address that
must receive Arbitrum Sepolia ETH before result submission.

The registration should move through:

```text
PENDING_PROVISION -> PROVISIONED -> PENDING_VERIFICATION
-> ACTIVE_AWAITING_HEARTBEAT -> ACTIVE
```

Invitation failures become `USED_FAILED` and are retryable only for the same
registration. Use `/admin` to retry provisioning or run reconciliation; do not
issue a second registration with the reserved invitation.

## 9. Run a Real Workflow

Create and fund a workflow with source CCIP-BnM through the normal workflow API.
The managed Agent must be `ACTIVE`, `matchable`, Gas-ready, and capable of the
requested capability. Runner calls DeepSeek, Sidecar signs the canonical result,
Worker constructs and HMAC-signs the intent, Sidecar allocates/signs the Agent
nonce, and Worker alone broadcasts.

For an explicit unconfirmed Agent transaction cancel, an administrator calls:

```http
POST {api}/api/v1/workflows/{workflowId}/nodes/{nodeId}/attempts/{attemptId}/agent-transaction-cancel
Authorization: Bearer {adminJwt}
Idempotency-Key: {uniqueValue}
Content-Type: application/json

{"reason":"operator-approved cancellation reason"}
```

Worker first proves the original transaction is `PENDING` or `DROPPED`; Sidecar
reuses the original nonce and signs only a zero-value self-transfer. Confirmed
transactions cannot be cancelled.

## 10. CCIP Completion and Evidence

The expected sequence is:

```text
Agent submitResult confirmed on Arbitrum Sepolia
-> source CCIP settlement sent
-> target CCIP-BnM mint and CCIPWorkflowSettlementReceived confirmed
-> target balanceOf(homeWallet) delta verified
-> source confirmSettlement broadcast and confirmed
```

Worker waits up to 20 minutes by default and alerts at 60 minutes. It persists
message ID, block number, block hash, and confirmation state so reorg recovery
does not rely on one receipt observation.

Create `docs/testnet-acceptance.json` from
`docs/testnet-acceptance.example.json`, fill only public IDs, hashes, and local
evidence paths, then run:

```powershell
pnpm testnet:verify-acceptance
```

Acceptance succeeds only after validating the canonical signature, Worker HMAC
intent, raw transaction sender and calldata, CCIP send/receive/confirmation
events, historical CCIP-BnM balance delta, confirmation depth, deployed
bytecode hashes, manifest hash, and final API attempt state.
