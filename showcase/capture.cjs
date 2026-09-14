const { chromium } = require(process.env.PLAYWRIGHT_MODULE || 'playwright');
const path = require('node:path');
const routes = {
  dashboard: '/', datasets: '/datasets', weights: '/weights', training: '/training',
  inference: '/inference', review: '/review', feedback: '/feedback', models: '/models',
  pipelines: '/pipelines', hardware: '/hardware', annotation: '/annotation',
};
(async () => {
  const browser = await chromium.launch({ headless: true });
  const page = await browser.newPage({ viewport: { width: 1440, height: 920 }, deviceScaleFactor: 1 });
  const errors = [];
  page.on('pageerror', error => errors.push(error.message));
  try {
    for (const [name, route] of Object.entries(routes)) {
      await page.goto(`http://localhost:5173${route}`, { waitUntil: 'networkidle', timeout: 30000 });
      await page.screenshot({ path: path.resolve(__dirname, 'assets/screenshots', `${name}.png`) });
    }
    if (errors.length) throw new Error(errors.join('\n'));
    console.log(`Captured ${Object.keys(routes).length} current UI routes without writes`);
  } finally { await browser.close(); }
})().catch(error => { console.error(error); process.exitCode = 1; });
