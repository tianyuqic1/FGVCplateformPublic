import { expect, test } from "@playwright/test";

test.beforeEach(async ({ page }) => {
  // These shell/navigation tests use auth fixtures, not real session cookies.
  // Keep unstubbed APIs offline so a running local backend cannot log them out
  // (or receive writes); each test's specific routes take precedence.
  await page.route("**/api/**", route => route.fulfill({
    status: 503,
    contentType: "application/json",
    body: JSON.stringify({ detail: "API unavailable in navigation test fixture" }),
  }));
});

async function signedInAs(page, role = "admin") {
  await page.route("**/api/auth/me", route => route.fulfill({ status: 200, contentType: "application/json", body: JSON.stringify({
    user: { id: "browser-test-user", email: "browser@example.com", display_name: "浏览器测试用户", role, status: "active" },
    csrf_token: "browser-test-csrf",
  }) }));
}

test("workbench navigation keeps the shared shell visible", async ({ page }) => {
  await signedInAs(page);
  await page.goto("/");
  await expect(page.getByRole("heading", { name: "平台概览" })).toBeVisible();

  await page.getByRole("link", { name: "数据集", exact: true }).click();
  await expect(page).toHaveURL(/\/datasets$/);
  await expect(page.getByText("数据资产", { exact: true }).first()).toBeVisible();
});

test("lazy routes render without browser errors", async ({ page }) => {
  await signedInAs(page);
  const errors = [];
  page.on("pageerror", (error) => errors.push(error.message));
  for (const [path, title] of [
    ["/training", "训练任务"],
    ["/inference", "模型推理"],
    ["/models", "模型版本"],
    ["/annotation", "AI 辅助标注"],
    ["/weights", "预训练权重"],
    ["/review", "人工复核"],
    ["/feedback", "反馈池"],
    ["/pipelines", "任务流水线"],
  ]) {
    await page.goto(path);
    await expect(page.locator(".topbar h1")).toHaveText(title);
  }
  expect(errors).toEqual([]);
});

test("anonymous visitors see login and cannot directly open business pages", async ({ page }) => {
  await page.route("**/api/auth/me", route => route.fulfill({ status: 401, contentType: "application/json", body: '{"detail":"请先登录"}' }));
  await page.goto("/training");
  await expect(page).toHaveURL(/\/login$/);
  await expect(page.getByRole("heading", { name: "登录工作台" })).toBeVisible();
});

test("approved users can log in and return to their requested page", async ({ page }) => {
  await page.route("**/api/auth/me", route => route.fulfill({ status: 401, contentType: "application/json", body: '{"detail":"请先登录"}' }));
  await page.route("**/api/auth/login", route => {
    expect(route.request().postDataJSON()).toMatchObject({ email: "operator@example.com", password: "correct horse battery staple" });
    return route.fulfill({ status: 200, contentType: "application/json", body: JSON.stringify({
      user: { id: "operator-id", email: "operator@example.com", display_name: "业务人员", role: "business", status: "active" },
      csrf_token: "operator-csrf",
    }) });
  });
  await page.goto("/training");
  await page.getByPlaceholder("name@example.com").fill("operator@example.com");
  await page.getByPlaceholder("输入密码").fill("correct horse battery staple");
  await page.getByRole("button", { name: "登录", exact: true }).click();
  await expect(page).toHaveURL(/\/training$/);
  await expect(page.locator(".topbar h1")).toHaveText("训练任务");
});

test("annotators cannot open training or account administration", async ({ page }) => {
  await signedInAs(page, "annotator");
  await page.goto("/training");
  await expect(page.getByRole("heading", { name: "此页面不在你的工作权限内" })).toBeVisible();
  await expect(page.getByRole("link", { name: "训练任务" })).toHaveCount(0);
  await page.goto("/admin/users");
  await expect(page.getByRole("heading", { name: "此页面不在你的工作权限内" })).toBeVisible();
});

test("administrator approves a registered annotator from the user management page", async ({ page }) => {
  await signedInAs(page);
  let saved = false;
  const account = { id: "user-2", email: "labeler@example.com", display_name: "测试标注员", role: "business", status: "pending" };
  await page.route("**/api/auth/users?*", route => route.fulfill({ status: 200, contentType: "application/json", body: JSON.stringify({ items: [{ ...account, role: saved ? "annotator" : "business", status: saved ? "active" : "pending" }], total: 1, page: 1, page_size: 20 }) }));
  await page.route("**/api/auth/users/user-2", route => {
    const payload = route.request().postDataJSON();
    expect(payload).toMatchObject({ role: "annotator", status: "active", reason: "审核通过" });
    expect(route.request().headers()["x-csrf-token"]).toBe("browser-test-csrf");
    saved = true;
    return route.fulfill({ status: 200, contentType: "application/json", body: JSON.stringify({ user: { ...account, ...payload } }) });
  });
  await page.goto("/admin/users");
  await expect(page.getByRole("heading", { name: "用户管理", level: 2 })).toBeVisible();
  await page.getByLabel("测试标注员的角色").selectOption("annotator");
  await page.getByLabel("测试标注员的状态").selectOption("active");
  await page.getByLabel("测试标注员的变更原因").fill("审核通过");
  await page.getByRole("button", { name: "保存变更" }).click();
  await expect(page.getByText("已更新 测试标注员")).toBeVisible();
  expect(saved).toBe(true);
});
