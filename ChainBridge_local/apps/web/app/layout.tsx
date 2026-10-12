import type { Metadata } from "next";
import "./globals.css";
import { AppShell } from "@/components/app-shell";

export const metadata: Metadata = {
  title: "多链 Agent 工作流控制台",
  description: "Agent-first 动态路由、节点审核、跨链结算与信息平台"
};

export default function RootLayout({ children }: Readonly<{ children: React.ReactNode }>) {
  return <html lang="zh-CN"><body><AppShell>{children}</AppShell></body></html>;
}
