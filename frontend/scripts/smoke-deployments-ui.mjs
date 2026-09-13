import assert from "node:assert/strict";
const { chromium } = await import(process.env.PLAYWRIGHT_MODULE || "playwright");
const base = process.env.SMOKE_BASE_URL || "http://localhost:5173";
const browser = await chromium.launch({ headless: true, ...(process.env.CHROMIUM_PATH ? { executablePath: process.env.CHROMIUM_PATH } : {}) });
try {
  const page = await browser.newPage({ viewport: { width: 1440, height: 1050 } });
  const errors = []; page.on("pageerror", e => errors.push(e.message));
  const response = await page.request.get(`${base}/api/model-versions`);
  assert.equal(response.status(), 200);
  const { model_versions: models } = await response.json();
  const model = models.find(v => v.status === "production" && v.head_type === "image_classifier_v2");
  assert.ok(model, "needs a published full model");
  model.id = model.id || model.model_version_id;
  await page.goto(`${base}/models/${model.id}`);
  await page.getByText("推理部署", { exact: true }).waitFor();
  await page.locator(".fv-deployment-card").first().waitFor();
  assert.ok(await page.getByRole("button", { name: "创建加速部署" }).isDisabled());
  await page.screenshot({ path: "/tmp/fgvc-deployments-desktop.png", fullPage: true });
  await page.setViewportSize({ width: 390, height: 900 });
  assert.ok(await page.locator(".fv-deployments").evaluate(el => el.scrollWidth <= el.clientWidth));
  await page.setViewportSize({ width: 1440, height: 1050 });
  await page.goto(`${base}/inference?dataset_version_id=${model.dataset_version_id}&model_version_id=${model.id}`);
  await page.getByText("推理部署 / 运行精度", { exact: true }).waitFor();
  await page.getByRole("button", { name: "推理部署", exact: true }).waitFor();
  assert.ok(await page.getByRole("button", { name: "推理部署", exact: true }).isEnabled());
  await page.screenshot({ path: "/tmp/fgvc-inference-deployment.png", fullPage: true });
  assert.deepEqual(errors, []);
  console.log("Deployment panel, CPU selection, unconfigured acceleration guard, mobile layout: passed");
} finally { await browser.close(); }
