import { NextRequest, NextResponse } from "next/server";

export const dynamic = "force-dynamic";

type RouteContext = { params: Promise<{ path: string[] }> };

const allowedPath = /^(chat|conversations\/[0-9a-f-]+(?:\/refine|\/workflows|\/coordinate|\/failure-advice|\/summary)?|workflows\/[0-9a-f-]+\/summary)$/i;

async function proxy(request: NextRequest, context: RouteContext) {
  const platformUrl = (process.env.API_URL || process.env.NEXT_PUBLIC_API_URL || "http://127.0.0.1:8000").replace(/\/$/, "");
  const profileId = request.headers.get("x-coordinator-profile-id") || "";
  if (!/^[0-9a-f-]{36}$/i.test(profileId)) {
    return NextResponse.json({ detail: "A valid Coordinator Agent profile is required" }, { status: 422 });
  }

  const { path } = await context.params;
  const upstreamPath = path.join("/");
  if (!allowedPath.test(upstreamPath)) {
    return NextResponse.json({ detail: "Coordinator path is not allowed" }, { status: 404 });
  }

  const body = request.method === "GET" ? undefined : await request.text();
  try {
    const response = await fetch(`${platformUrl}/api/v1/coordinator-runtime/${profileId}/${upstreamPath}`, {
      method: request.method,
      headers: {
        Accept: "application/json",
        ...(body ? { "Content-Type": "application/json" } : {}),
      },
      body,
      cache: "no-store",
      signal: AbortSignal.timeout(180_000),
    });
    return new NextResponse(await response.text(), {
      status: response.status,
      headers: { "Content-Type": response.headers.get("content-type") || "application/json" },
    });
  } catch (error) {
    return NextResponse.json(
      { detail: error instanceof Error ? error.message : "Coordinator request failed" },
      { status: 502 },
    );
  }
}

export const GET = proxy;
export const POST = proxy;
