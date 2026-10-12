"use client";

import {
  Bot,
  Check,
  Circle,
  Coins,
  KeyRound,
  LoaderCircle,
  Network,
  Radio,
  RefreshCw,
  Send,
  ShieldCheck,
} from "lucide-react";
import { useEffect, useMemo, useState } from "react";

import { InlineMetric } from "@/components/platform-ui";
import { PageHeader } from "@/components/ui";
import {
  dynamicApi,
  type ExternalAgentOnboardingResponse,
  type RawAgentProfile,
  type RawRoute,
} from "@/lib/workflow-api";

const chainLabel = (chainId: number) => `链 ${String.fromCharCode(65 + chainId - 31337)}`;

type FormState = {
  agentType: "EXECUTOR" | "COORDINATOR";
  ownerName: string;
  ownerEmail: string;
  organization: string;
  agentName: string;
  agentKey: string;
  did: string;
  endpoint: string;
  publicKey: string;
  runtimeToken: string;
  llmProvider: string;
  llmModel: string;
  systemPrompt: string;
  capabilities: string;
  payoutAsset: string;
  preferredChainId: string;
  homeWallet: string;
  priceFloor: string;
};

type Manifest = {
  agentId: string;
  agentType?: "EXECUTOR" | "COORDINATOR";
  serviceEndpoint?: string;
  did: string;
  name: string;
  verificationMethod: { publicKeyHex: string };
  capabilities: Array<{
    capabilityId: string;
    endpoint: string;
    basePrice?: string;
    currency?: string;
  }>;
  llm?: {
    provider?: string;
    model?: string;
  };
};

type RunnerWallet = {
  agentId: string;
  address: string;
  chainId: number;
  transactionSigningEnabled: boolean;
};

type RunnerWalletSignature = {
  agentId: string;
  algorithm: "EIP-191";
  address: string;
  signature: string;
};

const initialForm: FormState = {
  agentType: "EXECUTOR",
  ownerName: "",
  ownerEmail: "",
  organization: "",
  agentName: "",
  agentKey: "",
  did: "",
  endpoint: "",
  publicKey: "",
  runtimeToken: "",
  llmProvider: "deepseek",
  llmModel: "deepseek-chat",
  systemPrompt: "",
  capabilities: "",
  payoutAsset: "BTK",
  preferredChainId: "",
  homeWallet: "",
  priceFloor: "0",
};

const endpointBase = (endpoint: string) => endpoint.trim().replace(/\/+$/, "").replace(/\/(?:invoke|chat)$/, "");

function Field({ label, children }: { label: string; children: React.ReactNode }) {
  return <label className="block min-w-0"><span className="label">{label}</span>{children}</label>;
}

export default function AgentOnboardingPage() {
  const [routes, setRoutes] = useState<RawRoute[]>([]);
  const [form, setForm] = useState<FormState>(initialForm);
  const [registration, setRegistration] = useState<ExternalAgentOnboardingResponse | null>(null);
  const [platformState, setPlatformState] = useState<RawAgentProfile | null>(null);
  const [completed, setCompleted] = useState<string[]>([]);
  const [error, setError] = useState("");
  const [busy, setBusy] = useState("");

  useEffect(() => {
    dynamicApi.routes()
      .then((response) => setRoutes(response.items.filter((item) => item.status === "ACTIVE")))
      .catch((reason) => setError(reason instanceof Error ? reason.message : String(reason)));
  }, []);

  useEffect(() => {
    if (!registration) return;
    const profileId = registration.profile.id;
    const refresh = () => dynamicApi.onboardingRegistration(profileId)
      .then((profile) => {
        setPlatformState(profile);
        if (profile.admissionStatus === "APPROVED") done("approved");
      })
      .catch(() => undefined);
    void refresh();
    const timer = window.setInterval(refresh, 5000);
    return () => window.clearInterval(timer);
  }, [registration]);

  const selectedRoute = useMemo(() => {
    const chainId = Number(form.preferredChainId);
    return routes.find((item) => form.preferredChainId && item.destinationChainId === chainId)
      || routes.find((item) => item.payoutAsset === form.payoutAsset)
      || routes[0];
  }, [form.payoutAsset, form.preferredChainId, routes]);

  const payoutAssets = useMemo(() => Array.from(new Set(routes.map((item) => item.payoutAsset))), [routes]);
  const update = (key: keyof FormState, value: string) => setForm((current) => ({ ...current, [key]: value }));
  const done = (step: string) => setCompleted((current) => current.includes(step) ? current : [...current, step]);

  const run = async (name: string, action: () => Promise<void>) => {
    setBusy(name);
    setError("");
    try { await action(); }
    catch (reason) { setError(reason instanceof Error ? reason.message : String(reason)); }
    finally { setBusy(""); }
  };

  const runnerFetch = async <T,>(path: string, init: RequestInit = {}): Promise<T> => {
    if (!form.runtimeToken) throw new Error("请先填写 Agent Runner 访问令牌");
    const response = await fetch(`${endpointBase(form.endpoint)}${path}`, {
      ...init,
      headers: {
        Authorization: `Bearer ${form.runtimeToken}`,
        "Content-Type": "application/json",
        ...(init.headers || {}),
      },
      signal: AbortSignal.timeout(15_000),
    });
    const body = await response.json().catch(() => null) as T | { detail?: unknown } | null;
    if (!response.ok) throw new Error(`Agent Runner 请求失败 (${response.status}): ${JSON.stringify((body as { detail?: unknown } | null)?.detail || body)}`);
    return body as T;
  };

  const readManifest = () => run("manifest", async () => {
    const manifest = await runnerFetch<Manifest>("/manifest", { method: "GET" });
    const capability = manifest.capabilities[0];
    if (!capability) throw new Error("Agent 清单中没有能力定义");
    const manifestType = manifest.agentType || form.agentType;
    if (manifestType !== form.agentType) throw new Error(`Agent 清单类型是 ${manifestType}，与当前选择不一致`);
    const wallet = manifestType === "EXECUTOR"
      ? await runnerFetch<RunnerWallet>("/wallet", { method: "GET" })
      : null;
    if (wallet && wallet.agentId !== manifest.agentId) throw new Error("Runner 钱包与 Agent 清单不属于同一个 Agent");
    const availableRoutes = routes.length
      ? routes
      : (await dynamicApi.routes()).items.filter((item) => item.status === "ACTIVE");
    const route = wallet
      ? availableRoutes.find((item) => item.destinationChainId === wallet.chainId)
      : undefined;
    if (!routes.length) setRoutes(availableRoutes);

    setForm((current) => ({
      ...current,
      agentName: manifest.name,
      agentKey: manifest.agentId,
      did: manifest.did,
      endpoint: manifest.serviceEndpoint || (manifestType === "COORDINATOR" ? endpointBase(current.endpoint) : capability.endpoint),
      publicKey: manifest.verificationMethod.publicKeyHex,
      capabilities: manifest.capabilities.map((item) => item.capabilityId).join(", "),
      llmProvider: manifest.llm?.provider || current.llmProvider,
      llmModel: manifest.llm?.model || current.llmModel,
      preferredChainId: wallet ? String(wallet.chainId) : "",
      payoutAsset: route?.payoutAsset || current.payoutAsset,
      homeWallet: wallet?.address || "",
      priceFloor: manifestType === "COORDINATOR" ? "0" : capability.basePrice || current.priceFloor,
    }));
    done("manifest");
    if (wallet) done("wallet");
  });

  const register = (event: React.FormEvent<HTMLFormElement>) => {
    event.preventDefault();
    return run("register", async () => {
      const response = await dynamicApi.onboardAgent({
        agentType: form.agentType,
        ownerName: form.ownerName,
        ownerEmail: form.ownerEmail,
        organization: form.organization || undefined,
        agentName: form.agentName,
        agentKey: form.agentKey,
        did: form.did || undefined,
        endpoint: form.endpoint,
        publicKey: form.publicKey,
        llmProvider: form.llmProvider,
        llmModel: form.llmModel,
        systemPrompt: form.systemPrompt,
        capabilities: form.capabilities.split(",").map((item) => item.trim()).filter(Boolean),
        preferredChainId: form.agentType === "EXECUTOR" && form.preferredChainId ? Number(form.preferredChainId) : undefined,
        payoutAsset: form.payoutAsset,
        settlementAsset: "USDC",
        homeWallet: form.agentType === "EXECUTOR" ? form.homeWallet : undefined,
        priceFloor: form.priceFloor,
        currency: "USDC",
      });
      setRegistration(response);
      done("register");
    });
  };

  const verifyOwnership = () => run("ownership", async () => {
    if (!registration) throw new Error("请先登记 Agent");
    const challenge = await dynamicApi.onboardingChallenge(registration.profile.id);
    const identity = await runnerFetch<{ did: string; signature: string }>("/identity-signatures", {
      method: "POST",
      body: JSON.stringify({ message: challenge.message }),
    });
    if (identity.did !== registration.agent.did) throw new Error("Agent Runner 返回的 DID 与登记信息不一致");
    const wallet = form.agentType === "EXECUTOR"
      ? await runnerFetch<RunnerWalletSignature>("/wallet-signatures", {
        method: "POST",
        body: JSON.stringify({ message: challenge.message }),
      })
      : null;
    if (wallet && wallet.agentId !== form.agentKey) throw new Error("Runner 钱包签名与登记的 Agent Key 不一致");
    if (wallet && wallet.address.toLowerCase() !== form.homeWallet.toLowerCase()) throw new Error("Runner 签名钱包与登记的执行钱包不一致");
    const verified = await dynamicApi.verifyOnboarding(registration.profile.id, {
      challengeId: challenge.challengeId,
      identitySignature: identity.signature,
      walletSignature: wallet?.signature,
    });
    if (verified.profile) setPlatformState(verified.profile);
    done("ownership");
  });

  const refreshReview = () => run("review", async () => {
    if (!registration) throw new Error("请先登记 Agent");
    const profile = await dynamicApi.onboardingRegistration(registration.profile.id);
    setPlatformState(profile);
    if (profile.admissionStatus === "APPROVED") done("approved");
  });

  const startHeartbeat = () => run("heartbeat", async () => {
    if (!registration || platformState?.admissionStatus !== "APPROVED") throw new Error("请等待管理员批准 Agent");
    const response = await runnerFetch<{ heartbeat: { matchable?: boolean } }>("/platform-registration", {
      method: "POST",
      body: JSON.stringify({
        platformUrl: dynamicApi.baseUrl,
        profileId: registration.profile.id,
        status: "ONLINE",
        capacityAvailable: true,
        endpointLatencyMs: 500,
        heartbeatIntervalSeconds: 30,
      }),
    });
    if (!response.heartbeat) throw new Error("平台没有返回心跳结果");
    done("heartbeat");
  });

  const steps = [
    ["manifest", "读取 Runner 清单"],
    ...(form.agentType === "EXECUTOR" ? [["wallet", "读取执行钱包"]] : []),
    ["register", "提交平台登记"],
    ["ownership", "Runner 签名验证"],
    ["approved", "平台审核通过"],
    ["heartbeat", "在线心跳"],
  ];

  return <>
    <PageHeader title="Agent 接入" description="执行 Agent 进入任务匹配；Coordinator Agent 负责用户对话和 DAG 提案，均使用独立身份与签名心跳。" />

    <section className="surface grid divide-y divide-line sm:grid-cols-2 sm:divide-x sm:divide-y-0 xl:grid-cols-4">
      <InlineMetric label="准入状态" value={platformState?.admissionStatus || registration?.admissionStatus || "未登记"} detail={platformState?.matchable ? "可匹配" : "不可匹配"} icon={<Bot className="size-3.5" />} />
      <InlineMetric label="Agent 类型" value={form.agentType === "EXECUTOR" ? "执行 Agent" : "Coordinator"} detail={form.agentType === "EXECUTOR" ? "节点执行与收款" : "对话与 DAG 提案"} icon={<Coins className="size-3.5" />} />
      <InlineMetric label="归属链" value={form.agentType === "COORDINATOR" ? "无需执行链" : form.preferredChainId ? chainLabel(Number(form.preferredChainId)) : "-"} detail={platformState?.chainStatus || registration?.chainStatus || "未解析"} icon={<Network className="size-3.5" />} />
      <InlineMetric label="心跳" value={completed.includes("heartbeat") ? "30s" : "-"} detail="Agent 主动签名上报" icon={<Radio className="size-3.5" />} />
    </section>

    {error && <div className="mt-5 border border-red-200 bg-red-50 px-4 py-3 text-sm text-red-700">{error}</div>}

    <div className="mt-7 grid gap-7 xl:grid-cols-[minmax(0,1fr)_360px]">
      <form onSubmit={register} className="surface divide-y divide-line">
        <section className="p-5">
          <span className="label">接入类型</span>
          <div className="inline-flex border border-line bg-neutral-50 p-1" style={{ borderRadius: 6 }}>
            {(["EXECUTOR", "COORDINATOR"] as const).map((type) => <button key={type} type="button" onClick={() => setForm((current) => ({ ...initialForm, agentType: type, ownerName: current.ownerName, ownerEmail: current.ownerEmail, organization: current.organization }))} className={`px-3 py-1.5 text-xs ${form.agentType === type ? "bg-white font-semibold shadow-sm" : "text-neutral-500"}`} style={{ borderRadius: 4 }}>{type === "EXECUTOR" ? "执行 Agent" : "Coordinator Agent"}</button>)}
          </div>
        </section>
        <section className="grid gap-4 p-5 md:grid-cols-2">
          <Field label="Agent 服务地址"><input className="input font-mono" placeholder={form.agentType === "EXECUTOR" ? "https://agent.example.com/agents/research/invoke" : "https://coordinator.example.com"} value={form.endpoint} onChange={(event) => update("endpoint", event.target.value)} required /></Field>
          <Field label="Runner 访问令牌"><input className="input font-mono" type="password" autoComplete="off" value={form.runtimeToken} onChange={(event) => update("runtimeToken", event.target.value)} required /></Field>
          <div className="md:col-span-2 flex justify-end">
            <button className="btn-secondary" type="button" onClick={readManifest} disabled={Boolean(busy)}>
              {busy === "manifest" ? <LoaderCircle className="size-4 animate-spin" /> : <RefreshCw className="size-4" />}读取 Agent 清单
            </button>
          </div>
        </section>

        <section className="grid gap-4 p-5 md:grid-cols-2">
          <Field label="负责人"><input className="input" value={form.ownerName} onChange={(event) => update("ownerName", event.target.value)} required /></Field>
          <Field label="邮箱"><input className="input" type="email" value={form.ownerEmail} onChange={(event) => update("ownerEmail", event.target.value)} required /></Field>
          <Field label="组织"><input className="input" value={form.organization} onChange={(event) => update("organization", event.target.value)} /></Field>
          <Field label="Agent 名称"><input className="input" value={form.agentName} onChange={(event) => update("agentName", event.target.value)} required /></Field>
          <Field label="Agent Key"><input className="input font-mono" value={form.agentKey} onChange={(event) => update("agentKey", event.target.value)} required /></Field>
          <Field label="DID"><input className="input font-mono" value={form.did} onChange={(event) => update("did", event.target.value)} required /></Field>
          <Field label="身份公钥"><input className="input font-mono" value={form.publicKey} onChange={(event) => update("publicKey", event.target.value)} required /></Field>
          <Field label="能力"><input className="input" placeholder="research, extraction" value={form.capabilities} onChange={(event) => update("capabilities", event.target.value)} required /></Field>
          <Field label="LLM 提供方"><input className="input" value={form.llmProvider} onChange={(event) => update("llmProvider", event.target.value)} /></Field>
          <Field label="模型"><input className="input" value={form.llmModel} onChange={(event) => update("llmModel", event.target.value)} /></Field>
          <div className="md:col-span-2"><Field label="系统提示词"><textarea className="textarea" value={form.systemPrompt} onChange={(event) => update("systemPrompt", event.target.value)} /></Field></div>
        </section>

        {form.agentType === "EXECUTOR" && <section className="grid gap-4 p-5 md:grid-cols-2 xl:grid-cols-4">
          <Field label="接收币种"><input className="input font-mono" list="payout-assets" value={form.payoutAsset} onChange={(event) => update("payoutAsset", event.target.value.toUpperCase())} required /><datalist id="payout-assets">{payoutAssets.map((asset) => <option key={asset} value={asset} />)}</datalist></Field>
          <Field label="归属链"><input className="input font-mono" value={form.preferredChainId ? `${chainLabel(Number(form.preferredChainId))} / ${form.preferredChainId}` : ""} placeholder="由 Runner 自动读取" readOnly required /></Field>
          <Field label="最低报价"><input className="input" value={form.priceFloor} onChange={(event) => update("priceFloor", event.target.value)} required /></Field>
          <Field label="Agent 执行钱包"><input className="input font-mono" value={form.homeWallet} placeholder="由 Runner 自动读取" readOnly required /></Field>
        </section>}

        <div className="flex flex-wrap items-center justify-between gap-3 p-5">
          <div className="flex min-w-0 items-center gap-2 text-xs text-neutral-500"><KeyRound className="size-4 shrink-0" /><span>访问令牌保存在平台受控运行目录，不会返回给浏览器或写入 Agent 公开资料。</span></div>
          <button className="btn-primary" type="submit" disabled={Boolean(busy) || Boolean(registration)}>{busy === "register" ? <LoaderCircle className="size-4 animate-spin" /> : <Send className="size-4" />}登记 Agent</button>
        </div>
      </form>

      <aside className="space-y-5">
        <section className="surface p-5">
          <h2 className="section-title">接入进度</h2>
          <div className="mt-4 space-y-3">
            {steps.map(([key, label]) => <div key={key} className="flex items-center gap-3 text-sm">{completed.includes(key) ? <Check className="size-4 text-emerald-600" /> : <Circle className="size-4 text-neutral-300" />}<span className={completed.includes(key) ? "font-medium text-neutral-900" : "text-neutral-500"}>{label}</span></div>)}
          </div>
        </section>

        {registration && <section className="surface divide-y divide-line">
          <div className="p-5"><h2 className="section-title">身份与平台审核</h2><p className="mt-2 break-all font-mono text-xs text-neutral-500">{registration.agent.did}</p></div>
          <div className="grid gap-3 p-5">
            <button className="btn-secondary justify-center" type="button" onClick={verifyOwnership} disabled={Boolean(busy) || completed.includes("ownership")}>{busy === "ownership" ? <LoaderCircle className="size-4 animate-spin" /> : <ShieldCheck className="size-4" />}{form.agentType === "EXECUTOR" ? "验证 Agent 与执行钱包" : "验证 Coordinator 身份"}</button>
            <button className="btn-secondary justify-center" type="button" onClick={refreshReview} disabled={Boolean(busy) || !completed.includes("ownership")}>{busy === "review" ? <LoaderCircle className="size-4 animate-spin" /> : <RefreshCw className="size-4" />}刷新审核状态</button>
            <button className="btn-primary justify-center" type="button" onClick={startHeartbeat} disabled={Boolean(busy) || platformState?.admissionStatus !== "APPROVED" || completed.includes("heartbeat")}>{busy === "heartbeat" ? <LoaderCircle className="size-4 animate-spin" /> : <Radio className="size-4" />}启动签名心跳</button>
            <div className="border-t border-line pt-3 text-xs text-neutral-500">准入：{platformState?.admissionStatus || registration.admissionStatus} · {form.agentType === "EXECUTOR" ? `路线：${platformState?.chainStatus || registration.chainStatus} · Gas：${platformState?.gasReady ? "READY" : "PENDING"}` : "执行链与 Gas：不适用"}</div>
          </div>
        </section>}
      </aside>
    </div>
  </>;
}
