/* eslint no-console: ["error", { "allow": ["debug", "warn", "error"] }] */

import { test, expect } from './utils/har.fixture';
import type {
  Page,
  Route,
  Request as PWRequest,
  Response as PWResponse,
  ConsoleMessage,
} from '@playwright/test';

import { installConfigFixtureE2E } from './fixtures/config';
import { installQuizMocks } from './fixtures/quiz';
import { stubTurnstile } from './utils/turnstile';

/** Extra diagnostics; install BEFORE other routes. */
async function installNetworkDiagnostics(page: Page) {
  page.on('request', (r: PWRequest) => {
    console.debug('[REQ]', r.method(), r.url());
  });

  page.on('response', async (res: PWResponse) => {
    const ct = res.headers()['content-type'] || '';
    console.debug('[RESP]', res.status(), res.request().method(), res.url(), ct);
  });

  page.on('requestfailed', (r: PWRequest) => {
    console.error('[FAIL]', r.method(), r.url(), r.failure()?.errorText);
  });

  page.on('pageerror', (e: Error) => {
    console.error('[PAGEERROR]', e.message);
  });

  page.on('console', (m: ConsoleMessage) => {
    console.debug('[CONSOLE]', m.type(), m.text());
  });

  // Non-invasive route "spy" — only under DEBUG to reduce overhead.
  if (process.env.DEBUG_E2E) {
    await page.route('**/*', (route: Route, request: PWRequest) => {
      const url = request.url();
      if (url.includes('/api/v1/config')) {
        console.debug('[ROUTE-SPY]', request.method(), url);
      }
      route.fallback(); // allow next matching handler / real network.
    });
  }
}

test.describe('E2E smoke', () => {
  test('app boots, nav works, quiz happy path', async ({ page }) => {
    await test.step('Setup diagnostics & mocks', async () => {
      if (process.env.DEBUG_E2E) await installNetworkDiagnostics(page);

      console.debug('[SETUP] Installing stubs/mocks…');
      await stubTurnstile(page);
      await installConfigFixtureE2E(page);
      await installQuizMocks(page);
      console.debug('[SANITY] project baseURL =', test.info().project.use.baseURL);
      console.debug('[SETUP] Done. Navigating…');
    });

    await test.step('Load app and assert /config', async () => {
      // Prefer waiting on the specific request rather than network idle for SPAs.
      const [configResp] = await Promise.all([
        page.waitForResponse(
          (r: PWResponse) => r.url().includes('/api/v1/config') && r.ok(),
          { timeout: 30_000 }
        ),
        page.goto('/'),
      ]);

      console.debug('[ASSERT] /config status', configResp.status());
      if (process.env.DEBUG_E2E) {
        try {
          console.debug('[ASSERT] /config body', await configResp.json());
        } catch {
          console.debug('[ASSERT] /config body <non-JSON>');
        }
      }

      const heading = page.getByRole('heading', { name: /unlock your inner persona/i });
      await expect(heading).toBeVisible({ timeout: 15_000 }); // built-in auto-retry
    });

    await test.step('Enter category & start quiz', async () => {
      let categoryInput = page.getByRole('textbox').first();
      if (!(await categoryInput.count())) {
        categoryInput = page.getByPlaceholder(/ancient rome|baking/i).first();
      }

      if (await categoryInput.count()) {
        await categoryInput.fill('Ancient Rome');
      } else {
        console.warn('[WARN] Category input not found; continuing without fill');
      }

      const createBtn =
        (await page.getByRole('button', { name: /create my quiz/i }).count())
          ? page.getByRole('button', { name: /create my quiz/i })
          : page.getByRole('button').first();

      await expect(createBtn).toBeVisible();

      const startRespPromise = page.waitForResponse(
        (r: PWResponse) => r.url().includes('/api/v1/quiz/start') && r.ok(),
        { timeout: 15_000 }
      );
      await createBtn.click();
      await startRespPromise;

      await expect(page.getByText(/the world of ancient rome/i)).toBeVisible({ timeout: 15_000 });
    });

    await test.step('Proceed to quiz & answer first question', async () => {
      const proceedCandidate =
        (await page.getByRole('button', { name: /start|continue|proceed|next/i }).count())
          ? page.getByRole('button', { name: /start|continue|proceed|next/i })
          : page.getByText(/start|continue|proceed|next/i);

      // Wait for /proceed BEFORE clicking the button that triggers it
      const proceedRespPromise = page.waitForResponse(
        (r: PWResponse) => r.url().includes('/api/v1/quiz/proceed') && r.ok(),
        { timeout: 15_000 }
      );
      await proceedCandidate.click();
      await proceedRespPromise;

      // Wait for the first question to arrive
      await page.waitForResponse(
        (r: PWResponse) => r.url().includes('/api/v1/quiz/status/e2e-1') && r.ok(),
        { timeout: 15_000 }
      );
      await expect(page.getByText(/which achievement is most impressive\?/i)).toBeVisible({
        timeout: 15_000,
      });

      // Select the answer. IMPORTANT: /quiz/next is triggered by the answer click.
      const aqueducts =
        (await page.getByRole('button', { name: /aqueducts/i }).count())
          ? page.getByRole('button', { name: /aqueducts/i })
          : page.getByText(/aqueducts/i);

      // Start waiting for /quiz/next BEFORE clicking the answer to avoid races
      const nextRespPromise = page.waitForResponse(
        (r: PWResponse) => r.url().includes('/api/v1/quiz/next') && r.ok(),
        { timeout: 15_000 }
      );
      await aqueducts.click();
      await nextRespPromise;

      // Result should be fetched after answering
      await page.waitForResponse(
        (r: PWResponse) => r.url().includes('/api/v1/quiz/status/e2e-1') && r.ok(),
        { timeout: 15_000 }
      );
      await expect(page.getByText(/the architect/i)).toBeVisible({ timeout: 15_000 });

      console.debug('[DONE] Happy path completed');
    });
  });
});
