import { NextRequest, NextResponse } from "next/server";

export const dynamic = "force-dynamic";

type RouteContext = { params: Promise<{ path: string[] }> };

const allowedPath = /^(wallet(?:\/faucet)?|workflows\/[0-9a-f-]+\/(?:fund|run|status|summary)|platform-workflows\/[0-9a-f-]+\/resume)$/i;

async function proxy(request: NextRequest, context: RouteContext) {
  const requesterUrl = (process.env.LOCAL_REQUESTER_URL || "http://127.0.0.1:8400").replace(/\/$/, "");
  const token = process.env.LOCAL_REQUESTER_API_TOKEN;
  if (!token) {
    return NextResponse.json(
      { error: { code: "REQUESTER_NOT_CONFIGURED", message: "本地 Requester 钱包服务尚未配置" } },
      { status: 503 },
    );
  }

  const { path } = await context.params;
  const upstreamPath = path.join("/");
  if (!allowedPath.test(upstreamPath)) {
    return NextResponse.json(
      { error: { code: "REQUESTER_PATH_NOT_ALLOWED", message: "Requester 路径不在允许列表中" } },
      { status: 404 },
    );
  }

  const body = request.method === "GET" ? undefined : await request.text();
  try {
    const response = await fetch(`${requesterUrl}/${upstreamPath}`, {
      method: request.method,
      headers: {
        Accept: "application/json",
        Authorization: `Bearer ${token}`,
        ...(body ? { "Content-Type": "application/json" } : {}),
      },
      body,
      cache: "no-store",
      signal: AbortSignal.timeout(upstreamPath.endsWith("/fund") ? 300_000 : 180_000),
    });
    return new NextResponse(await response.text(), {
      status: response.status,
      headers: { "Content-Type": response.headers.get("content-type") || "application/json" },
    });
  } catch (error) {
    return NextResponse.json(
      {
        error: {
          code: "REQUESTER_UNAVAILABLE",
          message: error instanceof Error ? error.message : "本地 Requester 钱包服务不可用",
        },
      },
      { status: 502 },
    );
  }
}

export const GET = proxy;
export const POST = proxy;
