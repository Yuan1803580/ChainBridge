"use client";

import {
  ArrowRight,
  Bot,
  CheckCircle2,
  Coins,
  FileText,
  HardDrive,
  KeyRound,
  LockKeyhole,
  RefreshCw,
  Route,
  ShieldCheck,
  Sparkles,
  WalletCards,
} from "lucide-react";
import Link from "next/link";
import { FormEvent, useEffect, useState } from "react";
import { ProofBoundaryNotice } from "@/components/platform-ui";
import { Breadcrumbs } from "@/components/ui";
import {
  coordinatorApi,
  dynamicApi,
  requesterApi,
  type CoordinatorConversation,
  type RawAgentProfile,
  type RawWorkflow,
  type RequesterWallet,
  type WorkflowPlan,
} from "@/lib/workflow-api";
import {
  approveWorkflowBudget,
  connectWorkflowWallet,
  depositWorkflowGas,
  lockWorkflowBudget,
} from "@/lib/workflow-contracts";

type FundingMode = "requester" | "browser";

function errorMessage(reason: unknown): string {
  if (reason instanceof Error) return reason.message;
  if (reason && typeof reason === "object") {
    const value = reason as Record<string, unknown>;
    for (const key of ["shortMessage", "message", "detail"]) {
      if (typeof value[key] === "string") return value[key];
    }
    try {
      return JSON.stringify(reason);
    } catch {
      return "未知错误";
    }
  }
  return String(reason);
}

export default function NewWorkflowPage() {
  const [objective, setObjective] = useState("检索并比较多链 Agent 工作流失败恢复相关论文，核验关键结论并输出带来源的研究摘要。");
  const [budget, setBudget] = useState("20.00");
  const [fundingMode, setFundingMode] = useState<FundingMode>("requester");
  const [stage, setStage] = useState<"input" | "plan" | "funded">("input");
  const [createdId, setCreatedId] = useState("");
  const [createdReference, setCreatedReference] = useState("");
  const [plan, setPlan] = useState<WorkflowPlan | null>(null);
  const [conversation, setConversation] = useState<CoordinatorConversation | null>(null);
  const [pendingWorkflow, setPendingWorkflow] = useState<RawWorkflow | null>(null);
  const [walletAddress, setWalletAddress] = useState("");
  const [requesterWallet, setRequesterWallet] = useState<RequesterWallet | null>(null);
  const [requesterLoading, setRequesterLoading] = useState(true);
  const [fundingProgress, setFundingProgress] = useState("");
  const [fundingEvidence, setFundingEvidence] = useState<Record<string, string>>({});
  const [runStarted, setRunStarted] = useState(false);
  const [refinement, setRefinement] = useState("");
  const [submitting, setSubmitting] = useState(false);
  const [error, setError] = useState("");
  const [coordinators, setCoordinators] = useState<RawAgentProfile[]>([]);
  const [selectedCoordinatorId, setSelectedCoordinatorId] = useState("");
  const [coordinatorsLoading, setCoordinatorsLoading] = useState(true);

  const refreshRequesterWallet = async () => {
    setRequesterLoading(true);
    try {
      setRequesterWallet(await requesterApi.wallet());
    } catch (reason) {
      setRequesterWallet(null);
      if (fundingMode === "requester") setError(errorMessage(reason));
    } finally {
      setRequesterLoading(false);
    }
  };

  useEffect(() => {
    void refreshRequesterWallet();
    // The selected mode only controls whether an unavailable Requester is surfaced as a page error.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  useEffect(() => {
    let disposed = false;
    dynamicApi.agents("COORDINATOR")
      .then((response) => {
        if (disposed) return;
        const available = response.items.filter((item) => item.matchable);
        setCoordinators(available);
        setSelectedCoordinatorId((current) => (
          available.some((item) => item.id === current) ? current : available[0]?.id || ""
        ));
      })
      .catch((reason) => {
        if (!disposed) setError(errorMessage(reason));
      })
      .finally(() => {
        if (!disposed) setCoordinatorsLoading(false);
      });
    return () => { disposed = true; };
  }, []);

  const returnToInput = () => {
    if (pendingWorkflow || fundingEvidence.lock) return;
    setPendingWorkflow(null);
    setConversation(null);
    setPlan(null);
    setCreatedId("");
    setCreatedReference("");
    setWalletAddress("");
    setFundingProgress("");
    setFundingEvidence({});
    setRunStarted(false);
    setRefinement("");
    setError("");
    setStage("input");
  };

  const refinePlan = async (event: FormEvent) => {
    event.preventDefault();
    if (!conversation || !refinement.trim() || pendingWorkflow) return;
    setSubmitting(true);
    setError("");
    try {
      const value = await coordinatorApi.refine(selectedCoordinatorId, conversation.conversationId, {
        message: refinement.trim(),
        budget: Number(budget).toFixed(2),
        platformFee: "1.10",
        currency: "USDC",
      });
      setConversation(value);
      setPlan(value.plan);
      setRefinement("");
    } catch (reason) {
      setError(errorMessage(reason));
    } finally {
      setSubmitting(false);
    }
  };

  const submit = async (event: FormEvent) => {
    event.preventDefault();
    if (!objective.trim() || Number(budget) <= 0 || !selectedCoordinatorId) return;
    setSubmitting(true);
    setError("");
    try {
      const value = await coordinatorApi.plan(selectedCoordinatorId, {
        message: objective,
        budget: Number(budget).toFixed(2),
        platformFee: "1.10",
        currency: "USDC",
        inputPayload: { source: "visual-console" },
      });
      setConversation(value);
      setPlan(value.plan);
      setStage("plan");
    } catch (reason) {
      setError(errorMessage(reason));
    } finally {
      setSubmitting(false);
    }
  };

  const rememberWorkflow = (workflow: RawWorkflow) => {
    setPendingWorkflow(workflow);
    setCreatedId(workflow.id);
    setCreatedReference(workflow.referenceCode);
  };

  const startExecution = async (conversationId: string) => {
    try {
      await requesterApi.run(selectedCoordinatorId, conversationId);
      setRunStarted(true);
    } catch (reason) {
      setError(`资金已锁定，但自动执行未启动：${errorMessage(reason)}`);
    }
  };

  const fundWithRequester = async () => {
    if (!conversation) throw new Error("Coordinator 计划不存在，请重新生成");
    setFundingProgress("本地 Requester 正在核对钱包、可信合约和 termsHash");
    const result = await requesterApi.fund(selectedCoordinatorId, conversation.conversationId);
    rememberWorkflow(result.workflow);
    setWalletAddress(result.wallet.address || "");
    setFundingEvidence({
      approval: result.funding.approveTxHash || "",
      lock: result.funding.lockTxHash || "",
      sourceGas: result.funding.sourceGasTxHash || "",
    });
    setRequesterWallet(result.wallet);
    setFundingProgress("链 A 锁款已通过 RPC 验证，正在启动 Coordinator 执行循环");
    await startExecution(conversation.conversationId);
  };

  const fundWithBrowser = async () => {
    if (!conversation) throw new Error("Coordinator 计划不存在，请重新生成");
    setFundingProgress("读取链 A 协议配置");
    const config = await dynamicApi.localProtocolConfig();
    setFundingProgress("连接浏览器钱包并切换到链 A");
    const account = await connectWorkflowWallet(config);
    setWalletAddress(account);
    const created = pendingWorkflow || (await coordinatorApi.createWorkflow(
      selectedCoordinatorId,
      conversation.conversationId,
      { fundingMode: "ONCHAIN", payerAddress: account },
    )).workflow;
    rememberWorkflow(created);
    setFundingProgress("向浏览器钱包发放本地测试 USDC 和 Gas");
    const faucet = await dynamicApi.localFaucet({
      requestId: crypto.randomUUID(),
      walletAddress: account,
      tokenAmount: Number(budget).toFixed(2),
      nativeAmount: "1",
    });
    setFundingEvidence((current) => ({
      ...current,
      faucetToken: faucet.tokenTransactionHash,
      faucetNative: faucet.nativeTransactionHash || "",
    }));
    setFundingProgress("等待钱包授权 TaskEscrow 使用测试 USDC");
    const approvalHash = await approveWorkflowBudget(config, account, created.budget);
    setFundingEvidence((current) => ({ ...current, approval: approvalHash }));
    setFundingProgress("等待钱包签署 TaskEscrow.lockBudget");
    const lockHash = await lockWorkflowBudget(config, account, created);
    setFundingEvidence((current) => ({ ...current, lock: lockHash }));
    setFundingProgress("等待钱包向 GasTreasury 存入链 A Gas");
    const sourceGasHash = await depositWorkflowGas(config, account, created);
    setFundingEvidence((current) => ({ ...current, sourceGas: sourceGasHash }));
    setFundingProgress("平台正在通过链 A RPC 验证锁款交易");
    await dynamicApi.confirmFunding(created.id, {
      walletAddress: account,
      termsHash: created.termsHash,
      approveTxHash: approvalHash,
      lockTxHash: lockHash,
      sourceGasTxHash: sourceGasHash,
      sourceGasAmount: config.sourceGasAmount,
      deploymentId: config.deploymentId,
      verificationMode: "RPC",
    });
    await dynamicApi.coordinate(created.id, 1);
    setFundingProgress("链 A 锁款已确认，正在启动 Coordinator 执行循环");
    await startExecution(conversation.conversationId);
  };

  const fund = async () => {
    setSubmitting(true);
    setError("");
    try {
      if (fundingMode === "requester") await fundWithRequester();
      else await fundWithBrowser();
      setStage("funded");
      setFundingProgress("");
    } catch (reason) {
      setError(errorMessage(reason));
    } finally {
      setSubmitting(false);
    }
  };

  const requesterReady = requesterWallet?.status === "READY" && requesterWallet.signerReady;

  return <>
    <Breadcrumbs items={[{ label: "工作流", href: "/workflows" }, { label: "创建任务" }]} />
    <div className="mb-6"><h1 className="page-title">创建动态工作流</h1><p className="page-subtitle">Coordinator Agent 生成 DAG 提案，Coordinator Core 验证预算、能力市场和执行约束。</p></div>
    <ProofBoundaryNotice />

    <div className="mt-6 grid gap-8 xl:grid-cols-[minmax(0,1.4fr)_minmax(320px,0.6fr)]">
      <section className="min-w-0">
        {stage === "input" && <form onSubmit={submit} className="surface">
          <div className="border-b border-line px-5 py-4"><div className="flex items-center gap-2"><span className="grid size-7 place-items-center rounded-md bg-brand-50 text-brand-700"><FileText className="size-4" /></span><h2 className="text-sm font-semibold">任务与预算</h2></div></div>
          <div className="space-y-5 p-5">
            <label><span className="label">Coordinator Agent</span><select className="input max-w-xl" value={selectedCoordinatorId} onChange={(event) => setSelectedCoordinatorId(event.target.value)} disabled={coordinatorsLoading || coordinators.length === 0} required><option value="">{coordinatorsLoading ? "正在读取已接入的 Coordinator..." : "请选择 Coordinator Agent"}</option>{coordinators.map((item) => <option key={item.id} value={item.id}>{item.agent.displayName} · {item.agent.did}</option>)}</select>{!coordinatorsLoading && coordinators.length === 0 && <p className="mt-2 text-xs text-amber-700">当前没有已审核、在线且有容量的 Coordinator Agent，请先在 Agent 接入页完成登记、审核和心跳。</p>}</label>
            <label><span className="label">任务内容</span><textarea className="textarea min-h-40" value={objective} onChange={(event) => setObjective(event.target.value)} placeholder="描述最终目标、输入材料和预期结果" required /></label>
            <label><span className="label">最高预算</span><div className="relative max-w-sm"><input className="input pr-28 font-mono" type="number" min="1.11" step="0.01" value={budget} onChange={(event) => setBudget(event.target.value)} required /><span className="absolute right-3 top-2.5 text-xs font-medium text-neutral-500">测试 USDC</span></div></label>
            <div>
              <span className="label">资金接入模式</span>
              <div className="inline-flex max-w-full flex-wrap border border-line bg-neutral-50 p-1" style={{ borderRadius: 6 }}>
                <button type="button" onClick={() => { setFundingMode("requester"); setError(""); }} className={`inline-flex items-center gap-2 px-3 py-1.5 text-xs ${fundingMode === "requester" ? "bg-white font-semibold shadow-sm" : "text-neutral-500"}`} style={{ borderRadius: 4 }}><HardDrive className="size-3.5" />独立 Requester 钱包</button>
                <button type="button" onClick={() => { setFundingMode("browser"); setError(""); }} className={`inline-flex items-center gap-2 px-3 py-1.5 text-xs ${fundingMode === "browser" ? "bg-white font-semibold shadow-sm" : "text-neutral-500"}`} style={{ borderRadius: 4 }}><WalletCards className="size-3.5" />MetaMask</button>
              </div>
            </div>
            {fundingMode === "requester" && <div className="border-y border-line py-3">
              <div className="flex flex-wrap items-center justify-between gap-3">
                <div className="min-w-0"><div className="flex items-center gap-2 text-xs font-semibold"><KeyRound className="size-3.5" />本地链 A 钱包</div><div className="mt-1 truncate font-mono text-[11px] text-neutral-500" title={requesterWallet?.address}>{requesterLoading ? "读取中..." : requesterWallet?.address || "服务未连接"}</div></div>
                <button type="button" className="icon-btn size-8" onClick={() => void refreshRequesterWallet()} aria-label="刷新钱包状态" title="刷新钱包状态"><RefreshCw className={`size-3.5 ${requesterLoading ? "animate-spin" : ""}`} /></button>
              </div>
              <div className="mt-3 grid grid-cols-3 gap-3 text-[11px]"><div><span className="text-neutral-500">测试 USDC</span><b className="mt-1 block font-mono">{requesterWallet?.balances?.token || "-"}</b></div><div><span className="text-neutral-500">本地 ETH</span><b className="mt-1 block font-mono">{requesterWallet?.balances?.native || "-"}</b></div><div><span className="text-neutral-500">签名状态</span><b className={`mt-1 block ${requesterReady ? "text-emerald-700" : "text-amber-700"}`}>{requesterReady ? "已就绪" : "未解锁"}</b></div></div>
              {!requesterReady && requesterWallet?.status === "READY" && <a href="http://127.0.0.1:8400/unlock" target="_blank" rel="noreferrer" className="mt-3 inline-flex items-center gap-2 text-xs font-semibold text-brand-700"><KeyRound className="size-3.5" />在本机解锁钱包</a>}
            </div>}
            {error && <div className="border border-red-200 bg-red-50 px-3 py-2 text-xs text-red-700" style={{ borderRadius: 6 }}>{error}</div>}
          </div>
          <div className="flex flex-wrap items-center justify-between gap-3 border-t border-line bg-neutral-50 px-5 py-4"><span className="flex items-center gap-2 text-[11px] text-neutral-500"><ShieldCheck className="size-3.5" />DAG、预算和 Agent 候选将在确认前完成 Core 验证</span><button className="btn-primary" disabled={submitting || !selectedCoordinatorId} type="submit"><Sparkles className="size-4" />{submitting ? "Coordinator 规划中..." : "生成执行计划"}</button></div>
        </form>}

        {stage === "plan" && <div className="surface">
          <div className="flex flex-wrap items-start justify-between gap-3 border-b border-line px-5 py-4"><div><div className="flex items-center gap-2"><span className="grid size-7 place-items-center rounded-md bg-violet-50 text-violet-700"><Bot className="size-4" /></span><h2 className="text-sm font-semibold">Coordinator Agent 计划预览</h2></div><p className="mt-2 text-xs text-neutral-500">{conversation?.coordinator?.agentKey || coordinators.find((item) => item.id === selectedCoordinatorId)?.agent.displayName} 提出 DAG，候选只包含通过 Core 检查的执行 Agent。</p></div><span className="max-w-64 truncate font-mono text-[10px] text-neutral-500" title={plan?.decisionHash}>{plan?.plannerVersion} · {plan?.decisionHash}</span></div>
          <div className="divide-y divide-line">
            {(plan?.nodes || []).map((node) => {
              const primary = node.candidates[0];
              return <div key={node.nodeKey} className="p-5">
                <div className="grid gap-4 md:grid-cols-[44px_minmax(0,1fr)_minmax(220px,0.55fr)] md:items-start">
                  <span className="grid size-10 place-items-center rounded-md bg-neutral-100 font-mono text-xs font-bold">{node.nodeKey}</span>
                  <div className="min-w-0"><div className="flex flex-wrap items-center gap-2"><h3 className="font-semibold">{node.title}</h3>{node.dependsOn.length > 0 && <span className="text-[10px] text-neutral-500">依赖 {node.dependsOn.join(", ")} SETTLED</span>}</div><p className="mt-1 text-xs text-neutral-500">{node.capability}</p><div className="mt-3 flex items-start gap-2 border-l-2 border-brand-500 pl-3"><Bot className="mt-0.5 size-4 shrink-0 text-brand-700" /><div className="min-w-0"><div className="text-xs font-semibold">{primary?.agentKey || "等待外部 Agent"}</div><div className="mt-1 text-[11px] leading-4 text-neutral-500">{primary ? `排名 #${primary.rank} · 得分 ${primary.score} · 备选 ${Math.max(0, node.candidates.length - 1)} 个` : "节点将进入 WAITING_FOR_AGENT"}</div></div></div></div>
                  <div className="border-t border-line pt-3 md:border-l md:border-t-0 md:pl-4 md:pt-0"><div className="flex items-center justify-between text-[11px]"><span className="text-neutral-500">homeChain</span><b>{primary ? primary.homeChainId : "-"}</b></div><div className="mt-2 flex items-center justify-between gap-3 text-[11px]"><span className="text-neutral-500">路由部署</span><b className="truncate font-mono text-[10px]">{primary?.deploymentStatus === "FAILED" ? "派发时重新部署" : primary?.deploymentStatus || "WAITING"}</b></div><div className="mt-2 min-w-0 text-[10px] text-neutral-500"><div>homeWallet</div><div className="truncate font-mono" title={primary?.homeWallet}>{primary?.homeWallet || "未配置"}</div></div><div className="mt-2 flex items-center justify-between text-[11px]"><span className="text-neutral-500">节点预算</span><b className="font-mono">{node.reward}</b></div></div>
                </div>
              </div>;
            })}
          </div>
          <form onSubmit={refinePlan} className="border-t border-line p-5"><label><span className="label">补充要求</span><textarea className="textarea min-h-24" value={refinement} onChange={(event) => setRefinement(event.target.value)} disabled={submitting || Boolean(pendingWorkflow)} /></label><div className="mt-3 flex justify-end"><button className="btn-secondary" type="submit" disabled={submitting || Boolean(pendingWorkflow) || !refinement.trim()}><Sparkles className="size-4" />更新计划</button></div></form>
          {fundingProgress && <div className="border-t border-blue-200 bg-blue-50 px-5 py-3 text-xs text-blue-800">{fundingProgress}</div>}
          {walletAddress && <div className="border-t border-line px-5 py-3"><div className="text-[10px] text-neutral-500">付款钱包 · {fundingMode === "requester" ? "Local Requester" : "MetaMask"}</div><div className="mt-1 break-all font-mono text-[11px]">{walletAddress}</div></div>}
          {error && <div className="border-t border-red-200 bg-red-50 px-5 py-3 text-xs text-red-700">{error}</div>}
          <div className="flex flex-wrap items-center justify-between gap-3 border-t border-line bg-neutral-50 px-5 py-4"><button className="btn-secondary" disabled={submitting || Boolean(fundingEvidence.lock)} onClick={returnToInput}>返回修改</button><button className="btn-primary" disabled={submitting || !plan || (fundingMode === "requester" && !requesterReady)} onClick={fund}><LockKeyhole className="size-4" />{submitting ? "正在签名并等待链上确认..." : `${fundingMode === "requester" ? "使用独立钱包" : "连接 MetaMask"}锁定 ${Number(budget).toFixed(2)} USDC`}</button></div>
        </div>}

        {stage === "funded" && <div className="surface overflow-hidden">
          <div className="grid min-h-80 place-items-center px-6 py-10 text-center"><div className="max-w-xl"><span className="mx-auto grid size-12 place-items-center rounded-md bg-emerald-50 text-emerald-700"><CheckCircle2 className="size-6" /></span><h2 className="mt-4 text-lg font-semibold">链 A 预算已锁定</h2><p className="mt-2 text-sm leading-6 text-neutral-500">{runStarted ? "Coordinator 已开始使用已接入的外部 Agent 执行节点，详情页会持续更新状态和最终结果。" : "锁款已经确认，可在工作流详情页查看后续执行状态。"}</p><div className="mt-5 grid grid-cols-2 divide-x divide-line border-y border-line py-4"><div><div className="text-[10px] text-neutral-500">Workflow</div><div className="mt-1 font-mono text-sm font-semibold">{createdReference}</div></div><div><div className="text-[10px] text-neutral-500">链上锁款</div><div className="mt-1 font-mono text-sm font-semibold">{Number(budget).toFixed(2)} USDC</div></div></div><div className="mt-4 space-y-1 text-left font-mono text-[10px] text-neutral-500">{Object.entries(fundingEvidence).filter(([, value]) => value).map(([key, value]) => <div key={key} className="truncate" title={value}>{key}: {value}</div>)}</div>{error && <div className="mt-4 border border-amber-200 bg-amber-50 px-3 py-2 text-left text-xs text-amber-800">{error}</div>}<div className="mt-6 flex flex-wrap justify-center gap-2"><Link href={`/workflows/${createdId}?conversationId=${conversation?.conversationId || ""}`} className="btn-primary"><Route className="size-4" />查看执行与结果</Link><Link href="/workflows" className="btn-secondary">工作流列表<ArrowRight className="size-4" /></Link></div></div></div>
        </div>}
      </section>

      <aside>
        <div className="section-head"><div><h2 className="section-title">可信执行边界</h2><p className="mt-1 text-xs text-neutral-500">智能规划与资金执行分离</p></div></div>
        <div className="surface divide-y divide-line">
          {[
            [fundingMode === "requester" ? HardDrive : WalletCards, fundingMode === "requester" ? "独立钱包签名" : "MetaMask 签名", "两种入口最终调用同一套链 A TaskEscrow 和 GasTreasury 合约。"],
            [Bot, "先选 Agent，再选链", "Core 按能力、在线状态、报价、Gas 和路由筛选已接入外部 Agent。"],
            [Route, "按 homeChain 部署", "匹配后固化 routeVersion 和合约地址快照。"],
            [ShieldCheck, "PASS 后才结算", "失败 attempt 释放预留并更换 Agent，nodeId 保持不变。"],
            [Coins, "到账后进入 SETTLED", "目标链付款确认后才解锁后续 DAG 节点。"],
          ].map(([Icon, title, detail]) => <div key={String(title)} className="flex gap-3 p-4"><span className="grid size-8 shrink-0 place-items-center rounded-md bg-neutral-100 text-neutral-600"><Icon className="size-4" /></span><div><div className="text-xs font-semibold">{String(title)}</div><p className="mt-1 text-[11px] leading-5 text-neutral-500">{String(detail)}</p></div></div>)}
        </div>
        <div className="mt-4 border border-amber-200 bg-amber-50 px-3 py-2 text-[11px] leading-5 text-amber-800" style={{ borderRadius: 6 }}>本地 Faucet 和测试资产没有现实价值；公网部署时必须关闭自动补充。</div>
      </aside>
    </div>
  </>;
}
