import { LocalProtocolWorker, loadLocalProtocolConfig } from "./local-protocol.ts";

const worker = new LocalProtocolWorker(loadLocalProtocolConfig());
await worker.initialize();
const staleAfterSeconds = Number(process.env.PROTOCOL_JOB_STALE_SECONDS || "3600");
const recovery = await worker.api.recoverStaleJobs(
  Number.isFinite(staleAfterSeconds) ? Math.max(0, staleAfterSeconds) : 3600,
);
if (recovery.recoveredCount > 0) {
  console.log(`protocol-worker recovered ${recovery.recoveredCount} stale RUNNING job(s)`);
}

let stopping = false;
for (const signal of ["SIGINT", "SIGTERM"] as const) {
  process.on(signal, () => { stopping = true; });
}

console.log(`protocol-worker started; source chain ${worker.config.sourceChainId}; routes are loaded dynamically from the API`);
while (!stopping) {
  try {
    const processed = await worker.processOne();
    if (!processed) await new Promise((resolve) => setTimeout(resolve, worker.config.pollIntervalMs));
  } catch (error) {
    console.error("protocol_worker_cycle_failed", error instanceof Error ? error.message : error);
    await new Promise((resolve) => setTimeout(resolve, worker.config.pollIntervalMs));
  }
}
console.log("local protocol-worker stopped");
