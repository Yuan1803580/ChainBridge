import { existsSync } from "node:fs";

import { readJson, writeJsonAtomic } from "./utils.js";

const TERMINAL_RUN_STATUSES = new Set(["COMPLETED", "CANCELLED"]);

export async function loadRunState(file) {
  if (!existsSync(file)) return new Map();
  const document = await readJson(file);
  const items = Array.isArray(document?.runs) ? document.runs : [];
  return new Map(items
    .filter((item) => item && typeof item.conversationId === "string")
    .map((item) => [item.conversationId, { ...item }]));
}

export async function saveRunState(file, runs) {
  await writeJsonAtomic(file, {
    version: 1,
    updatedAt: new Date().toISOString(),
    runs: [...runs.values()],
  });
}

export function isResumableRun(state) {
  return Boolean(state && !TERMINAL_RUN_STATUSES.has(state.status));
}

export function retryDelay(retryCount, baseMs, maxMs) {
  const exponent = Math.max(0, Math.min(Number(retryCount) - 1, 10));
  return Math.min(maxMs, baseMs * (2 ** exponent));
}
