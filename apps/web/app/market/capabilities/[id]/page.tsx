"use client";

import { Check, Clock3, Code2, Copy, ExternalLink, Play, ShieldCheck, ShoppingCart, Star, TerminalSquare } from "lucide-react";
import Link from "next/link";
import { useParams } from "next/navigation";
import { useMemo, useState } from "react";
import { Modal } from "@/components/modal";
import { SchemaForm } from "@/components/schema-form";
import { Breadcrumbs, CodeBlock, DataState, InlineSuccess, PageHeader, SourceBanner, StatusBadge, Tags } from "@/components/ui";
import { api } from "@/lib/api";
import { formatMoney, shortDid } from "@/lib/format";
import { useResource } from "@/lib/use-resource";

type Tab = "try" | "schema" | "example";

export default function CapabilityDetailPage() {
  const { id } = useParams<{ id: string }>();
  const resource = useResource(() => api.capabilities.get(id), [id]);
  const [tab, setTab] = useState<Tab>("try");
  const [response, setResponse] = useState<Record<string, unknown>>();
  const [responseSource, setResponseSource] = useState<"api" | "mock">();
  const [invokeError, setInvokeError] = useState<string>();
  const [orderOpen, setOrderOpen] = useState(false);
  const [orderSuccess, setOrderSuccess] = useState(false);
  const [createdOrderId, setCreatedOrderId] = useState<string>();
  const [orderError, setOrderError] = useState<string>();
  const capability = resource.data;
  const fullEndpoint = useMemo(() => capability ? `${api.baseUrl}${capability.endpoint}` : "", [capability]);

  const run = async (input: Record<string, unknown>) => {
    if (!capability) return;
    setResponse(undefined); setInvokeError(undefined);
    if (resource.source === "mock") { setResponse(capability.exampleResponse); setResponseSource("mock"); return; }
    try { setResponse(await api.capabilities.invoke(capability.id, input)); setResponseSource("api"); }
    catch (error) { setResponseSource(undefined); setInvokeError(error instanceof Error ? error.message : "调用能力失败"); }
  };

  return <>
    <Breadcrumbs items={[{ label: "能力市场", href: "/market/capabilities" }, { label: capability?.title || "能力详情" }]} />
    <DataState loading={resource.loading} error={resource.error} onRetry={resource.reload}>
      {capability && <div className="fade-in">
        <PageHeader title={capability.title} description={capability.summary} actions={<><StatusBadge status={capability.availability} /><button className="btn-primary" disabled={capability.availability === "offline"} onClick={() => { setOrderOpen(true); setOrderSuccess(false); setCreatedOrderId(undefined); setOrderError(undefined); }}><ShoppingCart className="size-4" />直接下单</button></>} />
        <SourceBanner source={resource.source} />
        <section className="mb-8 grid gap-5 border-y border-line py-5 sm:grid-cols-2 lg:grid-cols-4"><div><div className="text-xs text-neutral-500">服务价格</div><div className="mt-1 font-mono text-lg font-semibold">{formatMoney(capability.basePrice, capability.currency)}</div><div className="text-[11px] text-neutral-500">{capability.pricingModel === "per_request" ? "每次调用" : capability.pricingModel}</div></div><div><div className="text-xs text-neutral-500">预计耗时</div><div className="mt-1 flex items-center gap-2 text-lg font-semibold"><Clock3 className="size-4 text-neutral-400" />{capability.estimatedDuration}</div></div><div><div className="text-xs text-neutral-500">历史完成</div><div className="mt-1 text-lg font-semibold">{capability.completedOrders} 单</div><div className="flex items-center gap-1 text-[11px] text-neutral-500"><Star className="size-3 fill-amber-400 text-amber-400" />评分 {capability.rating}</div></div><div><div className="text-xs text-neutral-500">版本</div><div className="mt-1 font-mono text-lg font-semibold">v{capability.version}</div><div className="text-[11px] text-neutral-500">JSON Schema 2020-12</div></div></section>

        <div className="grid gap-8 xl:grid-cols-[minmax(0,1.5fr)_340px]">
          <div>
            <div className="mb-4 flex overflow-x-auto border-b border-line" role="tablist">{([{ id: "try", label: "在线调试", icon: Play }, { id: "schema", label: "接口 Schema", icon: Code2 }, { id: "example", label: "请求示例", icon: TerminalSquare }] as const).map((item) => <button key={item.id} role="tab" aria-selected={tab === item.id} className={`flex h-11 shrink-0 items-center gap-2 border-b-2 px-4 text-sm ${tab === item.id ? "border-brand-600 font-medium text-brand-700" : "border-transparent text-neutral-500 hover:text-ink"}`} onClick={() => setTab(item.id)}><item.icon className="size-4" />{item.label}</button>)}</div>
            {tab === "try" && <div className="grid gap-6 lg:grid-cols-2"><section><h2 className="section-title">构造请求</h2><p className="mb-4 mt-1 text-xs text-neutral-500">表单依据该能力的 inputSchema 动态生成。</p><SchemaForm schema={capability.inputSchema} onSubmit={run} disabled={capability.availability === "offline"} /></section><section><div className="mb-4 flex items-center justify-between"><div><h2 className="section-title">响应</h2><p className="mt-1 text-xs text-neutral-500">执行结果与来源会明确标记。</p></div>{responseSource && <StatusBadge status={responseSource === "api" ? "success" : "pending"} />}</div>{invokeError && <div className="mb-3 rounded-md border border-red-200 bg-red-50 p-3 text-sm text-red-700">{invokeError}</div>}{response ? <><CodeBlock value={response} maxHeight="430px" />{responseSource === "mock" && <p className="mt-2 text-xs leading-5 text-amber-700">后端未连接，以上为能力定义中的示例响应，不是实际执行结果。</p>}</> : <div className="grid min-h-72 place-items-center rounded-md border border-dashed border-line bg-neutral-50 p-5 text-center text-sm text-neutral-500"><div><Play className="mx-auto mb-3 size-6 text-neutral-400" />填写左侧参数并运行接口<br /><span className="mt-1 block text-xs text-neutral-400">响应不会自动创建订单</span></div></div>}</section></div>}
            {tab === "schema" && <div className="grid gap-6 lg:grid-cols-2"><section><h2 className="section-title">Input Schema</h2><div className="mt-3"><CodeBlock value={capability.inputSchema} maxHeight="560px" /></div></section><section><h2 className="section-title">Output Schema</h2><div className="mt-3"><CodeBlock value={capability.outputSchema} maxHeight="560px" /></div></section></div>}
            {tab === "example" && <div className="space-y-6"><section><h2 className="section-title">请求地址</h2><div className="mt-3 flex min-w-0 items-center gap-2 rounded-md border border-line bg-neutral-50 p-2"><span className="rounded bg-brand-600 px-2 py-1 font-mono text-[11px] font-semibold text-white">{capability.method}</span><code className="min-w-0 flex-1 truncate text-xs">{fullEndpoint}</code><button className="icon-btn size-8" aria-label="复制地址" onClick={() => navigator.clipboard?.writeText(fullEndpoint)}><Copy className="size-3.5" /></button></div></section><div className="grid gap-6 lg:grid-cols-2"><section><h2 className="section-title">请求体</h2><div className="mt-3"><CodeBlock value={{ input: capability.exampleRequest }} /></div></section><section><h2 className="section-title">响应体</h2><div className="mt-3"><CodeBlock value={capability.exampleResponse} /></div></section></div></div>}
          </div>

          <aside className="space-y-5">
            <div className="surface p-4"><div className="flex items-center gap-3"><span className="grid size-10 place-items-center rounded-md bg-brand-50 text-brand-700"><ShieldCheck className="size-5" /></span><div><div className="font-medium">{capability.providerName}</div><div className="mt-0.5 text-xs text-brand-700">DID 身份已验证</div></div></div><div className="mt-3 break-all rounded bg-neutral-50 p-2 font-mono text-[11px] text-neutral-500">{shortDid(capability.providerDid, 44)}</div><Link href={`/agents/${capability.providerId}`} className="btn-secondary mt-3 w-full">查看 Agent 身份<ExternalLink className="size-4" /></Link></div>
            <div className="surface p-4"><h2 className="text-sm font-semibold">服务标签</h2><div className="mt-3"><Tags values={capability.tags} /></div></div>
            <div className="rounded-md border border-amber-200 bg-amber-50 p-4 text-xs leading-5 text-amber-800"><strong className="font-medium">结算处于演示模式</strong><p className="mt-1">下单会生成结算意向和条款快照，但不会转移真实资产。</p></div>
          </aside>
        </div>
      </div>}
    </DataState>

    {capability && <Modal open={orderOpen} onClose={() => setOrderOpen(false)} title="创建直接订单" description={`${capability.providerName} · ${formatMoney(capability.basePrice, capability.currency)}`} width="max-w-2xl">
      {orderSuccess ? <div className="space-y-5"><InlineSuccess>{resource.source === "api" ? "订单和模拟结算意向已由后端创建。" : "订单参数已完成本地校验；当前为演示数据，未写入后端。"}</InlineSuccess><div className="rounded-md bg-neutral-50 p-3 text-xs leading-5 text-neutral-500"><div className="flex gap-2"><Check className="mt-0.5 size-4 text-brand-600" />能力条款、版本、价格与输入会固化为订单快照。</div></div>{createdOrderId ? <Link className="btn-primary w-full" href={`/orders/${createdOrderId}`}>查看新订单</Link> : <button className="btn-primary w-full" onClick={() => setOrderOpen(false)}>完成</button>}</div> : <div className="space-y-4">{orderError && <div className="rounded-md border border-red-200 bg-red-50 p-3 text-sm text-red-700">{orderError}</div>}<SchemaForm schema={capability.inputSchema} submitLabel={`确认下单 · ${formatMoney(capability.basePrice, capability.currency)}`} onSubmit={async (input) => { setOrderError(undefined); if (resource.source === "mock") { setOrderSuccess(true); return; } try { const order = await api.capabilities.order(capability.id, input); setCreatedOrderId(order.id); setOrderSuccess(true); } catch (error) { setOrderError(error instanceof Error ? error.message : "创建订单失败"); } }} /></div>}
    </Modal>}
  </>;
}
