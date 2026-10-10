import { NextRequest, NextResponse } from "next/server";

export const dynamic = "force-dynamic";

export function GET(request: NextRequest) {
  // Derive the public origin from the proxied request headers so the skill URL
  // is reachable from outside the container (nginx sets Host / X-Forwarded-*).
  const proto = request.headers.get("x-forwarded-proto") || "http";
  const host = request.headers.get("host") || request.nextUrl.host;
  const webOrigin = `${proto}://${host}`;
  const apiBaseUrl = (process.env.NEXT_PUBLIC_API_URL || process.env.API_URL || webOrigin).replace(/\/$/, "");
  const selfServiceFlag = (process.env.MANAGED_SELF_SERVICE_ONBOARDING || "false").toLowerCase();
  const selfServiceEnabled = process.env.PLATFORM_PROFILE === "managed-testnet"
    && ["1", "true", "yes"].includes(selfServiceFlag);
  return NextResponse.json({
    profile: process.env.PLATFORM_PROFILE === "managed-testnet" ? "managed-testnet" : "local-demo",
    apiBaseUrl,
    skillUrl: `${webOrigin}/skills/agent-market-onboarding/SKILL.md`,
    scriptUrl: `${webOrigin}/skills/agent-market-onboarding/onboard-agent.ps1`,
    statusUrl: `${webOrigin}/onboard`,
    selfService: {
      enabled: selfServiceEnabled,
      routeKey: process.env.MANAGED_SELF_SERVICE_ROUTE_KEY || "SEPOLIA_TO_ARBITRUM_SEPOLIA_CCIP_BNM_V1",
      routeVersion: Number(process.env.MANAGED_SELF_SERVICE_ROUTE_VERSION || "1"),
      sidecarUrl: process.env.MANAGED_SIDECAR_PUBLIC_URL || "http://127.0.0.1:8020",
    },
  }, {
    headers: { "Cache-Control": "no-store" },
  });
}
