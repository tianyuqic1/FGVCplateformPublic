import { expect, test } from "@playwright/test";

const worker = {
  id: "training-gpu-a", name: "训练实例 A", kind: "training", node_id: "gpu-a", backend: "pytorch",
  device: "cuda", version: "test", capacity: 1, readiness: "ready", connection: "online",
  active_count: 1, can_accept: false, received_at: "2026-09-26T10:00:00Z", tasks: [],
};
test.beforeEach(async ({ page }) => {
  await page.route("**/api/**", route => route.fulfill({ status: 503, json: { detail: "isolated test" } }));
  await page.route("**/api/auth/me", route => route.fulfill({ json: { user: { id: "test", display_name: "业务人员", role: "business", status: "active" }, csrf_token: "test" } }));
  await page.route("**/api/workers?*", route => {
    const url = new URL(route.request().url());
    const offset = Number(url.searchParams.get("offset"));
    return route.fulfill({ json: {
      items: [{ ...worker, name: offset ? "训练实例 B" : "训练实例 A", id: offset ? "training-gpu-b" : worker.id }],
      summary: { total: 7, available: 6, busy: 1, abnormal: 0 },
      pagination: { total: 7, offset, limit: 6 }, server_time: "2026-09-26T10:00:05Z",
    } });
  });
  await page.route("**/api/workers/training-gpu-a", route => route.fulfill({ json: { worker, server_time: "2026-09-26T10:00:05Z" } }));
});

test("worker pagination, filters, details and reload retain context", async ({ page }) => {
  await page.goto("/workers");
  await expect(page.getByRole("heading", { name: "Worker 状态", level: 2 })).toBeVisible();
  await page.getByRole("button", { name: "下一页" }).click();
  await expect(page).toHaveURL(/offset=6/);
  await expect(page.getByText("训练实例 B", { exact: true })).toBeVisible();
  await page.getByLabel("服务类型", { exact: true }).selectOption("training");
  await expect(page).not.toHaveURL(/offset=6/);
  await page.getByRole("link", { name: "查看 训练实例 A" }).click();
  await expect(page).toHaveURL(/workers\/training-gpu-a\?kind=training/);
  await expect(page.getByRole("heading", { name: "实例详情" })).toBeVisible();
  await page.reload();
  await expect(page.getByRole("heading", { name: "实例详情" })).toBeVisible();
  await expect(page.getByRole("link", { name: "查看节点计算资源" })).not.toBeVisible();
  await page.getByText("技术信息（排障时查看）", { exact: true }).click();
  await expect(page.getByRole("link", { name: "查看节点计算资源" })).toHaveAttribute("href", "/hardware?node=gpu-a");
  await page.getByRole("link", { name: "关闭详情" }).click();
  await expect(page).toHaveURL(/workers\?kind=training/);
});

test("annotators cannot view worker status via direct URL", async ({ page }) => {
  await page.route("**/api/auth/me", route => route.fulfill({ json: { user: { id: "annotator", role: "annotator", status: "active" } } }));
  await page.goto("/workers/training-gpu-a");
  await expect(page.getByRole("heading", { name: "此页面不在你的工作权限内" })).toBeVisible();
  await expect(page.getByRole("link", { name: "Worker 状态", exact: true })).toHaveCount(0);
});
