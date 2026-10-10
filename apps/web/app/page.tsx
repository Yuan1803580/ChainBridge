"use client";

import { Activity, ArrowRight, GitBranch, Network, Plus, Radio, Route, WalletCards } from "lucide-react";
import Link from "next/link";
import { useEffect, useMemo, useState } from "react";

import { InlineMetric, ProofBoundaryNotice, WorkflowMap, WorkflowStatusBadge } from "@/components/platform-ui";
import { PageHeader } from "@/components/ui";
import { dynamicApi, type RawCrossChainMessage, type RawRoute } from "@/lib/workflow-api";
import type { Workflow } from "@/lib/types";

const chainName = (chainId: number) => `Chain ${String.fromCharCode(65 + chainId - 31337)}`;

export default function DashboardPage() {
  const [workflows, setWorkflows] = useState<Workflow[]>([]);
  const [routes, setRoutes] = useState<RawRoute[]>([]);
  const [messages, setMessages] = useState<RawCrossChainMessage[]>([]);
  const [error, setError] = useState("");

  useEffect(() => {
    Promise.all([dynamicApi.workflows(), dynamicApi.routes(), dynamicApi.messages()])
      .then(([workflowResponse, routeResponse, messageResponse]) => {
        setWorkflows(workflowResponse.items);
        setRoutes(routeResponse.items);
        setMessages(messageResponse.items);
      })
      .catch((reason) => setError(reason instanceof Error ? reason.message : String(reason)));
  }, []);

  const current = workflows[0];
  const activeNodes = workflows.flatMap((item) => item.nodes)
    .filter((node) => ["ASSIGNING", "EXECUTING", "IN_REVIEW"].includes(node.status)).length;
  const queuedMessages = messages.filter((item) => item.status === "QUEUED").length;
  const tokenTotals = useMemo(() => workflows.reduce((value, item) => ({
    locked: value.locked + Number(item.budget.total),
    paid: value.paid + Number(item.budget.paid),
    refunded: value.refunded + Number(item.budget.refundable),
  }), { locked: 0, paid: 0, refunded: 0 }), [workflows]);

  return <>
    <PageHeader
      title="本地多链运行总览"
      description="数据来自 FastAPI、Coordinator 数据库与 routeVersion 路由注册表。"
      actions={<>
        <Link href="/routes" className="btn-secondary"><Route className="size-4" />查看路由</Link>
        <Link href="/workflows/new" className="btn-primary"><Plus className="size-4" />创建工作流</Link>
      </>}
    />
    <ProofBoundaryNotice />
    {error && <div className="mt-4 border border-red-200 bg-red-50 px-4 py-3 text-sm text-red-700">API 不可用：{error}</div>}

    <section className="surface mt-5 grid divide-y divide-line sm:grid-cols-2 sm:divide-x sm:divide-y-0 xl:grid-cols-4">
      <InlineMetric label="运行中工作流" value={String(workflows.filter((item) => item.status === "RUNNING").length)} detail={`${workflows.length} 个数据库记录`} icon={<GitBranch className="size-3.5" />} />
      <InlineMetric label="活跃节点" value={String(activeNodes)} detail="派发、执行或审核中" icon={<Radio className="size-3.5" />} />
      <InlineMetric label="可用路由" value={`${routes.filter((item) => item.status === "ACTIVE").length} / ${routes.length}`} detail="精确到 routeVersion" icon={<Network className="size-3.5" />} />
      <InlineMetric label="待投递消息" value={String(queuedMessages)} detail={`${messages.length} 条跨链消息`} icon={<Activity className="size-3.5" />} />
    </section>

    <div className="mt-8 grid gap-8 xl:grid-cols-[minmax(0,1.45fr)_minmax(320px,0.55fr)]">
      <section className="min-w-0">
        <div className="section-head"><div><h2 className="section-title">当前工作流</h2><p className="mt-1 text-xs text-neutral-500">依赖只在前置节点 SETTLED 后解锁</p></div>{current && <Link href={`/workflows/${current.id}`} className="inline-flex items-center gap-1 text-xs font-medium text-brand-700">打开详情<ArrowRight className="size-3.5" /></Link>}</div>
        <div className="surface p-5">
          {current ? <>
            <div className="mb-5 flex flex-wrap items-start justify-between gap-3 border-b border-line pb-4">
              <div><div className="flex items-center gap-2"><span className="font-mono text-xs font-semibold text-neutral-500">{current.displayId}</span><WorkflowStatusBadge status={current.status} /></div><h3 className="mt-2 text-base font-semibold">{current.title}</h3></div>
              <div className="text-right"><div className="text-[11px] text-neutral-500">锁定预算</div><div className="mt-1 font-mono text-sm font-semibold">{current.budget.total} {current.budget.currency}</div></div>
            </div>
            <WorkflowMap workflow={current} />
          </> : <div className="grid min-h-48 place-items-center text-sm text-neutral-500">数据库中还没有工作流</div>}
        </div>
      </section>

      <section className="min-w-0">
        <div className="section-head"><div><h2 className="section-title">动态路由注册表</h2><p className="mt-1 text-xs text-neutral-500">仅显示后端真实路由</p></div></div>
        <div className="surface divide-y divide-line">
          {routes.map((route) => <Link href="/routes" key={route.id} className="flex items-center gap-3 px-4 py-3.5 hover:bg-neutral-50">
            <span className="grid size-8 place-items-center rounded-md bg-neutral-900 text-xs font-bold text-white">{String.fromCharCode(65 + route.destinationChainId - 31337)}</span>
            <span className="min-w-0 flex-1"><span className="block truncate text-xs font-semibold">A → {chainName(route.destinationChainId)}</span><span className="mt-1 block truncate font-mono text-[10px] text-neutral-500">{route.routeKey} · v{route.routeVersion} · {route.destinationChainId}</span></span>
            <span className={`size-2 rounded-full ${route.status === "ACTIVE" ? "bg-emerald-500" : "bg-amber-500"}`} />
          </Link>)}
        </div>
      </section>
    </div>

    <section className="mt-8">
      <div className="section-head"><div><h2 className="section-title">Token 责任汇总</h2><p className="mt-1 text-xs text-neutral-500">业务 Token 与 Gas 在详情账本中分开</p></div></div>
      <div className="surface grid divide-y divide-line sm:grid-cols-3 sm:divide-x sm:divide-y-0">
        <InlineMetric label="累计锁定" value={tokenTotals.locked.toFixed(2)} detail="USDC" icon={<WalletCards className="size-3.5" />} />
        <InlineMetric label="Agent 已支付" value={tokenTotals.paid.toFixed(2)} detail="仅通过验收的尝试" icon={<Activity className="size-3.5" />} />
        <InlineMetric label="累计退款" value={tokenTotals.refunded.toFixed(2)} detail="未使用预算" icon={<ArrowRight className="size-3.5" />} />
      </div>
    </section>
  </>;
}
