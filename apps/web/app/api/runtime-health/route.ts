import { NextResponse } from "next/server";

export const dynamic = "force-dynamic";

export function GET() {
  return NextResponse.json({
    status: "ok",
    service: "agent-crosschain-platform-web",
    profile: process.env.PLATFORM_PROFILE === "managed-testnet" ? "managed-testnet" : "local-demo",
    runtimeId: process.env.LOCAL_PLATFORM_RUNTIME_ID ?? null,
  });
}
