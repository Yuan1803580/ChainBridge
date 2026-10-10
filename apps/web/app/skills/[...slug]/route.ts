import { NextRequest } from "next/server";
import { promises as fs } from "fs";
import path from "path";

export const runtime = "nodejs";
export const dynamic = "force-dynamic";

// The Next.js standalone server does not serve files from `public/`, so this
// route handler serves the agent onboarding skill assets (SKILL.md, the
// onboard-agent.ps1 script, and the agent YAML descriptors) that the "打开"
// button and the self-onboarding script expect at /skills/.../...
const SKILLS_BASE_CANDIDATES = [
  path.resolve(process.cwd(), "apps/web/public/skills"),
  path.resolve("/app/apps/web/public/skills"),
  path.resolve(process.cwd(), "public/skills"),
];

const CONTENT_TYPES: Record<string, string> = {
  ".md": "text/markdown; charset=utf-8",
  ".ps1": "text/plain; charset=utf-8",
  ".yaml": "application/yaml; charset=utf-8",
  ".yml": "application/yaml; charset=utf-8",
  ".json": "application/json; charset=utf-8",
  ".txt": "text/plain; charset=utf-8",
};

async function resolveBase(): Promise<string | null> {
  for (const candidate of SKILLS_BASE_CANDIDATES) {
    try {
      const stat = await fs.stat(candidate);
      if (stat.isDirectory()) return candidate;
    } catch {
      // candidate missing, try the next one
    }
  }
  return null;
}

export async function GET(
  _request: NextRequest,
  { params }: { params: Promise<{ slug: string[] }> },
) {
  const { slug } = await params;
  const base = await resolveBase();
  if (!base) {
    return new Response("Skills directory not found", { status: 404 });
  }

  const relative = (slug ?? []).join("/");
  if (!relative) {
    return new Response("Not Found", { status: 404 });
  }

  const target = path.resolve(base, relative);
  // Prevent path traversal outside the skills directory.
  if (target !== base && !target.startsWith(base + path.sep)) {
    return new Response("Forbidden", { status: 403 });
  }

  try {
    const data = await fs.readFile(target);
    const ext = path.extname(target).toLowerCase();
    return new Response(data, {
      status: 200,
      headers: {
        "Content-Type": CONTENT_TYPES[ext] ?? "application/octet-stream",
        "Cache-Control": "public, max-age=300",
      },
    });
  } catch {
    return new Response("Not Found", { status: 404 });
  }
}
