"use client";

import { Activity, ArrowRight, Bot, CheckCircle2, Filter, KeyRound, Network, Search, ShieldCheck } from "lucide-react";
import Link from "next/link";
import { useEffect, useMemo, useState } from "react";

import { InlineMetric } from "@/components/platform-ui";
import { PageHeader } from "@/components/ui";
import { dynamicApi, type RawAgentProfile, type RawRoute } from "@/lib/workflow-api";

const chainLabel = (chainId: number) => `Chain ${String.fromCharCode(65 + chainId - 31337)}`;

export default function AgentsPage() {
  const [agents, setAgents] = useState<RawAgentProfile[]>([]);
  const [routes, setRoutes] = useState<RawRoute[]>([]);
  const [query, setQuery] = useState("");
  const [chainId, setChainId] = useState("ALL");
  const [error, setError] = useState("");
  useEffect(() => {
    Promise.all([dynamicApi.agents(), dynamicApi.routes()])
      .then(([agentResponse, routeResponse]) => {
        setAgents(agentResponse.items);
        setRoutes(routeResponse.items);
      })
      .catch((reason) => setError(reason instanceof Error ? reason.message : String(reason)));
  }, []);
  const filtered = useMemo(() => agents.filter((profile) => {
    const text = `${profile.agentKey}${profile.agent.displayName}${profile.agent.did}${profile.capabilities.join(" ")}`.toLowerCase();
    return text.includes(query.toLowerCase()) && (chainId === "ALL" || profile.chainId === Number(chainId));
  }), [agents, query, chainId]);

  return <>
    <PageHeader title="Agent 信息化平台" description="展示 Coordinator 实际用于匹配的 Agent、能力、归属链和优先级。" />
    {error && <div className="mb-4 border border-red-200 bg-red-50 px-4 py-3 text-sm text-red-700">Agent API 不可用：{error}</div>}
    <section className="surface grid divide-y divide-line sm:grid-cols-2 sm:divide-x sm:divide-y-0 xl:grid-cols-4">
      <InlineMetric label="已注册 Agent" value={String(agents.length)} detail="workflow_agent_profiles" icon={<Bot className="size-3.5" />} />
      <InlineMetric label="活跃" value={String(agents.filter((item) => item.active).length)} detail="可参与 Coordinator 匹配" icon={<Activity className="size-3.5" />} />
      <InlineMetric label="执行链" value={String(new Set(agents.map((item) => item.chainId)).size)} detail="B / C / D" icon={<Network className="size-3.5" />} />
      <InlineMetric label="DID 有效" value={String(agents.filter((item) => item.agent.status === "active").length)} detail="本地身份注册" icon={<ShieldCheck className="size-3.5" />} />
    </section>

    <div className="my-5 flex flex-col gap-3 sm:flex-row">
      <label className="relative min-w-0 flex-1"><Search className="absolute left-3 top-3 size-4 text-neutral-400" /><input className="input pl-9" value={query} onChange={(event) => setQuery(event.target.value)} placeholder="搜索 Agent、DID 或能力" /></label>
      <label className="relative"><Filter className="pointer-events-none absolute left-3 top-3 size-4 text-neutral-400" /><select className="select min-w-40 pl-9" value={chainId} onChange={(event) => setChainId(event.target.value)}><option value="ALL">全部执行链</option><option value="31338">Chain B</option><option value="31339">Chain C</option><option value="31340">Chain D</option></select></label>
    </div>

    <div className="surface divide-y divide-line">
      {filtered.map((profile) => {
        const route = routes
          .filter((item) => item.destinationChainId === profile.chainId && item.status === "ACTIVE")
          .sort((left, right) => right.routeVersion - left.routeVersion)[0];
        const credentialStatus = profile.credentialStatus || "UNCONFIGURED";
        return <Link key={profile.id} href={`/agents/${profile.id}`} className="group grid cursor-pointer gap-4 p-5 transition-colors hover:bg-neutral-50 lg:grid-cols-[minmax(0,1.1fr)_minmax(240px,0.8fr)_minmax(190px,0.55fr)_28px] lg:items-center">
          <div className="min-w-0"><div className="flex flex-wrap items-center gap-2"><span className="text-sm font-semibold">{profile.agentKey}</span><span className="inline-flex items-center gap-1 text-[11px] text-emerald-700"><CheckCircle2 className="size-3.5" />ACTIVE</span></div><div className="mt-1 text-xs text-neutral-600">{profile.agent.displayName}</div><div className="mt-1 truncate font-mono text-[10px] text-neutral-500">{profile.agent.did}</div><p className="mt-2 line-clamp-2 text-xs leading-5 text-neutral-500">{profile.agent.description}</p></div>
          <div className="border-t border-line pt-3 lg:border-l lg:border-t-0 lg:pl-5 lg:pt-0"><div className="flex flex-wrap gap-1.5">{profile.capabilities.map((capability) => <span key={capability} className="border border-line bg-neutral-50 px-2 py-1 text-[10px] font-medium" style={{ borderRadius: 4 }}>{capability}</span>)}</div><div className="mt-3 flex items-center gap-1.5 text-[10px] text-neutral-500"><KeyRound className="size-3.5" /><span>{credentialStatus}</span>{profile.credentialFingerprint && <span className="truncate font-mono">{profile.credentialFingerprint}</span>}</div><div className="mt-1 text-[10px] text-neutral-400">仅保存凭据引用状态与指纹，不保存 API key 明文</div></div>
          <div className="border-t border-line pt-3 lg:border-l lg:border-t-0 lg:pl-5 lg:pt-0"><div className="text-xs font-semibold">{chainLabel(profile.chainId)} · homeChain</div><div className="mt-1 font-mono text-[10px] text-neutral-500">chainId {profile.chainId}</div><div className="mt-1 truncate font-mono text-[10px] text-neutral-500" title={profile.homeWallet}>{profile.homeWallet || "homeWallet 未配置"}</div><div className="mt-3 text-[10px] text-neutral-500">route v{route?.routeVersion || "-"} · {route?.deploymentStatus || "NOT_PLANNED"}</div><div className="mt-1 text-[10px] text-neutral-500">priority {profile.priority}</div></div>
          <div className="hidden items-center justify-end self-center text-neutral-400 transition-colors group-hover:text-brand-600 sm:flex"><ArrowRight className="size-4" /></div>
        </Link>;
      })}
      {!filtered.length && <div className="px-5 py-16 text-center text-sm text-neutral-500">没有匹配的 Agent</div>}
    </div>
  </>;
}
