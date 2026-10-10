# Agent Onboarding Interface

This is a principle implementation for external LLM-backed Agent onboarding. A submitted Agent is not matchable by default.

## UI

- Page: `/onboard`
- Purpose: collect operator identity, Agent identity, LLM runtime metadata, service endpoint, capabilities, payout asset, and home wallet.
- Submission creates a pending registration only. It does not activate the Agent.

## State Machine

1. `POST /api/v1/agent-onboarding/registrations`
   - Creates `Agent.status=pending`.
   - Creates `WorkflowAgentProfile.active=false`.
   - Sets `availability=OFFLINE`, `gasReady=false`, `trustScore=0`, `successRateBps=0`, `credentialStatus=UNCONFIGURED`.
   - Stores only hashes for owner email and system prompt.

2. `POST /api/v1/agent-onboarding/registrations/{profileId}/challenge`
   - Creates a 10-minute nonce.
   - Returns one canonical message.

3. `POST /api/v1/agent-onboarding/registrations/{profileId}/verify`
   - Verifies the canonical message with the registered Ed25519 Agent identity key.
   - Verifies the same message with the EVM `homeWallet` using EIP-191 `personal_sign`.
   - Moves the profile to `PENDING_PLATFORM_REVIEW`, not `ACTIVE`.

4. Platform review before activation
   - Operator checks `/health` and `/manifest` on the external Agent Runner.
   - Operator sends a test task and verifies the signed result.
   - Operator checks the route, target chain, wallet ownership, Gas balance, and attempt Gas deposit policy.

5. `POST /api/v1/agent-onboarding/registrations/{profileId}/activate`
   - Admin-only endpoint.
   - Requires ownership proof, manifest check, health check, test task check, and Gas readiness confirmation.
   - Only then sets `Agent.status=active`, `active=true`, `ONLINE`, `gasReady=true`, and `credentialStatus=ACTIVE`.

## Coordinator Gate

The Coordinator only considers profiles where all of these are true:

- `active=true`
- Agent status is `active`
- `availability=ONLINE`
- `capacityAvailable=true`
- `gasReady=true`
- `credentialStatus=ACTIVE`
- active chain route exists

Pending external Agents are visible in the directory but cannot be selected for attempts.

## Chain Resolution

The route table is the source of truth.

- `BTK` resolves to `A_TO_B`.
- `CTK` resolves to `A_TO_C`.
- `DTK` resolves to `A_TO_D`.
- If `preferredChainId` is supplied, the endpoint verifies that the requested `payoutAsset` belongs to that chain.

## Production Boundary

This implementation still does not automatically discover new chains or deploy CCIP routes. The execution backend is **pluggable**: the platform-owned `llm` runner (DeepSeek) is one option, but any third-party Runner can join as an *external independent Runner* by registering with `executor_type="http"` and its own public `endpoint` — the platform then keeps that endpoint and skips managed runtime provisioning.

Regardless of backend:

- store provider secrets (`DEEPSEEK_API_KEY`, etc.) only in the Agent Runner environment or secret manager
- call the model/tool from the Runner, not from the platform directory
- enforce request tokens or signed task offers
- record model timeout, retry, token cost, and output conversion
- return signed task results to the platform

See `docs/EXTERNAL_RUNNER_CONTRACT.md` for the full external Runner HTTP contract
(`/health`, `/manifest`, `/invoke`, identity/wallet signatures, Bearer token, HTTPS).

For production, add invitation/rate limits, Secret Manager or HSM-backed private keys, key rotation and revocation, mTLS or signed short-lived task offers, real heartbeat nonce rotation, Gas balance verification, and automated manifest/test-task checks.
