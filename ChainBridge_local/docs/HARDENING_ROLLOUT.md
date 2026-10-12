# ChainBridge Hardening Rollout

## Non-negotiable wallet boundary

This rollout does not modify `tools/local-requester-client`, its encrypted
keystore, its password, its address or its chain-A configuration. Do not delete
local chain data, redeploy funded Escrow/Vault contracts or replace Route
manifests during hardening.

Before and after every stage record only:

- Requester address.
- Keystore SHA-256.
- Chain ID.
- Escrow/Vault address and token balance.

Never record a password, private key, mnemonic or API key in experiment data.

## Stage 1: External Agent V2

1. Start each Agent in its existing `RUNNER` signer mode.
2. Verify `/health` and `/manifest` report protocol `2.0` and RFC8785-JCS.
3. Verify `/schemas/input`, `/schemas/output` and `/artifacts`.
4. Run one test invocation and let Coordinator Core verify the signed V2
   result.
5. Keep V1 fallback enabled for an older Agent without `protocolVersion`.

Rollback: restore only the Runner and `workflow_api.py` files from the
pre-hardening ZIP. No wallet or database rollback is required.

## Stage 2: Signer Sidecar shadow validation

1. Create an Agent-specific policy with the current Agent DID, home-chain
   wallet, chain ID, deployed `RemoteWorkBoard`, allowed selector and limits.
2. Start the Sidecar with the same execution Agent secret file. Never point it
   at the Requester keystore.
3. Compare Sidecar `/v1/public` with the current manifest: DID, public key,
   wallet and chain ID must match exactly.
4. Send offline result and transaction intents to both implementations. Compare
   signer address and recovered signature, but do not broadcast shadow output.
5. Switch only that Runner to `SIGNER_MODE=SIDECAR` and provide a separate
   Runner runtime token.
6. Run onboarding challenge, heartbeat, test invoke and one zero-value
   `submitResult` transaction.

Rollback: set `SIGNER_MODE=RUNNER` and restart that Agent. The Agent address and
on-chain balances remain unchanged because the same key is used.

## Stage 3: Worker reliability

1. Keep one broadcast-authorized Worker.
2. Start a second Worker only in observation/shadow mode and confirm it cannot
   obtain the first Worker's active lease.
3. Kill the authorized Worker after a raw transaction enters `prepared` state.
4. Restart it and confirm the same transaction hash is broadcast and the
   prepared row is cleared only after the API accepts the result.
5. Test expired lease recovery, heartbeat renewal, RPC retry, dead-letter replay
   and a simulated block-hash change.

Rollback: stop the new Worker and restore Worker source files. Do not delete its
SQLite journal while a `prepared` submission exists; reconcile that hash first.

## Stage 4: Security evidence

1. Run `npm run security:offline`.
2. Preserve `run.json`, `results.csv` and raw logs under the generated run ID.
3. Create a separate runtime directory with an
   `.isolated-security-experiment` marker for fault injection.
4. Run S01-S14 at least three times per configuration.
5. Report rejection/block rates, false rejection rate, recovery time, duplicate
   payout count, lease conflict count, added latency and wallet checkpoint
   equality.

The normal local runtime must not be used for kill, reorg or forged-transaction
experiments.
