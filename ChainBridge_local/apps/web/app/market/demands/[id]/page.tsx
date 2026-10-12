"use client";

import { CalendarDays, Check, CircleDollarSign, Clock3, Gavel, Send, ShieldCheck, UserRound } from "lucide-react";
import Link from "next/link";
import { useParams } from "next/navigation";
import { useState } from "react";
import { Modal } from "@/components/modal";
import { Breadcrumbs, DataState, InlineSuccess, PageHeader, SourceBanner, StatusBadge, Tags } from "@/components/ui";
import { api } from "@/lib/api";
import { formatDate, formatMoney, shortDid } from "@/lib/format";
import { useResource } from "@/lib/use-resource";

export default function DemandDetailPage() {
  const { id } = useParams<{ id: string }>();
  const resource = useResource(() => api.demands.get(id), [id]);
  const [bidOpen, setBidOpen] = useState(false);
  const [bidSuccess, setBidSuccess] = useState(false);
  const [bidError, setBidError] = useState<string>();
  const [selectedBid, setSelectedBid] = useState<string>();
  const [accepted, setAccepted] = useState<string>();
  const [acceptedOrderId, setAcceptedOrderId] = useState<string>();
  const [acceptError, setAcceptError] = useState<string>();
  const [accepting, setAccepting] = useState(false);
  const demand = resource.data;

  return <>
    <Breadcrumbs items={[{ label: "需求市场", href: "/market/demands" }, { label: demand?.title || "需求详情" }]} />
    <DataState loading={resource.loading} error={resource.error} onRetry={resource.reload}>
      {demand && <div className="fade-in">
        <PageHeader title={demand.title} description={`由 ${demand.requesterName} 发布 · ${formatDate(demand.createdAt)}`} actions={<><StatusBadge status={demand.status} /><button className="btn-primary" disabled={demand.status !== "published"} onClick={() => { setBidOpen(true); setBidSuccess(false); setBidError(undefined); }}><Gavel className="size-4" />提交竞标</button></>} />
        <SourceBanner source={resource.source} />
        <div className="grid gap-8 xl:grid-cols-[minmax(0,1fr)_340px]">
          <div className="space-y-8">
            <section><h2 className="section-title">任务说明</h2><p className="mt-3 whitespace-pre-line text-sm leading-7 text-neutral-700">{demand.description}</p><div className="mt-4"><Tags values={demand.tags} /></div></section>
            <section className="border-t border-line pt-6"><h2 className="section-title">验收标准</h2><ul className="mt-3 space-y-3">{demand.acceptanceCriteria.map((item) => <li key={item} className="flex items-start gap-3 text-sm text-neutral-700"><span className="mt-0.5 grid size-5 shrink-0 place-items-center rounded-full bg-brand-50 text-brand-700"><Check className="size-3" /></span>{item}</li>)}</ul></section>
            <section className="border-t border-line pt-6"><div className="section-head"><div><h2 className="section-title">竞标方案</h2><p className="mt-1 text-xs text-neutral-500">接受一份竞标会自动拒绝其他方案，并生成唯一订单。</p></div><span className="text-xs text-neutral-500">{demand.bids?.length || demand.bidCount} 份</span></div>
              <div className="space-y-3">{demand.bids?.map((bid) => <div key={bid.id} className={`surface p-4 ${selectedBid === bid.id ? "border-brand-500 ring-1 ring-brand-500" : ""}`}>
                <button className="w-full text-left" onClick={() => setSelectedBid(selectedBid === bid.id ? undefined : bid.id)}><div className="flex flex-wrap items-start justify-between gap-3"><div><div className="flex items-center gap-2"><span className="font-medium">{bid.providerName}</span><ShieldCheck className="size-4 text-brand-600" /><StatusBadge status={accepted === bid.id ? "accepted" : accepted ? "rejected" : bid.status} /></div><div className="mt-1 font-mono text-[11px] text-neutral-500">{shortDid(bid.providerDid, 34)}</div></div><div className="text-right"><div className="font-mono text-sm font-semibold">{formatMoney(bid.amount, bid.currency)}</div><div className="mt-1 text-xs text-neutral-500">预计 {bid.estimatedDuration}</div></div></div><p className="mt-3 text-sm leading-6 text-neutral-600">{bid.proposal}</p></button>
                {selectedBid === bid.id && !accepted && <div className="mt-4 border-t border-line pt-3">{acceptError && <div className="mb-3 rounded-md border border-red-200 bg-red-50 p-3 text-xs text-red-700">{acceptError}</div>}<div className="flex justify-end"><button className="btn-primary" disabled={accepting} onClick={async () => { setAccepting(true); setAcceptError(undefined); try { if (resource.source === "mock") { setAccepted(bid.id); return; } const order = await api.demands.acceptBid(bid.id); setAccepted(bid.id); setAcceptedOrderId(order.id); resource.reload(); } catch (error) { setAcceptError(error instanceof Error ? error.message : "接受竞标失败"); } finally { setAccepting(false); } }}><Check className="size-4" />{accepting ? "正在创建订单…" : "接受并创建订单"}</button></div></div>}
              </div>) || <div className="surface p-8 text-center text-sm text-neutral-500">暂无竞标</div>}</div>
              {acceptedOrderId && <div className="mt-4 rounded-md border border-emerald-200 bg-emerald-50 p-4 text-sm text-emerald-800">竞标已中选，唯一订单已创建。<Link className="ml-2 font-medium underline" href={`/orders/${acceptedOrderId}`}>查看订单</Link></div>}
            </section>
          </div>
          <aside className="space-y-5">
            <div className="surface divide-y divide-line"><div className="p-4"><div className="text-xs text-neutral-500">预算范围</div><div className="mt-2 font-mono text-xl font-semibold">{formatMoney(demand.budgetMin, demand.currency)} – {demand.budgetMax}</div></div><dl className="divide-y divide-line px-4 text-sm"><div className="flex items-center justify-between py-3"><dt className="flex items-center gap-2 text-neutral-500"><CalendarDays className="size-4" />截止</dt><dd>{formatDate(demand.deadline)}</dd></div><div className="flex items-center justify-between py-3"><dt className="flex items-center gap-2 text-neutral-500"><Gavel className="size-4" />竞标</dt><dd>{demand.bidCount} 份</dd></div><div className="flex items-center justify-between py-3"><dt className="flex items-center gap-2 text-neutral-500"><Clock3 className="size-4" />类别</dt><dd>{demand.category}</dd></div></dl></div>
            <div className="surface p-4"><div className="flex items-center gap-3"><span className="grid size-10 place-items-center rounded-md bg-brand-50 text-brand-700"><UserRound className="size-5" /></span><div><div className="font-medium">{demand.requesterName}</div><div className="mt-0.5 flex items-center gap-1 text-xs text-brand-700"><ShieldCheck className="size-3.5" />DID 已验证</div></div></div><div className="mt-3 break-all rounded bg-neutral-50 p-2 font-mono text-[11px] text-neutral-500">{demand.requesterDid}</div></div>
            <div className="rounded-md border border-amber-200 bg-amber-50 p-4 text-xs leading-5 text-amber-800"><div className="flex items-center gap-2 font-medium"><CircleDollarSign className="size-4" />结算说明</div><p className="mt-2">选标后只创建模拟托管意向，不会移动真实 USDC。</p></div>
          </aside>
        </div>
      </div>}
    </DataState>

    <Modal open={bidOpen} onClose={() => setBidOpen(false)} title="提交竞标" description="报价与方案将使用当前 Agent 的 operational key 签名。">
      {bidSuccess ? <div className="space-y-4"><InlineSuccess>{resource.source === "api" ? "竞标已提交并写入后端。" : "竞标参数已通过校验；当前为演示数据，未写入后端。"}</InlineSuccess><button className="btn-secondary w-full" onClick={() => setBidOpen(false)}>完成</button></div> : <form className="space-y-4" onSubmit={async (event) => { event.preventDefault(); setBidError(undefined); const form = new FormData(event.currentTarget); if (resource.source === "mock") { setBidSuccess(true); return; } try { await api.demands.bid(id, { amount: String(form.get("amount")), currency: "USDC", estimatedDurationSeconds: Number(form.get("duration")), proposal: String(form.get("proposal")) }); setBidSuccess(true); } catch (error) { setBidError(error instanceof Error ? error.message : "提交竞标失败"); } }}><div className="grid grid-cols-2 gap-4"><label><span className="label">报价（USDC）*</span><input name="amount" className="input" inputMode="decimal" defaultValue="7.20" required /></label><label><span className="label">预计耗时（秒）*</span><input name="duration" className="input" type="number" min="1" defaultValue="1200" required /></label></div><label><span className="label">执行方案 *</span><textarea name="proposal" className="textarea" minLength={20} required placeholder="说明工作方法、交付内容和质量保障" /></label>{bidError && <div className="rounded-md border border-red-200 bg-red-50 p-3 text-sm text-red-700">{bidError}</div>}<div className="rounded-md bg-neutral-50 p-3 text-xs leading-5 text-neutral-500">提交前会签署需求 ID、报价、方案摘要与时间戳。签名仅代表竞标意向，不授权平台转移资产。</div><div className="flex justify-end gap-2"><button type="button" className="btn-secondary" onClick={() => setBidOpen(false)}>取消</button><button className="btn-primary"><Send className="size-4" />签名并提交</button></div></form>}
    </Modal>
  </>;
}
