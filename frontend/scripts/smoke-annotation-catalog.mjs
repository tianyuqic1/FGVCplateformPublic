import assert from "node:assert/strict";
import { catalogTemplate } from "../src/features/annotation/catalog.js";
const { chromium } = await import(process.env.PLAYWRIGHT_MODULE || "playwright");
const browser = await chromium.launch({ headless: true });
try {
  const page = await browser.newPage({ viewport: { width: 1500, height: 1100 } });
  const errors = [], writes = [];
  page.on("pageerror", e => errors.push(e.message));
  page.on("request", r => { if (r.method() === "POST" && !r.url().endsWith("/catalog/validate")) writes.push(r.url()); });
  await page.goto("http://localhost:5173/annotation");
  await page.getByRole("button", { name: "新建项目", exact: true }).waitFor();
  const batch = page.getByRole("button", { name: "分析全部待标图片", exact: true });
  assert.ok(await batch.isDisabled());
  await page.locator(".ann-batch-bar input[type=checkbox]").check();
  // Inspect and cancel, never submit a potentially paid task.
  if (await batch.isEnabled()) {
    await batch.click(); await page.getByRole("dialog", { name: "确认批量分析" }).waitFor();
    await page.getByRole("button", { name: "取消批量分析" }).click();
  }
  await page.getByRole("button", { name: "新建项目", exact: true }).click();
  assert.equal(await page.getByRole("textbox", { name: "类别目录", exact: true }).count(), 0);
  assert.ok(await page.getByRole("button", { name: "创建项目", exact: true }).isDisabled());
  const file = page.getByLabel("导入类别 JSON 文件", { exact: true });
  await file.setInputFiles({ name: "bad.json", mimeType: "application/json", buffer: Buffer.from("{bad") });
  await page.getByText("JSON 格式不正确，请检查引号、逗号和括号。", { exact: true }).waitFor();
  const catalog = { ...catalogTemplate, classes: Array.from({ length: 14 }, (_, i) => ({ id: String(i), name: `Bird ${i}` })) };
  await file.setInputFiles({ name: "classes.json", mimeType: "application/json", buffer: Buffer.from(JSON.stringify(catalog)) });
  await page.getByText("14 类 · 校验通过 · 点击更换", { exact: true }).waitFor();
  assert.equal(await page.locator(".ann-catalog-table tbody tr").count(), 6);
  await page.getByRole("button", { name: "类别预览下一页" }).click();
  assert.equal(await page.locator(".ann-catalog-table tbody tr").count(), 6);
  await page.getByRole("textbox", { name: "查找导入类别" }).fill("Bird 13");
  assert.equal(await page.locator(".ann-catalog-table tbody tr").count(), 1);
  assert.ok(await page.getByRole("button", { name: "创建项目", exact: true }).isEnabled());
  await page.screenshot({ path: "/tmp/finevision-catalog-desktop.png", fullPage: true });
  await page.setViewportSize({ width: 390, height: 1000 });
  assert.equal(await page.evaluate(() => document.documentElement.scrollWidth > innerWidth), false);
  await page.screenshot({ path: "/tmp/finevision-catalog-mobile.png", fullPage: true });
  assert.deepEqual(errors, []); assert.deepEqual(writes, []);
  console.log("Catalog + batch UI: JSON-only, invalid input, server validation, pagination/search, consent/confirmation, responsive layout; no project/queue writes.");
} finally { await browser.close(); }
