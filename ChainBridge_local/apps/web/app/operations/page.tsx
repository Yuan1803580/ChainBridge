"use client";

import { Activity, AlertTriangle, CheckCircle2, Clock3, Database, Radio, RefreshCcw, Server } from "lucide-react";
import { useEffect, useState } from "react";

import { InlineMetric } from "@/components/platform-ui";
import { PageHeader } from "@/components/ui";
import { dynamicApi, type RawCrossChainMessage } from "@/lib/workflow-api";

const short = (value: string) => value.length > 18 ? `${value.slice(0, 10)}...${value.slice(-6)}` : value;

export default function OperationsPage() {
  const [messages, setMessages] = useState<RawCrossChainMessage[]>([]);
  const [health, setHealth] = useState<{ status: string; database: string; timestamp: string } | null>(null);
  const [error, setError] = useState("");
  const load = () => {
    setError("");
    Promise.all([dynamicApi.messages(), dynamicApi.health()])
      .then(([messageResponse, healthResponse]) => {
        setMessages(messageResponse.items);
        setHealth(healthResponse);
      })
      .catch((reason) => setError(reason instanceof Error ? reason.message : String(reason)));
  };
  useEffect(load, []);
  const queued = messages.filter((item) => item.status === "QUEUED").length;
  const delivered = messages.filter((item) => item.status === "DELIVERED").length;
  const failures = messages.filter((item) => item.status === "FAILED" || item.lastError).length;

  return <>
    <PageHeader title="Worker 与 Relay 运维" description="跨链消息、routeVersion、业务 attemptId 和传输重试次数来自真实数据库。" actions={<button className="btn-secondary" onClick={load}><RefreshCcw className="size-4" />刷新</button>} />
    {error && <div className="mb-4 border border-red-200 bg-red-50 px-4 py-3 text-sm text-red-700">运维 API 不可用：{error}</div>}
    <section className="surface grid divide-y divide-line sm:grid-cols-2 sm:divide-x sm:divide-y-0 xl:grid-cols-4">
      <InlineMetric label="FastAPI" value={health?.status || "检查中"} detail={health?.database || "database"} icon={<Server className="size-3.5" />} />
      <InlineMetric label="队列" value={String(queued)} detail="QUEUED messages" icon={<Database className="size-3.5" />} />
      <InlineMetric label="已投递" value={String(delivered)} detail={`${messages.length} 条总消息`} icon={<Radio className="size-3.5" />} />
      <InlineMetric label="异常" value={String(failures)} detail="失败或含 lastError" icon={<AlertTriangle className="size-3.5" />} />
    </section>

    <section className="mt-8">
      <div className="section-head"><div><h2 className="section-title">Cross-chain Message Queue</h2><p className="mt-1 text-xs text-neutral-500">relayAttempts 是传输重试，不是 NodeAttempt.attemptNumber</p></div></div>
      <div className="surface overflow-x-auto scrollbar-thin">
        <table className="w-full min-w-[1080px] text-left">
          <thead className="border-b border-line bg-neutral-50 text-[11px] text-neutral-500"><tr><th className="px-4 py-3">消息</th><th className="px-4 py-3">类型</th><th className="px-4 py-3">链路</th><th className="px-4 py-3">routeVersion</th><th className="px-4 py-3">业务 attemptId</th><th className="px-4 py-3">Relay 尝试</th><th className="px-4 py-3">状态</th><th className="px-4 py-3">时间</th></tr></thead>
          <tbody className="divide-y divide-line">
            {messages.map((message) => <tr key={message.id} className="hover:bg-neutral-50">
              <td className="px-4 py-3 font-mono text-[10px]" title={message.messageId}>{short(message.messageId)}</td>
              <td className="px-4 py-3 font-mono text-[10px] font-semibold">{message.messageType}</td>
              <td className="px-4 py-3 font-mono text-[10px]">{message.sourceChainId} → {message.destinationChainId}</td>
              <td className="px-4 py-3 font-mono text-xs">v{message.routeVersion}</td>
              <td className="px-4 py-3 font-mono text-[10px]" title={message.attemptId}>{short(message.attemptId)}</td>
              <td className="px-4 py-3 font-mono text-xs">{message.relayAttempts}</td>
              <td className="px-4 py-3"><span className={`inline-flex items-center gap-1.5 text-[11px] font-semibold ${message.status === "DELIVERED" ? "text-emerald-700" : message.status === "QUEUED" ? "text-amber-700" : "text-red-700"}`}>{message.status === "DELIVERED" ? <CheckCircle2 className="size-3.5" /> : message.status === "QUEUED" ? <Clock3 className="size-3.5" /> : <AlertTriangle className="size-3.5" />}{message.status}</span></td>
              <td className="px-4 py-3 font-mono text-[10px] text-neutral-500">{new Date(message.createdAt).toLocaleString("zh-CN", { hour12: false })}</td>
            </tr>)}
            {!messages.length && <tr><td colSpan={8} className="px-4 py-16 text-center text-sm text-neutral-500">暂无跨链消息</td></tr>}
          </tbody>
        </table>
      </div>
    </section>

    <section className="mt-8">
      <div className="section-head"><div><h2 className="section-title">运行边界</h2><p className="mt-1 text-xs text-neutral-500">Worker 不持有业务预算</p></div></div>
      <div className="surface grid divide-y divide-line sm:grid-cols-3 sm:divide-x sm:divide-y-0">
        <div className="flex gap-3 p-4"><Activity className="size-4 text-brand-700" /><div><div className="text-xs font-semibold">精确路由上下文</div><p className="mt-1 text-[11px] leading-5 text-neutral-500">按 routeId + routeVersion 解析 RPC 和 RemoteWorkBoard。</p></div></div>
        <div className="flex gap-3 p-4"><Database className="size-4 text-brand-700" /><div><div className="text-xs font-semibold">幂等消息 ID</div><p className="mt-1 text-[11px] leading-5 text-neutral-500">相同 messageId 不能重复入队或再次执行。</p></div></div>
        <div className="flex gap-3 p-4"><Radio className="size-4 text-brand-700" /><div><div className="text-xs font-semibold">异步传输重试</div><p className="mt-1 text-[11px] leading-5 text-neutral-500">RPC 重试不会生成新的业务 attemptId。</p></div></div>
      </div>
    </section>
  </>;
}
