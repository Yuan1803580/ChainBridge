# Dynamic Workflow Console

This document records the implemented visual boundary for the V1 console.

## Preserved design

- The user supplies only the task objective and maximum budget.
- Funds remain in `TaskEscrow` on Task Chain A.
- The coordinator chooses an Agent before reading its `homeChainId`.
- Only `AVAILABLE` route snapshots can receive new work.
- A failed review closes the old `attemptId`; the `nodeId` remains stable.
- Settlement is created only after `PASS`, and downstream nodes wait for
  `SETTLED` rather than merely successful execution.
- Scanner, Relayer, execution Agent and system failure responsibilities remain
  distinct in the event and fee views.

## Visual proof scenario

`WF-1001` follows the acceptance scenario from the implementation plan:

1. N1 selects Agent-B1 and dynamically uses Chain B.
2. Chain B returns a result proof; N1 passes review and settles.
3. N2 attempt 1 selects Agent-C1 on Chain C.
4. The result has a valid hash but fails the immutable output criteria, so no
   reward settlement is created.
5. N2 attempt 2 selects Agent-D1 on Chain D without changing the node ID.
6. The second result passes, settles, and a late Chain C result is rejected.
7. The remaining token budget is refunded and the fee ledger is displayed.

`WF-1002` is intentionally left in `IN_REVIEW` so the dashboard has a live
operational state to inspect.

## Agent information platform additions

The directory now exposes data required for coordinator selection and route
readiness: DID state, capabilities, specialization, price floor, response and
execution SLA, concurrency, 30-day uptime, endpoint latency, trust score,
settled/rejected attempts, dispute rate, home wallet, accepted asset, gas
balance, route status/version, heartbeat and attestations.

## Proof boundary

The local console demonstrates data contracts, UI state, route/version logic,
failure switching, stale-attempt handling and accounting presentation. Public
testnet CCIP lanes, real USDC liquidity and production contract safety still
require the deployment and audit work described in the original plan.
