import { mkdir } from "node:fs/promises";
import path from "node:path";

import { expect, test, type Page, type TestInfo } from "@playwright/test";

const apiBase = "http://127.0.0.1:8000/api/v1";

async function expectCleanPage(page: Page) {
  await expect(page.getByText(/API 不可用|无法连接工作流 API|无法读取工作流|路由 API 不可用|Agent API 不可用|运维 API 不可用/)).toHaveCount(0);
}

async function saveScreenshot(page: Page, testInfo: TestInfo, name: string) {
  const configPath = testInfo.config.configFile;
  if (!configPath) throw new Error("Playwright config path is unavailable");
  const artifacts = path.resolve(path.dirname(configPath), "../../.runtime/local-v1");
  await mkdir(artifacts, { recursive: true });
  await page.evaluate(() => window.scrollTo(0, 0));
  await page.screenshot({
    path: path.join(artifacts, `${name}-${testInfo.project.name}.png`),
    fullPage: true,
  });
}

test("local V1 console uses live workflow, agent, route and relay APIs", async ({ page, request }, testInfo) => {
  const runtimeErrors: string[] = [];
  page.on("pageerror", (error) => runtimeErrors.push(error.message));
  page.on("console", (message) => {
    if (message.type() === "error") runtimeErrors.push(message.text());
  });

  const workflowResponse = await request.get(`${apiBase}/workflows`);
  expect(workflowResponse.ok()).toBeTruthy();
  const workflowBody = await workflowResponse.json() as {
    items: Array<{
      id: string;
      referenceCode: string;
      title: string;
      status: string;
      nodes: Array<{
        nodeKey: string;
        title: string;
        status: string;
        dependsOn: string[];
        attempts: Array<{ agentKey: string; payoutAsset?: string; payoutStatus?: string }>;
        matchRounds?: Array<{ candidates: Array<{ agentKey: string; status: string }> }>;
      }>;
      events: Array<{ eventType: string }>;
      ledger: unknown[];
      ledgerSummary: { balanced: boolean };
    }>;
  };
  const workflow = workflowBody.items.find((item) => (
    item.status === "COMPLETED"
    && item.nodes.some((node) => node.attempts.some((attempt) => attempt.payoutStatus === "SWAPPED"))
  ))
    || workflowBody.items.find((item) => item.nodes.some((node) => node.matchRounds?.length))
    || workflowBody.items.find((item) => item.title === "Local V1 B-success C-failure D-retry workflow")
    || workflowBody.items.find((item) => item.referenceCode === "WF-1001");
  expect(workflow).toBeTruthy();
  expect(workflow!.nodes.length).toBeGreaterThan(0);
  expect(workflow!.nodes.find((node) => node.nodeKey === "N2")?.dependsOn).toEqual(["N1"]);
  expect(workflow).toHaveProperty("events");
  expect(workflow).toHaveProperty("ledger");
  expect(workflow!.ledgerSummary.balanced).toBe(true);

  await page.goto("/");
  await expect(page.getByRole("heading", { name: "本地多链运行总览" })).toBeVisible();
  await expect(page.getByText(workflow!.referenceCode, { exact: true }).first()).toBeVisible();
  await expectCleanPage(page);
  await saveScreenshot(page, testInfo, "dashboard");

  await page.goto(`/workflows/${workflow!.id}`);
  await expect(page.getByText(workflow!.referenceCode, { exact: true }).first()).toBeVisible();
  await expect(page.getByRole("heading", { name: "节点依赖图" })).toBeVisible();
  await expect(page.getByText("本地四链可执行证明", { exact: true })).toBeVisible();
  await page.getByRole("button", { name: /Verify evidence and publish report/ }).click();
  await expect(page.getByTestId("candidate-Agent-C1")).toContainText("FAILED");
  await expect(page.getByTestId("candidate-Agent-D1")).toContainText("SETTLED");
  await expect(page.getByText("NOT_EXECUTED", { exact: true })).toBeVisible();
  await expect(page.getByText("USDC → DTK", { exact: true })).toBeVisible();
  await expect(page.getByText("27.2 DTK", { exact: true }).first()).toBeVisible();
  await expect(page.getByText("SWAPPED", { exact: true }).first()).toBeVisible();
  await expectCleanPage(page);
  await saveScreenshot(page, testInfo, "workflow-detail");

  await page.goto("/agents");
  await expect(page.getByRole("heading", { name: "Agent 信息化平台" })).toBeVisible();
  for (const agent of ["Agent-B1", "Agent-C1", "Agent-D1"]) {
    await expect(page.getByText(agent, { exact: true })).toBeVisible();
  }
  await expectCleanPage(page);

  await page.goto("/routes");
  await expect(page.getByRole("heading", { name: "动态链路注册表" })).toBeVisible();
  for (const chainId of ["31337", "31338", "31339", "31340"]) {
    await expect(page.locator("main")).toContainText(chainId);
  }
  for (const conversion of ["USDC → BTK", "USDC → CTK", "USDC → DTK"]) {
    await expect(page.getByText(conversion, { exact: true })).toBeVisible();
  }
  await expect(page.locator("main")).not.toContainText("11155111");
  await expect(page.locator("main")).not.toContainText("421614");
  await expectCleanPage(page);

  await page.goto("/operations");
  await expect(page.getByRole("heading", { name: "Worker 与 Relay 运维" })).toBeVisible();
  await expect(page.getByText("DISPATCH", { exact: true }).first()).toBeVisible();
  await expect(page.getByText("31337 → 31338", { exact: true }).first()).toBeVisible();
  await expect(page.locator("tbody tr").filter({ hasText: "DISPATCH" }).first()).toContainText("DELIVERED");
  await expectCleanPage(page);

  expect(runtimeErrors).toEqual([]);
});
