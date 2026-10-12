"use client";

import { AlertCircle, RefreshCw } from "lucide-react";

export default function ErrorPage({ error, reset }: { error: Error & { digest?: string }; reset: () => void }) {
  return <div className="surface grid min-h-[50vh] place-items-center px-6"><div className="max-w-lg text-center"><AlertCircle className="mx-auto mb-3 size-8 text-red-500" /><h1 className="text-lg font-semibold">页面暂时无法显示</h1><p className="mt-2 text-sm text-neutral-500">{error.message || "出现了未预期的错误。"}</p><button className="btn-secondary mt-5" onClick={reset}><RefreshCw className="size-4" />重新加载</button></div></div>;
}
