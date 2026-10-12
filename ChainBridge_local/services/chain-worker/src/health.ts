import { createServer, type Server } from "node:http";

import type { StateStore } from "./state.ts";

export type WorkerHealth = {
  startedAt: string;
  lastCycleAt?: string;
  sourceScanAt?: string;
  destinationScanAt?: string;
  relayerAt?: string;
  relayerBalanceWei?: string;
  lastError?: string;
};

export function startHealthServer(port: number, state: StateStore, health: WorkerHealth): Server {
  return createServer((request, response) => {
    if (request.url === "/health") {
      const cursors = {
        sourceNextBlock: state.getStoredCursor("source")?.toString(),
        destinationNextBlock: state.getStoredCursor("destination")?.toString(),
      };
      response.writeHead(200, { "Content-Type": "application/json" });
      response.end(JSON.stringify({ status: health.lastCycleAt ? "ok" : "starting", ...health, ...cursors, ...state.metrics() }));
      return;
    }
    if (request.url === "/metrics") {
      const metrics = state.metrics();
      response.writeHead(200, { "Content-Type": "text/plain; version=0.0.4" });
      response.end([
        `chain_worker_pending_events ${metrics.pendingEvents}`,
        `chain_worker_dead_letters ${metrics.deadLetters}`,
        `chain_worker_relayer_submissions ${metrics.submissions}`,
        `chain_worker_prepared_submissions ${metrics.preparedSubmissions}`,
        `chain_worker_source_next_block ${state.getStoredCursor("source") ?? 0n}`,
        `chain_worker_destination_next_block ${state.getStoredCursor("destination") ?? 0n}`,
        `chain_worker_relayer_balance_wei ${health.relayerBalanceWei ?? "0"}`,
        "",
      ].join("\n"));
      return;
    }
    response.writeHead(404).end();
  }).listen(port, "0.0.0.0");
}
