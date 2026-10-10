"use client";

import { Activity, CheckCircle2, Network, Radio, RefreshCcw, Route, ShieldCheck } from "lucide-react";
import { useEffect, useState } from "react";

import { InlineMetric } from "@/components/platform-ui";
import { PageHeader } from "@/components/ui";
import { dynamicApi, type RawRoute } from "@/lib/workflow-api";

const chainLabel = (chainId: number) => `Chain ${String.fromCharCode(65 + chainId - 31337)}`;
const shortAddress = (value?: string) => value ? `${value.slice(0, 8)}...${value.slice(-6)}` : "待部署";

export default function RoutesPage() {
  const [routes, setRoutes] = useState<RawRoute[]>([]);
  const [error, setError] = useState("");
  const [loading, setLoading] = useState(true);

  const load = () => {
    setLoading(true);
    dynamicApi.routes()
      .then((response) => setRoutes(response.items))
      .catch((reason) => setError(reason instanceof Error ? reason.message : String(reason)))
      .finally(() => setLoading(false));
  };
  useEffect(load, []);

  return <>
    <PageHeader title="动态链路注册表" description="路由按 routeKey + routeVersion 固化；Worker 必须使用任务创建时的精确版本。" actions={<button className="btn-secondary" onClick={load}><RefreshCcw className="size-4" />刷新</button>} />
    {error && <div className="mb-4 border border-red-200 bg-red-50 px-4 py-3 text-sm text-red-700">路由 API 不可用：{error}</div>}

    <section className="surface grid divide-y divide-line sm:grid-cols-2 sm:divide-x sm:divide-y-0 xl:grid-cols-4">
      <InlineMetric label="Task Chain" value="31337" detail="Chain A" icon={<Network className="size-3.5" />} />
      <InlineMetric label="Execution Chains" value="3" detail="31338 / 31339 / 31340" icon={<Route className="size-3.5" />} />
      <InlineMetric label="ACTIVE 路由" value={String(routes.filter((item) => item.status === "ACTIVE").length)} detail={`${routes.length} 个版本记录`} icon={<CheckCircle2 className="size-3.5" />} />
      <InlineMetric label="确认深度" value={routes.length ? `${Math.max(...routes.map((item) => item.confirmationBlocks))}` : "-"} detail="本地确认块" icon={<ShieldCheck className="size-3.5" />} />
    </section>

    <section className="mt-8">
      <div className="section-head"><div><h2 className="section-title">Route Version</h2><p className="mt-1 text-xs text-neutral-500">数据直接来自 FastAPI chain_routes 表</p></div></div>
      <div className="surface overflow-x-auto scrollbar-thin">
        <table className="w-full min-w-[1180px] text-left">
          <thead className="border-b border-line bg-neutral-50 text-[11px] text-neutral-500"><tr><th className="px-4 py-3">路由</th><th className="px-4 py-3">链路</th><th className="px-4 py-3">结算 / 本链支付</th><th className="px-4 py-3">RemoteWorkBoard</th><th className="px-4 py-3">Payout Receiver</th><th className="px-4 py-3">RPC</th><th className="px-4 py-3">状态</th></tr></thead>
          <tbody className="divide-y divide-line">
            {routes.map((route) => <tr key={route.id} className="hover:bg-neutral-50">
              <td className="px-4 py-3"><div className="font-mono text-xs font-semibold">{route.routeKey}</div><div className="mt-1 font-mono text-[10px] text-neutral-500">v{route.routeVersion} · {route.id.slice(0, 8)}</div></td>
              <td className="px-4 py-3"><div className="text-xs font-medium">A → {chainLabel(route.destinationChainId)}</div><div className="mt-1 font-mono text-[10px] text-neutral-500">{route.sourceChainId} → {route.destinationChainId}</div></td>
              <td className="px-4 py-3"><div className="text-xs font-semibold">{route.settlementAsset} → {route.payoutAsset}</div><div className="mt-1 font-mono text-[10px] text-neutral-500">1 {route.settlementAsset} = {route.payoutRate} {route.payoutAsset} · {route.payoutDecimals} decimals</div><div className="mt-1 font-mono text-[10px] text-neutral-400">swap {shortAddress(route.payoutSwapAddress)} · slippage {route.payoutSlippageBps} bps</div></td>
              <td className="px-4 py-3 font-mono text-[10px]">{shortAddress(route.remoteWorkBoardAddress)}</td>
              <td className="px-4 py-3 font-mono text-[10px]">{shortAddress(route.payoutReceiverAddress)}</td>
              <td className="px-4 py-3 font-mono text-[10px] text-neutral-600">{route.rpcUrl}</td>
              <td className="px-4 py-3"><span className={`inline-flex items-center gap-1.5 text-[11px] font-semibold ${route.status === "ACTIVE" ? "text-emerald-700" : "text-amber-700"}`}><span className={`size-2 rounded-full ${route.status === "ACTIVE" ? "bg-emerald-500" : "bg-amber-500"}`} />{route.status}</span></td>
            </tr>)}
            {!loading && !routes.length && <tr><td colSpan={7} className="px-4 py-14 text-center text-sm text-neutral-500">没有路由记录</td></tr>}
            {loading && <tr><td colSpan={7} className="px-4 py-14 text-center text-sm text-neutral-500">正在读取路由注册表...</td></tr>}
          </tbody>
        </table>
      </div>
    </section>

    <section className="mt-8">
      <div className="section-head"><div><h2 className="section-title">本地链边界</h2><p className="mt-1 text-xs text-neutral-500">本项目只使用用户确认的四个 chainId</p></div></div>
      <div className="surface grid divide-y divide-line sm:grid-cols-2 sm:divide-x sm:divide-y-0 xl:grid-cols-4">
        {[31337, 31338, 31339, 31340].map((chainId) => <div key={chainId} className="p-4"><div className="flex items-center gap-2"><span className="grid size-8 place-items-center rounded-md bg-neutral-900 text-xs font-bold text-white">{String.fromCharCode(65 + chainId - 31337)}</span><div><div className="text-xs font-semibold">{chainLabel(chainId)}</div><div className="font-mono text-[10px] text-neutral-500">chainId {chainId}</div></div></div><div className="mt-3 flex items-center gap-1.5 text-[11px] text-emerald-700"><Radio className="size-3.5" />本地独立 RPC</div></div>)}
      </div>
    </section>
  </>;
}
