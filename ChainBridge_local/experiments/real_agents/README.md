# Real Agent experiment runner

This runner executes the platform-level real Agent acceptance scenarios and
exports data that can be opened directly in Excel.

Scenarios:

- REAL-F1: one real Delivery Agent completes and settles one node.
- REAL-F2: Kimi researches, Codex verifies, and Delivery creates a Markdown
  report in a three-node dependency DAG.
- REAL-F3: the first Codex attempt receives a controlled timeout fault, then a
  new Kimi attempt completes the same stable node.

Run all scenarios:

```powershell
pnpm experiment:real-agents
```

Collect an existing REAL-F1 and run only REAL-F2 and REAL-F3:

```powershell
.\.venv\Scripts\python.exe experiments\real_agents\run_real_agent_experiments.py `
  --real-f1-workflow-id 60ee4ef1-0ee1-46d2-aaf6-e11e4fc43324
```

Existing REAL-F2 and REAL-F3 workflows can also be continued or collected with
`--real-f2-workflow-id` and `--real-f3-workflow-id`. Workflow execution uses a
2200-second time budget instead of a fixed coordination-step limit.

Each immutable suite under `experiments/results/real-agents/` contains:

- `runs.csv`: one acceptance row per scenario.
- `attempts.csv`: one row per node attempt with execution, review, and
  settlement timing.
- `summary.csv` and `summary.json`: suite-level pass/fail totals.
- `<scenario>/workflow.json`: full workflow, event, ledger, and result evidence.
- `<scenario>/coordination-trace.json`: every controlled coordination step.
- `<scenario>/run.json`: scenario verdict and acceptance errors.

The Runner temporarily narrows each Agent profile to the capability used by its
assigned experiment node. This keeps all Runners online and still exercises the
normal Core eligibility and matching rules without adding a privileged
"force Agent" path. Original capabilities, capacity, availability, trust,
success rate, price, and latency values are restored in a `finally` block. The
Runner also retries a coordination call when the background recovery scheduler
briefly owns the workflow lock. REAL-F3 is a controlled platform fault
injection; it does not wait for the full production timeout window.
