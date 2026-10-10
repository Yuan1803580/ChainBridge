import { chromium } from "../apps/web/node_modules/@playwright/test/index.mjs";
import { mkdir } from "node:fs/promises";
import path from "node:path";

const baseUrl = process.env.PLATFORM_URL || "http://127.0.0.1:3000";
const outputDir = path.resolve(process.cwd(), "..", "..", "work", "platform-qa");
await mkdir(outputDir, { recursive: true });

const executablePath = process.env.PLAYWRIGHT_CHROMIUM_EXECUTABLE || "C:\\Program Files (x86)\\Microsoft\\Edge\\Application\\msedge.exe";
const browser = await chromium.launch({ headless: true, executablePath });
const checks = [];

async function inspect(name, route, viewport, expectedHeading) {
  const page = await browser.newPage({ viewport });
  const consoleErrors = [];
  page.on("console", (message) => {
    if (message.type() === "error") consoleErrors.push(message.text());
  });
  page.on("pageerror", (error) => consoleErrors.push(error.message));
  const response = await page.goto(`${baseUrl}${route}`, { waitUntil: "networkidle" });
  await page.getByRole("heading", { name: expectedHeading, exact: true }).waitFor();
  const layout = await page.evaluate(() => ({
    viewportWidth: window.innerWidth,
    documentWidth: document.documentElement.scrollWidth,
    bodyWidth: document.body.scrollWidth,
    offenders: [...document.querySelectorAll("body *")]
      .map((element) => {
        const rect = element.getBoundingClientRect();
        return { className: element.className, tag: element.tagName, left: Math.round(rect.left), right: Math.round(rect.right), width: Math.round(rect.width) };
      })
      .filter((item) => item.left < window.innerWidth && item.right > window.innerWidth + 1)
      .slice(0, 8),
  }));
  const screenshot = path.join(outputDir, `${name}.png`);
  await page.screenshot({ path: screenshot, fullPage: false });
  checks.push({
    name,
    route,
    status: response?.status(),
    heading: expectedHeading,
    horizontalOverflow: Math.max(layout.documentWidth, layout.bodyWidth) > layout.viewportWidth + 1,
    overflowElements: layout.offenders,
    consoleErrors,
    screenshot,
  });
  await page.close();
}

await inspect("dashboard-desktop", "/", { width: 1440, height: 1000 }, "运行总览");
await inspect("workflow-desktop", "/workflows/workflow-1001", { width: 1440, height: 1000 }, "跨链协议调研与 Escrow 安全审查");
await inspect("agents-mobile", "/agents", { width: 390, height: 844 }, "Agent 信息平台");
await inspect("routes-mobile", "/routes", { width: 390, height: 844 }, "动态路由");
await inspect("operations-mobile", "/operations", { width: 390, height: 844 }, "Worker 运维");

const interactionPage = await browser.newPage({ viewport: { width: 1280, height: 800 } });
const interactions = [];

await interactionPage.goto(`${baseUrl}/workflows/workflow-1001`, { waitUntil: "networkidle" });
await interactionPage.getByRole("button", { name: "重放 V1 闭环" }).click();
await interactionPage.getByText(/STEP 01/).waitFor();
await interactionPage.waitForTimeout(950);
interactions.push({ name: "workflow replay", passed: await interactionPage.getByText(/STEP 02/).isVisible() });

await interactionPage.goto(`${baseUrl}/workflows/new`, { waitUntil: "networkidle" });
await interactionPage.getByRole("button", { name: "生成执行计划" }).click();
await interactionPage.getByRole("heading", { name: "协调 Agent 计划预览" }).waitFor();
await interactionPage.getByRole("button", { name: /确认并锁定/ }).click();
await interactionPage.getByRole("heading", { name: "预算锁定请求已生成" }).waitFor();
interactions.push({ name: "two-field workflow creation", passed: true });

await interactionPage.goto(`${baseUrl}/agents`, { waitUntil: "networkidle" });
await Promise.all([
  interactionPage.waitForURL(/\/agents\/[a-z0-9-]+$/),
  interactionPage.locator('a[href^="/agents/"]').first().click(),
]);
await interactionPage.getByRole("heading", { name: "文本摘要 Agent", exact: true }).waitFor();
interactions.push({ name: "agent profile navigation", passed: interactionPage.url().includes("/agents/") });
await interactionPage.close();

await browser.close();

const failures = checks.filter((check) => check.status !== 200 || check.horizontalOverflow || check.consoleErrors.length > 0);
const interactionFailures = interactions.filter((interaction) => !interaction.passed);
console.log(JSON.stringify({ checks, interactions, failures: failures.length + interactionFailures.length }, null, 2));
if (failures.length || interactionFailures.length) process.exitCode = 1;
