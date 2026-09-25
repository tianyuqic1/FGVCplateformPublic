const assert = require('node:assert/strict');
const { chromium } = require(process.env.PLAYWRIGHT_MODULE || 'playwright');
const base = process.env.SHOWCASE_URL || 'http://localhost:5180';

(async () => {
  const browser = await chromium.launch({ headless: true });
  const errors = [];
  try {
    const page = await browser.newPage();
    await page.emulateMedia({ reducedMotion: 'reduce' });
    page.on('pageerror', error => errors.push(error.message));
    for (const width of [1440, 390]) {
      await page.setViewportSize({ width, height: 1000 });
      const response = await page.goto(base, { waitUntil: 'domcontentloaded' });
      assert.equal(response.status(), 200);
      await page.reload();
      assert.equal(await page.locator('h1').count(), 1);
      assert.equal(await page.locator('main > h2').count(), 6);
      assert.ok(await page.locator('aside nav a').count() >= 36);

      if (width > 800) {
        await page.locator('aside a[href="#chapter-2-5"]').click();
      } else {
        await page.locator('aside a[href="#chapter-2"]').click();
        await page.evaluate(() => { location.hash = '#chapter-2-5'; });
      }
      assert.equal(new URL(page.url()).hash, '#chapter-2-5');
      await page.getByRole('heading', { name: '2.5 AI 标注', exact: false }).waitFor();

      if (width > 800) {
        await page.locator('aside a[href="#chapter-3-10"]').click();
      } else {
        await page.locator('aside a[href="#chapter-3"]').click();
        await page.evaluate(() => { location.hash = '#chapter-3-10'; });
      }
      assert.equal(new URL(page.url()).hash, '#chapter-3-10');
      await page.getByRole('heading', { name: '3.10 图像工具层与受限工作流', exact: false }).waitFor();

      const routeTable = page.locator('#chapter-0-2 + .table-wrap');
      assert.equal(await routeTable.count(), 1);
      assert.equal(await routeTable.locator('a').count(), 0);
      assert.equal(await routeTable.getByText('/annotation', { exact: true }).count(), 1);
      assert.equal(await page.locator('a[href^="./annotation/"]').count(), 0);
      assert.equal(
        await page.evaluate(() => document.documentElement.scrollWidth > innerWidth),
        false,
        'horizontal overflow at ' + width + 'px',
      );
      await page.screenshot({ path: '/tmp/finevision-handbook-' + width + '.png' });
    }
    assert.deepEqual(errors, []);
    console.log('PASS: single handbook × 2 viewports; 6 chapters, anchor navigation, integrated annotation and no overflow/runtime errors');
  } finally {
    await browser.close();
  }
})().catch(error => {
  console.error(error);
  process.exitCode = 1;
});
