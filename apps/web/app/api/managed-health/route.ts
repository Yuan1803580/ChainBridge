import { readFile } from "node:fs/promises";
import { isAbsolute, resolve } from "node:path";
import { NextResponse } from "next/server";

export const dynamic = "force-dynamic";

type ServiceHealth = { name: string; status: "ok" | "down" | "unknown"; detail?: string };

async function probe(name: string, url: string): Promise<ServiceHealth> {
  try {
    const response = await fetch(url, { cache: "no-store", signal: AbortSignal.timeout(2000) });
    if (!response.ok) return { name, status: "down", detail: `HTTP ${response.status}` };
    const body = await response.json() as { status?: string; rpcConnected?: boolean };
    return { name, status: body.status === "ok" ? "ok" : "down", detail: body.rpcConnected === false ? "zero RPC" : undefined };
  } catch (reason) {
    return { name, status: "down", detail: reason instanceof Error ? reason.message : String(reason) };
  }
}

export async function GET() {
  const apiUrl = (process.env.API_URL || process.env.NEXT_PUBLIC_API_URL || "http://127.0.0.1:8000").replace(/\/$/, "");
  const runnerUrl = (process.env.AGENT_RUNNER_BASE_URL || "http://127.0.0.1:8010").replace(/\/$/, "");
  const signerUrl = (process.env.AGENT_SIGNER_BASE_URL || "http://127.0.0.1:8020").replace(/\/$/, "");
  const services = await Promise.all([
    probe("Platform API", `${apiUrl}/api/v1/health`),
    probe("Agent Runner", `${runnerUrl}/health`),
    probe("Signer Sidecar", `${signerUrl}/health`),
  ]);

  const projectRoot = resolve(process.cwd(), "../..");
  const configuredRuntime = process.env.MANAGED_RUNTIME_DIR || ".runtime/managed-testnet";
  const runtimeDir = isAbsolute(configuredRuntime) ? configuredRuntime : resolve(projectRoot, configuredRuntime);
  try {
    const text = (await readFile(resolve(runtimeDir, "services.json"), "utf8")).replace(/^\uFEFF/, "");
    const state = JSON.parse(text) as { services?: Array<{ name: string; pid: number }> };
    const worker = state.services?.find((item) => item.name === "worker");
    if (!worker) {
      services.push({ name: "Protocol Worker", status: "unknown", detail: "not recorded" });
    } else {
      try {
        process.kill(worker.pid, 0);
        services.push({ name: "Protocol Worker", status: "ok", detail: `pid ${worker.pid}` });
      } catch {
        services.push({ name: "Protocol Worker", status: "down", detail: `pid ${worker.pid}` });
      }
    }
  } catch {
    services.push({ name: "Protocol Worker", status: "unknown", detail: "managed state unavailable" });
  }
  return NextResponse.json({ status: services.every((item) => item.status === "ok") ? "ok" : "degraded", services }, {
    headers: { "Cache-Control": "no-store" },
  });
}
