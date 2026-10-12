import { resolve } from "node:path";

import { addChainD } from "./experiment-v2-deploy-lib.ts";

const path = resolve(process.env.EXPERIMENT_V2_MANIFEST ?? ".runtime/experiment-v2/deployment.json");
const deployment = await addChainD(path);
console.log(JSON.stringify({
  event: "experiment_v2_chain_d_active",
  manifest: path,
  chain: deployment.chains.find((chain) => chain.chainId === 31340),
  destination: deployment.destinations["31340"],
}, null, 2));

