"use client";

import { ArrowRight, BriefcaseBusiness, Search, WalletCards } from "lucide-react";
import Link from "next/link";
import { useMemo, useState } from "react";
import { DataState, PageHeader, SourceBanner, StatusBadge } from "@/components/ui";
import { api } from "@/lib/api";
import { formatDate, formatMoney } from "@/lib/format";
import { useResource } from "@/lib/use-resource";

export default function OrdersPage() {
  const resource = useResource(() => api.orders.list(), []);
  const [search, setSearch] = useState("");
  const [status, setStatus] = useState("all");
  const filtered = useMemo(() => (resource.data || []).filter((order) => (status === "all" || order.status === status) && `${order.orderNumber}${order.sourceTitle}${order.providerName}${order.requesterName}`.toLowerCase().includes(search.toLowerCase())), [resource.data, search, status]);

  return <>
    <PageHeader title="订单中心" description="跟踪条款快照、执行、交付、模拟结算及评价状态。" actions={<div className="flex items-center gap-2 rounded-md border border-amber-200 bg-amber-50 px-3 py-2 text-xs text-amber-700"><WalletCards className="size-4" />Mock Settlement</div>} />
    <SourceBanner source={resource.source} />
    <div className="mb-5 flex flex-col gap-3 sm:flex-row"><label className="relative min-w-0 flex-1"><span className="sr-only">搜索订单</span><Search className="absolute left-3 top-3 size-4 text-neutral-400" /><input className="input pl-9" value={search} onChange={(e) => setSearch(e.target.value)} placeholder="搜索订单编号、任务或 Agent" /></label><select className="select sm:w-48" value={status} onChange={(e) => setStatus(e.target.value)}><option value="all">全部状态</option><option value="awaiting_funding">待锁款</option><option value="funded">已锁款</option><option value="in_progress">执行中</option><option value="delivered">待验收</option><option value="completed">已完成</option><option value="disputed">争议中</option></select></div>
    <DataState loading={resource.loading} error={resource.error} empty={!filtered.length} onRetry={resource.reload}>
      <div className="surface overflow-x-auto scrollbar-thin fade-in"><table className="w-full min-w-[900px] text-left"><thead className="border-b border-line bg-neutral-50 text-[11px] font-medium text-neutral-500"><tr><th className="px-4 py-3">订单</th><th className="px-4 py-3">来源</th><th className="px-4 py-3">交易双方</th><th className="px-4 py-3">金额</th><th className="px-4 py-3">订单状态</th><th className="px-4 py-3">结算状态</th><th className="px-4 py-3">更新时间</th><th className="px-4 py-3"><span className="sr-only">操作</span></th></tr></thead><tbody className="divide-y divide-line">{filtered.map((order) => <tr key={order.id} className="hover:bg-neutral-50"><td className="px-4 py-4"><div className="flex items-center gap-3"><span className="grid size-9 shrink-0 place-items-center rounded-md bg-neutral-100 text-neutral-600"><BriefcaseBusiness className="size-4" /></span><div><div className="font-mono text-xs font-semibold">{order.orderNumber}</div><div className="mt-1 max-w-52 truncate text-xs text-neutral-500">{order.sourceTitle}</div></div></div></td><td className="px-4 py-4 text-xs text-neutral-600">{order.source === "demand" ? "需求竞标" : "能力直购"}</td><td className="px-4 py-4 text-xs"><div>{order.requesterName}</div><div className="mt-1 text-neutral-500">→ {order.providerName}</div></td><td className="px-4 py-4 font-mono text-xs font-medium">{formatMoney(order.amount, order.currency)}</td><td className="px-4 py-4"><StatusBadge status={order.status} /></td><td className="px-4 py-4"><StatusBadge status={order.settlementStatus} /></td><td className="px-4 py-4 text-xs text-neutral-500">{formatDate(order.updatedAt)}</td><td className="px-4 py-4 text-right"><Link href={`/orders/${order.id}`} className="inline-flex size-8 items-center justify-center rounded hover:bg-neutral-100" aria-label={`查看 ${order.orderNumber}`}><ArrowRight className="size-4" /></Link></td></tr>)}</tbody></table></div>
    </DataState>
  </>;
}
