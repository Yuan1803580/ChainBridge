"use client";

import {
  Activity,
  ArrowLeft,
  Bot,
  CheckCircle2,
  Clock3,
  Coins,
  Cpu,
  Fingerprint,
  Gauge,
  KeyRound,
  MessageSquareText,
  Radio,
  RefreshCw,
  Route,
  ShieldCheck,
  WalletCards,
} from "lucide-react";
import Link from "next/link";
import { useParams } from "next/navigation";
import { useCallback, useEffect, useState } from "react";
import { formatEther } from "viem";

import { HealthDot, InlineMetric } from "@/components/platform-ui";
import { Breadcrumbs, DataState, Tags } from "@/components/ui";
import {
  dynamicApi,
  type AgentAccountSnapshot,
  type RawAgentProfile,
  type RawRoute,
} from "@/lib/workflow-api";

type AgentMetadata = {
  onboarding?: {
    organization?: string;
    status?: string;
    submittedAt?: string;
  };
  llm?: {
    provider?: string;
    model?: string;
  };
  chainResolution?: {
    requestedPayoutAsset?: string;
    matchedRouteKey?: string;
    matchedRouteVersion?: number;
  };
  verification?: {
    identityVerified?: boolean;
    walletVerified?: boolean;
    manifestCheckPassed?: boolean;
    healthCheckPassed?: boolean;
    testTaskPassed?: boolean;
    gasReadyVerified?: boolean;
    gasBalanceWei?: string;
    gasMinimumBalanceWei?: string;
    gasCheckedAt?: string;
    checkedAt?: string;
    manifestHash?: string;
    testResultHash?: string;
    manifest?: {
      conversation?: {
        enabled?: boolean;
      };
    };
  };
  reputation?: {
    policyVersion?: string;
    sampleCount?: number;
    successfulAttempts?: number;
    failedAttempts?: number;
    historicalAttemptsExcluded?: number;
  };
};

const chainLabel = (chainId: number) => `Chain ${String.fromCharCode(65 + chainId - 31337)}`;

function shortHash(value?: string) {
  if (!value) return "-";
  return value.length > 24 ? `${value.slice(0, 14)}...${value.slice(-8)}` : value;
}

function nativeBalance(balanceWei?: string) {
  if (!balanceWei) return "0";
  try {
    return formatEther(BigInt(balanceWei));
  } catch {
    return "0";
  }
}

export default function AgentDetailPage() {
  const params = useParams<{ id: string }>();
  const [profile, setProfile] = useState<RawAgentProfile | null>(null);
  const [route, setRoute] = useState<RawRoute | null>(null);
  const [account, setAccount] = useState<AgentAccountSnapshot | null>(null);
  const [accountError, setAccountError] = useState("");
  const [loading, setLoading] = useState(true);
  const [refreshing, setRefreshing] = useState(false);
  const [syncing, setSyncing] = useState(false);
  const [checking, setChecking] = useState(false);
  const [syncMessage, setSyncMessage] = useState("");
  const [error, setError] = useState("");

  const load = useCallback(async (silent = false) => {
    if (!silent) {
      setLoading(true);
      setError("");
    }
    try {
      const [agentResponse, routeResponse] = await Promise.all([
        dynamicApi.agents(),
        dynamicApi.routes(),
      ]);
      const selected = agentResponse.items.find((item) =>
        item.id === params.id || item.agentId === params.id || item.agent.id === params.id,
      );
      if (!selected) throw new Error("平台中不存在这个 Agent Profile");
      const selectedRoute = routeResponse.items
        .filter((item) => item.destinationChainId === selected.chainId && item.status === "ACTIVE")
        .sort((left, right) => right.routeVersion - left.routeVersion)[0] || null;
      setProfile(selected);
      setRoute(selectedRoute);
      if (selected.agentType === "EXECUTOR") {
        try {
          setAccount(await dynamicApi.agentAccount(selected.id));
          setAccountError("");
        } catch (reason) {
          setAccount(null);
          setAccountError(reason instanceof Error ? reason.message : String(reason));
        }
      } else {
        setAccount(null);
        setAccountError("");
      }
    } catch (reason) {
      if (!silent) {
        setProfile(null);
        setRoute(null);
        setAccount(null);
        setError(reason instanceof Error ? reason.message : String(reason));
      }
    } finally {
      if (!silent) setLoading(false);
    }
  }, [params.id]);

  useEffect(() => {
    void load();
    const timer = window.setInterval(() => {
      if (document.visibilityState === "visible") void load(true);
    }, 15_000);
    return () => window.clearInterval(timer);
  }, [load]);

  const refreshNow = async () => {
    setRefreshing(true);
    await load(true);
    setRefreshing(false);
  };

  const syncCapabilities = async () => {
    if (!profile) return;
    setSyncing(true);
    setSyncMessage("");
    try {
      const result = await dynamicApi.syncAgentCapabilities(profile.id);
      setSyncMessage(`已从 Runner Manifest 同步 ${result.total} 项能力`);
      await load(true);
    } catch (reason) {
      setSyncMessage(reason instanceof Error ? reason.message : String(reason));
    } finally {
      setSyncing(false);
    }
  };

  const runTechnicalChecks = async () => {
    if (!profile) return;
    setChecking(true);
    setSyncMessage("");
    try {
      await dynamicApi.recheckOnboarding(profile.id);
      setSyncMessage("平台复检已完成，身份、钱包、Runner 和测试任务状态已更新");
      await load(true);
    } catch (reason) {
      setSyncMessage(reason instanceof Error ? reason.message : String(reason));
    } finally {
      setChecking(false);
    }
  };

  const metadata = (profile?.metadata || {}) as AgentMetadata;
  const verification = metadata.verification || {};
  const reputation = metadata.reputation || {};
  const reputationSampleCount = Number(reputation.sampleCount || 0);
  const historicalAttemptsExcluded = Number(reputation.historicalAttemptsExcluded || 0);
  const conversationEnabled = Boolean(verification.manifest?.conversation?.enabled);
  const heartbeatFresh = Boolean(
    profile?.lastHeartbeatAt
    && Date.now() - new Date(profile.lastHeartbeatAt).getTime() <= 60_000,
  );
  const matchable = Boolean(profile?.matchable);
  const isCoordinator = profile?.agentType === "COORDINATOR";

  return <>
    <Breadcrumbs items={[{ label: "Agent 信息平台", href: "/agents" }, { label: profile?.agent.displayName || "Agent 详情" }]} />
    <DataState loading={loading} error={error} onRetry={() => void load()}>
      {profile && <div className="fade-in">
        <div className="mb-6 flex flex-wrap items-start justify-between gap-4">
          <div className="flex min-w-0 items-start gap-4">
            <span className="grid size-12 shrink-0 place-items-center rounded-md bg-brand-50 text-brand-700"><Bot className="size-6" /></span>
            <div className="min-w-0">
              <div className="flex flex-wrap items-center gap-2">
                <h1 className="page-title">{profile.agent.displayName}</h1>
                <span className={`inline-flex items-center gap-1 text-xs font-medium ${matchable ? "text-emerald-700" : "text-amber-700"}`}>
                  {matchable ? <CheckCircle2 className="size-4" /> : <Clock3 className="size-4" />}
                  {matchable ? (isCoordinator ? "可用于协调" : "可参与执行匹配") : "暂不可用"}
                </span>
              </div>
              <p className="mt-1 break-all font-mono text-[11px] text-neutral-500">{profile.agent.did}</p>
              <p className="mt-2 max-w-3xl text-sm leading-6 text-neutral-600">{profile.agent.description}</p>
            </div>
          </div>
          <div className="flex max-w-full flex-wrap items-center gap-2">
            <Link href="/agents" className="icon-btn" aria-label="返回 Agent 信息平台"><ArrowLeft className="size-4" /></Link>
            <button type="button" className="icon-btn" aria-label="立即刷新 Agent 状态" title="立即刷新 Agent 状态" disabled={refreshing} onClick={() => void refreshNow()}><RefreshCw className={`size-4 ${refreshing ? "animate-spin" : ""}`} /></button>
            <button type="button" className="btn-secondary" disabled={syncing} onClick={() => void syncCapabilities()}><RefreshCw className={`size-4 ${syncing ? "animate-spin" : ""}`} />{syncing ? "同步中..." : "同步 Runner 能力"}</button>
            {conversationEnabled && <Link href={`/agents/${profile.id}/chat`} className="btn-secondary"><MessageSquareText className="size-4" />与 Agent 对话</Link>}
            <Link href="/workflows/new" className="btn-primary"><Route className="size-4" />用于新工作流</Link>
          </div>
        </div>

        {syncMessage && <div className="mb-5 border border-line bg-neutral-50 px-3 py-2 text-xs text-neutral-700" style={{ borderRadius: 6 }}>{syncMessage}</div>}

        <section className="surface grid divide-y divide-line sm:grid-cols-2 sm:divide-x sm:divide-y-0 xl:grid-cols-4">
          <InlineMetric label="综合信任分" value={`${profile.trustScore} / 100`} detail={`${reputationSampleCount > 0 ? `平滑成功率 ${(profile.successRateBps / 100).toFixed(1)}% · ${reputationSampleCount} 次计分` : "冷启动 · 暂无计分任务"}${historicalAttemptsExcluded > 0 ? ` · 已排除 ${historicalAttemptsExcluded} 条旧记录` : ""}`} icon={<Gauge className="size-3.5" />} />
          <InlineMetric label="运行状态" value={profile.availability} detail={heartbeatFresh ? "签名心跳有效" : "签名心跳已过期"} icon={<Activity className="size-3.5" />} />
          <InlineMetric label={isCoordinator ? "角色" : "链上 Gas"} value={isCoordinator ? "COORDINATOR" : profile.gasReady ? "READY" : "NOT READY"} detail={isCoordinator ? "不参与节点执行和收款" : `${nativeBalance(account?.gasBalanceWei || verification.gasBalanceWei)} ${account?.gasSymbol || "ETH"}`} icon={<Coins className="size-3.5" />} />
          <InlineMetric label={isCoordinator ? "协调能力" : "能力起步价"} value={isCoordinator ? "DAG 提案" : `${profile.priceFloor} ${profile.currency}`} detail={`priority ${profile.priority}`} icon={<Cpu className="size-3.5" />} />
        </section>

        <div className="mt-8 grid gap-8 xl:grid-cols-[minmax(0,1.25fr)_minmax(320px,0.75fr)]">
          <div className="space-y-8">
            <section>
              <div className="section-head"><div><h2 className="section-title">能力与调度条件</h2><p className="mt-1 text-xs text-neutral-500">Coordinator 使用真实 Profile 字段进行过滤和评分</p></div></div>
              <div className="surface divide-y divide-line">
                <div className="p-5"><div className="text-[11px] font-semibold text-neutral-500">已登记能力</div><div className="mt-3"><Tags values={profile.capabilities} /></div></div>
                <div className="grid gap-4 p-5 sm:grid-cols-2 lg:grid-cols-4">
                  {[
                    ["平台激活", profile.active],
                    ["凭证有效", profile.credentialStatus === "ACTIVE"],
                    ["容量可用", profile.capacityAvailable],
                    [isCoordinator ? "协调协议" : "Gas 就绪", isCoordinator || profile.gasReady],
                  ].map(([label, passed]) => <div key={String(label)}><div className="text-[10px] text-neutral-500">{label}</div><div className={`mt-1 flex items-center gap-1 text-xs font-semibold ${passed ? "text-emerald-700" : "text-red-700"}`}>{passed ? <CheckCircle2 className="size-3.5" /> : <Clock3 className="size-3.5" />}{passed ? "通过" : "未通过"}</div></div>)}
                </div>
              </div>
            </section>

            <section>
              <div className="section-head">
                <div><h2 className="section-title">身份与服务验证</h2><p className="mt-1 text-xs text-neutral-500">所有权、Runner 和测试任务由平台独立检查</p></div>
                <button type="button" className="btn-secondary" disabled={checking} onClick={() => void runTechnicalChecks()}>
                  <ShieldCheck className="size-4" />{checking ? "复检中..." : "运行完整复检"}
                </button>
              </div>
              <div className="surface divide-y divide-line">
                {[
                  ["Agent 身份签名", verification.identityVerified],
                  [isCoordinator ? "协调身份类型" : "EVM 钱包所有权", isCoordinator || verification.walletVerified],
                  ["Runner manifest", verification.manifestCheckPassed],
                  ["Runner 健康检查", verification.healthCheckPassed],
                  ["真实测试任务", verification.testTaskPassed],
                ].map(([label, passed]) => <div key={String(label)} className="flex items-center justify-between gap-4 px-5 py-3"><span className="text-xs text-neutral-600">{label}</span><span className={`inline-flex items-center gap-1 text-xs font-medium ${passed ? "text-emerald-700" : "text-red-700"}`}>{passed ? <CheckCircle2 className="size-3.5" /> : <Clock3 className="size-3.5" />}{passed ? "已验证" : "未验证"}</span></div>)}
                <div className="flex items-center justify-between gap-4 px-5 py-3"><span className="text-xs text-neutral-600">最近完整复检</span><span className="text-xs font-medium text-neutral-700">{verification.checkedAt ? new Date(verification.checkedAt).toLocaleString("zh-CN", { hour12: false }) : "尚未检查"}</span></div>
              </div>
            </section>

            <section>
              <div className="section-head"><div><h2 className="section-title">模型与运行端点</h2><p className="mt-1 text-xs text-neutral-500">API Key 不会保存或显示在平台</p></div></div>
              <div className="surface divide-y divide-line">
                <div className="grid gap-4 p-5 sm:grid-cols-2"><div><div className="text-[10px] text-neutral-500">Provider</div><div className="mt-1 text-sm font-semibold">{metadata.llm?.provider || "未登记"}</div></div><div><div className="text-[10px] text-neutral-500">Model</div><div className="mt-1 text-sm font-semibold">{metadata.llm?.model || "未登记"}</div></div></div>
                <div className="p-5"><div className="flex items-center gap-2 text-[10px] text-neutral-500"><Radio className="size-3.5" />Runner endpoint</div><div className="mt-2 break-all font-mono text-xs">{profile.agent.endpoint || "未登记"}</div></div>
                <div className="grid gap-4 p-5 sm:grid-cols-2"><div><div className="text-[10px] text-neutral-500">Profile ID</div><div className="mt-1 break-all font-mono text-[11px]">{profile.id}</div></div><div><div className="text-[10px] text-neutral-500">基础 Agent ID</div><div className="mt-1 break-all font-mono text-[11px]">{profile.agentId}</div></div></div>
              </div>
            </section>
          </div>

          <aside className="space-y-8">
            {!isCoordinator && <section>
              <div className="section-head"><div><h2 className="section-title">归属链账户</h2><p className="mt-1 text-xs text-neutral-500">匹配后钱包与路线写入 attempt 快照</p></div></div>
              <div className="surface divide-y divide-line">
                <div className="p-4"><div className="flex items-center justify-between"><div><div className="text-sm font-semibold">{chainLabel(profile.chainId)}</div><div className="mt-1 font-mono text-[10px] text-neutral-500">chainId {profile.chainId}</div></div><HealthDot status={profile.gasReady ? "healthy" : "degraded"} label={profile.gasReady ? "Gas 可用" : "Gas 不足"} /></div></div>
                <div className="p-4"><div className="flex items-center gap-2 text-[10px] text-neutral-500"><WalletCards className="size-3.5" />homeWallet</div><div className="mt-2 break-all font-mono text-xs font-semibold">{profile.homeWallet || "未配置"}</div></div>
                <div className="grid grid-cols-2 divide-x divide-line">
                  <div className="p-4">
                    <div className="text-[10px] text-neutral-500">Gas 余额</div>
                    <div className="mt-1 font-mono text-xs font-semibold">{nativeBalance(account?.gasBalanceWei ?? verification.gasBalanceWei)} {account?.gasSymbol || "ETH"}</div>
                    {account?.gasBalanceError && <div className="mt-1 text-[10px] text-red-700">当前 Gas 余额暂时无法读取</div>}
                  </div>
                  <div className="p-4">
                    <div className="text-[10px] text-neutral-500">收款资产余额</div>
                    <div className="mt-1 font-mono text-xs font-semibold">{account?.payoutBalance ?? "-"} {account?.payoutAsset || metadata.chainResolution?.requestedPayoutAsset || route?.payoutAsset || ""}</div>
                    {(account?.payoutBalanceError || accountError) && <div className="mt-1 text-[10px] text-red-700">当前收款余额暂时无法读取</div>}
                  </div>
                </div>
                <div className="p-4"><div className="flex items-center justify-between gap-3"><span className="text-[10px] text-neutral-500">最近余额检查</span><span className="text-right text-[10px]">{account?.checkedAt ? new Date(account.checkedAt).toLocaleString("zh-CN", { hour12: false }) : verification.gasCheckedAt ? new Date(verification.gasCheckedAt).toLocaleString("zh-CN", { hour12: false }) : "-"}</span></div></div>
              </div>
            </section>}

            {!isCoordinator && <section>
              <div className="section-head"><div><h2 className="section-title">动态路线</h2><p className="mt-1 text-xs text-neutral-500">当前 Agent 归属链的最高 ACTIVE 版本</p></div></div>
              <div className="surface divide-y divide-line">
                <div className="p-4"><div className="flex items-center justify-between"><span className="font-mono text-xs font-semibold">{route?.routeKey || metadata.chainResolution?.matchedRouteKey || "未配置"}</span><span className={`text-[10px] font-semibold ${route ? "text-emerald-700" : "text-red-700"}`}>{route?.status || "MISSING"}</span></div></div>
                <div className="grid grid-cols-2 divide-x divide-line"><div className="p-4"><div className="text-[10px] text-neutral-500">路线版本</div><div className="mt-1 font-mono text-xs">v{route?.routeVersion || metadata.chainResolution?.matchedRouteVersion || "-"}</div></div><div className="p-4"><div className="text-[10px] text-neutral-500">部署状态</div><div className="mt-1 font-mono text-xs">{route?.deploymentStatus || "-"}</div></div></div>
                <div className="p-4"><div className="text-[10px] text-neutral-500">结算 / 到账资产</div><div className="mt-1 font-mono text-xs">{route ? `${route.settlementAsset} -> ${route.payoutAsset}` : "-"}</div></div>
              </div>
            </section>}

            <section>
              <div className="section-head"><div><h2 className="section-title">凭证与心跳</h2><p className="mt-1 text-xs text-neutral-500">Coordinator 匹配前检查当前状态</p></div></div>
              <div className="surface divide-y divide-line">
                <div className="p-4"><div className="flex items-center gap-2 text-[10px] text-neutral-500"><KeyRound className="size-3.5" />credentialStatus</div><div className="mt-1 text-xs font-semibold">{profile.credentialStatus || "UNCONFIGURED"}</div></div>
                <div className="p-4"><div className="flex items-center gap-2 text-[10px] text-neutral-500"><Fingerprint className="size-3.5" />credentialFingerprint</div><div className="mt-1 font-mono text-[10px]" title={profile.credentialFingerprint}>{shortHash(profile.credentialFingerprint)}</div></div>
                <div className="p-4"><div className="flex items-center gap-2 text-[10px] text-neutral-500"><ShieldCheck className="size-3.5" />manifest / result</div><div className="mt-2 space-y-1 font-mono text-[10px]"><div>{shortHash(verification.manifestHash)}</div><div>{shortHash(verification.testResultHash)}</div></div></div>
                <div className="p-4"><div className="flex items-center gap-2 text-[10px] text-neutral-500"><Clock3 className="size-3.5" />最后签名心跳</div><div className="mt-1 text-xs font-medium">{profile.lastHeartbeatAt ? new Date(profile.lastHeartbeatAt).toLocaleString("zh-CN", { hour12: false }) : "尚未收到"}</div></div>
              </div>
            </section>
          </aside>
        </div>
      </div>}
    </DataState>
  </>;
}
