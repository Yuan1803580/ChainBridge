"use client";

import { AlertTriangle, Check, CheckCircle2, CircleDollarSign, Copy, FileCheck2, Hash, MessageSquare, Play, Send, Shield, UploadCloud, WalletCards } from "lucide-react";
import { useParams } from "next/navigation";
import { useEffect, useState } from "react";
import { Modal } from "@/components/modal";
import { Breadcrumbs, CodeBlock, DataState, InlineSuccess, PageHeader, SourceBanner, StatusBadge } from "@/components/ui";
import { api } from "@/lib/api";
import { formatDate, formatMoney, shortHash } from "@/lib/format";
import {
  approveSettlementToken,
  connectWallet,
  createAndLockWithQuote,
  getWalletState,
  readLocalQuote,
  releaseCrossChain as releaseLocalCrossChain,
  type LocalSettlementQuote,
  type WalletState,
} from "@/lib/local-contracts";
import type { SettlementQuote } from "@/lib/types";
import { useResource } from "@/lib/use-resource";

const normalSteps = ["awaiting_funding", "funded", "in_progress", "delivered", "accepted", "completed"];

function sourceAmountForSettlementAmount(amount: string): string {
  const value = Number(amount) / 2000;
  return value.toFixed(8).replace(/\.?0+$/, "") || "0";
}

export default function OrderDetailPage() {
  const { id } = useParams<{ id: string }>();
  const resource = useResource(() => api.orders.get(id), [id]);
  const [message, setMessage] = useState("");
  const [localMessages, setLocalMessages] = useState<Array<{ content: string; at: string }>>([]);
  const [acceptOpen, setAcceptOpen] = useState(false);
  const [disputeOpen, setDisputeOpen] = useState(false);
  const [deliverOpen, setDeliverOpen] = useState(false);
  const [actionBusy, setActionBusy] = useState(false);
  const [actionError, setActionError] = useState<string>();
  const [actionSuccess, setActionSuccess] = useState<string>();
  const [quote, setQuote] = useState<SettlementQuote>();
  const [wallet, setWallet] = useState<WalletState>({ ready: false, message: "Wallet is not connected." });
  const [localQuote, setLocalQuote] = useState<LocalSettlementQuote>();
  const [localApproveHash, setLocalApproveHash] = useState<string>();
  const [localLockHash, setLocalLockHash] = useState<string>();
  const [localReleaseHash, setLocalReleaseHash] = useState<string>();
  const [localMessageId, setLocalMessageId] = useState<string>();
  const order = resource.data;
  const activeQuote = quote || order?.settlementQuote;
  const accepted = order?.status === "completed";
  const currentStatus = order?.status;
  const currentIndex = currentStatus ? normalSteps.indexOf(currentStatus) : -1;

  useEffect(() => {
    getWalletState().then(setWallet).catch(() => setWallet({ ready: false, message: "MetaMask is not available." }));
  }, []);

  const getSettlementQuote = async () => {
    if (!order) return undefined;
    if (resource.source === "mock") { setActionError("当前为离线演示数据，未执行写操作。"); return undefined; }
    setActionBusy(true); setActionError(undefined); setActionSuccess(undefined);
    try {
      const nextQuote = await api.orders.quoteSettlement(id, {
        sourceAsset: "ETH-like",
        settlementToken: "USDC",
        sourceAmount: sourceAmountForSettlementAmount(order.amount),
        maxSlippageBps: 100,
        sourceDecimals: 18,
        settlementDecimals: 6
      });
      setQuote(nextQuote);
      setActionSuccess("Settlement quote generated and bound to the order terms.");
      resource.reload();
      return nextQuote;
    } catch (error) {
      setActionError(error instanceof Error ? error.message : "Settlement quote failed");
      return undefined;
    } finally {
      setActionBusy(false);
    }
  };

  const fundWithQuote = async (providedQuote?: SettlementQuote) => {
    if (!order) return;
    const nextQuote = providedQuote || activeQuote || await getSettlementQuote();
    if (!nextQuote) return;
    if (resource.source === "mock") { setActionError("当前为离线演示数据，未执行写操作。"); return; }
    setActionBusy(true); setActionError(undefined); setActionSuccess(undefined);
    try {
      await api.orders.action(id, "fund", {
        payerAccount: "eip155:11155111:0xRequester",
        payeeAccount: "eip155:421614:0xProvider",
        assetId: "eip155:11155111/erc20:0xMockUSDC",
        sourceChain: "eip155:11155111",
        destinationChain: "eip155:421614",
        quotedSettlementAmount: nextQuote.quotedSettlementAmount,
        minSettlementAmount: nextQuote.minSettlementAmount,
        maxSlippageBps: nextQuote.maxSlippageBps,
        quoteDeadline: nextQuote.quoteDeadline,
        quoteHash: nextQuote.quoteHash
      });
      setActionSuccess("Quote-protected settlement funds are locked.");
      resource.reload();
    } catch (error) {
      setActionError(error instanceof Error ? error.message : "Quote-protected funding failed");
    } finally {
      setActionBusy(false);
    }
  };

  const connectLocalWallet = async () => {
    setActionBusy(true); setActionError(undefined); setActionSuccess(undefined);
    try {
      const nextWallet = await connectWallet();
      setWallet(nextWallet);
      setActionSuccess(nextWallet.message);
    } catch (error) {
      setActionError(error instanceof Error ? error.message : "Wallet connection failed");
    } finally {
      setActionBusy(false);
    }
  };

  const getLocalSettlementQuote = async () => {
    if (!order || !wallet.account) return;
    setActionBusy(true); setActionError(undefined); setActionSuccess(undefined);
    try {
      const nextQuote = await readLocalQuote(order, wallet.account);
      setLocalQuote(nextQuote);
      setActionSuccess("Local contract quote loaded.");
    } catch (error) {
      setActionError(error instanceof Error ? error.message : "Local contract quote failed");
    } finally {
      setActionBusy(false);
    }
  };

  const approveLocalUsdc = async () => {
    if (!order || !wallet.account) return;
    setActionBusy(true); setActionError(undefined); setActionSuccess(undefined);
    try {
      const result = await approveSettlementToken(order, wallet.account);
      setLocalApproveHash(result.hash);
      setActionSuccess("MockUSDC approved for MarketplaceEscrow.");
    } catch (error) {
      setActionError(error instanceof Error ? error.message : "MockUSDC approve failed");
    } finally {
      setActionBusy(false);
    }
  };

  const lockLocalSettlement = async () => {
    if (!order || !wallet.account) return;
    setActionBusy(true); setActionError(undefined); setActionSuccess(undefined);
    try {
      const nextQuote = localQuote || await readLocalQuote(order, wallet.account);
      setLocalQuote(nextQuote);
      const result = await createAndLockWithQuote(order, wallet.account, nextQuote);
      setLocalLockHash(result.hash);
      setActionSuccess("Local Escrow createAndLockWithQuote transaction confirmed.");
    } catch (error) {
      setActionError(error instanceof Error ? error.message : "Local Escrow lock failed");
    } finally {
      setActionBusy(false);
    }
  };

  const releaseLocalSettlement = async () => {
    if (!order || !wallet.account) return;
    setActionBusy(true); setActionError(undefined); setActionSuccess(undefined);
    try {
      const result = await releaseLocalCrossChain(order, wallet.account);
      setLocalReleaseHash(result.hash);
      setLocalMessageId(result.messageId);
      setActionSuccess("Local mock CCIP release confirmed.");
    } catch (error) {
      setActionError(error instanceof Error ? error.message : "Local mock CCIP release failed");
    } finally {
      setActionBusy(false);
    }
  };

  const runAction = async (action: "fund" | "start" | "accept" | "dispute", body?: Record<string, unknown>) => {
    if (resource.source === "mock") { setActionError("当前为离线演示数据，未执行写操作。"); return; }
    setActionBusy(true); setActionError(undefined); setActionSuccess(undefined);
    try { await api.orders.action(id, action, body); setActionSuccess("订单状态已由后端更新。"); resource.reload(); }
    catch (error) { setActionError(error instanceof Error ? error.message : "订单操作失败"); }
    finally { setActionBusy(false); }
  };

  const sendMessage = async (event: React.FormEvent) => {
    event.preventDefault();
    if (!message.trim()) return;
    const content = message.trim();
    setActionError(undefined);
    try { if (resource.source === "mock") setLocalMessages((items) => [...items, { content, at: new Date().toISOString() }]); else { await api.orders.message(id, content); resource.reload(); } setMessage(""); }
    catch (error) { setActionError(error instanceof Error ? error.message : "发送消息失败"); }
  };

  return <>
    <Breadcrumbs items={[{ label: "订单中心", href: "/orders" }, { label: order?.orderNumber || "订单详情" }]} />
    <DataState loading={resource.loading} error={resource.error} onRetry={resource.reload}>
      {order && <div className="fade-in">
        <PageHeader title={order.sourceTitle} description={`${order.orderNumber} · 创建于 ${formatDate(order.createdAt)}`} actions={<><StatusBadge status={order.status} />{order.status === "awaiting_funding" && <button className="btn-primary" disabled={actionBusy} onClick={() => fundWithQuote()}><WalletCards className="size-4" />Quote + Lock</button>}{order.status === "funded" && <button className="btn-primary" disabled={actionBusy} onClick={() => runAction("start")}><Play className="size-4" />开始执行</button>}{order.status === "in_progress" && <button className="btn-primary" disabled={actionBusy} onClick={() => setDeliverOpen(true)}><UploadCloud className="size-4" />提交交付</button>}{order.status === "delivered" && <button className="btn-primary" disabled={actionBusy} onClick={() => setAcceptOpen(true)}><Check className="size-4" />验收交付</button>}<button className="btn-danger" disabled={actionBusy || !["funded", "in_progress", "delivered"].includes(order.status)} onClick={() => setDisputeOpen(true)}><AlertTriangle className="size-4" />发起争议</button></>} />
        <SourceBanner source={resource.source} />
        {actionError && <div className="mb-5 rounded-md border border-red-200 bg-red-50 p-3 text-sm text-red-700">{actionError} 请确认当前 Agent 身份与该操作角色一致。</div>}
        {actionSuccess && <div className="mb-5"><InlineSuccess>{actionSuccess}</InlineSuccess></div>}

        <section className="surface mb-8 overflow-x-auto px-5 py-5 scrollbar-thin"><div className="flex min-w-[680px] items-start">{normalSteps.map((step, index) => { const done = currentIndex >= index; const active = currentIndex === index; return <div key={step} className="relative flex flex-1 flex-col items-center text-center"><div className={`absolute left-0 top-3 h-px w-full ${index === 0 ? "hidden" : done ? "bg-brand-500" : "bg-line"}`} /><span className={`relative z-10 grid size-7 place-items-center rounded-full border-2 ${done ? "border-brand-600 bg-brand-600 text-white" : "border-line bg-white text-neutral-400"}`}>{done && !active ? <Check className="size-3.5" /> : <span className="text-[10px]">{index + 1}</span>}</span><span className={`mt-2 text-[11px] ${active ? "font-semibold text-brand-700" : "text-neutral-500"}`}>{({ awaiting_funding: "待锁款", funded: "已锁款", in_progress: "执行中", delivered: "已交付", accepted: "已验收", completed: "已完成" } as Record<string, string>)[step]}</span></div>; })}</div></section>

        <div className="grid gap-8 xl:grid-cols-[minmax(0,1.45fr)_360px]">
          <div className="space-y-8">
            {order.delivery && <section><div className="section-head"><div><h2 className="section-title">交付内容</h2><p className="mt-1 text-xs text-neutral-500">由供给方提交，内容哈希用于完整性校验。</p></div><StatusBadge status={accepted ? "accepted" : "delivered"} /></div><div className="surface p-5"><div className="flex items-start gap-3"><span className="grid size-10 shrink-0 place-items-center rounded-md bg-brand-50 text-brand-700"><FileCheck2 className="size-5" /></span><div className="min-w-0"><h3 className="font-medium">{order.delivery.summary}</h3><div className="mt-3 space-y-2 text-xs text-neutral-500"><div className="flex min-w-0 items-center gap-2"><span className="shrink-0">交付地址</span><code className="truncate rounded bg-neutral-50 px-2 py-1">{order.delivery.uri}</code></div><div className="flex items-center gap-2"><Hash className="size-3.5" /><code>{shortHash(order.delivery.contentHash, 24)}</code><button aria-label="复制内容哈希" onClick={() => navigator.clipboard?.writeText(order.delivery!.contentHash)}><Copy className="size-3.5" /></button></div><div>{formatDate(order.delivery.createdAt)}</div></div></div></div></div></section>}
            <section><div className="section-head"><div><h2 className="section-title">协商消息</h2><p className="mt-1 text-xs text-neutral-500">消息保存在平台并进入审计记录。</p></div><MessageSquare className="size-4 text-neutral-400" /></div><div className="surface"><div className="max-h-[360px] space-y-4 overflow-auto p-4 scrollbar-thin">{[...order.messages.map((item) => ({ name: item.senderName, content: item.content, at: item.createdAt, own: item.senderId === order.requesterId })), ...localMessages.map((item) => ({ name: "校园科研助理", content: item.content, at: item.at, own: true }))].map((item, index) => <div key={`${item.at}-${index}`} className={`flex gap-3 ${item.own ? "flex-row-reverse" : ""}`}><span className="grid size-8 shrink-0 place-items-center rounded bg-neutral-100 text-xs font-medium">{item.name[0]}</span><div className={`max-w-[78%] rounded-md px-3 py-2 ${item.own ? "bg-brand-600 text-white" : "bg-neutral-100 text-neutral-700"}`}><div className="text-xs leading-5">{item.content}</div><div className={`mt-1 text-[10px] ${item.own ? "text-emerald-100" : "text-neutral-400"}`}>{formatDate(item.at)}</div></div></div>)}{!order.messages.length && !localMessages.length && <div className="py-8 text-center text-sm text-neutral-500">暂无消息</div>}</div><form className="flex gap-2 border-t border-line p-3" onSubmit={sendMessage}><input className="input min-w-0 flex-1" value={message} onChange={(e) => setMessage(e.target.value)} placeholder="输入协商消息" /><button className="icon-btn border-brand-600 bg-brand-600 text-white hover:bg-brand-700" disabled={!message.trim()} aria-label="发送消息"><Send className="size-4" /></button></form></div></section>
            <section><h2 className="section-title">事件时间线</h2><div className="mt-4 ml-3 border-l border-line pl-6">{order.events.map((event, index) => <div key={event.id} className="relative pb-6 last:pb-0"><span className={`absolute -left-[31px] top-0 grid size-3 rounded-full border-2 border-white ${index === order.events.length - 1 ? "bg-brand-600 ring-2 ring-brand-100" : "bg-neutral-300"}`} /><div className="flex flex-wrap items-center justify-between gap-2"><h3 className="text-sm font-medium">{event.title}</h3><time className="text-[11px] text-neutral-500">{formatDate(event.createdAt)}</time></div><p className="mt-1 text-xs leading-5 text-neutral-500">{event.description}</p><div className="mt-1 text-[11px] text-neutral-400">操作者：{event.actorName}</div></div>)}</div></section>
          </div>

          <aside className="space-y-5">
            <div className="surface divide-y divide-line"><div className="p-4"><div className="text-xs text-neutral-500">订单金额</div><div className="mt-1 font-mono text-xl font-semibold">{formatMoney(order.amount, order.currency)}</div></div><dl className="divide-y divide-line px-4 text-sm"><div className="flex justify-between gap-4 py-3"><dt className="text-neutral-500">需求方</dt><dd className="text-right">{order.requesterName}</dd></div><div className="flex justify-between gap-4 py-3"><dt className="text-neutral-500">供给方</dt><dd className="text-right">{order.providerName}</dd></div><div className="flex justify-between gap-4 py-3"><dt className="text-neutral-500">订单来源</dt><dd>{order.source === "demand" ? "需求竞标" : "能力直购"}</dd></div></dl></div>
            <div className="surface p-4"><div className="flex items-center justify-between"><h2 className="text-sm font-semibold">Quote-Protected Settlement</h2><StatusBadge status={accepted ? "released" : order.settlementStatus} /></div>{order.status === "awaiting_funding" && <div className="mt-4"><div className="mb-2 text-[11px] font-semibold uppercase tracking-wide text-neutral-500">API Mock</div><div className="grid grid-cols-2 gap-2"><button className="btn-secondary" disabled={actionBusy} onClick={getSettlementQuote}>Get Quote</button><button className="btn-primary" disabled={actionBusy} onClick={() => fundWithQuote()}>Confirm Lock</button></div></div>}<div className="mt-4 rounded-md border border-line p-3"><div className="mb-2 text-[11px] font-semibold uppercase tracking-wide text-neutral-500">Wallet Local Chain</div><div className="mb-3 space-y-1 text-xs"><div className="flex justify-between gap-3"><span className="text-neutral-500">Wallet</span><span className="font-mono text-right">{wallet.account ? shortHash(wallet.account, 18) : "not connected"}</span></div><div className="flex justify-between gap-3"><span className="text-neutral-500">Chain</span><span>{wallet.chainId || "-"} {wallet.ready ? "(ready)" : ""}</span></div><div className="text-[11px] text-neutral-500">{wallet.message}</div></div><div className="grid grid-cols-2 gap-2"><button className="btn-secondary" disabled={actionBusy} onClick={connectLocalWallet}>Connect Wallet</button><button className="btn-secondary" disabled={actionBusy || !wallet.ready} onClick={getLocalSettlementQuote}>Read Quote</button><button className="btn-secondary" disabled={actionBusy || !wallet.ready} onClick={approveLocalUsdc}>Approve USDC</button><button className="btn-primary" disabled={actionBusy || !wallet.ready} onClick={lockLocalSettlement}>Lock With Quote</button><button className="btn-primary col-span-2" disabled={actionBusy || !wallet.ready} onClick={releaseLocalSettlement}>Release Cross-chain</button></div></div><dl className="mt-4 space-y-3 text-xs"><div className="flex justify-between gap-3"><dt className="text-neutral-500">Settlement ID</dt><dd className="font-mono text-right">{order.settlementId}</dd></div><div className="flex justify-between gap-3"><dt className="text-neutral-500">Mode</dt><dd>API mock + wallet local chain</dd></div><div className="flex justify-between gap-3"><dt className="text-neutral-500">Asset</dt><dd>{order.settlement?.assetId || "USDC-like token"}</dd></div>{activeQuote && <><div className="flex justify-between gap-3"><dt className="text-neutral-500">API source asset</dt><dd>{activeQuote.sourceAmount} {activeQuote.sourceAsset}</dd></div><div className="flex justify-between gap-3"><dt className="text-neutral-500">API quoted</dt><dd>{activeQuote.quotedSettlementAmount} {activeQuote.settlementToken}</dd></div><div className="flex justify-between gap-3"><dt className="text-neutral-500">API min</dt><dd>{activeQuote.minSettlementAmount} {activeQuote.settlementToken}</dd></div><div className="flex justify-between gap-3"><dt className="text-neutral-500">API quote hash</dt><dd className="font-mono text-right">{shortHash(activeQuote.quoteHash, 18)}</dd></div></>}{localQuote && <><div className="flex justify-between gap-3"><dt className="text-neutral-500">Local source asset</dt><dd>{localQuote.sourceAmountDisplay} ETH-like</dd></div><div className="flex justify-between gap-3"><dt className="text-neutral-500">Local quoted</dt><dd>{localQuote.quotedSettlementAmountDisplay} USDC</dd></div><div className="flex justify-between gap-3"><dt className="text-neutral-500">Local min</dt><dd>{localQuote.minSettlementAmountDisplay} USDC</dd></div><div className="flex justify-between gap-3"><dt className="text-neutral-500">Local quote hash</dt><dd className="font-mono text-right">{shortHash(localQuote.quoteHash, 18)}</dd></div></>}{order.settlement?.transactionHash && <div className="flex justify-between gap-3"><dt className="text-neutral-500">API lock tx hash</dt><dd className="font-mono text-right">{shortHash(order.settlement.transactionHash, 18)}</dd></div>}{order.settlement?.ccipMessageId && <div className="flex justify-between gap-3"><dt className="text-neutral-500">API messageId</dt><dd className="font-mono text-right">{shortHash(order.settlement.ccipMessageId, 18)}</dd></div>}{localApproveHash && <div className="flex justify-between gap-3"><dt className="text-neutral-500">Approve tx</dt><dd className="font-mono text-right">{shortHash(localApproveHash, 18)}</dd></div>}{localLockHash && <div className="flex justify-between gap-3"><dt className="text-neutral-500">Lock tx</dt><dd className="font-mono text-right">{shortHash(localLockHash, 18)}</dd></div>}{localReleaseHash && <div className="flex justify-between gap-3"><dt className="text-neutral-500">Release tx</dt><dd className="font-mono text-right">{shortHash(localReleaseHash, 18)}</dd></div>}{localMessageId && <div className="flex justify-between gap-3"><dt className="text-neutral-500">Local messageId</dt><dd className="font-mono text-right">{shortHash(localMessageId, 18)}</dd></div>}</dl><div className="mt-4 flex gap-2 rounded-md bg-amber-50 p-3 text-xs leading-5 text-amber-800"><WalletCards className="mt-0.5 size-4 shrink-0" />Local wallet mode uses Hardhat + MockUSDC + mock CCIP only. No real DEX swap or real testnet transaction is executed.</div></div>
            <div className="surface p-4"><h2 className="text-sm font-semibold">条款完整性</h2><div className="mt-3 flex items-center gap-2 text-xs text-brand-700"><Shield className="size-4" />双方签名已验证</div><div className="mt-3 flex items-center justify-between rounded bg-neutral-50 px-2 py-2"><code className="truncate text-[11px] text-neutral-500">{order.termsHash}</code><button aria-label="复制条款哈希" onClick={() => navigator.clipboard?.writeText(order.termsHash)}><Copy className="size-3.5" /></button></div><div className="mt-3"><CodeBlock value={order.input} maxHeight="180px" /></div></div>
          </aside>
        </div>
      </div>}
    </DataState>

    <Modal open={acceptOpen} onClose={() => setAcceptOpen(false)} title="确认验收并放款" description="此操作将完成订单，并调用结算适配器释放模拟资金。">
      <form className="space-y-4" onSubmit={async (event) => { event.preventDefault(); await runAction("accept"); setAcceptOpen(false); }}><div className="rounded-md bg-neutral-50 p-4 text-sm"><div className="flex items-center justify-between"><span className="text-neutral-500">放款金额</span><strong className="font-mono">{order && formatMoney(order.amount, order.currency)}</strong></div><div className="mt-3 flex items-center justify-between"><span className="text-neutral-500">接收方</span><span>{order?.providerName}</span></div></div><label className="flex items-start gap-3 text-sm leading-6"><input className="mt-1" type="checkbox" required /><span>我已核验交付内容，并确认其满足订单验收标准。</span></label><div className="flex justify-end gap-2"><button type="button" className="btn-secondary" onClick={() => setAcceptOpen(false)}>关闭</button><button className="btn-primary" disabled={actionBusy}><CheckCircle2 className="size-4" />确认验收</button></div></form>
    </Modal>
    <Modal open={deliverOpen} onClose={() => setDeliverOpen(false)} title="提交交付物" description="交付摘要、地址和内容哈希会写入订单审计记录。"><form className="space-y-4" onSubmit={async (event) => { event.preventDefault(); const form = new FormData(event.currentTarget); setActionBusy(true); setActionError(undefined); try { if (resource.source === "mock") throw new Error("当前为离线演示数据，未执行写操作。"); await api.orders.deliver(id, { artifactUri: String(form.get("artifactUri")), contentHash: String(form.get("contentHash")), summary: String(form.get("summary")) }); setDeliverOpen(false); setActionSuccess("交付物已提交，等待需求方验收。"); resource.reload(); } catch (error) { setActionError(error instanceof Error ? error.message : "提交交付失败"); } finally { setActionBusy(false); } }}><label><span className="label">交付地址 *</span><input name="artifactUri" className="input" type="url" defaultValue="https://example.local/deliveries/demo-result.json" required /></label><label><span className="label">内容哈希 *</span><input name="contentHash" className="input font-mono text-xs" minLength={16} defaultValue="sha256:demo-delivery-content-0000000000000000" required /></label><label><span className="label">交付摘要 *</span><textarea name="summary" className="textarea" required placeholder="说明完成内容、输出格式和核验方法" /></label><div className="flex justify-end gap-2"><button type="button" className="btn-secondary" onClick={() => setDeliverOpen(false)}>取消</button><button className="btn-primary" disabled={actionBusy}><UploadCloud className="size-4" />提交交付</button></div></form></Modal>
    <Modal open={disputeOpen} onClose={() => setDisputeOpen(false)} title="发起订单争议" description="争议会暂停结算，管理员仅依据订单条款和平台证据处理。"><form className="space-y-4" onSubmit={async (event) => { event.preventDefault(); const form = new FormData(event.currentTarget); await runAction("dispute", { reason: `${String(form.get("category"))}：${String(form.get("reason"))}` }); setDisputeOpen(false); }}><label><span className="label">争议原因 *</span><select name="category" className="select w-full"><option>交付不符合验收标准</option><option>交付内容不完整</option><option>任务超时</option><option>其他</option></select></label><label><span className="label">情况说明 *</span><textarea name="reason" className="textarea" minLength={20} required placeholder="说明争议事实，并引用对应的验收标准" /></label><div className="rounded-md border border-amber-200 bg-amber-50 p-3 text-xs leading-5 text-amber-800"><div className="flex gap-2"><CircleDollarSign className="mt-0.5 size-4 shrink-0" />提交后 Mock Settlement 将保持锁定，不会自动退款或放款。</div></div><div className="flex justify-end gap-2"><button type="button" className="btn-secondary" onClick={() => setDisputeOpen(false)}>取消</button><button className="btn-danger" disabled={actionBusy}><AlertTriangle className="size-4" />提交争议</button></div></form></Modal>
  </>;
}
