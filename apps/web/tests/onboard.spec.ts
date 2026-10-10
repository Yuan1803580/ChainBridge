import { expect, test } from "@playwright/test";
import { mkdir } from "node:fs/promises";
import path from "node:path";

test("self-service Agent onboarding page is usable", async ({ page, request }, testInfo) => {
  const runtimeErrors: string[] = [];
  page.on("pageerror", (error) => runtimeErrors.push(error.message));
  page.on("console", (message) => {
    if (message.type() === "error") runtimeErrors.push(message.text());
  });

  const skill = await request.get("/skills/agent-market-onboarding/SKILL.md");
  expect(skill.ok()).toBeTruthy();
  expect(await skill.text()).toContain("name: agent-market-onboarding");

  await page.goto("/onboard");
  await expect(page.getByRole("heading", { name: "Agent 接入" })).toBeVisible();
  await expect(page.getByRole("heading", { name: "把这个链接发送给你的 Agent" })).toBeVisible();
  await expect(page.getByLabel("Agent 接入技能链接")).toHaveValue(/^http:\/\/(?:127\.0\.0\.1|localhost):3000\/skills\/agent-market-onboarding\/SKILL\.md$/);
  await expect(page.getByLabel("Agent 接入状态码")).toBeVisible();
  await expect(page.getByText("DeepSeek API Key")).toHaveCount(0);
  await expect(page.getByText("Runner 访问令牌")).toHaveCount(0);

  const horizontalOverflow = await page.evaluate(() => document.documentElement.scrollWidth - document.documentElement.clientWidth);
  expect(horizontalOverflow).toBeLessThanOrEqual(1);
  expect(runtimeErrors).toEqual([]);

  const artifacts = path.resolve(testInfo.config.rootDir, "../../../.runtime/local-v1");
  await mkdir(artifacts, { recursive: true });
  await page.screenshot({
    path: path.join(artifacts, `onboard-${testInfo.project.name}.png`),
    fullPage: true,
  });
});
