"use client";

import { useCallback, useEffect, useState } from "react";
import type { ApiResult } from "./types";

export function useResource<T>(loader: () => Promise<ApiResult<T>>, dependencies: unknown[] = []) {
  const [state, setState] = useState<{ data?: T; source?: "api" | "mock"; loading: boolean; error?: string }>({ loading: true });

  const reload = useCallback(() => {
    let active = true;
    setState((current) => ({ ...current, loading: true, error: undefined }));
    loader()
      .then((result) => active && setState({ data: result.data, source: result.source, loading: false }))
      .catch((error: unknown) => active && setState({ loading: false, error: error instanceof Error ? error.message : "加载失败" }));
    return () => { active = false; };
  // Call sites pass stable primitive dependencies; loader identity is intentionally excluded.
  // eslint-disable-next-line react-hooks/exhaustive-deps
  }, dependencies);

  useEffect(() => reload(), [reload]);
  useEffect(() => {
    const handleAuthChange = () => reload();
    window.addEventListener("agent-market-auth-changed", handleAuthChange);
    return () => window.removeEventListener("agent-market-auth-changed", handleAuthChange);
  }, [reload]);
  return { ...state, reload };
}
