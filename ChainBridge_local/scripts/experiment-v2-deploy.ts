import { resolve } from "node:path";

import { deployInitialExperiment } from "./experiment-v2-deploy-lib.ts";

const path = resolve(process.env.EXPERIMENT_V2_MANIFEST ?? ".runtime/experiment-v2/deployment.json");
const deployment = await deployInitialExperiment(path);
console.log(JSON.stringify({
  event: "experiment_v2_initial_deployment_ready",
  manifest: path,
  chains: deployment.chains.map((chain) => ({ key: chain.key, chainId: chain.chainId })),
  metrics: deployment.metrics,
}, null, 2));

