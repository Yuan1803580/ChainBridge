# Mock Agent Runner control

The local experiment already runs the built-in Mock Agents as isolated services:

- Agent-B1 / `workflow-research-b` / port 8111
- Agent-C1 / `workflow-verification-c` / port 8112
- Agent-D1 / `workflow-verification-d` / port 8113

Use the control script from the project root:

```powershell
powershell -ExecutionPolicy Bypass -File scripts/local/mock-agent-runner-control.ps1 -Action status
powershell -ExecutionPolicy Bypass -File scripts/local/mock-agent-runner-control.ps1 -Action stop -Agent C
powershell -ExecutionPolicy Bypass -File scripts/local/mock-agent-runner-control.ps1 -Action start -Agent C
```

For an NFRDA run, stop C only after its attempt starts. B and D remain online,
so the platform can preserve the settled predecessor and rematch the failed node
to D. The script validates service and runtime identities before stopping a
process; it will not terminate an unrelated listener.

The built-in Agents are marked as local experimental identities. Stopping C
therefore injects failure through its real HTTP invocation path: the Coordinator
receives a connection failure, records `AGENT_TIMEOUT`, releases the failed
attempt reservation and rematches the node. Heartbeat expiry is not used as the
failure timestamp for these built-in Agents.

An individual Runner can only be started while the matching platform API is
online. If the whole platform is stopped, use `pnpm local:experiment:start`.
