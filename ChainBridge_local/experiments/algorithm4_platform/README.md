# Algorithm 4 platform experiment

This benchmark drives the deployed FastAPI API and three independent HTTP Mock Agent Runners.
It uses a dedicated SQLite database per variant and never opens the Requester wallet.

Run a one-round smoke test:

```powershell
pnpm experiment:algorithm4:platform:smoke
```

Run the complete 30-round suite:

```powershell
pnpm experiment:algorithm4:platform
```

The six checkpoints are cumulative statistics after 5, 10, 15, 20, 25 and 30 actual rounds.

`ATTEMPT_GUARD_DISABLED` disables stale-attempt result rejection while retaining
the normal Agent matching policy and all independent settlement guards. Attack
outcomes are recorded from actual HTTP responses and are not forced to succeed.
