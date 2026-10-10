"use client";

import {
  Activity,
  ArrowLeft,
  Bot,
  CheckCircle2,
  Clock3,
  Coins,
  Cpu,
  ExternalLink,
  Fingerprint,
  Gauge,
  KeyRound,
  Route,
  ShieldCheck,
  Star,
  WalletCards,
} from "lucide-react";
import Link from "next/link";
import { useParams } from "next/navigation";
import { ChainMark, HealthDot } from "@/components/platform-ui";
import { Breadcrumbs, DataState, SourceBanner, Tags } from "@/components/ui";
import { formatDate, shortDid } from "@/lib/format";
import { dynamicApi, type RawAgentProfile } from "@/lib/workflow-api";
import type { ChainSummary } from "@/lib/types";
import { useResource } from "@/lib/use-resource";

const chainLabel = (chainId: number) => `Chain ${String.fromCharCode(65 + chainId - 31337)}`;
const CHAIN_COLORS: Record<number, string> = { 31337: "#6366f1", 31338: "#0ea5e9", 31339: "#10b981", 31340: "#f59e0b" };
const chainSummary = (chainId: number): ChainSummary => {
  const letter = String.fromCharCode(65 + chainId - 31337);
  return {
    chainId,
    name: `Chain ${letter}`,
    shortName: letter,
    role: "execution",
    rpcStatus: "healthy",
    blockNumber: 0,
    blockTime: "",
    color: CHAIN_COLORS[chainId] || "#64748b",
  };
};
const availabilityStatus = (value: string) =>
  value === "ONLINE" ? "healthy" : value === "BUSY" ? "degraded" : "offline";

export default function AgentDetailPage() {
  const params = useParams<{ id: string }>();
  const resource = useResource(() => dynamicApi.agent(params.id).then((data) => ({ data, source: "api" as const })), [params.id]);
  const profile = resource.data;

  const metadata = (profile?.metadata && typeof profile.metadata === "object" ? profile.metadata : {}) as Record<string, unknown>;
  const metadataEntries = Object.entries(metadata).filter(([key]) => key !== "onboarding");

  return <>
    <Breadcrumbs items={[{ label: "Agent 信息平台", href: "/agents" }, { label: profile?.agent.displayName || "Agent 详情" }]} />
    <SourceBanner source={resource.source} />
    <DataState loading={resource.loading} error={resource.error} onRetry={resource.reload}>
      {profile && <div className="fade-in">
        <div className="mb-6 flex flex-wrap items-start justify-between gap-4">
          <div className="flex min-w-0 items-start gap-4">
            <span className="grid size-12 shrink-0 place-items-center rounded-md bg-brand-50 text-brand-700"><Bot className="size-6" /></span>
            <div className="min-w-0"><div className="flex flex-wrap items-center gap-2"><h1 className="page-title">{profile.agent.displayName}</h1><span className="inline-flex items-center gap-1 text-xs font-medium text-emerald-700"><CheckCircle2 className="size-4" />DID 已验证</span></div><p className="mt-1 break-all font-mono text-[11px] text-neutral-500">{profile.agent.did}</p><p className="mt-2 max-w-3xl text-sm leading-6 text-neutral-600">{profile.agent.description}</p></div>
          </div>
          <div className="flex items-center gap-2"><Link href="/agents" className="icon-btn" aria-label="返回 Agent 信息平台"><ArrowLeft className="size-4" /></Link><Link href="/workflows/new" className="btn-primary"><Route className="size-4" />用于新工作流</Link></div>
        </div>

        <section className="surface grid divide-y divide-line sm:grid-cols-2 sm:divide-x sm:divide-y-0 xl:grid-cols-4">
          <div className="p-4"><div className="flex items-center gap-2 text-[11px] text-neutral-500"><Gauge className="size-3.5" />综合信任分</div><div className="mt-2 text-xl font-semibold">{profile.trustScore}<span className="text-xs font-normal text-neutral-400"> / 100</span></div><div className="mt-1 text-[10px] text-neutral-500">Coordinator 评分</div></div>
          <div className="p-4"><div className="flex items-center gap-2 text-[11px] text-neutral-500"><Activity className="size-3.5" />成功率</div><div className="mt-2 text-xl font-semibold">{(profile.successRateBps / 100).toFixed(1)}<span className="text-xs font-normal text-neutral-400"> %</span></div><div className="mt-1"><HealthDot status={availabilityStatus(profile.availability)} label={`端点 ${profile.endpointLatencyMs} ms`} /></div></div>
          <div className="p-4"><div className="flex items-center gap-2 text-[11px] text-neutral-500"><Star className="size-3.5" />运力 / 容量</div><div className="mt-2 text-xl font-semibold">{profile.capacityAvailable ? "可接单" : "满载"}</div><div className="mt-1 text-[10px] text-neutral-500">优先级 {profile.priority}</div></div>
          <div className="p-4"><div className="flex items-center gap-2 text-[11px] text-neutral-500"><Coins className="size-3.5" />能力起步价</div><div className="mt-2 font-mono text-xl font-semibold">{profile.priceFloor}</div><div className="mt-1 text-[10px] text-neutral-500">{profile.currency} · 具体节点重新报价</div></div>
        </section>

        <div className="mt-8 grid gap-8 xl:grid-cols-[minmax(0,1.3fr)_minmax(320px,0.7fr)]">
          <div className="space-y-8">
            <section>
              <div className="section-head"><div><h2 className="section-title">能力与执行策略</h2><p className="mt-1 text-xs text-neutral-500">Coordinator 搜索索引使用这些字段，不使用链作为能力代理</p></div></div>
              <div className="surface divide-y divide-line">
                <div className="grid gap-5 p-5 md:grid-cols-2"><div><div className="text-[11px] font-semibold text-neutral-500">专业方向 / 能力</div><div className="mt-3"><Tags values={profile.capabilities} /></div></div><div><div className="text-[11px] font-semibold text-neutral-500">运行状态</div><div className="mt-3 flex flex-wrap gap-2"><span className="inline-flex items-center gap-1.5 rounded-full bg-neutral-100 px-2.5 py-1 text-[11px] font-medium"><HealthDot status={availabilityStatus(profile.availability)} />{profile.availability}</span>{profile.active && <span className="inline-flex items-center gap-1 rounded-full bg-emerald-50 px-2.5 py-1 text-[11px] font-medium text-emerald-700"><CheckCircle2 className="size-3.5" />ACTIVE</span>}{profile.gasReady && <span className="inline-flex items-center gap-1 rounded-full bg-neutral-100 px-2.5 py-1 text-[11px] font-medium">Gas 就绪</span>}</div></div></div>
                <div className="flex items-start gap-3 p-5"><span className="grid size-8 shrink-0 place-items-center rounded-md bg-neutral-100 text-neutral-600"><Cpu className="size-4" /></span><div><div className="text-[11px] font-semibold text-neutral-500">执行策略</div><p className="mt-1 text-xs leading-5">由 Coordinator 根据 <span className="font-mono">homeChainId</span>（{chainLabel(profile.chainId)}）和容量动态派发；失败 attempt 关闭预留并重新匹配，不改变 nodeId。</p></div></div>
              </div>
            </section>

            {metadataEntries.length > 0 && <section>
              <div className="section-head"><div><h2 className="section-title">运行时元数据</h2><p className="mt-1 text-xs text-neutral-500">来自 Agent 注册 / 心跳上报的 profile.metadata</p></div></div>
              <div className="surface divide-y divide-line">
                {metadataEntries.map(([key, value]) => <div key={key} className="grid gap-2 p-4 sm:grid-cols-[200px_minmax(0,1fr)]"><div className="text-[11px] font-semibold text-neutral-500">{key}</div><div className="break-words font-mono text-[11px] text-neutral-700">{typeof value === "object" ? JSON.stringify(value) : String(value)}</div></div>)}
              </div>
            </section>}
          </div>

          <aside className="space-y-8">
            <section>
              <div className="section-head"><div><h2 className="section-title">归属链账户</h2><p className="mt-1 text-xs text-neutral-500">一个执行 Agent 在 V1 只有一个 homeChainId</p></div></div>
              <div className="surface divide-y divide-line">
                <div className="p-4"><div className="flex items-center gap-3"><ChainMark chain={chainSummary(profile.chainId)} /><HealthDot status={profile.gasReady ? "healthy" : "offline"} label={profile.gasReady ? "Gas 就绪" : "Gas 不足"} /></div></div>
                <div className="p-4"><div className="flex items-center gap-2 text-[10px] text-neutral-500"><WalletCards className="size-3.5" />homeWallet</div><div className="mt-2 break-all font-mono text-xs font-semibold">{profile.homeWallet || "未配置"}</div><div className="mt-2 text-[10px] text-neutral-500">收款钱包由 attempt 快照固定，协调 Agent 无权修改。</div></div>
                <div className="grid grid-cols-2 divide-x divide-line"><div className="p-4"><div className="text-[10px] text-neutral-500">结算资产</div><div className="mt-1 font-mono text-xs font-semibold">{profile.currency}</div></div><div className="p-4"><div className="text-[10px] text-neutral-500">容量</div><div className={`mt-1 font-mono text-xs font-semibold ${profile.capacityAvailable ? "text-emerald-700" : "text-amber-700"}`}>{profile.capacityAvailable ? "可接单" : "满载"}</div></div></div>
              </div>
            </section>

            <section>
              <div className="section-head"><div><h2 className="section-title">凭证与身份</h2><p className="mt-1 text-xs text-neutral-500">仅保存凭据引用状态与指纹，不保存 API key 明文</p></div></div>
              <div className="surface divide-y divide-line">
                <div className="p-4"><div className="flex items-center gap-2 text-[10px] text-neutral-500"><KeyRound className="size-3.5" />credentialStatus</div><div className="mt-2 text-xs font-semibold">{profile.credentialStatus || "UNCONFIGURED"}</div>{profile.credentialFingerprint && <div className="mt-1 break-all font-mono text-[10px] text-neutral-500">{profile.credentialFingerprint}</div>}</div>
                <div className="p-4"><div className="flex items-center gap-2 text-[10px] text-neutral-500"><ShieldCheck className="size-3.5" />Agent 状态</div><div className="mt-1 text-xs font-medium">{profile.agent.status}</div></div>
                <div className="p-4"><div className="flex items-center gap-2 text-[10px] text-neutral-500"><Fingerprint className="size-3.5" />DID</div><div className="mt-1 break-all font-mono text-[10px] text-neutral-600">{shortDid(profile.agent.did)}</div></div>
                <div className="p-4"><div className="flex items-center gap-2 text-[10px] text-neutral-500"><Clock3 className="size-3.5" />信誉分</div><div className="mt-1 text-xs font-medium">{profile.agent.reputationScore}</div></div>
              </div>
            </section>

            <div className="flex items-start gap-2 text-[11px] leading-5 text-neutral-500"><ExternalLink className="mt-0.5 size-3.5 shrink-0" />公开测试网上线前，应在部署当天重新核对 CCIP Directory、Router、chain selector 与受支持 Token。</div>
          </aside>
        </div>
      </div>}
    </DataState>
  </>;
}
