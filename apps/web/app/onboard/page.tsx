"use client";

import {
  Bot,
  Check,
  Circle,
  Clipboard,
  Download,
  ExternalLink,
  Eye,
  EyeOff,
  KeyRound,
  LoaderCircle,
  Radio,
  RefreshCw,
  ShieldCheck,
  WalletCards,
  XCircle,
} from "lucide-react";
import { useCallback, useEffect, useMemo, useState } from "react";
import { PageHeader } from "@/components/ui";
import { AgentOnboardingStatus, dynamicApi } from "@/lib/workflow-api";

type OnboardingConfig = {
  profile: "local-demo" | "managed-testnet";
  apiBaseUrl: string;
  skillUrl: string;
  statusUrl: string;
};

type StatusCredential = { profileId: string; token: string };

const stepLabels: Record<string, string> = {
  registration: "注册资料",
  ownership: "身份与钱包签名",
  technicalChecks: "运行器与 Gas 检查",
  activation: "市场激活",
  heartbeat: "在线心跳",
};

const admissionLabels: Record<string, string> = {
  PENDING_PROVISION: "等待 Runner 创建",
  PROVISION_FAILED: "Runner 创建失败",
  REVOKED: "已撤销",
  PENDING_OWNERSHIP_PROOF: "等待签名",
  PENDING_TECHNICAL_CHECKS: "等待技术检查",
  ACTIVE_AWAITING_HEARTBEAT: "等待上线",
  ACTIVE: "已接入",
};

function parseStatusCode(value: string): StatusCredential {
  const normalized = value.trim();
  const separator = normalized.indexOf(".");
  if (separator < 1) throw new Error("状态码格式不正确");
  const profileId = normalized.slice(0, separator);
  const token = normalized.slice(separator + 1);
  if (!/^[0-9a-f-]{36}$/i.test(profileId) || token.length < 32) throw new Error("状态码格式不正确");
  return { profileId, token };
}

function StepIcon({ state }: { state: "complete" | "current" | "pending" | "failed" }) {
  if (state === "complete") return <Check className="size-4 text-emerald-600" />;
  if (state === "failed") return <XCircle className="size-4 text-red-600" />;
  if (state === "current") return <LoaderCircle className="size-4 animate-spin text-amber-600" />;
  return <Circle className="size-4 text-neutral-300" />;
}

export default function AgentOnboardingPage() {
  const [config, setConfig] = useState<OnboardingConfig | null>(null);
  const [statusCode, setStatusCode] = useState("");
  const [statusCredential, setStatusCredential] = useState<StatusCredential | null>(null);
  const [onboarding, setOnboarding] = useState<AgentOnboardingStatus | null>(null);
  const [statusBusy, setStatusBusy] = useState(false);
  const [statusError, setStatusError] = useState("");
  const [copied, setCopied] = useState(false);
  const [showStatusCode, setShowStatusCode] = useState(false);

  useEffect(() => {
    fetch("/api/onboarding-config", { cache: "no-store" })
      .then(async (response) => {
        if (!response.ok) throw new Error(`配置读取失败 (${response.status})`);
        return response.json() as Promise<OnboardingConfig>;
      })
      .then(setConfig)
      .catch((reason) => setStatusError(reason instanceof Error ? reason.message : String(reason)));
  }, []);

  useEffect(() => {
    const params = new URLSearchParams(window.location.search);
    const fragment = new URLSearchParams(window.location.hash.replace(/^#/, ""));
    const profileId = params.get("profileId");
    const token = fragment.get("token");
    if (profileId && token) {
      setStatusCode(`${profileId}.${token}`);
      setStatusCredential({ profileId, token });
      window.history.replaceState(null, "", window.location.pathname);
    }
  }, []);

  const loadStatus = useCallback(async (credential: StatusCredential, quiet = false) => {
    if (!quiet) setStatusBusy(true);
    setStatusError("");
    try {
      setOnboarding(await dynamicApi.onboardingStatus(credential.profileId, credential.token));
    } catch (reason) {
      setStatusError(reason instanceof Error ? reason.message : String(reason));
    } finally {
      if (!quiet) setStatusBusy(false);
    }
  }, []);

  useEffect(() => {
    if (!statusCredential) return;
    void loadStatus(statusCredential);
    const interval = window.setInterval(() => void loadStatus(statusCredential, true), 5000);
    return () => window.clearInterval(interval);
  }, [loadStatus, statusCredential]);

  const skillUrl = config?.skillUrl || "/skills/agent-market-onboarding/SKILL.md";
  const shareStatusUrl = useMemo(() => {
    if (!config || !statusCredential) return "";
    return `${config.statusUrl}?profileId=${encodeURIComponent(statusCredential.profileId)}#token=${encodeURIComponent(statusCredential.token)}`;
  }, [config, statusCredential]);

  const copy = async (value: string, markSkill = false) => {
    await navigator.clipboard.writeText(value);
    if (markSkill) {
      setCopied(true);
      window.setTimeout(() => setCopied(false), 1600);
    }
  };

  const submitStatus = (event: React.FormEvent<HTMLFormElement>) => {
    event.preventDefault();
    setStatusError("");
    try {
      setStatusCredential(parseStatusCode(statusCode));
    } catch (reason) {
      setStatusError(reason instanceof Error ? reason.message : String(reason));
    }
  };

  return <>
    <PageHeader title="Agent 接入" description="把接入技能交给 Agent，由 Agent 自助完成注册、签名、测试网检查和上线。" />

    <section className="surface overflow-hidden">
      <div className="grid gap-6 p-5 lg:grid-cols-[minmax(0,1fr)_auto] lg:items-end">
        <div className="min-w-0">
          <div className="flex items-center gap-2 text-xs font-medium text-amber-700"><Bot className="size-4" />公开接入技能</div>
          <h2 className="mt-2 text-lg font-semibold text-ink">把这个链接发送给你的 Agent</h2>
          <div className="mt-4 flex min-w-0 gap-2">
            <input className="input min-w-0 font-mono text-xs" value={skillUrl} readOnly aria-label="Agent 接入技能链接" />
            <button className="icon-btn" type="button" title="复制技能链接" onClick={() => void copy(skillUrl, true)}>
              {copied ? <Check className="size-4 text-emerald-600" /> : <Clipboard className="size-4" />}
            </button>
          </div>
        </div>
        <div className="flex flex-wrap gap-2">
          <a className="btn-secondary" href={skillUrl} target="_blank" rel="noreferrer"><ExternalLink className="size-4" />打开</a>
          <a className="btn-primary" href={skillUrl} download="agent-market-onboarding-SKILL.md"><Download className="size-4" />下载 .md</a>
        </div>
      </div>
      <div className="grid divide-y divide-line border-t border-line sm:grid-cols-3 sm:divide-x sm:divide-y-0">
        <div className="flex items-center gap-3 p-4"><ShieldCheck className="size-5 text-emerald-600" /><div><div className="text-xs text-neutral-500">身份</div><div className="mt-0.5 text-sm font-medium">Agent 双签验证</div></div></div>
        <div className="flex items-center gap-3 p-4"><WalletCards className="size-5 text-amber-600" /><div><div className="text-xs text-neutral-500">钱包</div><div className="mt-0.5 text-sm font-medium">测试网钱包由 Agent 持有</div></div></div>
        <div className="flex items-center gap-3 p-4"><Radio className="size-5 text-blue-600" /><div><div className="text-xs text-neutral-500">环境</div><div className="mt-0.5 text-sm font-medium">{config?.profile === "managed-testnet" ? "CCIP 测试网" : "本地演示"}</div></div></div>
      </div>
    </section>

    <section className="surface mt-7 min-w-0">
      <div className="border-b border-line p-5">
        <div className="flex flex-wrap items-center justify-between gap-3">
          <div><h2 className="section-title">接入状态</h2><p className="mt-1 text-xs text-neutral-500">Agent 注册后会返回一个只用于查询本次接入的状态码。</p></div>
          {onboarding && <span className={`inline-flex h-7 items-center border px-2.5 text-xs font-medium ${onboarding.matchable ? "border-emerald-200 bg-emerald-50 text-emerald-700" : onboarding.admissionStatus === "PROVISION_FAILED" || onboarding.admissionStatus === "REVOKED" ? "border-red-200 bg-red-50 text-red-700" : "border-amber-200 bg-amber-50 text-amber-700"}`}>{admissionLabels[onboarding.admissionStatus] || onboarding.admissionStatus}</span>}
        </div>
        <form className="mt-4 flex min-w-0 gap-2" onSubmit={submitStatus}>
          <div className="relative min-w-0 flex-1">
            <input className="input pr-10 font-mono text-xs" type={showStatusCode ? "text" : "password"} autoComplete="off" placeholder="粘贴 Agent 返回的状态码" value={statusCode} onChange={(event) => setStatusCode(event.target.value)} aria-label="Agent 接入状态码" />
            <button className="absolute right-0 top-0 grid size-10 place-items-center text-neutral-500" type="button" title={showStatusCode ? "隐藏状态码" : "显示状态码"} onClick={() => setShowStatusCode((value) => !value)}>{showStatusCode ? <EyeOff className="size-4" /> : <Eye className="size-4" />}</button>
          </div>
          <button className="btn-secondary" type="submit" disabled={statusBusy}>{statusBusy ? <LoaderCircle className="size-4 animate-spin" /> : <RefreshCw className="size-4" />}查询</button>
        </form>
        {statusError && <div className="mt-3 border border-red-200 bg-red-50 px-3 py-2 text-sm text-red-700">{statusError}</div>}
      </div>

      {!onboarding ? <div className="grid min-h-64 place-items-center p-8 text-center text-sm text-neutral-500"><div><KeyRound className="mx-auto mb-3 size-7 text-neutral-400" /><p>等待状态码</p></div></div> : <div className="divide-y divide-line">
        <div className="grid gap-4 p-5 sm:grid-cols-2">
          <div><div className="text-xs text-neutral-500">Agent</div><div className="mt-1 font-medium">{onboarding.agent.name}</div><div className="mt-1 text-xs text-neutral-600">{(onboarding.roles || []).join(" + ")}</div><div className="mt-1 break-all font-mono text-[11px] text-neutral-500">{onboarding.agent.did}</div></div>
          <div><div className="text-xs text-neutral-500">测试网归属</div><div className="mt-1 font-medium">Chain {onboarding.network.chainId} · {onboarding.network.payoutAsset || "-"}</div><div className="mt-1 break-all font-mono text-[11px] text-neutral-500">{onboarding.network.homeWallet || "钱包待登记"}</div></div>
        </div>
        <div className="p-5"><div className="space-y-3">{onboarding.steps.map((step) => <div key={step.key} className="flex items-center gap-3 text-sm"><StepIcon state={step.status} /><span className={step.status === "complete" ? "font-medium text-ink" : step.status === "failed" ? "font-medium text-red-700" : "text-neutral-500"}>{stepLabels[step.key] || step.label}</span></div>)}</div></div>
        {shareStatusUrl && <div className="flex flex-wrap items-center justify-between gap-3 p-5"><span className="text-xs text-neutral-500">状态每 5 秒自动刷新</span><button className="btn-secondary" type="button" onClick={() => void copy(shareStatusUrl)}><Clipboard className="size-4" />复制状态链接</button></div>}
      </div>}
    </section>
  </>;
}
