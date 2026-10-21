const http = require('node:http');
const fs = require('node:fs');
const path = require('node:path');

const playwrightModule = process.env.A_PI_PLAYWRIGHT_MODULE;
const browserPath = process.env.A_PI_BROWSER_PATH;
if (!playwrightModule || !browserPath) {
  throw new Error('Set A_PI_PLAYWRIGHT_MODULE and A_PI_BROWSER_PATH for the optional browser smoke test.');
}
const { chromium } = require(playwrightModule);
const root = path.resolve(__dirname, '..', 'dist');
const liveUrl = process.env.A_PI_BASE_URL;
const mime = { '.html': 'text/html', '.js': 'text/javascript', '.mjs': 'text/javascript', '.css': 'text/css', '.svg': 'image/svg+xml', '.png': 'image/png', '.ico': 'image/x-icon' };

const server = http.createServer((request, response) => {
  const pathname = decodeURIComponent(new URL(request.url, 'http://localhost').pathname);
  if (pathname.startsWith('/api/')) {
    response.writeHead(503, { 'Content-Type': 'application/json' });
    response.end(JSON.stringify({ ok: false, error: 'browser smoke fixture' }));
    return;
  }
  const target = path.resolve(root, `.${pathname}`, pathname.endsWith('/') ? 'index.html' : '');
  if (!target.startsWith(`${root}${path.sep}`)) {
    response.writeHead(403);
    response.end();
    return;
  }
  fs.readFile(target, (error, data) => {
    if (error) {
      response.writeHead(404);
      response.end();
      return;
    }
    response.writeHead(200, { 'Content-Type': mime[path.extname(target)] || 'application/octet-stream' });
    response.end(data);
  });
});

(async () => {
  if (!liveUrl) await new Promise((resolve) => server.listen(0, '127.0.0.1', resolve));
  const browser = await chromium.launch({ headless: true, executablePath: browserPath, args: ['--no-sandbox'] });
  try {
    const page = await browser.newPage({ reducedMotion: 'reduce' });
    const errors = [];
    const missing = [];
    page.on('pageerror', (error) => errors.push(error.stack || error.message));
    page.on('response', (response) => {
      if (!response.url().includes('/api/') && response.status() >= 400) {
        missing.push(`${response.status()} ${response.url()}`);
      }
    });
    await page.goto(liveUrl || `http://127.0.0.1:${server.address().port}/`, { waitUntil: 'load' });
    await page.locator('#guestButton').click();
    for (const section of ['overview', 'research', 'factorMining', 'backtest', 'risk', 'paper']) {
      await page.locator(`.nav-item[data-section="${section}"]`).click();
      const active = await page.locator(`#${section}`).evaluate((element) => element.classList.contains('active'));
      if (!active) errors.push(`Navigation failed: ${section}`);
    }
    await page.waitForTimeout(1200);
    console.log(JSON.stringify({ errors, missing, activeSection: await page.locator('.nav-item.active').getAttribute('data-section') }));
    if (errors.length || missing.length) process.exitCode = 1;
  } finally {
    await browser.close();
    if (!liveUrl) server.close();
  }
})().catch((error) => {
  console.error(error);
  if (!liveUrl) server.close();
  process.exitCode = 1;
});
