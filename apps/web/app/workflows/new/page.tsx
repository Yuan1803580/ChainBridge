"use client";

import {
  ArrowRight,
  Bot,
  CheckCircle2,
  Coins,
  FileText,
  Fuel,
  LoaderCircle,
  LockKeyhole,
  Network,
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
  dynamicApi,
  type RawWorkflow,
  type WorkflowFundingConfig,
  type WorkflowPlan,
} from "@/lib/workflow-api";
import {
  approveWorkflowBudget,
  connectWorkflowWallet,
  depositWorkflowSourceGas,
  getWorkflowWalletState,
  lockWorkflowBudget,
  mintLocalWorkflowToken,
  readWorkflowWalletBalances,
  switchWorkflowChain,
  type WorkflowWalletBalances,
  type WorkflowWalletState,
} from "@/lib/workflow-funding";

type FundingMode = "RESERVED" | "ONCHAIN";

const compact = (value?: string, left = 8, right = 6) => value
  ? `${value.slice(0, left)}...${value.slice(-right)}`
  : "-";

const LOGIN_HINT = "请先在页面右上角点击『控制台登录』，使用控制台 DID 与密钥完成登录后再创建工作流。未登录时后端会返回 Authentication required (401)。";

export default function NewWorkflowPage() {
  const [objective, setObjective] = useState("汇总四份跨链协议材料，比较消息验证、代币转移与失败补偿机制，并审查 Escrow 状态机风险。");
  const [budget, setBudget] = useState("20.00");
  const [fundingMode, setFundingMode] = useState<FundingMode>("RESERVED");
  const [sourceGasAmount, setSourceGasAmount] = useState("0.01");
  const [stage, setStage] = useState<"input" | "plan" | "funded">("input");
  const [createdId, setCreatedId] = useState("");
  const [createdReference, setCreatedReference] = useState("");
  const [plan, setPlan] = useState<WorkflowPlan | null>(null);
  const [fundingConfig, setFundingConfig] = useState<WorkflowFundingConfig | null>(null);
  const [wallet, setWallet] = useState<WorkflowWalletState | null>(null);
  const [balances, setBalances] = useState<WorkflowWalletBalances | null>(null);
  const [pendingWorkflow, setPendingWorkflow] = useState<RawWorkflow | null>(null);
  const [approvalTxHash, setApprovalTxHash] = useState("");
  const [lockTxHash, setLockTxHash] = useState("");
  const [sourceGasTxHash, setSourceGasTxHash] = useState("");
  const [fundingStep, setFundingStep] = useState("");
  const [configLoading, setConfigLoading] = useState(false);
  const [walletBusy, setWalletBusy] = useState(false);
  const [submitting, setSubmitting] = useState(false);
  const [error, setError] = useState("");
  const [loggedIn, setLoggedIn] = useState(false);

  useEffect(() => {
    const sync = () => setLoggedIn(Boolean(typeof window !== "undefined" && window.localStorage.getItem("agentMarketToken")));
    sync();
    window.addEventListener("agent-market-auth-changed", sync);
    window.addEventListener("storage", sync);
    return () => {
      window.removeEventListener("agent-market-auth-changed", sync);
      window.removeEventListener("storage", sync);
    };
  }, []);

  const refreshBalances = async (config: WorkflowFundingConfig, state: WorkflowWalletState) => {
    if (!state.ready || !state.account) {
      setBalances(null);
      return;
    }
    setBalances(await readWorkflowWalletBalances(config, state.account));
  };

  useEffect(() => {
    if (fundingMode !== "ONCHAIN") return;
    let active = true;
    setConfigLoading(true);
    setError("");
    dynamicApi.workflowFundingConfig()
      .then(async (config) => {
        if (!active) return;
        setFundingConfig(config);
        setSourceGasAmount(config.defaultSourceGasAmount);
        const state = await getWorkflowWalletState(config);
        if (!active) return;
        setWallet(state);
        await refreshBalances(config, state);
      })
      .catch((reason) => {
        if (active) setError(reason instanceof Error ? reason.message : String(reason));
      })
      .finally(() => {
        if (active) setConfigLoading(false);
      });
    return () => { active = false; };
  }, [fundingMode]);

  const updateWallet = async (action: (config: WorkflowFundingConfig) => Promise<WorkflowWalletState>) => {
    if (!fundingConfig) return;
    setWalletBusy(true);
    setError("");
    try {
      const state = await action(fundingConfig);
      setWallet(state);
      await refreshBalances(fundingConfig, state);
    } catch (reason) {
      setError(reason instanceof Error ? reason.message : String(reason));
    } finally {
      setWalletBusy(false);
    }
  };

  const mintTestToken = async () => {
    if (!fundingConfig || !wallet?.account || !wallet.ready) return;
    setWalletBusy(true);
    setError("");
    try {
      await mintLocalWorkflowToken(fundingConfig, wallet.account, Number(budget).toFixed(2));
      await refreshBalances(fundingConfig, wallet);
    } catch (reason) {
      setError(reason instanceof Error ? reason.message : String(reason));
    } finally {
      setWalletBusy(false);
    }
  };

  const submit = async (event: FormEvent) => {
    event.preventDefault();
    if (!loggedIn) { setError(LOGIN_HINT); return; }
    if (!objective.trim() || Number(budget) <= 0) return;
    setSubmitting(true);
    setError("");
    try {
      const value = await dynamicApi.planWorkflow({
        objective,
        budget: Number(budget).toFixed(2),
        platformFee: "1.10",
        inputPayload: { source: "visual-console" },
      });
      setPlan(value);
      setStage("plan");
    } catch (reason) {
      setError(reason instanceof Error ? reason.message : String(reason));
    } finally {
      setSubmitting(false);
    }
  };

  const fundReserved = async () => {
    const created = await dynamicApi.createWorkflow({
      title: "本地 V1 动态多链任务",
      objective,
      budget: Number(budget).toFixed(2),
      platformFee: "1.10",
      inputPayload: { source: "visual-console" },
      fundingMode: "RESERVED",
    });
    await dynamicApi.coordinate(created.id, 1);
    setCreatedId(created.id);
    setCreatedReference(created.referenceCode);
    setStage("funded");
  };

  const fundOnchain = async () => {
    if (!fundingConfig) throw new Error("链上资金配置尚未加载完成。");
    let currentWallet = wallet;
    if (!currentWallet?.ready || !currentWallet.account) {
      currentWallet = await connectWorkflowWallet(fundingConfig);
      setWallet(currentWallet);
    }
    if (!currentWallet.ready || !currentWallet.account) {
      throw new Error(currentWallet.message || `请将钱包切换到链 ${fundingConfig.chainId}。`);
    }
    const account = currentWallet.account;
    let created = pendingWorkflow;
    if (!created) {
      setFundingStep("正在创建待锁款工作流");
      created = await dynamicApi.createWorkflow({
        title: "本地 V1 动态多链任务",
        objective,
        budget: Number(budget).toFixed(2),
        platformFee: "1.10",
        inputPayload: { source: "visual-console" },
        fundingMode: "ONCHAIN",
        payerAddress: account,
      });
      setPendingWorkflow(created);
      setCreatedId(created.id);
      setCreatedReference(created.referenceCode);
    }

    let confirmedLockHash = lockTxHash;
    if (!confirmedLockHash) {
      setFundingStep("等待钱包确认 Token 授权");
      const approvalHash = await approveWorkflowBudget(fundingConfig, created, account);
      setApprovalTxHash(approvalHash);
      setFundingStep("等待钱包确认 TaskEscrow 锁款");
      confirmedLockHash = await lockWorkflowBudget(fundingConfig, created, account);
      setLockTxHash(confirmedLockHash);
    }

    let confirmedGasHash = sourceGasTxHash;
    if (Number(sourceGasAmount) > 0 && !confirmedGasHash) {
      setFundingStep("等待钱包确认源链 Gas 存入");
      confirmedGasHash = await depositWorkflowSourceGas(
        fundingConfig,
        created,
        account,
        sourceGasAmount,
      ) || "";
      setSourceGasTxHash(confirmedGasHash);
    }

    setFundingStep("后端正在核验链上交易回执");
    await dynamicApi.confirmWorkflowFunding(created.id, {
      walletAddress: account,
      lockTxHash: confirmedLockHash,
      sourceGasTxHash: confirmedGasHash || undefined,
      sourceGasAmount,
    });
    setFundingStep("链上资金已确认");
    await dynamicApi.coordinate(created.id, 1).catch(() => undefined);
    await refreshBalances(fundingConfig, currentWallet);
    setStage("funded");
  };

  const fund = async () => {
    if (!loggedIn) { setError(LOGIN_HINT); return; }
    setSubmitting(true);
    setError("");
    try {
      if (fundingMode === "ONCHAIN") await fundOnchain();
      else await fundReserved();
    } catch (reason) {
      setError(reason instanceof Error ? reason.message : String(reason));
    } finally {
      setSubmitting(false);
    }
  };

  const walletAction = wallet?.account && wallet.chainId !== fundingConfig?.chainId
    ? () => updateWallet(switchWorkflowChain)
    : () => updateWallet(connectWorkflowWallet);

  return <>
    <Breadcrumbs items={[{ label: "工作流", href: "/workflows" }, { label: "创建任务" }]} />
    <div className="mb-6">
      <h1 className="page-title">创建动态工作流</h1>
      <p className="page-subtitle">用户提交任务并确认最高预算；拆分、匹配、路由接入、失败重试和结算由受约束的协调 Agent 完成。</p>
    </div>
    <ProofBoundaryNotice />

    {!loggedIn && <div className="mb-4 flex items-start gap-2 border border-amber-200 bg-amber-50 px-4 py-3 text-sm text-amber-800" style={{ borderRadius: 6 }}><LockKeyhole className="mt-0.5 size-4 shrink-0" />{LOGIN_HINT}</div>}

    <div className="mt-6 grid gap-8 xl:grid-cols-[minmax(0,1.4fr)_minmax(320px,0.6fr)]">
      <section className="min-w-0">
        {stage === "input" && <form onSubmit={submit} className="surface">
          <div className="border-b border-line px-5 py-4">
            <div className="flex items-center gap-2">
              <span className="grid size-7 place-items-center rounded-md bg-brand-50 text-brand-700"><FileText className="size-4" /></span>
              <h2 className="text-sm font-semibold">任务与预算</h2>
            </div>
          </div>
          <div className="space-y-5 p-5">
            <label>
              <span className="label">任务内容</span>
              <textarea className="textarea min-h-40" value={objective} onChange={(event) => setObjective(event.target.value)} placeholder="描述需要完成的最终目标、输入材料和期望结果" required />
              <span className="mt-1.5 block text-[11px] text-neutral-500">协调 Agent 将固化 criteriaHash，创建后不能自行修改验收条件。</span>
            </label>
            <label>
              <span className="label">最高预算</span>
              <div className="relative max-w-sm">
                <input className="input pr-28 font-mono" type="number" min="1.10" step="0.01" value={budget} onChange={(event) => setBudget(event.target.value)} required />
                <span className="absolute right-3 top-2.5 text-xs font-medium text-neutral-500">{fundingMode === "ONCHAIN" ? fundingConfig?.assetLabel || "Token" : "USDC 模型"}</span>
              </div>
              <span className="mt-1.5 block text-[11px] text-neutral-500">平台费上限为 1.10 USDC，节点报酬与审核预算不能超过最高预算。</span>
            </label>

            <div>
              <span className="label">资金接入模式</span>
              <div className="inline-flex max-w-full border border-line bg-neutral-50 p-1" style={{ borderRadius: 6 }} role="group" aria-label="资金接入模式">
                <button type="button" aria-pressed={fundingMode === "RESERVED"} onClick={() => setFundingMode("RESERVED")} className={`px-3 py-1.5 text-xs font-semibold ${fundingMode === "RESERVED" ? "bg-white text-ink shadow-sm" : "text-neutral-500"}`} style={{ borderRadius: 4 }}>模型预留</button>
                <button type="button" aria-pressed={fundingMode === "ONCHAIN"} onClick={() => setFundingMode("ONCHAIN")} className={`px-3 py-1.5 text-xs font-semibold ${fundingMode === "ONCHAIN" ? "bg-white text-ink shadow-sm" : "text-neutral-500"}`} style={{ borderRadius: 4 }}>链上资金</button>
              </div>
            </div>

            {fundingMode === "ONCHAIN" && <div className="border-y border-line bg-neutral-50 px-4 py-4">
              <div className="flex flex-wrap items-start justify-between gap-3">
                <div>
                  <div className="flex items-center gap-2 text-xs font-semibold"><WalletCards className="size-4" />付款钱包</div>
                  <div className="mt-1 font-mono text-[10px] text-neutral-500">{configLoading ? "正在读取协议部署..." : wallet?.account ? compact(wallet.account, 10, 8) : wallet?.message || "等待连接"}</div>
                </div>
                <button type="button" className="btn-secondary h-9" onClick={walletAction} disabled={walletBusy || configLoading || !fundingConfig}>
                  {walletBusy ? <LoaderCircle className="size-4 animate-spin" /> : wallet?.account && wallet.chainId !== fundingConfig?.chainId ? <Network className="size-4" /> : <WalletCards className="size-4" />}
                  {walletBusy ? "钱包处理中" : wallet?.account && wallet.chainId !== fundingConfig?.chainId ? `切换到 ${fundingConfig?.chainId}` : wallet?.ready ? "刷新钱包" : "连接钱包"}
                </button>
              </div>
              {fundingConfig && <div className="mt-4 grid gap-3 text-[11px] sm:grid-cols-2">
                <div><div className="text-neutral-500">网络</div><div className="mt-1 font-mono font-semibold">{fundingConfig.chainName} · {fundingConfig.chainId}</div></div>
                <div><div className="text-neutral-500">账户状态</div><div className={`mt-1 font-semibold ${wallet?.ready ? "text-emerald-700" : "text-amber-700"}`}>{wallet?.message || "未连接"}</div></div>
                <div><div className="text-neutral-500">Token 余额</div><div className="mt-1 font-mono font-semibold">{balances ? `${balances.tokenBalanceDisplay} ${balances.tokenSymbol}` : "-"}</div></div>
                <div><div className="text-neutral-500">Gas 余额</div><div className="mt-1 font-mono font-semibold">{balances ? `${balances.nativeBalanceDisplay} ETH` : "-"}</div></div>
              </div>}
              {fundingConfig?.localTestnet && wallet?.ready && <div className="mt-4 flex flex-wrap items-center justify-between gap-3 border-t border-line pt-3">
                <span className="text-[11px] text-neutral-500">本地测试 Token · {compact(fundingConfig.contracts.sourceToken)}</span>
                <button type="button" className="btn-secondary h-8" onClick={mintTestToken} disabled={walletBusy || Number(budget) <= 0}><Coins className="size-3.5" />领取 {Number(budget || 0).toFixed(2)} 测试币</button>
              </div>}
              <label className="mt-4 block max-w-sm">
                <span className="label">源链 Gas 存入</span>
                <div className="relative"><input className="input pr-16 font-mono" type="number" min="0" step="0.001" value={sourceGasAmount} onChange={(event) => setSourceGasAmount(event.target.value)} required /><span className="absolute right-3 top-2.5 text-xs text-neutral-500">ETH</span></div>
              </label>
            </div>}
            {error && <div className="border border-red-200 bg-red-50 px-3 py-2 text-xs text-red-700" style={{ borderRadius: 6 }}>{error}</div>}
          </div>
          <div className="flex flex-wrap items-center justify-between gap-3 border-t border-line bg-neutral-50 px-5 py-4">
            <span className="flex items-center gap-2 text-[11px] text-neutral-500"><ShieldCheck className="size-3.5" />{fundingMode === "ONCHAIN" ? "锁款后由 API 独立核验交易回执" : "模型预留不会标记为真实链上锁款"}</span>
            <button className="btn-primary" disabled={submitting || (fundingMode === "ONCHAIN" && configLoading)} type="submit"><Sparkles className="size-4" />{submitting ? "Coordinator 规划中..." : "生成执行计划"}</button>
          </div>
        </form>}

        {stage === "plan" && <div className="surface">
          <div className="flex flex-wrap items-start justify-between gap-3 border-b border-line px-5 py-4">
            <div>
              <div className="flex items-center gap-2"><span className="grid size-7 place-items-center rounded-md bg-violet-50 text-violet-700"><Bot className="size-4" /></span><h2 className="text-sm font-semibold">协调 Agent 计划预览</h2></div>
              <p className="mt-2 text-xs text-neutral-500">节点、Top-K Agent 和 homeChain 路由均来自 Coordinator API。</p>
            </div>
            <span className="max-w-56 truncate font-mono text-[10px] text-neutral-500" title={plan?.decisionHash}>{plan?.plannerVersion} · {plan?.decisionHash}</span>
          </div>
          <div className="divide-y divide-line">
            {(plan?.nodes || []).map((node) => {
              const primary = node.candidates[0];
              return <div key={node.nodeKey} className="p-5">
                <div className="grid gap-4 md:grid-cols-[44px_minmax(0,1fr)_minmax(220px,0.55fr)] md:items-start">
                  <span className="grid size-10 place-items-center rounded-md bg-neutral-100 font-mono text-xs font-bold">{node.nodeKey}</span>
                  <div className="min-w-0">
                    <div className="flex flex-wrap items-center gap-2"><h3 className="font-semibold">{node.title}</h3>{node.dependsOn.length > 0 && <span className="text-[10px] text-neutral-500">依赖 {node.dependsOn.join(", ")} SETTLED</span>}</div>
                    <p className="mt-1 text-xs text-neutral-500">{node.capability}</p>
                    <div className="mt-3 flex items-start gap-2 border-l-2 border-brand-500 pl-3"><Bot className="mt-0.5 size-4 shrink-0 text-brand-700" /><div className="min-w-0"><div className="text-xs font-semibold">{primary?.agentKey || "等待信息平台候选"}</div><div className="mt-1 text-[11px] leading-4 text-neutral-500">{primary ? `排名 #${primary.rank} · 得分 ${primary.score} · 候补 ${Math.max(0, node.candidates.length - 1)} 个` : "节点会进入 WAITING_FOR_AGENT"}</div></div></div>
                  </div>
                  <div className="border-t border-line pt-3 md:border-l md:border-t-0 md:pl-4 md:pt-0">
                    <div className="flex items-center justify-between text-[11px]"><span className="text-neutral-500">homeChain</span><b>{primary ? primary.homeChainId : "-"}</b></div>
                    <div className="mt-2 flex items-center justify-between gap-3 text-[11px]"><span className="text-neutral-500">部署状态</span><b className="truncate font-mono text-[10px]">{primary?.deploymentStatus || "WAITING"}</b></div>
                    <div className="mt-2 min-w-0 text-[10px] text-neutral-500"><div>homeWallet</div><div className="truncate font-mono" title={primary?.homeWallet}>{primary?.homeWallet || "未配置"}</div></div>
                    <div className="mt-2 flex items-center justify-between text-[11px]"><span className="text-neutral-500">节点预算</span><b className="font-mono">{node.reward}</b></div>
                  </div>
                </div>
              </div>;
            })}
          </div>

          {fundingMode === "ONCHAIN" && fundingConfig && <div className="border-t border-line bg-neutral-50 px-5 py-4">
            <div className="flex items-center gap-2 text-xs font-semibold"><LockKeyhole className="size-4" />链上交易确认</div>
            <div className="mt-3 grid gap-x-6 gap-y-3 text-[11px] sm:grid-cols-2">
              <div><div className="text-neutral-500">付款账户</div><div className="mt-1 break-all font-mono font-semibold">{wallet?.account || "未连接"}</div></div>
              <div><div className="text-neutral-500">网络</div><div className="mt-1 font-mono font-semibold">{fundingConfig.chainName} · {fundingConfig.chainId}</div></div>
              <div><div className="text-neutral-500">授权与锁款</div><div className="mt-1 font-mono font-semibold">{Number(budget).toFixed(2)} {balances?.tokenSymbol || fundingConfig.assetLabel}</div></div>
              <div><div className="text-neutral-500">平台费上限</div><div className="mt-1 font-mono font-semibold">1.10 {balances?.tokenSymbol || fundingConfig.assetLabel}</div></div>
              <div><div className="text-neutral-500">TaskEscrow</div><div className="mt-1 break-all font-mono">{fundingConfig.contracts.taskEscrow}</div></div>
              <div><div className="text-neutral-500">Token 合约</div><div className="mt-1 break-all font-mono">{fundingConfig.contracts.sourceToken}</div></div>
              <div><div className="text-neutral-500">源链 Gas</div><div className="mt-1 font-mono font-semibold">{sourceGasAmount} ETH</div></div>
              <div><div className="text-neutral-500">GasTreasury</div><div className="mt-1 break-all font-mono">{fundingConfig.contracts.gasTreasury}</div></div>
            </div>
            {fundingConfig.localTestnet && <div className="mt-4 border border-amber-200 bg-amber-50 px-3 py-2 text-[11px] leading-5 text-amber-800" style={{ borderRadius: 6 }}>交易会真实写入本地 Chain A，但资产是 MockUSDC；这不是公网上的真实 USDC 或生产资金。</div>}
            {(fundingStep || approvalTxHash || lockTxHash || sourceGasTxHash) && <div className="mt-4 border-t border-line pt-3 text-[10px]">
              {fundingStep && <div className="mb-2 flex items-center gap-2 font-semibold text-brand-700">{submitting && <LoaderCircle className="size-3.5 animate-spin" />}{fundingStep}</div>}
              <div className="grid gap-1 font-mono text-neutral-500">
                {pendingWorkflow && <span>Workflow {pendingWorkflow.referenceCode}</span>}
                {approvalTxHash && <span title={approvalTxHash}>Approve tx {compact(approvalTxHash, 12, 10)}</span>}
                {lockTxHash && <span title={lockTxHash}>Lock tx {compact(lockTxHash, 12, 10)}</span>}
                {sourceGasTxHash && <span title={sourceGasTxHash}>Gas tx {compact(sourceGasTxHash, 12, 10)}</span>}
              </div>
            </div>}
          </div>}

          {error && <div className="border-t border-red-200 bg-red-50 px-5 py-3 text-xs text-red-700">创建失败：{error}{pendingWorkflow && <Link className="ml-2 font-semibold underline" href={`/workflows/${pendingWorkflow.id}`}>查看待锁款工作流</Link>}</div>}
          <div className="flex flex-wrap items-center justify-between gap-3 border-t border-line bg-neutral-50 px-5 py-4">
            <button className="btn-secondary" disabled={Boolean(pendingWorkflow)} onClick={() => setStage("input")}>返回修改</button>
            <button className="btn-primary" disabled={submitting || !plan || (fundingMode === "ONCHAIN" && (!fundingConfig || !wallet?.ready))} onClick={fund}>
              {submitting ? <LoaderCircle className="size-4 animate-spin" /> : <LockKeyhole className="size-4" />}
              {submitting ? fundingStep || "正在创建..." : fundingMode === "ONCHAIN" ? lockTxHash ? "继续核验链上资金" : `授权并锁定 ${Number(budget).toFixed(2)}` : `确认模型预留 ${Number(budget).toFixed(2)} USDC`}
            </button>
          </div>
        </div>}

        {stage === "funded" && <div className="surface overflow-hidden">
          <div className="grid min-h-80 place-items-center px-6 py-10 text-center">
            <div className="max-w-lg">
              <span className="mx-auto grid size-12 place-items-center rounded-md bg-emerald-50 text-emerald-700"><CheckCircle2 className="size-6" /></span>
              <h2 className="mt-4 text-lg font-semibold">{fundingMode === "ONCHAIN" ? "链上预算已确认" : "模型预算已预留"}</h2>
              <p className="mt-2 text-sm leading-6 text-neutral-500">{fundingMode === "ONCHAIN" ? "TaskEscrow 已接收预算，API 已核验付款账户、合约、方法、金额、条款哈希和事件回执。" : "Coordinator 已创建节点并派发首个可执行节点。该状态用于本地模型，不代表用户钱包已经锁款。"}</p>
              <div className="mt-5 grid grid-cols-2 divide-x divide-line border-y border-line py-4">
                <div><div className="text-[10px] text-neutral-500">Workflow</div><div className="mt-1 font-mono text-sm font-semibold">{createdReference}</div></div>
                <div><div className="text-[10px] text-neutral-500">{fundingMode === "ONCHAIN" ? "锁定预算" : "预算模型"}</div><div className="mt-1 font-mono text-sm font-semibold">{Number(budget).toFixed(2)} {fundingMode === "ONCHAIN" ? balances?.tokenSymbol || fundingConfig?.assetLabel : "USDC"}</div></div>
              </div>
              {fundingMode === "ONCHAIN" && lockTxHash && <div className="mt-4 break-all font-mono text-[10px] text-neutral-500">Lock tx · {lockTxHash}</div>}
              <div className="mt-6 flex flex-wrap justify-center gap-2"><Link href={`/workflows/${createdId}`} className="btn-secondary"><Route className="size-4" />查看工作流</Link><Link href="/workflows" className="btn-primary">进入工作流列表<ArrowRight className="size-4" /></Link></div>
            </div>
          </div>
        </div>}
      </section>

      <aside>
        <div className="section-head"><div><h2 className="section-title">自动执行边界</h2><p className="mt-1 text-xs text-neutral-500">合约约束优先于协调策略</p></div></div>
        <div className="surface divide-y divide-line">
          {[
            [WalletCards, fundingMode === "ONCHAIN" ? "资金回执可验证" : "模型资金预留", fundingMode === "ONCHAIN" ? "仅在 TaskEscrow 事件、付款账户、Token、金额和条款全部匹配后确认。" : "只记录预算责任和结算上限，不声明用户钱包已锁款。"],
            [Bot, "先选 Agent，再选链", "由能力、信誉、价格和 SLA 匹配，再读取 homeChainId。"],
            [Route, "按 homeChain 动态部署", "匹配 Agent 后生成 routeVersion 部署任务并固化合约地址快照。"],
            [ShieldCheck, "PASS 后才结算", "失败 attempt 关闭预留并重新匹配，不改变 nodeId。"],
            [Coins, "Gas 责任可核对", "失败 Agent 自付执行与结果回传，系统故障不错误归责。"],
          ].map(([Icon, title, detail]) => <div key={String(title)} className="flex gap-3 p-4"><span className="grid size-8 shrink-0 place-items-center rounded-md bg-neutral-100 text-neutral-600"><Icon className="size-4" /></span><div><div className="text-xs font-semibold">{String(title)}</div><p className="mt-1 text-[11px] leading-5 text-neutral-500">{String(detail)}</p></div></div>)}
        </div>
        <div className={`mt-4 border px-3 py-2 text-[11px] leading-5 ${fundingMode === "ONCHAIN" ? "border-emerald-200 bg-emerald-50 text-emerald-800" : "border-amber-200 bg-amber-50 text-amber-800"}`} style={{ borderRadius: 6 }}>
          <span className="flex items-start gap-2">{fundingMode === "ONCHAIN" ? <Fuel className="mt-0.5 size-3.5 shrink-0" /> : <RefreshCw className="mt-0.5 size-3.5 shrink-0" />}{fundingMode === "ONCHAIN" ? `链上模式 · ${fundingConfig?.chainName || "正在读取 Chain A"}` : "MODEL_RESERVED · 非真实锁款"}</span>
        </div>
      </aside>
    </div>
  </>;
}
