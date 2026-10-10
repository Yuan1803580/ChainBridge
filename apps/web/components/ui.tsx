import { AlertCircle, Check, ChevronRight, Inbox, LoaderCircle, RefreshCw, TriangleAlert } from "lucide-react";
import Link from "next/link";
import { stateLabel } from "@/lib/format";

export function PageHeader({ title, description, actions }: { title: string; description?: string; actions?: React.ReactNode }) {
  return <div className="mb-7 flex flex-wrap items-start justify-between gap-4"><div><h1 className="page-title">{title}</h1>{description && <p className="page-subtitle">{description}</p>}</div>{actions && <div className="flex flex-wrap items-center gap-2">{actions}</div>}</div>;
}

export function SourceBanner({ source }: { source?: "api" | "mock" }) {
  if (source !== "mock") return null;
  return <div className="mb-5 flex items-start gap-2 rounded-md border border-amber-200 bg-amber-50 px-3 py-2 text-xs leading-5 text-amber-800"><TriangleAlert className="mt-0.5 size-4 shrink-0" /><span>当前使用内置四链演示数据。连接 API 与链服务后才会提交真实状态变更。</span></div>;
}

export function StatusBadge({ status }: { status: string }) {
  const green = ["published", "completed", "accepted", "active", "valid", "online", "available", "released", "success"].includes(status);
  const amber = ["pending", "awarded", "funded", "in_progress", "delivered", "busy", "locked", "created", "awaiting_funding", "release_pending"].includes(status);
  const red = ["cancelled", "disputed", "failed", "revoked", "suspended", "blocked"].includes(status);
  const cls = green ? "border-emerald-200 bg-emerald-50 text-emerald-700" : amber ? "border-amber-200 bg-amber-50 text-amber-700" : red ? "border-red-200 bg-red-50 text-red-700" : "border-neutral-200 bg-neutral-50 text-neutral-600";
  return <span className={`inline-flex h-6 items-center gap-1 rounded-full border px-2 text-[11px] font-medium ${cls}`}><span className="size-1.5 rounded-full bg-current opacity-70" />{stateLabel(status)}</span>;
}

export function DataState({ loading, error, empty, onRetry, children }: { loading?: boolean; error?: string; empty?: boolean; onRetry?: () => void; children: React.ReactNode }) {
  if (loading) return <div className="surface grid min-h-56 place-items-center"><div className="text-center text-sm text-neutral-500"><LoaderCircle className="mx-auto mb-3 size-6 animate-spin text-brand-600" />正在加载数据</div></div>;
  if (error) return <div className="surface grid min-h-56 place-items-center px-6"><div className="max-w-md text-center"><AlertCircle className="mx-auto mb-3 size-7 text-red-500" /><h2 className="font-medium">数据加载失败</h2><p className="mt-1 text-sm text-neutral-500">{error}</p>{onRetry && <button className="btn-secondary mt-4" onClick={onRetry}><RefreshCw className="size-4" />重试</button>}</div></div>;
  if (empty) return <div className="surface grid min-h-56 place-items-center px-6"><div className="text-center"><Inbox className="mx-auto mb-3 size-7 text-neutral-400" /><h2 className="font-medium">暂无数据</h2><p className="mt-1 text-sm text-neutral-500">调整筛选条件或创建第一条记录。</p></div></div>;
  return <>{children}</>;
}

export function Metric({ label, value, detail, icon }: { label: string; value: string; detail?: string; icon?: React.ReactNode }) {
  return <div className="border-l-2 border-brand-500 px-4 py-1"><div className="flex items-center gap-2 text-xs text-neutral-500">{icon}{label}</div><div className="mt-2 text-2xl font-semibold text-ink">{value}</div>{detail && <div className="mt-1 text-xs text-neutral-500">{detail}</div>}</div>;
}

export function Tags({ values }: { values: string[] }) {
  return <div className="flex flex-wrap gap-1.5">{values.map((tag) => <span key={tag} className="rounded border border-line bg-neutral-50 px-2 py-1 text-[11px] text-neutral-600">{tag}</span>)}</div>;
}

export function Breadcrumbs({ items }: { items: Array<{ label: string; href?: string }> }) {
  return <nav className="mb-5 flex flex-wrap items-center gap-1 text-xs text-neutral-500" aria-label="面包屑">{items.map((item, index) => <span key={`${item.label}-${index}`} className="flex items-center gap-1">{index > 0 && <ChevronRight className="size-3.5" />}{item.href ? <Link href={item.href} className="hover:text-brand-700">{item.label}</Link> : <span className="text-neutral-700">{item.label}</span>}</span>)}</nav>;
}

export function InlineSuccess({ children }: { children: React.ReactNode }) {
  return <div className="flex items-start gap-2 rounded-md border border-emerald-200 bg-emerald-50 px-3 py-2 text-sm text-emerald-800"><Check className="mt-0.5 size-4 shrink-0" />{children}</div>;
}

export function CodeBlock({ value, maxHeight = "320px" }: { value: unknown; maxHeight?: string }) {
  const text = typeof value === "string" ? value : JSON.stringify(value, null, 2);
  return <pre className="code-scroll overflow-auto rounded-md bg-[#17201c] p-4 font-mono text-xs leading-5 text-emerald-50" style={{ maxHeight }}><code>{text}</code></pre>;
}
