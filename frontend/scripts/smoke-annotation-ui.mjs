import assert from "node:assert/strict";
const { chromium } = await import(process.env.PLAYWRIGHT_MODULE || "playwright");
const browser = await chromium.launch({ headless: true });
try {
  const page = await browser.newPage({ viewport: { width: 1580, height: 1100 } });
  const errors = [];
  page.on("pageerror", e => errors.push(e.message));
  await page.goto((process.env.SMOKE_BASE_URL || "http://localhost:5173") + "/annotation");
  await page.getByRole("heading", { name: "AI 标注工作区" }).waitFor();
  await page.getByRole("button", { name: /query-1.jpg/ }).waitFor();
  assert.equal(await page.locator(".ann-thumb").count(), 12);
  await page.getByRole("button", { name: "图片下一页" }).click();
  await page.getByRole("button", { name: /query-3.jpg/ }).waitFor();
  assert.equal(await page.locator(".ann-thumb").count(), 1);
  assert.ok(await page.getByRole("button", { name: "图片下一页" }).isDisabled());
  await page.getByRole("button", { name: "图片上一页" }).click();
  await page.getByRole("button", { name: /query-1.jpg/ }).click();
  assert.ok(await page.getByRole("button", { name: "分析当前图片", exact: true }).isDisabled());
  await page.getByRole("button", { name: "最终类别", exact: true }).click();
  await page.getByRole("textbox", { name: "搜索最终类别" }).fill("Laysan");
  await page.getByRole("button", { name: /Laysan Albatross/ }).click();
  assert.ok(await page.getByRole("button", { name: "确认标注", exact: true }).isEnabled());
  // Merely choosing a candidate must not send a label or start remote calls.
  if (await page.locator(".ann-candidates button").count()) await page.locator(".ann-candidates button").first().click();
  await page.evaluate(() => { window.scrollTo(0, 0); document.querySelector(".main").scrollTop = 0; document.querySelector(".ann-thumbnails").scrollTop = 0; });
  await page.screenshot({ path: "/tmp/fgvc-annotation-desktop.png", fullPage: true });
  assert.equal(await page.evaluate(() => document.documentElement.scrollWidth > innerWidth), false);
  await page.setViewportSize({ width: 390, height: 1000 });
  await page.screenshot({ path: "/tmp/fgvc-annotation-mobile.png", fullPage: true });
  assert.equal(await page.evaluate(() => document.documentElement.scrollWidth > innerWidth), false);
  assert.deepEqual(errors, []);
  console.log("Annotation live UI: pagination, class search, consent guard, manual selection, desktop/mobile overflow passed.");
} finally { await browser.close(); }
