# ChainBridge V1 Threat Model

## Security objective

An untrusted task, Agent, web page, document, RPC endpoint or Worker process
must not be able to move Requester funds outside an accepted workflow, sign for
another Agent, settle an old attempt, pay twice, or corrupt the evidence used
for final settlement.

## Protected assets

1. Requester Client keystore, password and source-chain budget.
2. Execution Agent identity keys and home-chain wallet keys.
3. `workflowId/nodeId/attemptId/reservationId/settlementId` bindings.
4. Escrow and Vault balances, payout uniqueness and refund correctness.
5. Agent result, JCS hash, signature and artifact integrity.
6. Worker cursors, leases, raw transaction journal and chain evidence.

The Requester wallet is outside the Agent Signer Sidecar trust boundary. The
Sidecar must never receive its keystore, password, private key or transaction
signing requests.

## Trust boundaries

- **Requester boundary:** Local Requester Client signs only user-authorized
  source-chain funding and cancellation actions.
- **Coordinator Core boundary:** validates DAG, budgets, Agent eligibility,
  attempts, state transitions and settlement uniqueness.
- **Runner boundary:** executes models and controlled tools but owns no private
  key when `SIGNER_MODE=SIDECAR`.
- **Signer boundary:** holds one execution Agent's keys and signs only policy
  constrained payloads. It never broadcasts.
- **Worker boundary:** observes chains, leases settlement work, persists signed
  raw transactions and broadcasts them idempotently.
- **Contract boundary:** Escrow/Vault/Route contracts enforce on-chain state and
  final value movement.

## Adversaries and experiments

| ID | Threat | Control | Expected result |
|---|---|---|---|
| S01 | Forged Agent signature | ES256K + registered public key | Rejected before review or settlement |
| S02 | Result changed after signing | RFC 8785 JCS output hash | Hash/signature mismatch |
| S03 | Signing nonce replay | Sidecar and integration nonce stores | HTTP 409, no second signature/broadcast |
| S04 | Late old attempt result | current-attempt check | Old attempt remains failed, no payout |
| S05 | Duplicate settlement event/payment | event ID and settlement idempotency | One state transition and one payout |
| S06 | Unknown contract/selector | Signer contract and selector allowlist | Signing denied |
| S07 | Excessive value/Gas/Gas price | Signer numeric policy ceilings | Signing denied |
| S08 | Relay process crash after signing | prepared raw transaction journal | Same raw transaction is resumed |
| S09 | RPC interruption | persistent queue + exponential retry | Event remains pending or enters DLQ |
| S10 | Chain reorganization | confirmation depth + block hash audit | Orphan reported and state reconciled |
| S11 | Worker lease contention | atomic conditional lease + lease token | At most one active owner |
| S12 | Artifact path traversal | controlled roots + generated IDs | Files outside roots are unreachable |
| S13 | Malicious prompt/PDF asks for payment | Runner has no key in Sidecar mode | No signing capability available to tools |
| S14 | Requester wallet regression | address and encrypted keystore checkpoint | Address/keystore hash unchanged |

## Security invariants

- A settled `attemptId` can produce at most one effective payout.
- A failed or superseded attempt cannot become the current attempt by sending a
  late result.
- A signature covers its protocol version and complete workflow context.
- A Worker task has one active lease owner and one transaction hash binding.
- A crash cannot replace a prepared transaction with a different transaction.
- Private keys, API keys and wallet passwords never enter experiment outputs.
- Security fault injection runs only against an explicitly marked isolated
  runtime, never the normal local runtime or a funded testnet deployment.

## Residual risks

- A compromised host can inspect process memory; production deployment still
  needs OS account separation or a hardware/KMS signer.
- The local four-chain environment cannot reproduce every public-chain reorg,
  RPC censorship or fee-market behavior.
- A valid but low-quality Agent result may pass cryptographic checks; semantic
  review remains a separate control.
