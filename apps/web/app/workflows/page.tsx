"use client";

import { ArrowRight, Filter, Plus, Search, Workflow as WorkflowIcon } from "lucide-react";
import Link from "next/link";
import { useEffect, useMemo, useState } from "react";
import { BudgetMeter, WorkflowStatusBadge } from "@/components/platform-ui";
import { PageHeader } from "@/components/ui";
import { dynamicApi } from "@/lib/workflow-api";
import type { Workflow } from "@/lib/types";

export default function WorkflowsPage() {
  const [workflows, setWorkflows] = useState<Workflow[]>([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState("");
  const [query, setQuery] = useState("");
  const [status, setStatus] = useState("ALL");
  useEffect(() => {
    dynamicApi.workflows()
      .then((response) => setWorkflows(response.items))
      .catch((reason) => setError(reason instanceof Error ? reason.message : String(reason)))
      .finally(() => setLoading(false));
  }, []);
  const filtered = useMemo(() => workflows.filter((workflow) => {
    const matchesQuery = `${workflow.displayId}${workflow.title}${workflow.objective}`.toLowerCase().includes(query.toLowerCase());
    return matchesQuery && (status === "ALL" || workflow.status === status);
  }), [workflows, query, status]);

  return <>
    <PageHeader title="工作流" description="每个任务由稳定的 nodeId 和可替换的 attemptId 组成；付款确认后节点才进入 SETTLED。" actions={<Link href="/workflows/new" className="btn-primary"><Plus className="size-4" />创建工作流</Link>} />

    <div className="mb-5 flex flex-col gap-3 sm:flex-row">
      <label className="relative min-w-0 flex-1"><span className="sr-only">搜索工作流</span><Search className="absolute left-3 top-3 size-4 text-neutral-400" /><input className="input pl-9" value={query} onChange={(event) => setQuery(event.target.value)} placeholder="搜索编号、标题或任务目标" /></label>
      <label className="relative"><span className="sr-only">筛选状态</span><Filter className="pointer-events-none absolute left-3 top-3 size-4 text-neutral-400" /><select className="select min-w-40 pl-9" value={status} onChange={(event) => setStatus(event.target.value)}><option value="ALL">全部状态</option><option value="RUNNING">运行中</option><option value="COMPLETED">已完成</option><option value="DRAFT">草稿</option><option value="FAILED">失败</option></select></label>
    </div>

    {error && <div className="mb-4 border border-red-200 bg-red-50 px-4 py-3 text-sm text-red-700">无法连接工作流 API：{error}</div>}
    <div className="surface divide-y divide-line fade-in">
      {loading && <div className="grid min-h-52 place-items-center text-sm text-neutral-500">正在读取 FastAPI 工作流数据库...</div>}
      {filtered.map((workflow) => {
        const settled = workflow.nodes.filter((node) => node.status === "SETTLED").length;
        const attemptCount = workflow.nodes.reduce((sum, node) => sum + node.attempts.length, 0);
        const chainNames = [...new Set(workflow.nodes.flatMap((node) => node.attempts.map((attempt) => attempt.homeChainName)))];
        return <Link href={`/workflows/${workflow.id}`} key={workflow.id} className="block p-5 hover:bg-neutral-50">
          <div className="grid gap-5 lg:grid-cols-[minmax(0,1.3fr)_minmax(320px,0.7fr)_36px] lg:items-center">
            <div className="min-w-0">
              <div className="flex flex-wrap items-center gap-2"><span className="font-mono text-xs font-semibold text-neutral-500">{workflow.displayId}</span><WorkflowStatusBadge status={workflow.status} /></div>
              <h2 className="mt-2 truncate text-base font-semibold">{workflow.title}</h2>
              <p className="mt-1 line-clamp-2 text-xs leading-5 text-neutral-500">{workflow.objective}</p>
              <div className="mt-3 flex flex-wrap items-center gap-x-5 gap-y-2 text-[11px] text-neutral-500">
                <span>{workflow.nodes.length} 节点 · {attemptCount} attempts</span>
                <span>{settled} 节点已结算</span>
                <span>{chainNames.length ? `执行链 ${chainNames.join(" / ")}` : "尚未选择执行链"}</span>
              </div>
            </div>
            <div className="border-t border-line pt-4 lg:border-l lg:border-t-0 lg:pl-5 lg:pt-0">
              <div className="mb-2 flex items-center justify-between text-[11px]"><span className="text-neutral-500">预算使用</span><span className="font-mono font-semibold">{workflow.budget.total} {workflow.budget.currency}</span></div>
              <BudgetMeter workflow={workflow} />
            </div>
            <ArrowRight className="hidden size-4 text-neutral-400 lg:block" />
          </div>
        </Link>;
      })}
      {!loading && !filtered.length && <div className="grid min-h-52 place-items-center px-6 text-center"><div><WorkflowIcon className="mx-auto size-7 text-neutral-400" /><div className="mt-3 font-medium">没有匹配的工作流</div><p className="mt-1 text-xs text-neutral-500">创建一个工作流，或调整搜索和状态筛选。</p></div></div>}
    </div>
  </>;
}
