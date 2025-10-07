// frontend/tests/e2e/utils/har.fixture.ts
/* eslint react-hooks/rules-of-hooks: "off", no-console: ["error", { "allow": ["debug", "warn", "error"] }] */
import { test as base } from '@playwright/test';
import fs from 'node:fs';
import path from 'node:path';

const SHOULD_RECORD_HAR = process.env.RECORD_HAR === '1';

export const test = base.extend({
  context: async ({ browser, contextOptions }, use, testInfo) => {
    const dir = path.join(process.cwd(), 'test-artifacts', 'har');
    fs.mkdirSync(dir, { recursive: true });

    const safeTitle = testInfo.title.replace(/[^\w.-]+/g, '_');
    const harPath = path.join(dir, `${testInfo.project.name}-${safeTitle}.har`);

    const ctx = await browser.newContext({
      ...contextOptions, // <-- keeps baseURL, headers, etc.
      ...(SHOULD_RECORD_HAR ? { recordHar: { path: harPath, content: 'embed' } } : {}),
    });

    console.debug('[HAR] context baseURL =', (ctx as any)._options?.baseURL ?? '(none)');
    try {
      await use(ctx);
    } finally {
      await ctx.close();
      if (SHOULD_RECORD_HAR) console.debug('[HAR] saved:', harPath);
    }
  },
});

export const expect = test.expect;
