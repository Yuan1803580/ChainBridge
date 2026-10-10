import { expect, test, type APIRequestContext, type Page } from "@playwright/test";

const apiBase = "http://127.0.0.1:8000/api/v1";

async function loginAs(request: APIRequestContext, page: Page, did: string) {
  const response = await request.post(`${apiBase}/auth/dev-login`, { data: { did } });
  expect(response.ok()).toBeTruthy();
  const session = await response.json() as { accessToken: string; expiresAt: string };
  await page.addInitScript(({ token, expiresAt, currentDid }) => {
    window.localStorage.setItem("agentMarketToken", token);
    window.localStorage.setItem("agentMarketTokenExpiresAt", expiresAt);
    window.localStorage.setItem("agentMarketCurrentDid", currentDid);
  }, { token: session.accessToken, expiresAt: session.expiresAt, currentDid: did });
}

test("dashboard and navigation render", async ({ page }) => {
  await page.goto("/");
  await expect(page.getByRole("heading", { name: "本地多链运行总览" })).toBeVisible();
  await page.goto("/market/capabilities");
  await expect(page.getByRole("heading", { name: "能力市场" })).toBeVisible();
});

test("requester creates a direct order from a live capability schema", async ({ request, page }) => {
  await loginAs(request, page, "did:local:campus-requester");
  const response = await request.get(`${apiBase}/capabilities`);
  const body = await response.json() as { items: Array<{ id: string; slug: string }> };
  const capability = body.items.find((item) => item.slug === "text-summary-v1");
  expect(capability).toBeTruthy();

  await page.goto(`/market/capabilities/${capability!.id}`);
  await expect(page.getByRole("heading", { name: "文本摘要" })).toBeVisible();
  await page.getByRole("tab", { name: "接口 Schema" }).click();
  await expect(page.getByText("Input Schema")).toBeVisible();
  await page.getByRole("button", { name: "直接下单" }).click();

  const dialog = page.getByRole("dialog", { name: "创建直接订单" });
  await dialog.locator("#field-text").fill("Agent 市场通过 DID 验证服务身份，并保留可审计的订单与结算状态。");
  await dialog.locator("#field-maxLength").fill("50");
  await dialog.locator("#field-language").selectOption("zh-CN");
  await dialog.getByRole("button", { name: /确认下单/ }).click();
  await expect(dialog.getByText("订单和模拟结算意向已由后端创建。")).toBeVisible();
});

test("provider submits a bid to a live demand", async ({ request, page }) => {
  await loginAs(request, page, "did:local:text-summary");
  const response = await request.get(`${apiBase}/demands?status=published`);
  const body = await response.json() as { items: Array<{ id: string }> };
  expect(body.items.length).toBeGreaterThan(0);

  await page.goto(`/market/demands/${body.items[0].id}`);
  await page.getByRole("button", { name: "提交竞标" }).click();
  await page.getByLabel("执行方案 *").fill("先分析输入材料，再按验收规则输出可复核的结构化报告和内容哈希。");
  await page.getByRole("button", { name: "签名并提交" }).click();
  await expect(page.getByText("竞标已提交并写入后端。")).toBeVisible();
});
