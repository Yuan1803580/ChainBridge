"use client";

import {
  ArrowLeft,
  Bot,
  BookOpen,
  CheckCircle2,
  LoaderCircle,
  RefreshCw,
  Send,
  Square,
  TriangleAlert,
  Wrench,
} from "lucide-react";
import Link from "next/link";
import { FormEvent, KeyboardEvent, useCallback, useEffect, useRef, useState } from "react";
import { useParams } from "next/navigation";

import { Breadcrumbs, DataState } from "@/components/ui";
import {
  dynamicApi,
  type AgentChatResponse,
  type RawAgentProfile,
} from "@/lib/workflow-api";

type ChatMessage = {
  id: string;
  role: "user" | "assistant";
  content: string;
  response?: AgentChatResponse;
};

const CHAT_TIMEOUT_MS = 300_000;

export default function AgentChatPage() {
  const params = useParams<{ id: string }>();
  const [profile, setProfile] = useState<RawAgentProfile | null>(null);
  const [sessionId, setSessionId] = useState("");
  const [messages, setMessages] = useState<ChatMessage[]>([]);
  const [input, setInput] = useState("");
  const [loading, setLoading] = useState(true);
  const [sending, setSending] = useState(false);
  const [error, setError] = useState("");
  const endRef = useRef<HTMLDivElement>(null);
  const requestControllerRef = useRef<AbortController | null>(null);
  const abortReasonRef = useRef<"cancelled" | "timeout" | null>(null);

  const createSession = useCallback(async (profileId: string) => {
    const session = await dynamicApi.createAgentChatSession(profileId, "Agent 平台对话");
    setSessionId(session.sessionId);
    return session.sessionId;
  }, []);

  const initialize = useCallback(async () => {
    setLoading(true);
    setError("");
    try {
      const response = await dynamicApi.agents();
      const selected = response.items.find((item) => (
        item.id === params.id || item.agentId === params.id || item.agent.id === params.id
      ));
      if (!selected) throw new Error("平台中不存在这个 Agent");
      setProfile(selected);
      await createSession(selected.id);
    } catch (reason) {
      setError(reason instanceof Error ? reason.message : String(reason));
    } finally {
      setLoading(false);
    }
  }, [createSession, params.id]);

  useEffect(() => {
    void initialize();
  }, [initialize]);

  useEffect(() => {
    endRef.current?.scrollIntoView({ behavior: "smooth", block: "end" });
  }, [messages, sending]);

  useEffect(() => () => {
    abortReasonRef.current = "cancelled";
    requestControllerRef.current?.abort();
  }, []);

  const reset = async () => {
    if (!profile || sending) return;
    setSending(true);
    setError("");
    try {
      await createSession(profile.id);
      setMessages([]);
      setInput("");
    } catch (reason) {
      setError(reason instanceof Error ? reason.message : String(reason));
    } finally {
      setSending(false);
    }
  };

  const send = async (event?: FormEvent) => {
    event?.preventDefault();
    const content = input.trim();
    if (!profile || !content || sending) return;
    setSending(true);
    setError("");
    setInput("");
    setMessages((current) => [...current, { id: crypto.randomUUID(), role: "user", content }]);
    const controller = new AbortController();
    requestControllerRef.current = controller;
    abortReasonRef.current = null;
    const timeoutId = window.setTimeout(() => {
      abortReasonRef.current = "timeout";
      controller.abort();
    }, CHAT_TIMEOUT_MS);
    try {
      const activeSession = sessionId || await createSession(profile.id);
      const response = await dynamicApi.chatWithAgent(profile.id, activeSession, content, controller.signal);
      setMessages((current) => [
        ...current,
        { id: crypto.randomUUID(), role: "assistant", content: response.message, response },
      ]);
    } catch (reason) {
      setInput(content);
      if (abortReasonRef.current === "cancelled") {
        setError("已取消本次回答，可以修改问题后重新发送。");
      } else if (abortReasonRef.current === "timeout") {
        setError("Agent 在 5 分钟内没有返回结果，请稍后重试。");
      } else {
        setError(reason instanceof Error ? reason.message : String(reason));
      }
    } finally {
      window.clearTimeout(timeoutId);
      if (requestControllerRef.current === controller) requestControllerRef.current = null;
      abortReasonRef.current = null;
      setSending(false);
    }
  };

  const cancelResponse = () => {
    const controller = requestControllerRef.current;
    if (!controller || controller.signal.aborted) return;
    abortReasonRef.current = "cancelled";
    controller.abort();
  };

  const handleKeyDown = (event: KeyboardEvent<HTMLTextAreaElement>) => {
    if (event.key === "Enter" && !event.shiftKey) {
      event.preventDefault();
      void send();
    }
  };

  return <>
    <Breadcrumbs items={[
      { label: "Agent 信息平台", href: "/agents" },
      { label: profile?.agent.displayName || "Agent", href: `/agents/${params.id}` },
      { label: "对话" },
    ]} />
    <DataState loading={loading} error={error && !profile ? error : ""} onRetry={() => void initialize()}>
      {profile && <div className="fade-in">
        <header className="mb-5 flex flex-wrap items-start justify-between gap-4">
          <div className="flex min-w-0 items-center gap-3">
            <span className="grid size-11 shrink-0 place-items-center rounded-md bg-brand-50 text-brand-700">
              <Bot className="size-5" />
            </span>
            <div className="min-w-0">
              <h1 className="page-title">{profile.agent.displayName}</h1>
              <div className="mt-1 flex items-center gap-2 text-xs text-neutral-500">
                <span className="size-2 rounded-full bg-emerald-500" />
                {profile.availability}
                <span className="font-mono">{profile.agentKey}</span>
              </div>
            </div>
          </div>
          <div className="flex items-center gap-2">
            <Link href={`/agents/${profile.id}`} className="icon-btn" aria-label="返回 Agent 详情">
              <ArrowLeft className="size-4" />
            </Link>
            <button type="button" className="icon-btn" aria-label="新建对话" onClick={() => void reset()} disabled={sending}>
              <RefreshCw className={`size-4 ${sending ? "animate-spin" : ""}`} />
            </button>
          </div>
        </header>

        <div className="grid min-h-[calc(100vh-210px)] gap-5 xl:grid-cols-[minmax(0,1fr)_300px]">
          <section className="surface flex min-h-[620px] min-w-0 flex-col overflow-hidden">
            <div className="flex-1 space-y-5 overflow-y-auto p-4 sm:p-6" aria-live="polite">
              {messages.length === 0 && <div className="grid min-h-[420px] place-items-center text-center">
                <div>
                  <Bot className="mx-auto size-8 text-neutral-300" />
                  <div className="mt-3 text-sm font-medium text-neutral-700">Agent 已就绪</div>
                </div>
              </div>}
              {messages.map((message) => <article key={message.id} className={`flex gap-3 ${message.role === "user" ? "justify-end" : "justify-start"}`}>
                {message.role === "assistant" && <span className="grid size-8 shrink-0 place-items-center rounded-md bg-brand-50 text-brand-700"><Bot className="size-4" /></span>}
                <div className={`min-w-0 max-w-[86%] ${message.role === "user" ? "bg-neutral-900 text-white" : "border border-line bg-white text-neutral-800"} px-4 py-3`} style={{ borderRadius: 6 }}>
                  <div className="whitespace-pre-wrap break-words text-sm leading-6">{message.content}</div>
                  {message.response && <div className="mt-3 flex flex-wrap gap-x-4 gap-y-1 border-t border-line pt-2 text-[10px] text-neutral-500">
                    <span>{message.response.model}</span>
                    <span>{String(message.response.usage.total_tokens || 0)} tokens</span>
                    <span>{message.response.updatedAt ? new Date(message.response.updatedAt).toLocaleTimeString("zh-CN", { hour12: false }) : ""}</span>
                  </div>}
                </div>
              </article>)}
              {sending && <div className="flex items-center justify-between gap-3 border border-line bg-neutral-50 px-3 py-2 text-xs text-neutral-600" role="status" aria-live="polite" style={{ borderRadius: 6 }}>
                <span className="flex items-center gap-3"><LoaderCircle className="size-4 animate-spin text-brand-600" />Agent 正在生成回答</span>
                <button type="button" className="btn-secondary h-8 px-3 text-xs" onClick={cancelResponse}>
                  <Square className="size-3.5" />取消回答
                </button>
              </div>}
              <div ref={endRef} />
            </div>

            <form onSubmit={(event) => void send(event)} className="border-t border-line bg-neutral-50 p-3 sm:p-4">
              {error && <div className="mb-3 flex items-start gap-2 text-xs text-red-700"><TriangleAlert className="mt-0.5 size-4 shrink-0" />{error}</div>}
              <div className="flex items-end gap-2">
                <textarea
                  value={input}
                  onChange={(event) => setInput(event.target.value)}
                  onKeyDown={handleKeyDown}
                  rows={3}
                  maxLength={20_000}
                  className="min-h-20 flex-1 resize-y border border-line bg-white px-3 py-2 text-sm leading-6 outline-none focus:border-brand-500 focus:ring-1 focus:ring-brand-500"
                  style={{ borderRadius: 6 }}
                  placeholder="向 Agent 提问"
                  disabled={sending}
                />
                <button type="submit" className="icon-btn size-10 shrink-0 bg-brand-600 text-white hover:bg-brand-700" aria-label="发送" disabled={!input.trim() || sending}>
                  {sending ? <LoaderCircle className="size-4 animate-spin" /> : <Send className="size-4" />}
                </button>
              </div>
            </form>
          </section>

          <aside className="space-y-5">
            <section>
              <div className="section-head"><h2 className="section-title">本轮工具</h2></div>
              <div className="surface divide-y divide-line">
                {messages.flatMap((message) => message.response?.toolInvocations || []).slice(-12).map((invocation, index) => <div key={`${invocation.argumentsHash || invocation.name}-${index}`} className="p-3">
                  <div className="flex items-center justify-between gap-2">
                    <span className="flex min-w-0 items-center gap-2 text-xs font-medium"><Wrench className="size-3.5 shrink-0" /><span className="truncate">{invocation.name}</span></span>
                    {invocation.ok ? <CheckCircle2 className="size-3.5 shrink-0 text-emerald-600" /> : <TriangleAlert className="size-3.5 shrink-0 text-red-600" />}
                  </div>
                  <div className="mt-1 font-mono text-[10px] text-neutral-500">{invocation.durationMs ?? 0} ms</div>
                </div>)}
                {!messages.some((message) => message.response?.toolInvocations?.length) && <div className="p-4 text-xs text-neutral-500">暂无调用</div>}
              </div>
            </section>

            <section>
              <div className="section-head"><h2 className="section-title">引用来源</h2></div>
              <div className="surface divide-y divide-line">
                {messages.flatMap((message) => message.response?.sources || []).slice(-12).map((source, index) => <div key={`${source.path}-${source.heading}-${index}`} className="p-3">
                  <div className="flex items-start gap-2"><BookOpen className="mt-0.5 size-3.5 shrink-0 text-neutral-400" /><div className="min-w-0"><div className="break-all font-mono text-[10px] text-neutral-600">{source.path}</div><div className="mt-1 text-[11px] text-neutral-500">{source.heading}</div></div></div>
                </div>)}
                {!messages.some((message) => message.response?.sources?.length) && <div className="p-4 text-xs text-neutral-500">暂无来源</div>}
              </div>
            </section>
          </aside>
        </div>
      </div>}
    </DataState>
  </>;
}
