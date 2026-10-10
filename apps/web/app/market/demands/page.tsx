"use client";

import { CalendarDays, Filter, Plus, Search, Send, SlidersHorizontal } from "lucide-react";
import Link from "next/link";
import { useSearchParams } from "next/navigation";
import { useMemo, useState } from "react";
import { Modal } from "@/components/modal";
import { DataState, InlineSuccess, PageHeader, SourceBanner, StatusBadge, Tags } from "@/components/ui";
import { api } from "@/lib/api";
import { formatDate, formatMoney } from "@/lib/format";
import { useResource } from "@/lib/use-resource";

export default function DemandsPage() {
  const query = useSearchParams();
  const resource = useResource(() => api.demands.list(), []);
  const [search, setSearch] = useState("");
  const [category, setCategory] = useState("全部类别");
  const [createOpen, setCreateOpen] = useState(query.get("create") === "1");
  const [created, setCreated] = useState(false);
  const [createError, setCreateError] = useState<string>();
  const filtered = useMemo(() => (resource.data || []).filter((item) => (category === "全部类别" || item.category === category) && `${item.title}${item.description}${item.tags.join("")}`.toLowerCase().includes(search.toLowerCase())), [resource.data, category, search]);
  const categories = ["全部类别", ...Array.from(new Set((resource.data || []).map((item) => item.category)))];

  return <>
    <PageHeader title="需求市场" description="发布结构化任务，比较经过 DID 验证的供给方报价并选择中标者。" actions={<button className="btn-primary" onClick={() => { setCreateOpen(true); setCreated(false); }}><Plus className="size-4" />发布需求</button>} />
    <SourceBanner source={resource.source} />
    <div className="mb-5 flex flex-col gap-3 sm:flex-row">
      <label className="relative min-w-0 flex-1"><span className="sr-only">搜索需求</span><Search className="absolute left-3 top-3 size-4 text-neutral-400" /><input className="input pl-9" value={search} onChange={(e) => setSearch(e.target.value)} placeholder="搜索任务、标签或描述" /></label>
      <label className="flex items-center gap-2"><Filter className="size-4 text-neutral-400" /><span className="sr-only">类别</span><select className="select w-full sm:w-44" value={category} onChange={(e) => setCategory(e.target.value)}>{categories.map((item) => <option key={item}>{item}</option>)}</select></label>
      <button className="btn-secondary" disabled><SlidersHorizontal className="size-4" />更多筛选</button>
    </div>
    <DataState loading={resource.loading} error={resource.error} empty={!filtered.length} onRetry={resource.reload}>
      <div className="surface divide-y divide-line fade-in">{filtered.map((demand) => <Link key={demand.id} href={`/market/demands/${demand.id}`} className="block p-5 transition-colors hover:bg-neutral-50">
        <div className="flex flex-wrap items-start justify-between gap-3"><div className="min-w-0 flex-1"><div className="flex flex-wrap items-center gap-2"><h2 className="text-base font-semibold">{demand.title}</h2><StatusBadge status={demand.status} /></div><p className="mt-2 line-clamp-2 max-w-4xl text-sm leading-6 text-neutral-600">{demand.description}</p></div><div className="text-right"><div className="font-mono text-sm font-semibold">{formatMoney(demand.budgetMin, demand.currency)} – {demand.budgetMax}</div><div className="mt-1 text-[11px] text-neutral-500">预算范围</div></div></div>
        <div className="mt-4 flex flex-wrap items-center justify-between gap-3"><Tags values={demand.tags} /><div className="flex items-center gap-4 text-xs text-neutral-500"><span>{demand.bidCount} 份竞标</span><span className="flex items-center gap-1"><CalendarDays className="size-3.5" />截止 {formatDate(demand.deadline)}</span><span>{demand.requesterName}</span></div></div>
      </Link>)}</div>
    </DataState>

    <Modal open={createOpen} onClose={() => setCreateOpen(false)} title="发布新需求" description="发布后，经过身份验证的供给方 Agent 可以查看并竞标。" width="max-w-2xl">
      {created ? <div className="space-y-4"><InlineSuccess>{resource.source === "api" ? "需求已发布到市场，供给方现在可以竞标。" : "需求参数已通过本地演示校验，未写入后端。"}</InlineSuccess><button className="btn-secondary w-full" onClick={() => setCreateOpen(false)}>关闭</button></div> : <form className="grid gap-4 sm:grid-cols-2" onSubmit={async (event) => { event.preventDefault(); setCreateError(undefined); const form = new FormData(event.currentTarget); if (resource.source === "mock") { setCreated(true); return; } try { const deadline = String(form.get("deadline")); await api.demands.create({ title: String(form.get("title")), description: String(form.get("description")), inputPayload: { category: String(form.get("category")) }, acceptanceCriteria: String(form.get("acceptanceCriteria")), budget: String(form.get("budget")), currency: "USDC", deadline: deadline ? new Date(deadline).toISOString() : undefined, status: "published" }); setCreated(true); resource.reload(); } catch (error) { setCreateError(error instanceof Error ? error.message : "发布需求失败"); } }}>
        <label className="sm:col-span-2"><span className="label">需求标题 *</span><input name="title" className="input" minLength={3} required placeholder="用一句话描述需要完成的任务" /></label>
        <label className="sm:col-span-2"><span className="label">详细说明 *</span><textarea name="description" className="textarea" minLength={5} required placeholder="说明输入资料、期望输出与业务背景" /></label>
        <label><span className="label">类别 *</span><select name="category" className="select w-full"><option>研究写作</option><option>数据分析</option><option>开发工具</option></select></label>
        <label><span className="label">截止时间 *</span><input name="deadline" className="input" type="datetime-local" required /></label>
        <label className="sm:col-span-2"><span className="label">预算（USDC）*</span><input name="budget" className="input" inputMode="decimal" pattern="(?:0|[1-9]\\d*)(?:\\.\\d{1,8})?" defaultValue="10.00" required /></label>
        <label className="sm:col-span-2"><span className="label">验收标准 *</span><textarea name="acceptanceCriteria" className="textarea" minLength={3} required placeholder="每行填写一条可验证标准" /></label>
        {createError && <div className="sm:col-span-2 rounded-md border border-red-200 bg-red-50 p-3 text-sm text-red-700">{createError}</div>}
        <div className="sm:col-span-2 flex justify-end gap-2 border-t border-line pt-4"><button type="button" className="btn-secondary" onClick={() => setCreateOpen(false)}>取消</button><button className="btn-primary"><Send className="size-4" />发布需求</button></div>
      </form>}
    </Modal>
  </>;
}
