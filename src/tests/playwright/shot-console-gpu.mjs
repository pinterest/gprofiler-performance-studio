// Screenshot the Dynamic Profiling console, checking the GPU (nsys) control.
// Usage: node shot-console-gpu.mjs [outfile]
import { chromium } from '@playwright/test';

const OUT = process.argv[2] || '/tmp/console_gpu.png';
const BASE = process.env.STUDIO_URL || 'https://localhost:30443';
const SERVICE = process.env.SERVICE || 'k8s-sandbox';

const browser = await chromium.launch();
const ctx = await browser.newContext({
    ignoreHTTPSErrors: true,
    httpCredentials: { username: 'admin', password: 'admin' },
    viewport: { width: 1600, height: 1100 },
});
const page = await ctx.newPage();

const url = `${BASE}/profiling?service=${SERVICE}`;
console.log('navigating:', url);
await page.goto(url, { waitUntil: 'networkidle', timeout: 90000 });
await page.waitForTimeout(7000);

const gpu = page.getByText('GPU (nsys)', { exact: false }).first();
console.log('GPU (nsys) control present:', await gpu.count() > 0);
if (await gpu.count() > 0) {
    await gpu.scrollIntoViewIfNeeded();
    await gpu.click();
    await page.waitForTimeout(1500);
}
await page.screenshot({ path: OUT, fullPage: true });
console.log('saved', OUT);
await browser.close();
