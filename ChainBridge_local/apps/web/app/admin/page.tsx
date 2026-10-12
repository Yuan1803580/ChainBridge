"use client";

import { Check, LoaderCircle, RefreshCw, Search, ShieldAlert, ShieldCheck, X } from "lucide-react";
import { useEffect, useMemo, useState } from "react";

import { PageHeader, SourceBanner, StatusBadge } from "@/components/ui";
import { api } from "@/lib/api";
import { formatDate, shortDid } from "@/lib/format";
import { dynamicApi, type RawAgentProfile } from "@/lib/workflow-api";
import type { AuditEntry } from "@/lib/types";

type Tab = "review" | "audit";
type RecordValue = Record<string, unknown>;

const asRecord = (value: unknown): RecordValue => value && typeof value === "object" && !Array.isArray(value) ? value as RecordValue : {};
const yesNo = (value: unknown) => value === true ? "通过" : "待检查";

export default function AdminPage() {
  const [tab, setTab] = useState<Tab>("review");
  const [filter, setFilter] = useState("PENDING_PLATFORM_REVIEW");
  const [items, setItems] = useState<RawAgentProfile[]>([]);
  const [runtimeTokens, setRuntimeTokens] = useState<Record<string, string>>({});
  const [notes, setNotes] = useState<Record<string, string>>({});
  const [busy, setBusy] = useState("");
  const [error, setError] = useState("");
  const [sessionReady, setSessionReady] = useState(false);
  const [auditItems, setAuditItems] = useState<AuditEntry[]>([]);
  const [auditSource, setAuditSource] = useState<"api" | "mock">("api");

  const load = async (status = filter) => {
    const response = await dynamicApi.onboardingRegistrations(status ? { admissionStatus: status } : {});
    setItems(response.items);
  };

  useEffect(() => {
    void (async () => {
      try {
        await api.auth.devLogin("did:local:platform-admin");
      } catch {
        // Public deployments use the administrator session already present in the browser.
      }
      try {
        await load("PENDING_PLATFORM_REVIEW");
        const auditResult = await api.audit();
        setAuditItems(auditResult.data);
        setAuditSource(auditResult.source);
        setSessionReady(true);
      } catch (reason) {
        setError(reason instanceof Error ? reason.message : String(reason));
      }
    })();
  }, []);

  const run = async (key: string, action: () => Promise<void>) => {
    setBusy(key);
    setError("");
    try {
      await action();
      await load();
    } catch (reason) {
      setError(reason instanceof Error ? reason.message : String(reason));
    } finally {
      setBusy("");
    }
  };

  const pendingCount = useMemo(
    () => items.filter((item) => item.admissionStatus === "PENDING_PLATFORM_REVIEW").length,
    [items],
  );

  const changeFilter = (value: string) => {
    setFilter(value);
    setBusy("filter");
    setError("");
    void load(value).catch((reason) => setError(reason instanceof Error ? reason.message : String(reason))).finally(() => setBusy(""));
  };

  return <>
    <PageHeader
      title="平台管理"
      description="审核外部 Agent 身份、运行服务和归属链状态。"
      actions={<div className="flex items-center gap-2 border border-line bg-white px-3 py-2 text-xs"><ShieldCheck className="size-4 text-brand-600" />{sessionReady ? "管理员会话已验证" : "管理员会话未验证"}</div>}
    />

    <div className="mb-6 flex overflow-x-auto border-b border-line" role="tablist">
      <button role="tab" aria-selected={tab === "review"} onClick={() => setTab("review")} className={`flex h-11 items-center gap-2 border-b-2 px-4 text-sm ${tab === "review" ? "border-brand-600 font-medium text-brand-700" : "border-transparent text-neutral-500"}`}>Agent 审核{pendingCount > 0 && <span className="grid size-5 place-items-center rounded-full bg-amber-100 text-[10px] text-amber-700">{pendingCount}</span>}</button>
      <button role="tab" aria-selected={tab === "audit"} onClick={() => setTab("audit")} className={`flex h-11 items-center gap-2 border-b-2 px-4 text-sm ${tab === "audit" ? "border-brand-600 font-medium text-brand-700" : "border-transparent text-neutral-500"}`}>审计日志</button>
    </div>

    {error && <div className="mb-5 border border-red-200 bg-red-50 px-4 py-3 text-sm text-red-700">{error}</div>}

    {tab === "review" && <div className="fade-in">
      <div className="section-head">
        <div><h2 className="section-title">Agent 准入队列</h2><p className="mt-1 text-xs text-neutral-500">身份所有权由申请人证明，运行检查和最终准入由管理员执行。</p></div>
        <div className="flex gap-2">
          <select className="select" value={filter} onChange={(event) => changeFilter(event.target.value)}>
            <option value="PENDING_PLATFORM_REVIEW">待平台审核</option>
            <option value="PENDING_OWNERSHIP">待所有权证明</option>
            <option value="APPROVED">已批准</option>
            <option value="REJECTED">已拒绝</option>
            <option value="">全部</option>
          </select>
          <button className="btn-secondary" onClick={() => void run("reload", () => load())} disabled={Boolean(busy)} title="刷新审核队列">{busy === "reload" ? <LoaderCircle className="size-4 animate-spin" /> : <RefreshCw className="size-4" />}</button>
        </div>
      </div>

      <div className="space-y-4">
        {!busy && items.length === 0 && <div className="surface p-8 text-center text-sm text-neutral-500">当前筛选条件下没有 Agent 申请。</div>}
        {items.map((profile) => {
          const metadata = asRecord(profile.metadata);
          const verification = asRecord(metadata.verification);
          const onboarding = asRecord(metadata.onboarding);
          const canReview = profile.admissionStatus === "PENDING_PLATFORM_REVIEW";
          const token = runtimeTokens[profile.id] || "";
          const reviewNote = notes[profile.id] || "";
          return <section className="surface p-5" key={profile.id}>
            <div className="flex flex-wrap items-start justify-between gap-4">
              <div className="min-w-0">
                <div className="flex flex-wrap items-center gap-2"><h3 className="font-semibold">{profile.agent.displayName}</h3><span className="border border-line px-2 py-1 text-[11px] text-neutral-500">{profile.agentType}</span><StatusBadge status={profile.admissionStatus.toLowerCase()} /><span className="border border-line px-2 py-1 text-[11px] text-neutral-500">{profile.agentType === "COORDINATOR" ? "NO EXECUTION CHAIN" : profile.chainStatus}</span>{profile.matchable && <span className="border border-emerald-200 bg-emerald-50 px-2 py-1 text-[11px] text-emerald-700">MATCHABLE</span>}</div>
                <p className="mt-2 break-all font-mono text-xs text-neutral-500">{profile.agent.did}</p>
                <p className="mt-1 break-all font-mono text-xs text-neutral-500">{profile.agent.endpoint}</p>
              </div>
              <div className="text-right text-xs text-neutral-500">提交于 {formatDate(String(onboarding.submittedAt || profile.createdAt || ""))}<div className="mt-1 font-mono">chainId {profile.chainId}</div></div>
            </div>

            <div className="mt-5 grid gap-4 border-y border-line py-4 sm:grid-cols-4">
              <div><div className="text-xs text-neutral-500">身份签名</div><div className={verification.identityVerified ? "mt-1 text-sm text-emerald-700" : "mt-1 text-sm text-amber-700"}>{yesNo(verification.identityVerified)}</div></div>
              <div><div className="text-xs text-neutral-500">钱包签名</div><div className={verification.walletVerified ? "mt-1 text-sm text-emerald-700" : "mt-1 text-sm text-amber-700"}>{profile.agentType === "COORDINATOR" ? "不适用" : yesNo(verification.walletVerified)}</div></div>
              <div><div className="text-xs text-neutral-500">Runner 检查</div><div className={verification.testTaskPassed ? "mt-1 text-sm text-emerald-700" : "mt-1 text-sm text-amber-700"}>{yesNo(verification.testTaskPassed)}</div></div>
              <div><div className="text-xs text-neutral-500">Gas</div><div className={profile.gasReady ? "mt-1 text-sm text-emerald-700" : "mt-1 text-sm text-neutral-500"}>{profile.agentType === "COORDINATOR" ? "不适用" : profile.gasReady ? "READY" : profile.chainStatus === "PENDING_ROUTE" ? "等待路线" : "待充值"}</div></div>
            </div>

            {canReview && <div className="mt-4 grid gap-3 lg:grid-cols-[minmax(220px,1fr)_minmax(260px,1fr)_auto]">
              <input className="input font-mono" type="password" autoComplete="off" placeholder="Runner 访问令牌" value={token} onChange={(event) => setRuntimeTokens((current) => ({ ...current, [profile.id]: event.target.value }))} />
              <input className="input" placeholder="审核备注或拒绝原因" value={reviewNote} onChange={(event) => setNotes((current) => ({ ...current, [profile.id]: event.target.value }))} />
              <div className="flex flex-wrap gap-2">
                <button className="btn-secondary" disabled={Boolean(busy) || token.length < 16} onClick={() => void run(`check:${profile.id}`, async () => { await dynamicApi.checkOnboarding(profile.id, token); })}>{busy === `check:${profile.id}` ? <LoaderCircle className="size-4 animate-spin" /> : <Search className="size-4" />}运行检查</button>
                <button className="btn-primary" disabled={Boolean(busy) || verification.testTaskPassed !== true} onClick={() => void run(`approve:${profile.id}`, async () => { await dynamicApi.activateOnboarding(profile.id, reviewNote); })}>{busy === `approve:${profile.id}` ? <LoaderCircle className="size-4 animate-spin" /> : <Check className="size-4" />}批准</button>
                <button className="btn-danger" disabled={Boolean(busy) || reviewNote.trim().length < 3} onClick={() => void run(`reject:${profile.id}`, async () => { await dynamicApi.rejectOnboarding(profile.id, reviewNote); })}>{busy === `reject:${profile.id}` ? <LoaderCircle className="size-4 animate-spin" /> : <X className="size-4" />}拒绝</button>
              </div>
            </div>}
          </section>;
        })}
      </div>
    </div>}

    {tab === "audit" && <div className="fade-in">
      <SourceBanner source={auditSource} />
      <div className="surface overflow-x-auto scrollbar-thin"><table className="w-full min-w-[760px] text-left"><thead className="border-b border-line bg-neutral-50 text-[11px] text-neutral-500"><tr><th className="px-4 py-3">时间</th><th className="px-4 py-3">操作者</th><th className="px-4 py-3">动作</th><th className="px-4 py-3">资源</th><th className="px-4 py-3">结果</th></tr></thead><tbody className="divide-y divide-line">{auditItems.map((item) => <tr key={item.id}><td className="px-4 py-4 text-xs text-neutral-500">{formatDate(item.createdAt)}</td><td className="px-4 py-4 font-mono text-xs">{shortDid(item.actor, 30)}</td><td className="px-4 py-4 font-mono text-xs">{item.action}</td><td className="px-4 py-4 font-mono text-xs text-neutral-500">{item.resource}</td><td className="px-4 py-4"><StatusBadge status={item.result} /></td></tr>)}</tbody></table></div>
      <div className="mt-4 flex items-start gap-2 text-xs leading-5 text-neutral-500"><ShieldAlert className="mt-0.5 size-4 shrink-0" />审核检查、批准和拒绝均写入后端审计记录。</div>
    </div>}
  </>;
}
