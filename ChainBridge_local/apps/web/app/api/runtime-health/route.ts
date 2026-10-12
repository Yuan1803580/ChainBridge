import { NextResponse } from "next/server";

export const dynamic = "force-dynamic";

export function GET() {
  return NextResponse.json({
    status: "ok",
    service: "agent-crosschain-platform-web",
    runtimeId: process.env.LOCAL_PLATFORM_RUNTIME_ID ?? null,
  });
}
