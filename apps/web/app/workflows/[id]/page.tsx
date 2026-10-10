"use client";

import {
  Activity,
  ArrowLeft,
  CheckCircle2,
  CircleDollarSign,
  Clock3,
  ExternalLink,
  FileCheck2,
  History,
  ListOrdered,
  Pause,
  Play,
  Radio,
  ReceiptText,
  RotateCcw,
  ShieldAlert,
  ShieldCheck,
  WalletCards,
  XCircle,
} from "lucide-react";
import Link from "next/link";
import { useParams } from "next/navigation";
import { useEffect, useMemo, useState } from "react";
import { BudgetMeter, ProofBoundaryNotice, ResponsibilityBadge, WorkflowMap, WorkflowStatusBadge } from "@/components/platform-ui";
import { Breadcrumbs } from "@/components/ui";
import { dynamicApi } from "@/lib/workflow-api";
import type { Workflow } from "@/lib/types";

const attemptTone = {
  OFFERED: "border-blue-200 bg-blue-50 text-blue-700",
  EXECUTING: "border-amber-200 bg-amber-50 text-amber-800",
  PROOF_RECEIVED: "border-violet-200 bg-violet-50 text-violet-700",
  REJECTED: "border-red-200 bg-red-50 text-red-700",
  APPROVED: "border-emerald-200 bg-emerald-50 text-emerald-700",
  SETTLED: "border-emerald-200 bg-emerald-50 text-emerald-700",
  EXPIRED: "border-neutral-200 bg-neutral-50 text-neutral-600",
};

const eventIcon = {
  workflow: FileCheck2,
  route: Radio,
  message: Activity,
  proof: ShieldCheck,
  review: FileCheck2,
  settlement: CircleDollarSign,
  security: ShieldAlert,
};

const candidateTone = {
  AVAILABLE: "bg-blue-50 text-blue-700",
  SELECTED: "bg-violet-50 text-violet-700",
  FAILED: "bg-red-50 text-red-700",
  UNAVAILABLE: "bg-neutral-100 text-neutral-600",
  SETTLED: "bg-emerald-50 text-emerald-700",
  NOT_NEEDED: "bg-neutral-100 text-neutral-500",
};

export default function WorkflowDetailPage() {
  const params = useParams<{ id: string }>();
  const [workflow, setWorkflow] = useState<Workflow | null>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState("");
  const [tab, setTab] = useState<"overview" | "events" | "ledger">("overview");
  const [selectedNodeId, setSelectedNodeId] = useState("");
  const [replayStep, setReplayStep] = useState<number | null>(null);
  const [playing, setPlaying] = useState(false);
  const selectedNode = workflow?.nodes.find((node) => node.id === selectedNodeId) || workflow?.nodes[0];
  const selectedMatchRounds = selectedNode?.matchRounds || [];
  const selectedMatchRound = selectedMatchRounds[selectedMatchRounds.length - 1];
  const currentEvent = replayStep && workflow ? workflow.events[Math.min(replayStep - 1, workflow.events.length - 1)] : undefined;

  useEffect(() => {
    setLoading(true);
    setError("");
    dynamicApi.workflow(params.id)
      .then((value) => {
        setWorkflow(value);
        setSelectedNodeId(value.nodes[0]?.id || "");
      })
      .catch((reason) => setError(reason instanceof Error ? reason.message : String(reason)))
      .finally(() => setLoading(false));
  }, [params.id]);

  useEffect(() => {
    if (!playing || !workflow) return;
    const timer = window.setInterval(() => {
      setReplayStep((step) => {
        const next = (step || 0) + 1;
        if (next > workflow.events.length) {
          setPlaying(false);
          return workflow.events.length;
        }
        return next;
      });
    }, 850);
    return () => window.clearInterval(timer);
  }, [playing, workflow]);

  const replay = () => {
    if (!workflow?.events.length) return;
    setReplayStep(1);
    setPlaying(true);
    setTab("overview");
  };

  const totalCredits = useMemo(() => workflow?.ledger
    .filter((entry) => !entry.assetSymbol || entry.assetSymbol === workflow.budget.currency)
    .reduce((sum, entry) => sum + Number(entry.credit), 0) || 0, [workflow]);

  if (loading) return <div className="surface grid min-h-80 place-items-center text-sm text-neutral-500">正在读取 FastAPI 工作流详情...</div>;
  if (error || !workflow) return <div className="border border-red-200 bg-red-50 px-5 py-4 text-sm text-red-700">无法读取工作流：{error || "工作流不存在"}</div>;

  return <>
    <Breadcrumbs items={[{ label: "工作流", href: "/workflows" }, { label: workflow.displayId }]} />
    <div className="mb-6 flex flex-wrap items-start justify-between gap-4">
      <div className="min-w-0">
        <div className="flex flex-wrap items-center gap-2"><span className="font-mono text-xs font-semibold text-neutral-500">{workflow.displayId}</span><WorkflowStatusBadge status={workflow.status} /></div>
        <h1 className="page-title mt-2">{workflow.title}</h1>
        <p className="page-subtitle">{workflow.objective}</p>
      </div>
      <div className="flex items-center gap-2">
        <Link href="/workflows" className="icon-btn" aria-label="返回工作流"><ArrowLeft className="size-4" /></Link>
        <button className="btn-secondary" onClick={replay}><RotateCcw className="size-4" />重放 V1 闭环</button>
      </div>
    </div>

    <ProofBoundaryNotice />

    <div className={`mt-4 flex flex-wrap items-center justify-between gap-3 border px-4 py-3 text-xs ${workflow.fundingMode === "ONCHAIN" && workflow.fundingStatus === "CONFIRMED" ? "border-emerald-200 bg-emerald-50 text-emerald-800" : "border-amber-200 bg-amber-50 text-amber-800"}`} style={{ borderRadius: 6 }}>
      <span className="flex items-center gap-2"><WalletCards className="size-4" /><b>{workflow.fundingMode === "ONCHAIN" ? "链上资金" : "模型资金预留"}</b><span>{workflow.fundingStatus || "MODEL_RESERVED"}</span></span>
      <span className="font-mono text-[10px]">CONTRACT SYNC · {workflow.contractSyncStatus || "MODEL_ONLY"}</span>
    </div>

    {replayStep !== null && <div className="surface mt-5 overflow-hidden">
      <div className="flex flex-wrap items-center gap-4 px-4 py-3">
        <button className="icon-btn size-8" onClick={() => setPlaying((value) => !value)} aria-label={playing ? "暂停演示" : "继续演示"}>{playing ? <Pause className="size-4" /> : <Play className="size-4" />}</button>
        <div className="min-w-0 flex-1"><div className="flex items-center gap-2"><span className="font-mono text-[10px] font-semibold text-brand-700">STEP {String(replayStep).padStart(2, "0")} / {workflow.events.length}</span><span className="truncate text-xs font-semibold">{currentEvent?.title}</span></div><p className="mt-1 truncate text-[11px] text-neutral-500">{currentEvent?.detail}</p></div>
        <span className="font-mono text-[11px] text-neutral-500">{currentEvent?.chainLabel || "Coordinator"}</span>
      </div>
      <div className="h-1 bg-neutral-100"><div className="h-full bg-brand-600 transition-all duration-300" style={{ width: `${(replayStep / workflow.events.length) * 100}%` }} /></div>
    </div>}

    <section className="surface mt-5 grid divide-y divide-line sm:grid-cols-2 sm:divide-x sm:divide-y-0 xl:grid-cols-4">
      <div className="p-4"><div className="flex items-center gap-2 text-[11px] text-neutral-500"><WalletCards className="size-3.5" />最高预算</div><div className="mt-2 font-mono text-lg font-semibold">{workflow.budget.total} <span className="text-xs font-normal text-neutral-500">{workflow.budget.currency}</span></div><div className="mt-1 text-[10px] text-neutral-500">{workflow.fundingMode === "ONCHAIN" ? `Chain A · ${workflow.fundingStatus}` : "MODEL_RESERVED · 非真实锁款"}</div></div>
      <div className="p-4"><div className="flex items-center gap-2 text-[11px] text-neutral-500"><ShieldCheck className="size-3.5" />协调 Agent</div><div className="mt-2 truncate text-sm font-semibold">{workflow.coordinatorName}</div><div className="mt-1 truncate font-mono text-[10px] text-neutral-500">{workflow.coordinatorDid}</div></div>
      <div className="p-4"><div className="flex items-center gap-2 text-[11px] text-neutral-500"><Radio className="size-3.5" />动态执行链</div><div className="mt-2 text-lg font-semibold">{new Set(workflow.nodes.flatMap((node) => node.attempts.map((attempt) => attempt.homeChainId))).size}</div><div className="mt-1 text-[10px] text-neutral-500">Agent 选择发生在链选择之前</div></div>
      <div className="p-4"><div className="flex items-center gap-2 text-[11px] text-neutral-500"><History className="size-3.5" />节点尝试</div><div className="mt-2 text-lg font-semibold">{workflow.nodes.reduce((sum, node) => sum + node.attempts.length, 0)}</div><div className="mt-1 text-[10px] text-neutral-500">旧 attempt 防重放已启用</div></div>
    </section>

    <div className="mt-6 flex overflow-x-auto border-b border-line" role="tablist">
      {([
        ["overview", "节点与尝试", Activity],
        ["events", "跨链事件", Radio],
        ["ledger", "费用账本", ReceiptText],
      ] as const).map(([value, label, Icon]) => <button key={value} role="tab" aria-selected={tab === value} onClick={() => setTab(value)} className={`inline-flex h-11 shrink-0 items-center gap-2 border-b-2 px-4 text-xs font-medium ${tab === value ? "border-brand-600 text-brand-700" : "border-transparent text-neutral-500 hover:text-ink"}`}><Icon className="size-4" />{label}</button>)}
    </div>

    {tab === "overview" && <div className="mt-6 grid gap-8 xl:grid-cols-[minmax(0,1.5fr)_minmax(320px,0.5fr)]">
      <section className="min-w-0">
        <div className="section-head"><div><h2 className="section-title">节点依赖图</h2><p className="mt-1 text-xs text-neutral-500">选择节点查看全部 attempt、审核决定与预算预留</p></div></div>
        <WorkflowMap workflow={workflow} selectedNodeId={selectedNodeId} onSelectNode={setSelectedNodeId} />

        <div className="mt-7 section-head"><div><h2 className="section-title">预算分布</h2><p className="mt-1 text-xs text-neutral-500">业务 Token 与结果回传 Gas 独立记账</p></div></div>
        <div className="surface p-5"><div className="mb-4 flex items-end justify-between"><div><div className="text-[11px] text-neutral-500">锁定总额</div><div className="mt-1 font-mono text-lg font-semibold">{workflow.budget.total} {workflow.budget.currency}</div></div><div className="text-right"><div className="text-[11px] text-neutral-500">结果回传 Gas</div><div className="mt-1 font-mono text-sm font-semibold">{workflow.budget.returnGasSpent}</div></div></div><BudgetMeter workflow={workflow} /></div>
      </section>

      <aside className="min-w-0">
        <div className="section-head"><div><h2 className="section-title">{selectedNode?.title || "节点详情"}</h2><p className="mt-1 font-mono text-[10px] text-neutral-500">{selectedNode?.id}</p></div></div>
        {selectedNode && <div className="surface divide-y divide-line">
          <div className="min-w-0 p-4"><div className="text-[11px] font-medium text-neutral-500">不可变验收规则</div><div className="mt-2 break-all font-mono text-xs">{selectedNode.criteriaHash}</div><p className="mt-2 text-xs leading-5 text-neutral-500">{selectedNode.description}</p></div>
          <div className="p-4">
            <div className="flex items-center justify-between gap-3">
              <span className="flex items-center gap-2 text-[11px] font-medium text-neutral-500"><ListOrdered className="size-3.5" />候选 Agent 排名</span>
              {selectedMatchRound && <span className="font-mono text-[10px] text-neutral-500">ROUND {selectedMatchRound.number} · {selectedMatchRound.policyVersion}</span>}
            </div>
            {selectedMatchRound?.candidates.length ? <div className="mt-3 divide-y divide-line border-y border-line" data-testid="candidate-ranking">
              {selectedMatchRound.candidates.map((candidate) => <div key={candidate.id} data-testid={`candidate-${candidate.agentKey}`} className="grid grid-cols-[28px_minmax(0,1fr)_auto] items-center gap-2 py-2.5">
                <span className="grid size-6 place-items-center bg-neutral-100 font-mono text-[10px] font-bold text-neutral-600">{candidate.rank}</span>
                <div className="min-w-0"><div className="truncate text-xs font-semibold">{candidate.agentName}</div><div className="mt-0.5 truncate font-mono text-[10px] text-neutral-500">{candidate.agentKey} · Chain {String.fromCharCode(65 + candidate.homeChainId - 31337)} · {candidate.routeId} v{candidate.routeVersion}</div>{candidate.exclusionReason && <div className="mt-1 truncate text-[10px] text-red-600">{candidate.exclusionReason}</div>}</div>
                <div className="text-right"><div className="font-mono text-xs font-semibold">{candidate.score}</div><span className={`mt-1 inline-flex px-1.5 py-0.5 font-mono text-[9px] font-semibold ${candidateTone[candidate.status]}`}>{candidate.status}</span></div>
              </div>)}
            </div> : <div className="mt-3 border border-dashed border-line px-3 py-4 text-center text-[11px] text-neutral-500">正在等待信息平台出现能力、报价、Gas 和路由均合格的新 Agent</div>}
            {!!selectedNode.failureIncidents?.length && <div className="mt-3 text-[10px] leading-4 text-red-700">最近失败：{selectedNode.failureIncidents[selectedNode.failureIncidents.length - 1].reasonCode} · {selectedNode.failureIncidents[selectedNode.failureIncidents.length - 1].gasTreatment}</div>}
          </div>
          {selectedNode.attempts.map((attempt) => <div key={attempt.id} className="p-4">
            <div className="flex items-start justify-between gap-3"><div><div className="font-mono text-[10px] font-semibold text-neutral-400">ATTEMPT {attempt.number}</div><div className="mt-1 text-sm font-semibold">{attempt.agentName}</div></div><span className={`inline-flex border px-2 py-1 font-mono text-[10px] font-semibold ${attemptTone[attempt.status]}`} style={{ borderRadius: 999 }}>{attempt.status}</span></div>
              <div className="mt-3 grid grid-cols-2 gap-x-4 gap-y-2 text-[11px]"><span className="text-neutral-500">归属链</span><b className="text-right font-mono">{attempt.homeChainName}</b><span className="text-neutral-500">路由快照</span><b className="break-all text-right font-mono">{attempt.routeId} v{attempt.routeVersion}</b><span className="text-neutral-500">预算预留</span><b className="text-right font-mono">{attempt.reservation.amount} {attempt.settlementAsset || workflow.budget.currency}</b><span className="text-neutral-500">RESULT_PROOF</span><b className="text-right font-mono">{attempt.resultProofStatus || "NOT_REQUIRED"}</b><span className="text-neutral-500">跨链结算</span><b className="text-right font-mono">{attempt.settlementStatus || "NONE"}</b><span className="text-neutral-500">回传 Gas</span><b className="text-right font-mono">{attempt.reservation.returnGasDeposit}</b></div>
              {attempt.payoutAsset && <div className="mt-3 border-t border-line pt-3" data-testid={`payout-${attempt.id}`}>
                <div className="flex flex-wrap items-center justify-between gap-2"><span className="text-[11px] font-semibold text-neutral-600">本链支付</span><span className="font-mono text-[10px] font-semibold text-emerald-700">{attempt.payoutStatus || "NOT_REQUIRED"}</span></div>
                <div className="mt-2 flex flex-wrap items-baseline gap-2"><b className="font-mono text-sm">{attempt.settlementAsset} → {attempt.payoutAsset}</b><span className="font-mono text-[10px] text-neutral-500">1 {attempt.settlementAsset} = {attempt.payoutRate} {attempt.payoutAsset}</span></div>
                <div className="mt-2 grid grid-cols-2 gap-x-4 gap-y-2 text-[10px]"><span className="text-neutral-500">报价到账</span><b className="text-right font-mono">{attempt.payoutQuoteAmount ? `${attempt.payoutQuoteAmount} ${attempt.payoutAsset}` : "PENDING"}</b><span className="text-neutral-500">最低到账</span><b className="text-right font-mono">{attempt.payoutMinAmount ? `${attempt.payoutMinAmount} ${attempt.payoutAsset}` : "-"}</b><span className="text-neutral-500">Token / 精度</span><b className="break-all text-right font-mono">{attempt.payoutTokenAddress ? `${attempt.payoutTokenAddress.slice(0, 8)}...${attempt.payoutTokenAddress.slice(-6)}` : "DIRECT"} / {attempt.payoutDecimals}</b><span className="text-neutral-500">Payout tx</span><b className="break-all text-right font-mono">{attempt.payoutTxHash ? `${attempt.payoutTxHash.slice(0, 10)}...${attempt.payoutTxHash.slice(-8)}` : "-"}</b></div>
              </div>}
            {attempt.review && <div className={`mt-3 flex items-start gap-2 border px-3 py-2.5 text-[11px] leading-4 ${attempt.review.decision === "PASS" ? "border-emerald-200 bg-emerald-50 text-emerald-800" : "border-red-200 bg-red-50 text-red-800"}`} style={{ borderRadius: 6 }}>{attempt.review.decision === "PASS" ? <CheckCircle2 className="mt-0.5 size-3.5 shrink-0" /> : <XCircle className="mt-0.5 size-3.5 shrink-0" />}<span><b className="font-mono">{attempt.review.reasonCode}</b><br />{attempt.review.summary}</span></div>}
          </div>)}
          {!selectedNode.attempts.length && <div className="p-5 text-center text-xs text-neutral-500"><Clock3 className="mx-auto mb-2 size-5" />等待前置节点 SETTLED</div>}
        </div>}
      </aside>
    </div>}

    {tab === "events" && <section className="mt-6">
      <div className="section-head"><div><h2 className="section-title">协议事件时间线</h2><p className="mt-1 text-xs text-neutral-500">Scanner 只读事件；受限 Relayer 只执行白名单 Job</p></div><button className="btn-secondary h-9" onClick={replay}><Play className="size-4" />从头播放</button></div>
      <div className="surface divide-y divide-line">
        {workflow.events.map((event) => {
          const Icon = eventIcon[event.kind];
          return <div key={event.id} className={`grid gap-3 px-4 py-3.5 sm:grid-cols-[42px_minmax(0,1fr)_160px] sm:items-center ${replayStep === event.step ? "bg-brand-50" : ""}`}>
            <span className={`grid size-8 place-items-center rounded-md ${event.kind === "security" ? "bg-red-50 text-red-700" : event.kind === "settlement" ? "bg-emerald-50 text-emerald-700" : "bg-neutral-100 text-neutral-600"}`}><Icon className="size-4" /></span>
            <div className="min-w-0"><div className="flex flex-wrap items-center gap-2"><span className="font-mono text-[10px] font-semibold text-neutral-400">#{String(event.step).padStart(2, "0")}</span><span className="text-xs font-semibold">{event.title}</span>{event.chainLabel && <span className="bg-neutral-100 px-1.5 py-0.5 font-mono text-[10px] text-neutral-600">{event.chainLabel}</span>}</div><p className="mt-1 text-[11px] leading-5 text-neutral-500">{event.detail}</p>{(event.messageId || event.txHash) && <div className="mt-1 flex flex-wrap gap-3 font-mono text-[10px] text-blue-700">{event.messageId && <span>messageId {event.messageId}</span>}{event.txHash && <span>tx {event.txHash}</span>}</div>}</div>
            <div className="font-mono text-[10px] text-neutral-500 sm:text-right">{new Date(event.occurredAt).toLocaleString("zh-CN", { hour12: false })}</div>
          </div>;
        })}
      </div>
    </section>}

    {tab === "ledger" && <section className="mt-6">
      <div className="section-head"><div><h2 className="section-title">费用与 Gas 责任账本</h2><p className="mt-1 text-xs text-neutral-500">失败 Agent 不获得报酬，并承担自己的执行、上链与结果回传成本</p></div><div className="text-right"><div className="text-[10px] text-neutral-500">账本可核对金额</div><div className="font-mono text-sm font-semibold">{totalCredits.toFixed(2)} {workflow.budget.currency}</div></div></div>
      {workflow.ledger.length ? <div className="surface overflow-x-auto scrollbar-thin"><table className="w-full min-w-[900px] text-left"><thead className="border-b border-line bg-neutral-50 text-[11px] text-neutral-500"><tr><th className="px-4 py-3">类别</th><th className="px-4 py-3">说明</th><th className="px-4 py-3">责任方</th><th className="px-4 py-3">node / attempt</th><th className="px-4 py-3 text-right">支出</th><th className="px-4 py-3 text-right">入账 / 退款</th></tr></thead><tbody className="divide-y divide-line">{workflow.ledger.map((entry) => <tr key={entry.id} className="hover:bg-neutral-50"><td className="px-4 py-3 font-mono text-[10px] font-semibold">{entry.category}</td><td className="px-4 py-3 text-xs">{entry.description}</td><td className="px-4 py-3"><ResponsibilityBadge value={entry.responsibleParty} /></td><td className="px-4 py-3 font-mono text-[10px] text-neutral-500">{entry.nodeId?.replace("node-1001-", "") || "-"}{entry.attemptId ? ` / #${entry.attemptId.slice(-1)}` : ""}</td><td className="px-4 py-3 text-right font-mono text-xs">{Number(entry.debit) ? `-${entry.debit} ${entry.assetSymbol || ""}` : "-"}</td><td className="px-4 py-3 text-right font-mono text-xs text-emerald-700">{Number(entry.credit) ? `+${entry.credit} ${entry.assetSymbol || ""}` : "-"}</td></tr>)}</tbody></table></div> : <div className="surface grid min-h-52 place-items-center text-center text-sm text-neutral-500">该工作流还没有可展示的账本记录。</div>}
      <div className="mt-4 flex items-start gap-2 text-[11px] leading-5 text-neutral-500"><ExternalLink className="mt-0.5 size-3.5 shrink-0" />本地 Mock 的 txHash 与 messageId 只用于验证状态关联和防重放，不链接到公开区块浏览器。</div>
    </section>}
  </>;
}
