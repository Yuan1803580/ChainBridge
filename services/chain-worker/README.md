# Chain Worker

The worker polls the source Escrow and destination CCIP receiver with viem,
persists cursors and undelivered events in SQLite, and submits HMAC-authenticated
reconciliation events to the API. It also leases `release` and
`confirm_release` jobs and signs only the corresponding Escrow calls.

Run from the repository root after filling the chain-worker variables in
`.env.example`:

```powershell
npm run worker:start
```

`CHAIN_WORKER_ID` must have a matching API entry in `LISTENER_HMAC_KEYS`.
Inject that entry's secret into the Worker as `CHAIN_WORKER_HMAC_SECRET`; the
Worker can also select its own entry from `LISTENER_HMAC_KEYS` for local runs.
The legacy `LISTENER_HMAC_SECRET` fallback is development-only.

The worker deliberately does not classify a delayed CCIP message as failed.
An absent destination event leaves the settlement pending for operator review.
