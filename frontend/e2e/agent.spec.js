import { expect, test } from "@playwright/test";

const conversation = { id: "11111111-1111-4111-8111-111111111111", title: "训练诊断", updated_at: "2026-10-24T00:00:00Z" };
const runId = "22222222-2222-4222-8222-222222222222";
const operationId = "33333333-3333-4333-8333-333333333333";
const preview = { dataset_version_id: "44444444-4444-4444-8444-444444444444", backbone_key: "dinov3_vits16", head_config: { epochs: 2, batch_size: 8 } };
const baseRun = { id: runId, conversation_id: conversation.id, input: "帮我准备训练", status: "waiting_confirmation", output: "", rounds: 1, tools: 2, input_tokens: 10, output_tokens: 20, proposal: { operation_id: operationId, arguments: preview, call: { function: { name: "training.submit_plan" } } } };

async function fixture(page, { role = "business", run = baseRun } = {}) {
  let currentRun = run;
  const actions = [];
  await page.route("**/api/**", async route => {
    const request = route.request();
    const path = new URL(request.url()).pathname;
    const respond = payload => route.fulfill({ status: 200, contentType: "application/json", body: JSON.stringify(payload) });
    if (path === "/api/auth/me") return respond({ user: { id: "test-user", role, display_name: "测试用户", status: "active" }, csrf_token: "csrf-test" });
    if (path === "/api/agent/conversations") return respond(request.method() === "POST" ? conversation : { items: [conversation] });
    if (path === `/api/agent/conversations/${conversation.id}`) return respond({ items: currentRun ? [currentRun] : [] });
    if (path.endsWith("/events")) return request.url().includes("format=json") ? respond({ items: [] }) : route.fulfill({ status: 200, contentType: "text/event-stream", body: ": heartbeat\n\n" });
    if (path === "/api/agent/watches") return respond({ items: [] });
    if (path === "/api/agent/runs") { actions.push({ path, body: request.postDataJSON(), csrf: request.headers()["x-csrf-token"] }); currentRun = { ...baseRun, status: "succeeded", proposal: null, output: "已查询到真实平台数据" }; return respond(currentRun); }
    if (path.endsWith("/confirm") || path.endsWith("/cancel")) { actions.push({ path, body: request.postDataJSON(), csrf: request.headers()["x-csrf-token"] }); currentRun = { ...baseRun, status: "cancelled", proposal: null }; return respond({ status: "queued" }); }
    return route.fulfill({ status: 503, contentType: "application/json", body: '{"detail":"offline fixture"}' });
  });
  return actions;
}

test("training writes require the visible parameter confirmation", async ({ page }) => {
  const actions = await fixture(page);
  await page.goto("/agent");
  await page.getByRole("button", { name: /训练诊断/ }).click();
  await expect(page.getByText("等待你确认", { exact: true })).toBeVisible();
  await expect(page.locator(".agent-proposal pre")).toContainText('"epochs": 2');
  expect(actions).toHaveLength(0);
  await page.getByRole("button", { name: "确认执行", exact: true }).click();
  await expect.poll(() => actions.length).toBe(1);
  expect(actions[0]).toMatchObject({ path: `/api/agent/runs/${runId}/confirm`, body: { operation_id: operationId }, csrf: "csrf-test" });
});

test("refresh restores the selected conversation without resubmission", async ({ page }) => {
  const actions = await fixture(page, { run: { ...baseRun, status: "succeeded", output: "数据集 bird 已准备好", proposal: null } });
  await page.goto("/agent");
  await page.getByRole("button", { name: /训练诊断/ }).click();
  await expect(page.getByText("数据集 bird 已准备好", { exact: true })).toBeVisible();
  await page.reload();
  await expect(page.getByText("数据集 bird 已准备好", { exact: true })).toBeVisible();
  expect(actions).toHaveLength(0);
});

test("new questions include a stable request key and show the result", async ({ page }) => {
  const actions = await fixture(page, { run: null });
  await page.goto("/agent");
  await page.getByRole("textbox", { name: "向实验助手提问" }).fill("查询数据");
  await page.getByRole("button", { name: "发送", exact: true }).click();
  await expect(page.getByText("已查询到真实平台数据", { exact: true })).toBeVisible();
  expect(actions).toHaveLength(1);
  expect(actions[0].body.request_key).toMatch(/^[0-9a-f-]{36}$/);
  expect(actions[0].csrf).toBe("csrf-test");
});

test("the drawer opens from training pages and can be closed", async ({ page }) => {
  await fixture(page);
  await page.goto("/training");
  await page.getByRole("button", { name: "打开实验助手" }).click();
  const drawer = page.getByRole("dialog", { name: "实验助手" });
  await expect(drawer).toBeVisible();
  await expect(drawer.getByText("当前页面：/training")).toBeVisible();
  await drawer.getByRole("button", { name: "关闭助手" }).click();
  await expect(drawer).not.toBeVisible();
});

test("annotators do not receive the experiment assistant entry", async ({ page }) => {
  await fixture(page, { role: "annotator" });
  await page.goto("/agent");
  await expect(page.getByText("此页面不在你的工作权限内", { exact: true })).toBeVisible();
  await expect(page.getByRole("button", { name: "打开实验助手" })).not.toBeVisible();
});

test("retry after a lost response preserves the original request key", async ({ page }) => {
  await fixture(page, { run: null });
  const submissions = [];
  await page.route("**/api/agent/runs", route => {
    submissions.push(route.request().postDataJSON());
    return submissions.length === 1 ? route.abort("failed") : route.fallback();
  });
  await page.goto("/agent");
  await page.getByRole("textbox", { name: "向实验助手提问" }).fill("重试同一个问题");
  await page.getByRole("button", { name: "发送", exact: true }).click();
  await expect(page.getByRole("alert")).toBeVisible();
  await page.getByRole("button", { name: "发送", exact: true }).click();
  await expect(page.getByText("已查询到真实平台数据", { exact: true })).toBeVisible();
  expect(submissions).toHaveLength(2);
  expect(submissions[0]).toEqual(submissions[1]);
});

test("a training analysis prompt opens a fresh conversation with the correct run", async ({ page }) => {
  const actions = await fixture(page);
  await page.addInitScript(() => sessionStorage.setItem("finevision:agent:test-user", "11111111-1111-4111-8111-111111111111"));
  await page.goto("/agent");
  await expect(page.getByText("等待你确认", { exact: true })).toBeVisible();
  // Use the real router navigation from a training detail page without making a model call.
  await page.goto(`/training/${runId}`);
  await page.route(`**/api/training-runs/${runId}`, route => route.fulfill({ status: 200, contentType: "application/json", body: JSON.stringify({ training_run: { id: runId, status: "succeeded", name: "已完成训练", dataset_id: "test-dataset", dataset_version_id: preview.dataset_version_id, metrics: {} } }) }));
  await page.reload();
  await page.getByRole("link", { name: "分析测试集错分" }).click();
  await expect(page).toHaveURL(/\/agent$/);
  await expect(page.getByRole("textbox", { name: "向实验助手提问" })).toHaveValue(new RegExp(runId));
  await expect(page.getByText("等待你确认", { exact: true })).not.toBeVisible();
  expect(actions).toHaveLength(0);
});
