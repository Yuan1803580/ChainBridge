import { CheckCircle2, ExternalLink, FileJson2 } from "lucide-react";
import type { ReactNode } from "react";

type ResultNode = {
  nodeKey: string;
  title: string;
  status: string;
  output?: unknown;
};

const fieldLabels: Record<string, string> = {
  paperTitle: "论文标题",
  title: "标题",
  authors: "作者",
  arxivUrl: "arXiv 地址",
  url: "访问地址",
  researchQuestion: "研究问题",
  researchQuestions: "研究问题",
  coreMethod: "核心方法",
  method: "核心方法",
  methods: "核心方法",
  mainConclusions: "主要结论",
  mainFindings: "主要结论",
  conclusions: "主要结论",
  keyFindings: "关键发现",
  keyPoints: "关键要点",
  finding: "结论",
  evidence: "依据",
  type: "内容类型",
  analysis: "分析",
  interpretation: "分析说明",
  factsFromFirst3Pages: "论文原文事实",
  limitations: "局限性",
  readScope: "阅读范围",
  sourceAssessment: "来源核验",
  verifiedArxivSource: "arXiv 来源已核验",
  verifiedPdfSource: "PDF 来源已核验",
  paperVersion: "论文版本",
  published: "首次发布",
  updated: "最近更新",
  primaryCategory: "主要分类",
  score: "结果评分",
  fileCount: "文件数量",
};

const hiddenFields = new Set([
  "summary",
  "sources",
  "sourceLinks",
  "toolInvocations",
  "score",
]);

function isRecord(value: unknown): value is Record<string, unknown> {
  return typeof value === "object" && value !== null && !Array.isArray(value);
}

function parseOutput(output: unknown): unknown {
  if (typeof output !== "string") return output;
  try {
    return JSON.parse(output) as unknown;
  } catch {
    return output;
  }
}

function summaryFrom(output: unknown): string | null {
  const parsed = parseOutput(output);
  if (typeof parsed === "string") return parsed.trim() || null;
  if (!isRecord(parsed)) return null;
  for (const key of ["summary", "answer", "result", "description"]) {
    const value = parsed[key];
    if (typeof value === "string" && value.trim()) return value.trim();
  }
  return null;
}

function sourceFrom(value: unknown): { label: string; href?: string } | null {
  if (typeof value === "string" && value.trim()) {
    const text = value.trim();
    return /^https?:\/\//i.test(text) ? { label: text, href: text } : { label: text };
  }
  if (!isRecord(value)) return null;
  const href = [value.url, value.href, value.arxivUrl, value.pdfUrl]
    .find((item): item is string => typeof item === "string" && /^https?:\/\//i.test(item));
  const label = [value.title, value.label, value.path, href]
    .find((item): item is string => typeof item === "string" && Boolean(item.trim()));
  return label ? { label, href } : null;
}

function sourcesFrom(output: unknown): Array<{ label: string; href?: string }> {
  const parsed = parseOutput(output);
  if (!isRecord(parsed)) return [];
  const candidates: unknown[] = [];
  for (const key of ["sources", "sourceLinks"]) {
    const value = parsed[key];
    if (Array.isArray(value)) candidates.push(...value);
    else if (value !== undefined && value !== null) candidates.push(value);
  }
  if (parsed.arxivUrl) candidates.push(parsed.arxivUrl);
  const seen = new Set<string>();
  return candidates.flatMap((value) => {
    const source = sourceFrom(value);
    if (!source) return [];
    const identity = source.href || source.label;
    if (seen.has(identity)) return [];
    seen.add(identity);
    return [source];
  });
}

function labelFor(key: string): string {
  return fieldLabels[key] || key.replace(/([a-z0-9])([A-Z])/g, "$1 $2");
}

function renderValue(value: unknown): ReactNode {
  if (value === null || value === undefined || value === "") return <span className="text-neutral-400">未提供</span>;
  if (typeof value === "boolean") return value ? "是" : "否";
  if (typeof value === "number") return String(value);
  if (typeof value === "string") {
    if (/^https?:\/\//i.test(value)) {
      return <a className="inline-flex max-w-full items-center gap-1 break-all text-blue-700 hover:underline" href={value} target="_blank" rel="noreferrer">{value}<ExternalLink className="size-3 shrink-0" /></a>;
    }
    return <span className="whitespace-pre-wrap leading-6">{value}</span>;
  }
  if (Array.isArray(value)) {
    if (value.every((item) => ["string", "number", "boolean"].includes(typeof item))) {
      return <ul className="space-y-1.5">{value.map((item, index) => <li key={`${String(item)}-${index}`} className="flex gap-2 leading-6"><span className="mt-2 size-1.5 shrink-0 rounded-full bg-brand-500" /><span>{String(item)}</span></li>)}</ul>;
    }
    return <div className="space-y-3">{value.map((item, index) => <div key={index} className="border-l-2 border-emerald-200 pl-3">{renderValue(item)}</div>)}</div>;
  }
  if (isRecord(value)) {
    return <dl className="divide-y divide-line bg-neutral-50 px-3">{Object.entries(value).map(([key, item]) => <div key={key} className="grid gap-1 py-2.5 sm:grid-cols-[120px_minmax(0,1fr)] sm:gap-3"><dt className="text-[11px] font-medium text-neutral-500">{labelFor(key)}</dt><dd className="min-w-0 text-xs text-neutral-700">{renderValue(item)}</dd></div>)}</dl>;
  }
  return String(value);
}

export function WorkflowCompletionSummary({ nodes }: { nodes: ResultNode[] }) {
  const completed = nodes.filter((node) => node.output !== undefined && node.output !== null);
  if (!completed.length) return null;
  return <div className="border-t border-line bg-emerald-50/60 px-5 py-4">
    <div className="flex items-start gap-3">
      <CheckCircle2 className="mt-0.5 size-5 shrink-0 text-emerald-700" />
      <div className="min-w-0">
        <h3 className="text-sm font-semibold text-emerald-950">用户交付摘要</h3>
        {completed.length === 1 ? <p className="mt-2 whitespace-pre-wrap text-sm leading-6 text-neutral-700">{summaryFrom(completed[0].output) || "任务已经完成，详细交付内容见下方。"}</p> : <div className="mt-2 space-y-2">{completed.map((node) => <div key={node.nodeKey} className="text-sm leading-6 text-neutral-700"><b>{node.title}：</b>{summaryFrom(node.output) || "该节点已完成。"}</div>)}</div>}
      </div>
    </div>
  </div>;
}

export function WorkflowNodeResult({ output }: { output: unknown }) {
  const parsed = parseOutput(output);
  const sources = sourcesFrom(parsed);
  const fields = isRecord(parsed)
    ? Object.entries(parsed).filter(([key, value]) => !hiddenFields.has(key) && value !== undefined && value !== null)
    : [];

  return <div className="mt-3 border-l-2 border-brand-500 bg-white pl-4">
    {fields.length > 0 && <dl className="divide-y divide-line">{fields.map(([key, value]) => <div key={key} className="grid gap-1 py-3 sm:grid-cols-[112px_minmax(0,1fr)] sm:gap-4"><dt className="text-[11px] font-medium text-neutral-500">{labelFor(key)}</dt><dd className="min-w-0 text-sm text-neutral-800">{renderValue(value)}</dd></div>)}</dl>}
    {sources.length > 0 && <div className="border-t border-line py-3"><div className="text-[11px] font-medium text-neutral-500">参考来源</div><ul className="mt-2 space-y-1.5 text-xs">{sources.map((source) => <li key={source.href || source.label}>{source.href ? <a className="inline-flex max-w-full items-center gap-1 break-all text-blue-700 hover:underline" href={source.href} target="_blank" rel="noreferrer">{source.label}<ExternalLink className="size-3 shrink-0" /></a> : <span>{source.label}</span>}</li>)}</ul></div>}
    <details className="border-t border-line py-3">
      <summary className="flex cursor-pointer list-none items-center gap-2 text-[11px] font-medium text-neutral-500 hover:text-neutral-800"><FileJson2 className="size-3.5" />查看原始结果</summary>
      <pre className="mt-3 max-h-72 overflow-auto whitespace-pre-wrap break-words bg-neutral-50 p-3 text-[11px] leading-5 text-neutral-700">{typeof output === "string" ? output : JSON.stringify(output, null, 2)}</pre>
    </details>
  </div>;
}
