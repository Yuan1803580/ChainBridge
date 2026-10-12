"use client";

import { Activity, ArrowUpRight, Clock3, Code2, Plus, Search, ShieldCheck, Star } from "lucide-react";
import Link from "next/link";
import { useMemo, useState } from "react";
import { Modal } from "@/components/modal";
import { DataState, InlineSuccess, PageHeader, SourceBanner, StatusBadge, Tags } from "@/components/ui";
import { api } from "@/lib/api";
import { formatMoney } from "@/lib/format";
import { useResource } from "@/lib/use-resource";

export default function CapabilitiesPage() {
  const resource = useResource(() => api.capabilities.list(), []);
  const [search, setSearch] = useState("");
  const [category, setCategory] = useState("全部类别");
  const [publishOpen, setPublishOpen] = useState(false);
  const [published, setPublished] = useState(false);
  const [publishError, setPublishError] = useState<string>();
  const categories = ["全部类别", ...Array.from(new Set((resource.data || []).map((item) => item.category)))];
  const filtered = useMemo(() => (resource.data || []).filter((item) => (category === "全部类别" || item.category === category) && `${item.title}${item.summary}${item.tags.join("")}`.toLowerCase().includes(search.toLowerCase())), [resource.data, category, search]);

  return <>
    <PageHeader title="能力市场" description="查看已验证 Agent 发布的服务能力、输入规范、价格与实时可用状态。" actions={<><Link href="/console" className="btn-secondary"><Code2 className="size-4" />API 控制台</Link><button className="btn-primary" onClick={() => { setPublishOpen(true); setPublished(false); setPublishError(undefined); }}><Plus className="size-4" />发布能力</button></>} />
    <SourceBanner source={resource.source} />
    <div className="mb-5 flex flex-col gap-3 sm:flex-row"><label className="relative min-w-0 flex-1"><span className="sr-only">搜索能力</span><Search className="absolute left-3 top-3 size-4 text-neutral-400" /><input className="input pl-9" value={search} onChange={(event) => setSearch(event.target.value)} placeholder="搜索能力、标签或供给方" /></label><select className="select sm:w-44" value={category} onChange={(event) => setCategory(event.target.value)}>{categories.map((item) => <option key={item}>{item}</option>)}</select></div>
    <DataState loading={resource.loading} error={resource.error} empty={!filtered.length} onRetry={resource.reload}>
      <div className="grid gap-4 md:grid-cols-2 xl:grid-cols-3 fade-in">{filtered.map((capability) => <article key={capability.id} className="surface flex min-h-[300px] flex-col p-5">
        <div className="flex items-start justify-between gap-3"><span className="grid size-10 place-items-center rounded-md bg-brand-50 text-brand-700"><Activity className="size-5" /></span><StatusBadge status={capability.availability} /></div>
        <div className="mt-4"><div className="flex items-center gap-1.5 text-xs text-neutral-500"><span>{capability.category}</span><span>·</span><span>v{capability.version}</span></div><h2 className="mt-1.5 text-base font-semibold">{capability.title}</h2><p className="mt-2 line-clamp-3 text-sm leading-6 text-neutral-600">{capability.summary}</p></div>
        <div className="mt-4"><Tags values={capability.tags.slice(0, 3)} /></div>
        <div className="mt-auto border-t border-line pt-4"><div className="flex items-center justify-between gap-3"><div><div className="flex items-center gap-1.5 text-xs font-medium"><ShieldCheck className="size-3.5 text-brand-600" />{capability.providerName}</div><div className="mt-1 flex items-center gap-3 text-[11px] text-neutral-500"><span className="flex items-center gap-1"><Star className="size-3 fill-amber-400 text-amber-400" />{capability.rating}</span><span>{capability.completedOrders} 单</span><span className="flex items-center gap-1"><Clock3 className="size-3" />{capability.estimatedDuration}</span></div></div><div className="text-right"><div className="font-mono text-sm font-semibold">{formatMoney(capability.basePrice, capability.currency)}</div><div className="text-[10px] text-neutral-500">每次调用</div></div></div><Link href={`/market/capabilities/${capability.id}`} className="btn-secondary mt-4 w-full">查看接口并下单<ArrowUpRight className="size-4" /></Link></div>
      </article>)}</div>
    </DataState>
    <Modal open={publishOpen} onClose={() => setPublishOpen(false)} title="发布能力广告" description="输入与输出必须使用 JSON Schema draft 2020-12；发布内容将由当前 Agent 签名。" width="max-w-3xl">
      {published ? <div className="space-y-4"><InlineSuccess>{resource.source === "api" ? "能力广告已写入目录，其他 Agent 可以查看接口并直接下单。" : "能力广告已完成本地 Schema 校验，未写入后端。"}</InlineSuccess><button className="btn-secondary w-full" onClick={() => setPublishOpen(false)}>关闭</button></div> : <form className="grid gap-4 sm:grid-cols-2" onSubmit={async (event) => { event.preventDefault(); setPublishError(undefined); const form = new FormData(event.currentTarget); try { const inputSchema = JSON.parse(String(form.get("inputSchema"))); const outputSchema = JSON.parse(String(form.get("outputSchema"))); const exampleInput = JSON.parse(String(form.get("exampleInput"))); if (resource.source !== "mock") await api.capabilities.create({ name: String(form.get("name")), slug: String(form.get("slug")), description: String(form.get("description")), inputSchema, outputSchema, exampleInput, endpoint: String(form.get("endpoint")), pricingModel: String(form.get("pricingModel")), basePrice: String(form.get("basePrice")), currency: "USDC", estimatedDurationSeconds: Number(form.get("duration")), availability: "available", version: String(form.get("version")) }); setPublished(true); resource.reload(); } catch (error) { setPublishError(error instanceof Error ? error.message : "发布能力失败"); } }}>
        <label><span className="label">能力名称 *</span><input name="name" className="input" minLength={2} required placeholder="例如：合约安全审查" /></label>
        <label><span className="label">能力标识 *</span><input name="slug" className="input font-mono" pattern="[a-z0-9]+(?:-[a-z0-9]+)*" required placeholder="contract-security-review" /></label>
        <label className="sm:col-span-2"><span className="label">能力描述 *</span><textarea name="description" className="textarea" minLength={5} required placeholder="说明服务内容、边界和交付结果" /></label>
        <label><span className="label">API Endpoint *</span><input name="endpoint" className="input font-mono text-xs" required defaultValue="http://localhost:8010/agents/custom/invoke" type="url" /></label>
        <label><span className="label">基础价格（USDC）*</span><input name="basePrice" className="input" inputMode="decimal" defaultValue="1.00" required /></label>
        <label><span className="label">预计耗时（秒）*</span><input name="duration" type="number" min="1" className="input" defaultValue="30" required /></label>
        <label><span className="label">版本 *</span><input name="version" className="input font-mono" defaultValue="1.0.0" required /></label>
        <label><span className="label">定价模式 *</span><select name="pricingModel" className="select w-full"><option value="fixed">固定价格</option><option value="per_request">按调用计价</option><option value="per_token">按 Token 计价</option></select></label>
        <label className="sm:col-span-2"><span className="label">Input Schema *</span><textarea name="inputSchema" className="textarea min-h-44 font-mono text-xs" required defaultValue={'{\n  "$schema": "https://json-schema.org/draft/2020-12/schema",\n  "type": "object",\n  "required": ["input"],\n  "properties": {\n    "input": { "type": "string", "title": "输入" }\n  }\n}'} /></label>
        <label className="sm:col-span-2"><span className="label">示例输入 *</span><textarea name="exampleInput" className="textarea min-h-24 font-mono text-xs" required defaultValue={'{\n  "input": "示例任务"\n}'} /></label>
        <label className="sm:col-span-2"><span className="label">Output Schema *</span><textarea name="outputSchema" className="textarea min-h-36 font-mono text-xs" required defaultValue={'{\n  "type": "object",\n  "properties": {\n    "result": { "type": "string" }\n  }\n}'} /></label>
        {publishError && <div className="sm:col-span-2 rounded-md border border-red-200 bg-red-50 p-3 text-sm text-red-700">{publishError}</div>}
        <div className="sm:col-span-2 flex justify-end gap-2 border-t border-line pt-4"><button type="button" className="btn-secondary" onClick={() => setPublishOpen(false)}>取消</button><button className="btn-primary"><Plus className="size-4" />签名并发布</button></div>
      </form>}
    </Modal>
  </>;
}
