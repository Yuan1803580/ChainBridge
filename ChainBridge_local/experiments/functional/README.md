# Functional experiment runner

Run the seven functional scenarios three times each:

```powershell
.\.venv\Scripts\python.exe experiments\functional\run_functional_experiments.py --rounds 3
```

The runner creates an immutable suite directory under
`experiments/results/functional/`. Every round contains `run.json`, a redacted
raw log and, for four-chain protocol cases, `protocol-result.json`.

The suite-level `runs.csv`, `summary.csv` and `summary.json` are the files used
for analysis. A failed run is retained and never silently removed.

Scenario mapping:

- F1: single-node settlement and balance preservation.
- F2: dependent two-node DAG settlement.
- F3: failed attempt followed by a new Agent attempt.
- F4: failed Coordinator review cannot create settlement.
- F5: Relay enqueue crash and idempotent recovery.
- F6: failed target payout retained in Vault and retried.
- F7: delayed superseded attempt rejection.

Hardhat uses on-demand mining. Durations are functional regression evidence,
not public-chain performance measurements.
