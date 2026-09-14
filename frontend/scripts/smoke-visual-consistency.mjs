import assert from "node:assert/strict";
import { mkdir } from "node:fs/promises";
const { chromium } = await import(process.env.PLAYWRIGHT_MODULE || "playwright");
const output = process.env.UI_SCREENSHOTS || "/tmp/finevision-style-audit";
await mkdir(output, { recursive: true });
const browser = await chromium.launch({ headless: true });
const routes = ["/", "/datasets", "/training", "/inference", "/models", "/review", "/feedback", "/weights", "/pipelines", "/hardware", "/annotation", "/annotation?tab=publish"];
const failures = [];
try {
  const page = await browser.newPage();
  // This audit never creates tasks, runs AI, or changes business data.
  await page.route("**/api/**", route => {
    if (!["GET", "HEAD", "OPTIONS"].includes(route.request().method())) {
      failures.push(`Blocked mutation: ${route.request().method()} ${route.request().url()}`);
      return route.abort();
    }
    return route.continue();
  });
  page.on("pageerror", error => failures.push(error.message));
  for (const width of [1440, 390]) {
    await page.setViewportSize({ width, height: 1000 });
    for (const route of routes) {
      await page.goto(`http://localhost:5173${route}`);
      await page.locator(".page").waitFor();
      await page.waitForTimeout(700);
      const layout = await page.evaluate(() => ({
        overflow: document.documentElement.scrollWidth > innerWidth + 1,
        text: document.querySelector(".page")?.textContent?.trim().length || 0,
      }));
      if (layout.overflow || !layout.text) failures.push(`${width} ${route}: ${JSON.stringify(layout)}`);
      await page.screenshot({ path: `${output}/${width}-${route.replace(/[^a-z0-9]/gi, "_") || "home"}.png` });
      console.log(`${width} ${route}: ${layout.overflow ? "OVERFLOW" : "OK"}`);
    }
  }
  assert.deepEqual(failures, []);
  console.log(`Visual route audit passed; screenshots: ${output}`);
} finally { await browser.close(); }
