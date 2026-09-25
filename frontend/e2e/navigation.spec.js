import { expect, test } from "@playwright/test";

test("workbench navigation keeps the shared shell visible", async ({ page }) => {
  await page.goto("/");
  await expect(page.getByRole("heading", { name: "今天先处理什么，依据是什么。" })).toBeVisible();

  await page.getByRole("link", { name: "数据集", exact: true }).click();
  await expect(page).toHaveURL(/\/datasets$/);
  await expect(page.getByText("数据资产", { exact: true }).first()).toBeVisible();
});

test("lazy routes render without browser errors", async ({ page }) => {
  const errors = [];
  page.on("pageerror", (error) => errors.push(error.message));
  for (const [path, title] of [
    ["/training", "训练任务"],
    ["/inference", "推理实验室"],
    ["/models", "模型版本"],
    ["/annotation", "AI 标注"],
    ["/weights", "权重管理"],
    ["/review", "人工复核"],
    ["/feedback", "反馈池"],
    ["/pipelines", "流水线"],
  ]) {
    await page.goto(path);
    await expect(page.locator(".topbar h1")).toHaveText(title);
  }
  expect(errors).toEqual([]);
});
