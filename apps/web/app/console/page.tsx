"use client";

import { CheckCircle2, ChevronDown, Copy, KeyRound, Play, Server, TerminalSquare } from "lucide-react";
import { useEffect, useMemo, useState } from "react";
import { CodeBlock, DataState, PageHeader, SourceBanner, StatusBadge } from "@/components/ui";
import { api } from "@/lib/api";
import { useResource } from "@/lib/use-resource";

export default function ConsolePage() {
  const capabilities = useResource(() => api.capabilities.list(), []);
  const [capabilityId, setCapabilityId] = useState("");
  const [body, setBody] = useState("{}");
  const [response, setResponse] = useState<Record<string, unknown>>();
  const [source, setSource] = useState<"api" | "mock">();
  const [running, setRunning] = useState(false);
  const [error, setError] = useState<string>();
  const selected = useMemo(() => capabilities.data?.find((item) => item.id === capabilityId) || capabilities.data?.[0], [capabilities.data, capabilityId]);

  useEffect(() => {
    if (!selected || capabilityId) return;
    setCapabilityId(selected.id);
    setBody(JSON.stringify({ inputPayload: selected.exampleRequest }, null, 2));
  }, [capabilityId, selected]);

  const changeCapability = (id: string) => { const next = capabilities.data?.find((item) => item.id === id); if (!next) return; setCapabilityId(id); setBody(JSON.stringify({ inputPayload: next.exampleRequest }, null, 2)); setResponse(undefined); setError(undefined); };
  const run = async () => {
    setRunning(true); setResponse(undefined); setError(undefined);
    try { if (!selected) throw new Error("没有可调用的能力"); const parsed = JSON.parse(body) as { inputPayload?: Record<string, unknown> }; if (!parsed.inputPayload) throw new Error("请求体必须包含 inputPayload 对象"); if (capabilities.source === "mock") { setResponse(selected.exampleResponse); setSource("mock"); } else { setResponse(await api.capabilities.invoke(selected.id, parsed.inputPayload)); setSource("api"); } } catch (value) { setError(value instanceof Error ? value.message : "请求格式无效"); } finally { setRunning(false); }
  };

  if (capabilities.loading || capabilities.error || !selected) return <><PageHeader title="API 控制台" description="查看 Agent 接入端点、构造调用请求并检查响应。" /><DataState loading={capabilities.loading} error={capabilities.error} empty={!capabilities.loading && !capabilities.error && !selected} onRetry={capabilities.reload}>{null}</DataState></>;

  return <>
    <PageHeader title="API 控制台" description="查看 Agent 接入端点、构造调用请求并检查响应。演示请求不会创建订单。" actions={<div className="flex items-center gap-2 rounded-md border border-line bg-white px-3 py-2 text-xs text-neutral-600"><Server className="size-4" />{api.baseUrl}</div>} />
    <SourceBanner source={capabilities.source} />
    <div className="grid gap-6 xl:grid-cols-[minmax(0,1fr)_minmax(360px,0.8fr)]">
      <section className="surface overflow-hidden">
        <div className="border-b border-line p-4"><label className="label">能力接口</label><div className="relative"><select className="select w-full appearance-none" value={selected.id} onChange={(e) => changeCapability(e.target.value)}>{capabilities.data?.map((item) => <option value={item.id} key={item.id}>{item.providerName} · {item.title}</option>)}</select><ChevronDown className="pointer-events-none absolute right-3 top-3 size-4 text-neutral-400" /></div></div>
        <div className="flex min-w-0 items-center gap-2 border-b border-line bg-neutral-50 p-3"><span className="rounded bg-brand-600 px-2 py-1 font-mono text-[11px] font-semibold text-white">POST</span><code className="min-w-0 flex-1 truncate text-xs">{api.baseUrl}{selected.endpoint}</code><button className="icon-btn size-8 bg-white" aria-label="复制端点" onClick={() => navigator.clipboard?.writeText(`${api.baseUrl}${selected.endpoint}`)}><Copy className="size-3.5" /></button></div>
        <div className="p-4"><div className="mb-2 flex items-center justify-between"><label htmlFor="request-body" className="text-xs font-medium text-neutral-600">请求体</label><span className="font-mono text-[10px] text-neutral-400">application/json</span></div><textarea id="request-body" value={body} onChange={(e) => setBody(e.target.value)} spellCheck={false} className="code-scroll min-h-[440px] w-full resize-y rounded-md border-0 bg-[#17201c] p-4 font-mono text-xs leading-5 text-emerald-50 outline-none" />{error && <div className="mt-3 rounded-md border border-red-200 bg-red-50 p-3 text-xs text-red-700">{error}</div>}<button className="btn-primary mt-4 w-full" onClick={run} disabled={running}>{running ? "正在发送…" : <><Play className="size-4" />发送请求</>}</button></div>
      </section>
      <div className="space-y-6">
        <section className="surface overflow-hidden"><div className="flex items-center justify-between border-b border-line px-4 py-3"><div className="flex items-center gap-2"><TerminalSquare className="size-4 text-neutral-500" /><h2 className="text-sm font-semibold">响应</h2></div>{source && <StatusBadge status={source === "api" ? "success" : "pending"} />}</div><div className="p-4">{response ? <><CodeBlock value={response} maxHeight="440px" />{source === "mock" && <p className="mt-2 text-xs leading-5 text-amber-700">这是能力定义中的示例响应，未实际调用 Agent。</p>}</> : <div className="grid min-h-64 place-items-center text-center text-sm text-neutral-500"><div><Play className="mx-auto mb-3 size-6 text-neutral-400" />发送请求后在此查看结果</div></div>}</div></section>
        <section className="surface p-4"><h2 className="text-sm font-semibold">认证与幂等</h2><div className="mt-4 space-y-3 text-xs"><div className="flex items-start gap-3"><KeyRound className="mt-0.5 size-4 shrink-0 text-brand-600" /><div><div className="font-medium">Authorization</div><code className="mt-1 block text-neutral-500">Bearer &lt;DID session token&gt;</code></div></div><div className="flex items-start gap-3"><CheckCircle2 className="mt-0.5 size-4 shrink-0 text-brand-600" /><div><div className="font-medium">Idempotency-Key</div><p className="mt-1 leading-5 text-neutral-500">所有变更型 Agent API 必须使用唯一 UUID，重复请求返回同一结果。</p></div></div></div></section>
      </div>
    </div>
  </>;
}
