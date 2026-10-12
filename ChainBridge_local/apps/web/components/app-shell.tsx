"use client";

import {
  Activity, Bot, Boxes, BriefcaseBusiness, ChevronDown, CircleHelp, ClipboardList,
  Code2, Gauge, GitBranch, Menu, Network, PlusSquare, Route, Search, Settings,
  ShieldCheck, Store, UserPlus, Users, Workflow, X
} from "lucide-react";
import Link from "next/link";
import { usePathname } from "next/navigation";
import { useEffect, useState } from "react";
import { notifications } from "@/lib/mock-data";
import { api } from "@/lib/api";
import { cnRole, shortDid } from "@/lib/format";
import { useResource } from "@/lib/use-resource";
import type { DevIdentity } from "@/lib/types";

const navigation = [
  { href: "/", label: "运行总览", icon: Gauge },
  { href: "/workflows", label: "工作流", icon: Workflow },
  { href: "/workflows/new", label: "创建任务", icon: PlusSquare },
  { href: "/agents", label: "Agent 信息平台", icon: Users },
  { href: "/routes", label: "动态路由", icon: Route },
  { href: "/operations", label: "Worker 运维", icon: Activity },
];

const legacyNavigation = [
  { href: "/onboard", label: "Agent 接入", icon: UserPlus },
  { href: "/market/demands", label: "需求市场", icon: ClipboardList },
  { href: "/market/capabilities", label: "能力市场", icon: Store },
  { href: "/orders", label: "单订单兼容", icon: BriefcaseBusiness },
  { href: "/console", label: "API 控制台", icon: Code2 },
  { href: "/admin", label: "平台管理", icon: ShieldCheck },
];

function Brand() {
  return (
    <Link href="/" className="flex h-16 items-center gap-3 border-b border-white/10 px-5">
      <span className="grid size-9 place-items-center rounded-md bg-white text-brand-700"><Network className="size-5" /></span>
      <span className="min-w-0">
        <span className="block text-sm font-semibold text-white">多链 Agent 控制台</span>
        <span className="block text-[11px] text-emerald-100/70">Dynamic workflow · V1</span>
      </span>
    </Link>
  );
}

function Sidebar({ close }: { close?: () => void }) {
  const pathname = usePathname();
  return (
    <div className="flex h-full flex-col bg-[#12382a] text-white">
      <Brand />
      <nav className="flex-1 space-y-1 px-3 py-5" aria-label="主导航">
        {navigation.map((item) => {
          const active = item.href === "/" ? pathname === "/" : pathname.startsWith(item.href);
          const Icon = item.icon;
          return (
            <Link key={item.href} href={item.href} onClick={close} className={`flex h-10 items-center gap-3 rounded-md px-3 text-sm transition-colors ${active ? "bg-white text-[#12382a]" : "text-emerald-50/80 hover:bg-white/10 hover:text-white"}`}>
              <Icon className="size-[18px]" />
              {item.label}
            </Link>
          );
        })}
        <div className="px-3 pb-1 pt-5 text-[10px] font-semibold uppercase text-emerald-100/40">市场与兼容入口</div>
        {legacyNavigation.map((item) => {
          const active = pathname.startsWith(item.href);
          const Icon = item.icon;
          return (
            <Link key={item.href} href={item.href} onClick={close} className={`flex h-9 items-center gap-3 rounded-md px-3 text-[13px] transition-colors ${active ? "bg-white text-[#12382a]" : "text-emerald-50/70 hover:bg-white/10 hover:text-white"}`}>
              <Icon className="size-4" />
              {item.label}
            </Link>
          );
        })}
      </nav>
      <div className="border-t border-white/10 p-3">
        <Link href="/console" onClick={close} className="flex items-center gap-3 rounded-md px-3 py-2.5 text-xs text-emerald-50/70 hover:bg-white/10 hover:text-white">
          <CircleHelp className="size-4" /> API 与接入帮助
        </Link>
        <div className="mt-1 flex items-center gap-3 rounded-md px-3 py-2.5 text-xs text-emerald-50/50">
          <Settings className="size-4" /> V1 · 四链本地证明
        </div>
      </div>
    </div>
  );
}

export function AppShell({ children }: { children: React.ReactNode }) {
  const pathname = usePathname();
  const [mobileOpen, setMobileOpen] = useState(false);
  const [agentOpen, setAgentOpen] = useState(false);
  const identities = useResource(() => api.auth.devIdentities(), []);
  const [agentId, setAgentId] = useState("");
  const [authState, setAuthState] = useState<"loading" | "verified" | "mock" | "error">("loading");
  const [searchOpen, setSearchOpen] = useState(false);
  const selectedAgent = identities.data?.find((agent) => agent.id === agentId) || identities.data?.[0];
  const unread = notifications.filter((item) => !item.read).length;

  useEffect(() => setMobileOpen(false), [pathname]);

  useEffect(() => {
    if (!identities.data?.length || agentId) return;
    const storedDid = window.localStorage.getItem("agentMarketCurrentDid");
    const preferred = identities.data.find((identity) => identity.did === storedDid)
      || identities.data.find((identity) => identity.did === "did:local:campus-requester")
      || identities.data.find((identity) => identity.roles.includes("requester"))
      || identities.data[0];
    setAgentId(preferred.id);
    if (identities.source === "mock") { setAuthState("mock"); return; }
    const expiresAt = window.localStorage.getItem("agentMarketTokenExpiresAt");
    if (storedDid === preferred.did && expiresAt && new Date(expiresAt).getTime() > Date.now() + 30_000) { setAuthState("verified"); return; }
    api.auth.devLogin(preferred.did).then(() => setAuthState("verified")).catch(() => setAuthState("error"));
  }, [agentId, identities.data, identities.source]);

  const switchAgent = async (identity: DevIdentity) => {
    setAgentId(identity.id);
    setAgentOpen(false);
    if (identities.source === "mock") { setAuthState("mock"); return; }
    setAuthState("loading");
    try { await api.auth.devLogin(identity.did); setAuthState("verified"); }
    catch { setAuthState("error"); }
  };

  return (
    <div className="min-h-screen lg:grid lg:grid-cols-[240px_minmax(0,1fr)]">
      <aside className="fixed inset-y-0 left-0 z-40 hidden w-60 lg:block"><Sidebar /></aside>
      {mobileOpen && (
        <div className="fixed inset-0 z-50 lg:hidden">
          <button aria-label="关闭导航" className="absolute inset-0 bg-black/35" onClick={() => setMobileOpen(false)} />
          <aside className="relative h-full w-[min(82vw,280px)] shadow-xl"><Sidebar close={() => setMobileOpen(false)} /></aside>
        </div>
      )}

      <div className="min-w-0 lg:col-start-2">
        <header className="sticky top-0 z-30 flex h-16 items-center gap-3 border-b border-line bg-white/95 px-4 backdrop-blur sm:px-6 lg:px-8">
          <button className="icon-btn lg:hidden" aria-label="打开导航" onClick={() => setMobileOpen(true)}><Menu className="size-5" /></button>
          <button className="hidden h-10 min-w-0 max-w-[320px] flex-1 items-center gap-2 rounded-md border border-line bg-neutral-50 px-3 text-left text-sm text-neutral-400 sm:flex" onClick={() => setSearchOpen(true)}>
            <Search className="size-4" /><span className="truncate">搜索工作流、节点、Agent 或路由</span><kbd className="ml-auto rounded border border-line bg-white px-1.5 py-0.5 text-[10px] text-neutral-500">Ctrl K</kbd>
          </button>
          <div className="ml-auto flex items-center gap-2">
            <div className="hidden items-center gap-2 rounded-md border border-blue-200 bg-blue-50 px-2.5 py-1.5 text-xs text-blue-700 md:flex">
              <span className="size-1.5 rounded-full bg-blue-500" /> Local CCIP · A/B/C/D
            </div>
            <Link href="/" className="relative icon-btn" aria-label={`${unread} 条未读通知`}>
              <Bot className="size-4" />
              {unread > 0 && <span className="absolute -right-1 -top-1 grid size-4 place-items-center rounded-full bg-red-600 text-[9px] font-semibold text-white">{unread}</span>}
            </Link>
            <div className="relative">
              <button className="flex h-10 max-w-[220px] items-center gap-2 rounded-md border border-line bg-white px-2 text-left hover:bg-neutral-50" onClick={() => setAgentOpen((open) => !open)} aria-expanded={agentOpen}>
                <span className="grid size-7 shrink-0 place-items-center rounded bg-brand-100 text-xs font-semibold text-brand-700">{selectedAgent?.displayName?.[0] || "A"}</span>
                <span className="hidden min-w-0 sm:block">
                  <span className="block truncate text-xs font-medium text-ink">{selectedAgent?.displayName || "正在加载身份"}</span>
                  <span className="flex items-center gap-1 truncate text-[10px] text-neutral-500"><span className={`size-1.5 rounded-full ${authState === "verified" ? "bg-emerald-500" : authState === "error" ? "bg-red-500" : "bg-amber-500"}`} />{authState === "verified" ? "DID 会话已验证" : authState === "mock" ? "本地演示身份" : authState === "error" ? "身份验证失败" : "正在验证 DID"}</span>
                </span>
                <ChevronDown className="size-4 shrink-0 text-neutral-400" />
              </button>
              {agentOpen && (
                <div className="absolute right-0 top-12 z-40 w-80 rounded-md border border-line bg-white p-2 shadow-lg">
                  <div className="px-2 py-2 text-[11px] font-medium text-neutral-500">切换当前 Agent</div>
                  {identities.data?.map((agent) => (
                    <button key={agent.id} className={`flex w-full items-center gap-3 rounded px-2 py-2 text-left hover:bg-neutral-50 ${agent.id === agentId ? "bg-brand-50" : ""}`} onClick={() => switchAgent(agent)}>
                      <span className="grid size-8 place-items-center rounded bg-neutral-100 text-xs font-semibold">{agent.displayName[0]}</span>
                      <span className="min-w-0"><span className="block truncate text-sm font-medium">{agent.displayName}</span><span className="block truncate text-[11px] text-neutral-500">{shortDid(agent.did, 30)} · {agent.roles.map(cnRole).join(" / ")}</span></span>
                      {agent.id === agentId && <span className="ml-auto size-2 rounded-full bg-brand-500" />}
                    </button>
                  ))}
                  {identities.error && <div className="px-2 py-3 text-xs text-red-600">{identities.error}</div>}
                </div>
              )}
            </div>
          </div>
        </header>
        <main className="mx-auto w-full max-w-[1480px] px-4 py-6 sm:px-6 lg:px-8 lg:py-8">{children}</main>
      </div>

      {searchOpen && (
        <div className="fixed inset-0 z-[60] flex items-start justify-center bg-black/35 px-4 pt-[12vh]" role="dialog" aria-modal="true" aria-label="全局搜索">
          <button className="absolute inset-0" onClick={() => setSearchOpen(false)} aria-label="关闭搜索" />
          <div className="relative w-full max-w-2xl rounded-md border border-line bg-white shadow-xl">
            <div className="flex items-center gap-3 border-b border-line px-4"><Search className="size-5 text-neutral-400" /><input autoFocus className="h-14 min-w-0 flex-1 outline-none" placeholder="输入关键词搜索…" /><button className="icon-btn size-8 border-0" onClick={() => setSearchOpen(false)}><X className="size-4" /></button></div>
            <div className="p-6 text-center text-sm text-neutral-500">输入工作流编号、nodeId、DID 或 routeId 开始搜索</div>
          </div>
        </div>
      )}
    </div>
  );
}
