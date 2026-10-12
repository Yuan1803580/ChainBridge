# Security Experiments

Run the offline suite before any live fault injection:

```powershell
python experiments/security/run_security_experiments.py --mode offline
```

Each run creates an independent directory under
`experiments/results/security/` containing `run.json`, `results.csv` and raw
command logs. The collector redacts values whose names contain `key`, `token`,
`password`, `secret` or `private`.

Live fault injection requires both `--confirm-isolated` and an
`.isolated-security-experiment` marker in the supplied runtime directory. This
prevents accidental execution against the normal local system. The live mode
only performs non-funding protocol probes; chain reorg and process-kill tests
remain explicit operator actions described in `scenarios.json`.
