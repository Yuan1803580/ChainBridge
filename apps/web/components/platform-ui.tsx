"use client";

import {
  AlertTriangle,
  ArrowDown,
  Check,
  CheckCircle2,
  CircleDashed,
  Clock3,
  Coins,
  LockKeyhole,
  Radio,
  RotateCcw,
  ShieldCheck,
  X,
} from "lucide-react";
import type { ReactNode } from "react";
import type { ChainSummary, RouteStatus, Workflow, WorkflowNodeStatus, WorkflowStatus } from "@/lib/types";

const workflowTone: Record<WorkflowStatus, string> = {
  DRAFT: "border-neutral-200 bg-neutral-50 text-neutral-600",
  FUNDED: "border-blue-200 bg-blue-50 text-blue-700",
  RUNNING: "border-amber-200 bg-amber-50 text-amber-800",
  COMPLETED: "border-emerald-200 bg-emerald-50 text-emerald-700",
  FAILED: "border-red-200 bg-red-50 text-red-700",
};

const workflowLabel: Record<WorkflowStatus, string> = {
  DRAFT: "草稿",
  FUNDED: "已锁定预算",
  RUNNING: "运行中",
  COMPLETED: "已完成",
  FAILED: "失败",
};

const routeTone: Record<RouteStatus, string> = {
  DISCOVERED: "border-neutral-200 bg-neutral-50 text-neutral-600",
  ONBOARDING: "border-amber-200 bg-amber-50 text-amber-800",
  CONFIGURED: "border-blue-200 bg-blue-50 text-blue-700",
  AVAILABLE: "border-emerald-200 bg-emerald-50 text-emerald-700",
  PAUSED: "border-red-200 bg-red-50 text-red-700",
};

const nodeTone: Record<WorkflowNodeStatus, { bar: string; badge: string; label: string; icon: ReactNode }> = {
  BLOCKED: { bar: "bg-neutral-300", badge: "bg-neutral-100 text-neutral-600", label: "等待前置节点", icon: <LockKeyhole className="size-3.5" /> },
  READY: { bar: "bg-blue-500", badge: "bg-blue-50 text-blue-700", label: "可分配", icon: <CircleDashed className="size-3.5" /> },
  WAITING: { bar: "bg-amber-500", badge: "bg-amber-50 text-amber-800", label: "等待候选 Agent", icon: <Clock3 className="size-3.5" /> },
  ASSIGNING: { bar: "bg-blue-500", badge: "bg-blue-50 text-blue-700", label: "匹配 Agent", icon: <Radio className="size-3.5" /> },
  EXECUTING: { bar: "bg-amber-500", badge: "bg-amber-50 text-amber-800", label: "执行中", icon: <Clock3 className="size-3.5" /> },
  IN_REVIEW: { bar: "bg-violet-500", badge: "bg-violet-50 text-violet-700", label: "协调审核", icon: <ShieldCheck className="size-3.5" /> },
  SETTLED: { bar: "bg-emerald-500", badge: "bg-emerald-50 text-emerald-700", label: "已结算", icon: <CheckCircle2 className="size-3.5" /> },
  FAILED: { bar: "bg-red-500", badge: "bg-red-50 text-red-700", label: "失败", icon: <X className="size-3.5" /> },
};

export function WorkflowStatusBadge({ status }: { status: WorkflowStatus }) {
  return <span className={`inline-flex h-6 items-center border px-2 text-[11px] font-semibold ${workflowTone[status]}`} style={{ borderRadius: 999 }}>{workflowLabel[status]}</span>;
}

export function RouteStatusBadge({ status }: { status: RouteStatus }) {
  return <span className={`inline-flex h-6 items-center border px-2 font-mono text-[10px] font-semibold ${routeTone[status]}`} style={{ borderRadius: 999 }}>{status}</span>;
}

export function HealthDot({ status, label }: { status: "healthy" | "degraded" | "offline"; label?: string }) {
  const tone = status === "healthy" ? "bg-emerald-500" : status === "degraded" ? "bg-amber-500" : "bg-red-500";
  return <span className="inline-flex items-center gap-1.5 text-xs text-neutral-600"><span className={`size-2 rounded-full ${tone}`} />{label || (status === "healthy" ? "正常" : status === "degraded" ? "需关注" : "离线")}</span>;
}

export function ChainMark({ chain, compact = false }: { chain: ChainSummary; compact?: boolean }) {
  return <span className="inline-flex min-w-0 items-center gap-2">
    <span className={`${compact ? "size-7 text-[11px]" : "size-9 text-xs"} grid shrink-0 place-items-center rounded-md font-bold text-white`} style={{ backgroundColor: chain.color }}>{chain.shortName}</span>
    {!compact && <span className="min-w-0"><span className="block truncate text-xs font-semibold">{chain.name}</span><span className="block font-mono text-[10px] text-neutral-500">chainId {chain.chainId}</span></span>}
  </span>;
}

export function BudgetMeter({ workflow }: { workflow: Workflow }) {
  const total = Number(workflow.budget.total) || 1;
  const paid = (Number(workflow.budget.paid) / total) * 100;
  const fee = (Number(workflow.budget.platformFee) / total) * 100;
  const reserved = (Number(workflow.budget.reserved) / total) * 100;
  const refundable = Math.max(0, 100 - paid - fee - reserved);
  return <div>
    <div className="flex h-2.5 w-full overflow-hidden rounded-sm bg-neutral-100" aria-label="预算使用情况">
      <span className="bg-emerald-500" style={{ width: `${paid}%` }} />
      <span className="bg-blue-500" style={{ width: `${fee}%` }} />
      <span className="bg-amber-400" style={{ width: `${reserved}%` }} />
      <span className="bg-neutral-200" style={{ width: `${refundable}%` }} />
    </div>
    <div className="mt-3 grid grid-cols-2 gap-x-4 gap-y-2 text-[11px] sm:grid-cols-4">
      <span className="flex items-center gap-1.5 text-neutral-600"><i className="size-2 bg-emerald-500" />已付 Agent <b className="ml-auto font-mono text-ink">{workflow.budget.paid}</b></span>
      <span className="flex items-center gap-1.5 text-neutral-600"><i className="size-2 bg-blue-500" />平台费 <b className="ml-auto font-mono text-ink">{workflow.budget.platformFee}</b></span>
      <span className="flex items-center gap-1.5 text-neutral-600"><i className="size-2 bg-amber-400" />预留中 <b className="ml-auto font-mono text-ink">{workflow.budget.reserved}</b></span>
      <span className="flex items-center gap-1.5 text-neutral-600"><i className="size-2 bg-neutral-300" />可退款 <b className="ml-auto font-mono text-ink">{workflow.budget.refundable}</b></span>
    </div>
  </div>;
}

export function WorkflowMap({ workflow, selectedNodeId, onSelectNode }: { workflow: Workflow; selectedNodeId?: string; onSelectNode?: (nodeId: string) => void }) {
  if (!workflow.nodes.length) return <div className="border border-dashed border-line px-4 py-10 text-center text-sm text-neutral-500">预算确认后，协调 Agent 才会生成节点并开始匹配。</div>;
  return <div className="grid gap-3 2xl:grid-cols-[minmax(0,1fr)_28px_minmax(0,1fr)_28px_minmax(0,1fr)] 2xl:items-stretch">
    {workflow.nodes.map((node, index) => {
      const tone = nodeTone[node.status];
      const selected = node.id === selectedNodeId;
      const currentAttempt = node.attempts[node.attempts.length - 1];
      return <div key={node.id} className="contents">
        <button type="button" onClick={() => onSelectNode?.(node.id)} className={`relative min-w-0 overflow-hidden border bg-white p-4 text-left transition-colors ${selected ? "border-brand-600 ring-1 ring-brand-600" : "border-line hover:border-neutral-400"}`} style={{ borderRadius: 8 }}>
          <span className={`absolute inset-x-0 top-0 h-1 ${tone.bar}`} />
          <div className="flex items-start justify-between gap-3 pt-1">
            <div className="min-w-0"><span className="font-mono text-[10px] font-semibold text-neutral-400">NODE {node.sequence}</span><h3 className="mt-1 text-sm font-semibold leading-5">{node.title.replace(/^N\d+ · /, "")}</h3></div>
            <span className={`inline-flex shrink-0 items-center gap-1 px-2 py-1 text-[10px] font-medium ${tone.badge}`} style={{ borderRadius: 999 }}>{tone.icon}{tone.label}</span>
          </div>
          <p className="mt-2 line-clamp-2 min-h-10 text-xs leading-5 text-neutral-500">{node.description}</p>
          <div className="mt-4 border-t border-line pt-3">
            <div className="flex items-center justify-between gap-3 text-[11px]"><span className="text-neutral-500">当前执行方</span><span className="truncate font-medium">{currentAttempt?.agentName || "等待分配"}</span></div>
            <div className="mt-2 flex items-center justify-between gap-3 text-[11px]"><span className="text-neutral-500">归属链 / 尝试</span><span className="font-mono">{currentAttempt ? `${currentAttempt.homeChainName} · #${currentAttempt.number}` : "-"}</span></div>
            {node.attempts.length > 1 && <div className="mt-2 flex items-center gap-1.5 text-[10px] font-medium text-amber-700"><RotateCcw className="size-3" />已更换 {node.attempts.length - 1} 次 Agent，nodeId 未变</div>}
          </div>
        </button>
        {index < workflow.nodes.length - 1 && <div className="flex items-center justify-center text-neutral-300"><span className="hidden h-px w-full bg-neutral-300 2xl:block" /><ArrowDown className="size-4 2xl:hidden" /></div>}
      </div>;
    })}
  </div>;
}

export function ProofBoundaryNotice() {
  return <div className="flex items-start gap-3 border border-blue-200 bg-blue-50 px-4 py-3 text-xs leading-5 text-blue-900" style={{ borderRadius: 6 }}>
    <ShieldCheck className="mt-0.5 size-4 shrink-0 text-blue-700" />
    <div><b>本地四链可执行证明</b><span className="text-blue-800">：验证独立 chainId、异步消息、状态机、失败换链、费用账本与防重放；不代表真实 CCIP、USDC 流动性或生产安全审计已经完成。</span></div>
  </div>;
}

export function InlineMetric({ label, value, detail, icon }: { label: string; value: string; detail: string; icon?: ReactNode }) {
  return <div className="min-w-0 px-5 py-4">
    <div className="flex items-center gap-2 text-[11px] font-medium text-neutral-500">{icon}{label}</div>
    <div className="mt-2 truncate text-xl font-semibold text-ink">{value}</div>
    <div className="mt-1 truncate text-[11px] text-neutral-500">{detail}</div>
  </div>;
}

export function ResponsibilityBadge({ value }: { value: "USER" | "PLATFORM" | "AGENT" | "SYSTEM" }) {
  const labels = { USER: "用户", PLATFORM: "平台", AGENT: "执行 Agent", SYSTEM: "系统退款" };
  const Icon = value === "AGENT" ? AlertTriangle : value === "SYSTEM" ? Check : value === "USER" ? Coins : ShieldCheck;
  return <span className="inline-flex items-center gap-1 text-[11px] text-neutral-600"><Icon className="size-3.5" />{labels[value]}</span>;
}
