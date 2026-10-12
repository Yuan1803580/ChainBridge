# Experiment data workflow

The project keeps immutable raw runs under `experiments/results/` and builds a
separate overview instead of rewriting historical evidence.

Generate the latest overview after the protocol, security, functional and real
Agent experiments have completed:

```powershell
pnpm experiment:summary
```

The generated `experiments/results/overview/<suite-id>/` directory contains:

- `overview.csv`: one row per experiment category.
- `scenario-summary.csv`: protocol, security, F1-F7 and REAL-F1-F3 rows.
- `protocol-runs.csv`: per-run transaction latency, gas and invariant checks.
- `overview.json`: machine-readable totals, source directories and verdicts.

`experiments/results/overview/LATEST.txt` points to the newest overview. Failed
or superseded runs remain in their original directories for audit purposes.
