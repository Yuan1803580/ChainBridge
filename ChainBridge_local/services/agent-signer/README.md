# Agent Signer Sidecar

The Sidecar owns one execution Agent identity key and one Agent home-chain
wallet key. It never broadcasts a transaction and it is not used for the
Requester Client wallet.

The Runner defaults to `SIGNER_MODE=RUNNER`. Switch to `SIDECAR` only after a
shadow test confirms that public identity, wallet address and signed result
hashes match the current Runner.

Required settings:

```text
SIGNER_AGENT_SLUG=
SIGNER_CHAIN_ID=
SIGNER_RPC_URL=
SIGNER_SECRETS_PATH=
SIGNER_POLICY_PATH=
SIGNER_DATABASE_PATH=
SIGNER_API_TOKEN=
```

The policy must bind the Agent, chain, wallet, allowed contract addresses,
function selectors, optional Route keys, maximum value, Gas, Gas price and TTL.
The service stores only nonce and decision hashes in SQLite. It does not store
task content, model prompts, private keys or raw signed transactions.
