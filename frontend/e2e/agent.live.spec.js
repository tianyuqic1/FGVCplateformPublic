import { readFile } from "node:fs/promises";
import { expect, test } from "@playwright/test";

test("live Agent history and drawer render against the running project", async ({ page }) => {
  test.skip(!process.env.FINEVISION_AGENT_LIVE_CREDENTIALS, "explicit opt-in required for the local running stack");
  const credentials = JSON.parse(await readFile(process.env.FINEVISION_AGENT_LIVE_CREDENTIALS, "utf8"));
  const errors = [];
  page.on("pageerror", error => errors.push(error.message));
  const login = await page.request.post("/api/auth/login", { data: { email: credentials.email, password: credentials.password } });
  expect(login.ok()).toBeTruthy();
  await page.goto("/agent");
  await expect(page.locator(".topbar h1")).toHaveText("实验助手");
  await page.getByRole("button", { name: /Agent 真实模型验收/ }).first().click();
  await expect(page.locator(".agent-prose")).toContainText("Worker");
  await expect(page.getByText("已完成", { exact: true })).toBeVisible();
  await page.reload();
  await expect(page.locator(".agent-prose")).toBeVisible();
  await page.locator(".agent-trace>summary").click();
  await expect(page.locator(".agent-trace")).toContainText("查询完成");
  await page.screenshot({ path: "test-results/agent-live.png", fullPage: true });
  await page.getByRole("link", { name: "训练任务", exact: true }).click();
  await page.getByRole("button", { name: "打开实验助手" }).click();
  await expect(page.getByRole("dialog", { name: "实验助手" })).toBeVisible();
  await expect(page.getByText("当前页面：/training")).toBeVisible();
  expect(errors).toEqual([]);
});

test("live completed training opens test error analysis without automatic execution", async ({ page }) => {
  test.skip(!process.env.FINEVISION_AGENT_LIVE_CREDENTIALS || !process.env.FINEVISION_TEST_ERRORS_RESULTS, "explicit opt-in required");
  const credentials = JSON.parse(await readFile(process.env.FINEVISION_AGENT_LIVE_CREDENTIALS, "utf8"));
  const result = JSON.parse(await readFile(process.env.FINEVISION_TEST_ERRORS_RESULTS, "utf8"));
  const login = await page.request.post("/api/auth/login", { data: { email: credentials.email, password: credentials.password } });
  expect(login.ok()).toBeTruthy();
  let modelRequests = 0;
  page.on("request", request => { if (request.method() === "POST" && new URL(request.url()).pathname === "/api/agent/runs") modelRequests++; });
  await page.goto(`/training/${result.training.id}`);
  await page.getByRole("link", { name: "分析测试集错分" }).click();
  await expect(page).toHaveURL(/\/agent$/);
  await expect(page.getByRole("textbox", { name: "向实验助手提问" })).toHaveValue(new RegExp(result.training.id));
  expect(modelRequests).toBe(0);
  await page.screenshot({ path: "test-results/test-error-analysis-entry.png", fullPage: true });
});
