import { LocalProtocolWorker, loadLocalProtocolConfig } from "./local-protocol.ts";

const worker = new LocalProtocolWorker(loadLocalProtocolConfig());
await worker.initialize();

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
