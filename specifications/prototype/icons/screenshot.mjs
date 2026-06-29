// Playwright screenshot driver for the Q&A image-enrichment prototype.
// Captures: the routed quiz question+answers (icons ON) across 3 demo
// questions, the brand-recolored icon grid, and a CLS/network measurement.
//
// Prereq: Vite dev server running with VITE_PROTO_QA_ICONS=1 on BASE.
// Run:    node screenshot.mjs

import { chromium } from 'playwright';
import { fileURLToPath, pathToFileURL } from 'node:url';
import { dirname, join } from 'node:path';
import { writeFileSync } from 'node:fs';

const __dirname = dirname(fileURLToPath(import.meta.url));
const PROTO = join(__dirname, '..');
const SHOTS = join(PROTO, 'screenshots');
const BASE = process.env.BASE || 'http://localhost:5180';

const browser = await chromium.launch();
const page = await browser.newPage({ viewport: { width: 1100, height: 1000 }, deviceScaleFactor: 2 });

// --- 1. Routed quiz questions+answers (icons ON) ---
await page.goto(`${BASE}/dev/qa-icons`, { waitUntil: 'networkidle' });
await page.waitForSelector('[data-testid="quiz-question-icon"]');
await page.waitForTimeout(400);
for (let i = 0; i < 3; i++) {
  if (i > 0) {
    await page.click(`text=Q${i + 1}`);
    await page.waitForTimeout(400);
  }
  await page.screenshot({ path: join(SHOTS, `quiz-icons-q${i + 1}.png`), fullPage: true });
  console.log(`shot quiz-icons-q${i + 1}.png`);
}

// --- 2. Brand-recolored icon grid ---
const gridUrl = pathToFileURL(join(__dirname, 'brand-grid.html')).href;
await page.goto(gridUrl, { waitUntil: 'networkidle' });
await page.waitForTimeout(300);
await page.screenshot({ path: join(SHOTS, 'brand-icon-grid.png'), fullPage: true });
console.log('shot brand-icon-grid.png');

// --- 3. CLS + network measurement on the routed page ---
// Reload and observe layout-shift entries + count of NON-inline image requests
// attributable to the icons (should be ZERO — inline sprite).
const imgRequests = [];
page.on('request', (r) => {
  if (r.resourceType() === 'image') imgRequests.push(r.url());
});
await page.goto(`${BASE}/dev/qa-icons`, { waitUntil: 'networkidle' });
await page.waitForSelector('[data-testid="quiz-question-icon"]');
const cls = await page.evaluate(
  () =>
    new Promise((resolve) => {
      let total = 0;
      new PerformanceObserver((list) => {
        for (const e of list.getEntries()) {
          if (!e.hadRecentInput) total += e.value;
        }
      }).observe({ type: 'layout-shift', buffered: true });
      // navigate Q1->Q2->Q3 to force the icon slots to swap, measuring shift
      const clickAll = async () => {
        for (const label of ['Q2', 'Q3', 'Q1']) {
          const btn = [...document.querySelectorAll('button')].find(
            (b) => b.textContent?.trim() === label
          );
          btn?.click();
          await new Promise((r) => setTimeout(r, 250));
        }
        setTimeout(() => resolve(total), 400);
      };
      clickAll();
    })
);

const measurement = {
  base: BASE,
  cls_after_3_question_swaps: Number(cls.toFixed(5)),
  image_http_requests: imgRequests.length,
  image_request_urls: imgRequests,
  note:
    'Icons are inline SVG (zero image HTTP requests). CLS is measured across ' +
    'three question swaps; the reserved fixed-size slots should keep it ~0.',
};
writeFileSync(join(PROTO, 'data', 'loadtime_measurement.json'), JSON.stringify(measurement, null, 2));
console.log('CLS after 3 swaps =', measurement.cls_after_3_question_swaps,
            '| image HTTP requests =', measurement.image_http_requests);

await browser.close();
