import { createServer, type Server } from "node:http";

import { scannerDiagnostics } from "./scanner.ts";
import type { StateStore } from "./state.ts";

export type StepFailure = {
  message: string;
  since: string;
  consecutive: number;
};

export type WorkerHealth = {
  startedAt: string;
  lastCycleAt?: string;
  sourceScanAt?: string;
  destinationScanAt?: string;
  relayerAt?: string;
  relayerBalanceWei?: string;
  /** Most recent failure of any step, kept for backwards compatibility. Historical, not current. */
  lastError?: string;
  /**
   * Steps that are failing RIGHT NOW, keyed by step name and cleared as soon as the step
   * succeeds again. `lastError` alone is sticky: a one-second DNS blip during startup used to
   * mark the worker as errored forever, which teaches operators to ignore the field.
   */
  failingSteps: Record<string, StepFailure>;
};

export function startHealthServer(port: number, state: StateStore, health: WorkerHealth): Server {
  return createServer((request, response) => {
    if (request.url === "/health") {
      const cursors = {
        sourceNextBlock: state.getStoredCursor("source")?.toString(),
        destinationNextBlock: state.getStoredCursor("destination")?.toString(),
      };
      const failingSteps = Object.keys(health.failingSteps ?? {});
      const degraded = scannerDiagnostics.abiMismatchCount > 0
        || scannerDiagnostics.duplicateConflictCount > 0
        || failingSteps.length > 0;
      response.writeHead(degraded ? 503 : 200, { "Content-Type": "application/json" });
      response.end(JSON.stringify({
        status: degraded ? "degraded" : (health.lastCycleAt ? "ok" : "starting"),
        failingStepNames: failingSteps,
        ...health,
        ...cursors,
        ...state.metrics(),
        abiMismatchCount: scannerDiagnostics.abiMismatchCount,
        lastAbiMismatchAt: scannerDiagnostics.lastAbiMismatchAt,
        lastAbiMismatchDetail: scannerDiagnostics.lastAbiMismatchDetail,
        duplicateReplayCount: scannerDiagnostics.duplicateReplayCount,
        duplicateConflictCount: scannerDiagnostics.duplicateConflictCount,
        lastDuplicateConflictAt: scannerDiagnostics.lastDuplicateConflictAt,
        lastDuplicateConflictDetail: scannerDiagnostics.lastDuplicateConflictDetail,
      }));
      return;
    }
    if (request.url === "/metrics") {
      const metrics = state.metrics();
      response.writeHead(200, { "Content-Type": "text/plain; version=0.0.4" });
      response.end([
        `chain_worker_pending_events ${metrics.pendingEvents}`,
        `chain_worker_dead_letters ${metrics.deadLetters}`,
        `chain_worker_relayer_submissions ${metrics.submissions}`,
        `chain_worker_source_next_block ${state.getStoredCursor("source") ?? 0n}`,
        `chain_worker_destination_next_block ${state.getStoredCursor("destination") ?? 0n}`,
        `chain_worker_relayer_balance_wei ${health.relayerBalanceWei ?? "0"}`,
        `chain_worker_abi_mismatch_total ${scannerDiagnostics.abiMismatchCount}`,
        `chain_worker_duplicate_replay_total ${scannerDiagnostics.duplicateReplayCount}`,
        `chain_worker_duplicate_conflict_total ${scannerDiagnostics.duplicateConflictCount}`,
        `chain_worker_failing_steps ${Object.keys(health.failingSteps ?? {}).length}`,
        "",
      ].join("\n"));
      return;
    }
    response.writeHead(404).end();
  }).listen(port, "0.0.0.0");
}
