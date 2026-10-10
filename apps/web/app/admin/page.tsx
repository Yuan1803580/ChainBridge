"use client";

import { Activity, AlertTriangle, Check, Clipboard, Clock3, FileSearch, KeyRound, LoaderCircle, RefreshCw, RotateCcw, Search, ShieldAlert, ShieldCheck, Ticket, UserCheck, X } from "lucide-react";
import { useCallback, useEffect, useState } from "react";
import { PageHeader, SourceBanner, StatusBadge } from "@/components/ui";
import { api } from "@/lib/api";
import { formatDate, shortDid } from "@/lib/format";
import { useResource } from "@/lib/use-resource";
import { AgentInvitation, AgentProvisioningRecord, RawRoute, dynamicApi } from "@/lib/workflow-api";

type Tab = "review" | "disputes" | "audit";

type RuntimeProfile = "local-demo" | "managed-testnet";
type ManagedHealth = { status: string; services: Array<{ name: string; status: "ok" | "down" | "unknown"; detail?: string }> };

export default function AdminPage() {
  const [profile, setProfile] = useState<RuntimeProfile | null>(null);
  useEffect(() => {
    fetch("/api/runtime-health", { cache: "no-store" })
      .then((response) => response.json())
      .then((value: { profile?: RuntimeProfile }) => setProfile(value.profile || "local-demo"))
      .catch(() => setProfile("local-demo"));
  }, []);
  if (!profile) return <div className="grid min-h-64 place-items-center"><LoaderCircle className="size-5 animate-spin text-neutral-400" /></div>;
  return profile === "managed-testnet" ? <ManagedAdminPage /> : <LegacyAdminPage />;
}

function ManagedAdminPage() {
  const [health, setHealth] = useState<ManagedHealth | null>(null);
  const [invitations, setInvitations] = useState<AgentInvitation[]>([]);
  const [provisioning, setProvisioning] = useState<AgentProvisioningRecord[]>([]);
  const [routes, setRoutes] = useState<RawRoute[]>([]);
  const [adminToken, setAdminToken] = useState("");
  const [invitationRole, setInvitationRole] = useState<"provider" | "requester" | "both">("provider");
  const [capabilities, setCapabilities] = useState("research");
  const [secretRef, setSecretRef] = useState("env:DEEPSEEK_API_KEY");
  const [expiresInHours, setExpiresInHours] = useState(24);
  const [runnerType, setRunnerType] = useState<"llm" | "http">("llm");
  const [createdCode, setCreatedCode] = useState("");
  const [busy, setBusy] = useState("");
  const [error, setError] = useState("");
  const [message, setMessage] = useState("");

  const load = useCallback(async () => {
    setError("");
    const healthRequest = fetch("/api/managed-health", { cache: "no-store" }).then((response) => response.json() as Promise<ManagedHealth>);
    const [healthResult, invitationResult, provisionResult, routeResult] = await Promise.allSettled([
      healthRequest,
      dynamicApi.invitations(),
      dynamicApi.provisioning(),
      dynamicApi.routes(),
    ]);
    if (healthResult.status === "fulfilled") setHealth(healthResult.value);
    if (invitationResult.status === "fulfilled") setInvitations(invitationResult.value.items);
    if (provisionResult.status === "fulfilled") setProvisioning(provisionResult.value.items);
    if (routeResult.status === "fulfilled") setRoutes(routeResult.value.items);
    const rejected = [invitationResult, provisionResult, routeResult].find((item) => item.status === "rejected") as PromiseRejectedResult | undefined;
    if (rejected) setError(rejected.reason instanceof Error ? rejected.reason.message : String(rejected.reason));
  }, []);

  useEffect(() => { void load(); }, [load]);

  const withAction = async (name: string, action: () => Promise<void>) => {
    setBusy(name);
    setError("");
    setMessage("");
    try {
      await action();
      await load();
    } catch (reason) {
      setError(reason instanceof Error ? reason.message : String(reason));
    } finally {
      setBusy("");
    }
  };

  const installSession = () => {
    if (adminToken.trim().length < 32) {
      setError("管理员会话令牌无效");
      return;
    }
    window.localStorage.setItem("agentMarketToken", adminToken.trim());
    setAdminToken("");
    setMessage("管理员会话已更新");
    void load();
  };

  const activeRoute = routes.find((route) => route.routeKey === "SEPOLIA_TO_ARBITRUM_SEPOLIA_CCIP_BNM_V1" && route.status === "ACTIVE");
  const invitationRoles: Array<"requester" | "provider"> = invitationRole === "both" ? ["requester", "provider"] : [invitationRole];
  const createInvitation = () => withAction("invite", async () => {
    if (!activeRoute) throw new Error("CCIP-BnM 测试网路由尚未激活");
    const values = capabilities.split(",").map((item) => item.trim().toLowerCase()).filter(Boolean);
    if (!values.length) throw new Error("至少填写一个 capability");
    const created = await dynamicApi.createInvitation({
      routeKey: activeRoute.routeKey,
      minRouteVersion: activeRoute.routeVersion,
      maxRouteVersion: activeRoute.routeVersion,
      capabilities: invitationRoles.includes("provider") ? values : [],
      roles: invitationRoles,
      executorType: runnerType,
      executorCredentialRef: runnerType === "llm" && invitationRoles.includes("provider") ? secretRef.trim() : undefined,
      expiresInHours,
    });
    setCreatedCode(created.inviteCode);
    setMessage("邀请码已创建");
  });

  const reconcile = () => withAction("reconcile", async () => {
    const result = await dynamicApi.reconcileProvisioning();
    setMessage(`已处理 ${result.processed} 项，完成 ${result.completed} 项`);
  });

  const retry = (profileId: string) => withAction(profileId, async () => {
    const result = await dynamicApi.adminRetryProvisioning(profileId);
    setMessage(`Runtime 状态：${result.status}`);
  });

  return <>
    <PageHeader title="平台管理" description="管理 Agent 邀请、Runner 创建状态和托管服务健康。" actions={<button className="icon-btn" type="button" title="刷新" onClick={() => void load()}><RefreshCw className="size-4" /></button>} />

    {error && <div className="mb-5 border border-red-200 bg-red-50 px-4 py-3 text-sm text-red-700">{error}</div>}
    {message && <div className="mb-5 border border-emerald-200 bg-emerald-50 px-4 py-3 text-sm text-emerald-700">{message}</div>}

    <section className="mb-7">
      <div className="section-head"><div><h2 className="section-title">服务状态</h2><p className="mt-1 text-xs text-neutral-500">Managed testnet</p></div></div>
      <div className="surface grid divide-y divide-line sm:grid-cols-2 sm:divide-x sm:divide-y-0 xl:grid-cols-4">
        {(health?.services || []).map((service) => <div className="flex min-w-0 items-center gap-3 p-4" key={service.name}>
          <span className={`size-2 shrink-0 rounded-full ${service.status === "ok" ? "bg-emerald-500" : service.status === "down" ? "bg-red-500" : "bg-amber-500"}`} />
          <div className="min-w-0"><div className="truncate text-sm font-medium">{service.name}</div><div className="mt-0.5 truncate text-[11px] text-neutral-500">{service.status.toUpperCase()}{service.detail ? ` · ${service.detail}` : ""}</div></div>
        </div>)}
        {!health?.services.length && <div className="col-span-full p-5 text-sm text-neutral-500">健康状态读取中</div>}
      </div>
    </section>

    <div className="grid gap-7 xl:grid-cols-[minmax(0,1fr)_420px]">
      <section className="min-w-0">
        <div className="section-head"><div><h2 className="section-title">Runtime 创建状态</h2><p className="mt-1 text-xs text-neutral-500">Provision outbox 与补偿记录</p></div><button className="btn-secondary" type="button" onClick={reconcile} disabled={Boolean(busy)}>{busy === "reconcile" ? <LoaderCircle className="size-4 animate-spin" /> : <RotateCcw className="size-4" />}执行补偿</button></div>
        <div className="surface overflow-x-auto scrollbar-thin">
          <table className="w-full min-w-[760px] text-left"><thead className="border-b border-line bg-neutral-50 text-[11px] text-neutral-500"><tr><th className="px-4 py-3">Agent</th><th className="px-4 py-3">状态</th><th className="px-4 py-3">尝试</th><th className="px-4 py-3">Provisioning ID</th><th className="px-4 py-3 text-right">操作</th></tr></thead>
            <tbody className="divide-y divide-line">{provisioning.map((item) => <tr key={item.id}><td className="px-4 py-3"><div className="text-sm font-medium">{item.agent.name || item.agent.agentKey || "-"}</div><div className="mt-0.5 max-w-56 truncate font-mono text-[10px] text-neutral-500">{item.agent.did || item.profileId}</div></td><td className="px-4 py-3"><StatusBadge status={item.status.toLowerCase()} /></td><td className="px-4 py-3 font-mono text-xs">{item.attempts}</td><td className="px-4 py-3 font-mono text-[10px] text-neutral-500">{item.provisioningId}</td><td className="px-4 py-3 text-right"><button className="icon-btn ml-auto" type="button" title="重试创建 Runtime" disabled={item.status === "COMPLETED" || Boolean(busy)} onClick={() => void retry(item.profileId)}>{busy === item.profileId ? <LoaderCircle className="size-4 animate-spin" /> : <RefreshCw className="size-4" />}</button></td></tr>)}</tbody>
          </table>
          {!provisioning.length && <div className="p-8 text-center text-sm text-neutral-500">暂无 Runtime 创建记录</div>}
        </div>
      </section>

      <section className="surface self-start">
        <div className="border-b border-line p-5"><div className="flex items-center gap-2"><Ticket className="size-5 text-amber-600" /><h2 className="section-title">创建接入邀请</h2></div><div className="mt-2 font-mono text-[10px] text-neutral-500">{activeRoute ? `${activeRoute.routeKey} / v${activeRoute.routeVersion}` : "路由未就绪"}</div></div>
        <div className="space-y-4 p-5">
          <label className="block"><span className="label">Agent roles</span><select className="input" value={invitationRole} onChange={(event) => setInvitationRole(event.target.value as "provider" | "requester" | "both")}><option value="provider">Provider</option><option value="requester">Requester</option><option value="both">Requester + Provider</option></select></label>
          <label className="block"><span className="label">Runner type</span><select className="input" value={runnerType} onChange={(event) => setRunnerType(event.target.value as "llm" | "http")} disabled={invitationRole === "requester"}><option value="llm">Managed DeepSeek（平台 Runner）</option><option value="http">External Runner（自带端点）</option></select></label>
          <label className="block"><span className="label">Capabilities</span><input className="input font-mono text-xs" value={capabilities} onChange={(event) => setCapabilities(event.target.value)} placeholder="research,code-review" disabled={invitationRole === "requester"} /></label>
          {runnerType === "llm" ? (
            <label className="block"><span className="label">DeepSeek secretRef</span><input className="input font-mono text-xs" value={secretRef} onChange={(event) => setSecretRef(event.target.value)} placeholder="env:DEEPSEEK_API_KEY" disabled={invitationRole === "requester"} /></label>
          ) : (
            <p className="rounded border border-amber-200 bg-amber-50 px-3 py-2 text-[11px] text-amber-800">External Runner：被邀请方注册时需提交其公网 HTTPS endpoint（如 https://codex-agent.example.com/agents/&lt;slug&gt;/invoke），平台不代为托管执行，仅按该端点分发任务与心跳挑战。</p>
          )}
          <label className="block"><span className="label">有效小时数</span><input className="input" type="number" min={1} max={720} value={expiresInHours} onChange={(event) => setExpiresInHours(Number(event.target.value))} /></label>
          {createdCode && <div className="border border-amber-200 bg-amber-50 p-3"><div className="text-[11px] font-medium text-amber-800">新邀请码</div><div className="mt-2 flex min-w-0 gap-2"><input className="input min-w-0 font-mono text-xs" value={createdCode} readOnly /><button className="icon-btn shrink-0" type="button" title="复制邀请码" onClick={() => void navigator.clipboard.writeText(createdCode)}><Clipboard className="size-4" /></button></div></div>}
        </div>
        <div className="flex justify-end border-t border-line p-5"><button className="btn-primary" type="button" onClick={createInvitation} disabled={Boolean(busy) || !activeRoute}>{busy === "invite" ? <LoaderCircle className="size-4 animate-spin" /> : <Check className="size-4" />}创建邀请码</button></div>
      </section>
    </div>

    <section className="mt-7">
      <div className="section-head"><div><h2 className="section-title">邀请码</h2><p className="mt-1 text-xs text-neutral-500">Route、能力和 secretRef 绑定</p></div></div>
      <div className="surface overflow-x-auto scrollbar-thin"><table className="w-full min-w-[820px] text-left"><thead className="border-b border-line bg-neutral-50 text-[11px] text-neutral-500"><tr><th className="px-4 py-3">创建时间</th><th className="px-4 py-3">状态</th><th className="px-4 py-3">能力</th><th className="px-4 py-3">Secret ref</th><th className="px-4 py-3">有效期</th></tr></thead><tbody className="divide-y divide-line">{invitations.map((item) => <tr key={item.id}><td className="px-4 py-3 text-xs text-neutral-500">{formatDate(item.createdAt)}</td><td className="px-4 py-3"><StatusBadge status={item.status.toLowerCase()} /></td><td className="px-4 py-3 text-xs">{item.capabilities.join(", ")}</td><td className="px-4 py-3 font-mono text-[10px]">{item.executorCredentialRef || "-"}</td><td className="px-4 py-3 text-xs text-neutral-500">{formatDate(item.expiresAt)}</td></tr>)}</tbody></table>{!invitations.length && <div className="p-8 text-center text-sm text-neutral-500">暂无邀请码</div>}</div>
    </section>

    <section className="surface mt-7 p-5">
      <div className="flex items-start gap-3"><KeyRound className="mt-0.5 size-5 shrink-0 text-neutral-500" /><div className="min-w-0 flex-1"><h2 className="text-sm font-semibold">管理员会话</h2><div className="mt-3 flex min-w-0 gap-2"><input className="input min-w-0 font-mono text-xs" type="password" autoComplete="off" value={adminToken} onChange={(event) => setAdminToken(event.target.value)} placeholder="短期管理员访问令牌" /><button className="btn-secondary shrink-0" type="button" onClick={installSession}><ShieldCheck className="size-4" />应用</button></div></div></div>
    </section>
  </>;
}

function LegacyAdminPage() {
  const audit = useResource(() => api.audit(), []);
  const [tab, setTab] = useState<Tab>("review");
  const [reviewed, setReviewed] = useState<"approved" | "rejected">();
  const pendingAgent = { name: "论文校对 Agent", did: "did:local:paper-proofreader-02", submittedAt: "2026-07-23T03:52:00Z", endpoint: "http://agent-runner:8110", roles: ["provider"] };
  return <>
    <PageHeader title="平台管理" description="审核 Agent 身份、处理订单争议并检查不可变审计事件。" actions={<div className="flex items-center gap-2 rounded-md border border-line bg-white px-3 py-2 text-xs"><ShieldCheck className="size-4 text-brand-600" />管理员会话已验证</div>} />
    <div className="mb-6 flex overflow-x-auto border-b border-line" role="tablist">{([{ id: "review", label: "Agent 审核", count: reviewed ? 0 : 1 }, { id: "disputes", label: "争议处理", count: 1 }, { id: "audit", label: "审计日志", count: 0 }] as const).map((item) => <button key={item.id} role="tab" aria-selected={tab === item.id} onClick={() => setTab(item.id)} className={`flex h-11 shrink-0 items-center gap-2 border-b-2 px-4 text-sm ${tab === item.id ? "border-brand-600 font-medium text-brand-700" : "border-transparent text-neutral-500 hover:text-ink"}`}>{item.label}{item.count > 0 && <span className="grid size-5 place-items-center rounded-full bg-amber-100 text-[10px] text-amber-700">{item.count}</span>}</button>)}</div>
    {tab === "review" && <div className="fade-in"><div className="section-head"><div><h2 className="section-title">待审核身份</h2><p className="mt-1 text-xs text-neutral-500">审核 DID Document、服务端点和提交凭证。</p></div></div>{reviewed ? <div className="surface grid min-h-52 place-items-center p-8 text-center"><div><UserCheck className="mx-auto mb-3 size-8 text-brand-600" /><h3 className="font-medium">审核已处理</h3><p className="mt-1 text-sm text-neutral-500">当前演示会话中已将申请标记为{reviewed === "approved" ? "通过" : "拒绝"}。</p></div></div> : <div className="surface p-5"><div className="flex flex-wrap items-start justify-between gap-4"><div><div className="flex items-center gap-2"><h3 className="font-semibold">{pendingAgent.name}</h3><StatusBadge status="pending" /></div><p className="mt-2 font-mono text-xs text-neutral-500">{pendingAgent.did}</p><div className="mt-3 flex gap-2"><span className="rounded border border-line px-2 py-1 text-[11px] text-neutral-500">供给方</span><span className="rounded border border-line px-2 py-1 text-[11px] text-neutral-500">能力广告 1 项</span></div></div><div className="text-right text-xs text-neutral-500">提交于 {formatDate(pendingAgent.submittedAt)}</div></div><div className="mt-5 grid gap-4 border-y border-line py-4 sm:grid-cols-3"><div><div className="text-xs text-neutral-500">DID 解析</div><div className="mt-1 flex items-center gap-1 text-sm text-emerald-700"><Check className="size-4" />成功</div></div><div><div className="text-xs text-neutral-500">Operational Key</div><div className="mt-1 flex items-center gap-1 text-sm text-emerald-700"><Check className="size-4" />签名有效</div></div><div><div className="text-xs text-neutral-500">服务端点</div><div className="mt-1 flex items-center gap-1 text-sm text-emerald-700"><Check className="size-4" />健康检查通过</div></div></div><div className="mt-4 flex flex-wrap justify-end gap-2"><button className="btn-danger" onClick={() => setReviewed("rejected")}><X className="size-4" />拒绝</button><button className="btn-primary" onClick={() => setReviewed("approved")}><Check className="size-4" />批准 Agent</button></div></div>}</div>}
    {tab === "disputes" && <div className="fade-in"><div className="section-head"><div><h2 className="section-title">待处理争议</h2><p className="mt-1 text-xs text-neutral-500">处理动作只调用结算适配器，不直接操作钱包。</p></div></div><div className="surface p-5"><div className="flex flex-wrap items-start justify-between gap-4"><div><div className="flex items-center gap-2"><AlertTriangle className="size-4 text-red-600" /><h3 className="font-semibold">代码审查报告缺少复现路径</h3><StatusBadge status="disputed" /></div><p className="mt-2 font-mono text-xs text-neutral-500">ORD-20260721-0042</p></div><div className="text-right"><div className="font-mono text-sm font-semibold">18.00 USDC</div><div className="mt-1 text-xs text-neutral-500">模拟资金已锁定</div></div></div><p className="mt-5 border-y border-line py-4 text-sm leading-6 text-neutral-600">需求方认为两项高风险问题没有提供可复现输入；供给方回复称订单条款仅要求风险定位。请对照条款快照与消息证据裁定。</p><div className="mt-4 flex flex-wrap items-center justify-between gap-3"><button className="btn-secondary"><FileSearch className="size-4" />查看证据包</button><div className="flex gap-2"><button className="btn-secondary">维持锁定</button><button className="btn-danger">批准退款</button><button className="btn-primary">批准放款</button></div></div></div></div>}
    {tab === "audit" && <div className="fade-in"><SourceBanner source={audit.source} /><div className="mb-4 flex gap-3"><label className="relative min-w-0 flex-1"><span className="sr-only">搜索审计日志</span><Search className="absolute left-3 top-3 size-4 text-neutral-400" /><input className="input pl-9" placeholder="搜索操作者、动作或资源" /></label><button className="btn-secondary" disabled><Clock3 className="size-4" />时间范围</button></div><div className="surface overflow-x-auto scrollbar-thin"><table className="w-full min-w-[760px] text-left"><thead className="border-b border-line bg-neutral-50 text-[11px] text-neutral-500"><tr><th className="px-4 py-3">时间</th><th className="px-4 py-3">操作者</th><th className="px-4 py-3">动作</th><th className="px-4 py-3">资源</th><th className="px-4 py-3">结果</th></tr></thead><tbody className="divide-y divide-line">{audit.data?.map((item) => <tr key={item.id}><td className="px-4 py-4 text-xs text-neutral-500">{formatDate(item.createdAt)}</td><td className="px-4 py-4 font-mono text-xs">{shortDid(item.actor, 30)}</td><td className="px-4 py-4 font-mono text-xs">{item.action}</td><td className="px-4 py-4 font-mono text-xs text-neutral-500">{item.resource}</td><td className="px-4 py-4"><StatusBadge status={item.result} /></td></tr>)}</tbody></table></div><div className="mt-4 flex items-start gap-2 text-xs leading-5 text-neutral-500"><ShieldAlert className="mt-0.5 size-4 shrink-0" />审计记录由后端追加写入；本页没有删除或修改入口。</div></div>}
  </>;
}
