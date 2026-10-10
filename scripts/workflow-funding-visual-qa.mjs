import { chromium } from "../apps/web/node_modules/@playwright/test/index.mjs";
import { mkdir } from "node:fs/promises";
import path from "node:path";

const baseUrl = process.env.PLATFORM_URL || "http://127.0.0.1:3000";
const outputDir = path.resolve(process.cwd(), ".runtime", "local-v1");
const executablePath = process.env.PLAYWRIGHT_CHROMIUM_EXECUTABLE
  || "C:\\Program Files (x86)\\Microsoft\\Edge\\Application\\msedge.exe";

await mkdir(outputDir, { recursive: true });
const browser = await chromium.launch({ headless: true, executablePath });
const results = [];

for (const [name, viewport] of [
  ["desktop", { width: 1440, height: 1000 }],
  ["mobile", { width: 390, height: 844 }],
]) {
  const page = await browser.newPage({ viewport });
  const runtimeErrors = [];
  page.on("pageerror", (error) => runtimeErrors.push(error.message));
  page.on("console", (message) => {
    if (message.type() === "error") runtimeErrors.push(message.text());
  });

  const response = await page.goto(`${baseUrl}/workflows/new`, { waitUntil: "networkidle" });
  await page.getByRole("heading", { name: "创建动态工作流", exact: true }).waitFor();
  await page.getByRole("button", { name: "链上资金", exact: true }).click();
  await page.getByText("Chain A (local) · 31337", { exact: true }).waitFor();
  await page.getByText("未检测到 MetaMask", { exact: true }).first().waitFor();

  const layout = await page.evaluate(() => {
    const clippedControls = [...document.querySelectorAll("button, input")]
      .filter((element) => element.scrollWidth > element.clientWidth + 1)
      .map((element) => ({
        tag: element.tagName,
        text: element.textContent?.trim() || element.getAttribute("value") || "",
        clientWidth: element.clientWidth,
        scrollWidth: element.scrollWidth,
      }));
    return {
      viewportWidth: window.innerWidth,
      documentWidth: document.documentElement.scrollWidth,
      bodyWidth: document.body.scrollWidth,
      clippedControls,
    };
  });

  const screenshot = path.join(outputDir, `workflow-funding-${name}.png`);
  await page.screenshot({ path: screenshot, fullPage: true });
  results.push({
    name: `${name}-input`,
    status: response?.status(),
    horizontalOverflow: Math.max(layout.documentWidth, layout.bodyWidth) > layout.viewportWidth + 1,
    clippedControls: layout.clippedControls,
    runtimeErrors,
    screenshot,
  });

  await page.getByRole("button", { name: "生成执行计划", exact: true }).click();
  await page.getByRole("heading", { name: "协调 Agent 计划预览", exact: true }).waitFor();
  await page.getByText("链上交易确认", { exact: true }).waitFor();
  const planLayout = await page.evaluate(() => {
    const clippedControls = [...document.querySelectorAll("button, input")]
      .filter((element) => element.scrollWidth > element.clientWidth + 1)
      .map((element) => ({
        tag: element.tagName,
        text: element.textContent?.trim() || element.getAttribute("value") || "",
        clientWidth: element.clientWidth,
        scrollWidth: element.scrollWidth,
      }));
    return {
      viewportWidth: window.innerWidth,
      documentWidth: document.documentElement.scrollWidth,
      bodyWidth: document.body.scrollWidth,
      clippedControls,
    };
  });
  const planScreenshot = path.join(outputDir, `workflow-funding-plan-${name}.png`);
  await page.screenshot({ path: planScreenshot, fullPage: true });
  results.push({
    name: `${name}-plan`,
    status: response?.status(),
    horizontalOverflow: Math.max(planLayout.documentWidth, planLayout.bodyWidth) > planLayout.viewportWidth + 1,
    clippedControls: planLayout.clippedControls,
    runtimeErrors: [...runtimeErrors],
    screenshot: planScreenshot,
  });
  await page.close();
}

await browser.close();
console.log(JSON.stringify(results, null, 2));
if (results.some((result) => (
  result.status !== 200
  || result.horizontalOverflow
  || result.clippedControls.length > 0
  || result.runtimeErrors.length > 0
))) process.exitCode = 1;
