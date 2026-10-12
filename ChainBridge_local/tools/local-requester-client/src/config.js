import { existsSync, readFileSync } from "node:fs";
import path from "node:path";
import { fileURLToPath } from "node:url";

import { readJson } from "./utils.js";

export const ROOT_DIR = path.resolve(path.dirname(fileURLToPath(import.meta.url)), "..");

function loadDotEnv() {
  const file = path.join(ROOT_DIR, ".env");
  if (!existsSync(file)) return;
  for (const rawLine of readFileSync(file, "utf8").split(/\r?\n/)) {
    const line = rawLine.trim();
    if (!line || line.startsWith("#")) continue;
    const separator = line.indexOf("=");
    if (separator < 1) continue;
    const key = line.slice(0, separator).trim();
    let value = line.slice(separator + 1).trim();
    if ((value.startsWith('"') && value.endsWith('"')) || (value.startsWith("'") && value.endsWith("'"))) {
      value = value.slice(1, -1);
    }
    if (process.env[key] === undefined) process.env[key] = value;
  }
}

function absolute(value) {
  return path.isAbsolute(value) ? value : path.join(ROOT_DIR, value);
}

export async function loadConfig() {
  loadDotEnv();
  const file = process.env.REQUESTER_CONFIG || path.join(ROOT_DIR, "config.json");
  const raw = await readJson(file);
  const config = {
    ...raw,
    platformUrl: (process.env.PLATFORM_API_URL || raw.platformUrl).replace(/\/$/, ""),
    coordinatorUrl: (process.env.COORDINATOR_URL || raw.coordinatorUrl).replace(/\/$/, ""),
    requesterDid: process.env.REQUESTER_DID || raw.requesterDid,
    platformToken: process.env.PLATFORM_API_TOKEN || "",
    coordinatorToken: process.env.COORDINATOR_API_TOKEN || "",
    walletFile: absolute(raw.walletFile),
    stateFile: absolute(raw.stateFile),
    runsFile: absolute(raw.runsFile || "data/requester-runs.json"),
    trustedDeploymentFile: absolute(raw.trustedDeploymentFile),
    outputDirectory: absolute(raw.outputDirectory),
    serviceHost: process.env.REQUESTER_SERVICE_HOST || raw.serviceHost || "127.0.0.1",
    servicePort: Number(process.env.REQUESTER_SERVICE_PORT || raw.servicePort || 8400),
    serviceToken: process.env.LOCAL_REQUESTER_API_TOKEN || raw.serviceToken || "",
    runtimeId: process.env.LOCAL_PLATFORM_RUNTIME_ID || raw.runtimeId || "local-requester-standalone",
    autoFundTokenTarget: process.env.REQUESTER_AUTO_FUND_TOKEN_TARGET || raw.autoFundTokenTarget || "100",
    autoFundNativeTarget: process.env.REQUESTER_AUTO_FUND_NATIVE_TARGET || raw.autoFundNativeTarget || "1",
    retryBaseMs: Number(process.env.REQUESTER_RETRY_BASE_MS || raw.retryBaseMs || 2000),
    retryMaxMs: Number(process.env.REQUESTER_RETRY_MAX_MS || raw.retryMaxMs || 30000),
  };
  if (config.mode !== "LOCAL_EXPERIMENT") throw new Error("This client is restricted to LOCAL_EXPERIMENT mode");
  return config;
}
