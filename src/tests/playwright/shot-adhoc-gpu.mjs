// Screenshot the Adhoc Profiling view and the rendered GPU (nsys) flamegraph.
// Usage: node shot-adhoc-gpu.mjs [outPrefix]
import { chromium } from '@playwright/test';

const PREFIX = process.argv[2] || '/tmp/adhoc_gpu';
const BASE = process.env.STUDIO_URL || 'https://localhost:30443';
const SERVICE = process.env.SERVICE || 'k8s-sandbox';
const HOST = process.env.GPU_HOST || 'gpu-nsys-host';

const browser = await chromium.launch();
const ctx = await browser.newContext({
    ignoreHTTPSErrors: true,
    httpCredentials: { username: 'admin', password: 'admin' },
    viewport: { width: 1600, height: 1000 },
});
const page = await ctx.newPage();

const url = `${BASE}/profiles?service=${SERVICE}&time=4h&view=adhoc`;
console.log('navigating:', url);
await page.goto(url, { waitUntil: 'networkidle', timeout: 90000 });
await page.waitForTimeout(6000);
await page.screenshot({ path: `${PREFIX}_list.png`, fullPage: true });
console.log('saved', `${PREFIX}_list.png`);

// Click "View" on the GPU host row.
const row = page.locator('tr', { hasText: HOST }).first();
await row.locator('button', { hasText: 'View' }).click();
await page.waitForTimeout(9000);
await page.screenshot({ path: `${PREFIX}_flamegraph.png`, fullPage: true });
console.log('saved', `${PREFIX}_flamegraph.png`);

// Report what the iframe actually rendered.
const frame = page.frameLocator('iframe[title="Adhoc Flamegraph"]');
try {
    const inner = await frame.locator('body').innerText({ timeout: 10000 });
    console.log('---- iframe text ----');
    console.log(inner.slice(0, 800));
} catch (e) {
    console.log('iframe text unavailable:', String(e).slice(0, 200));
}
await browser.close();
