// frontend/tests/e2e/smoke.spec.ts
import { test, expect } from '@playwright/test';
import { installConfigFixtureE2E } from './fixtures/config';
import { installQuizMocks } from './fixtures/quiz';

test.describe('E2E smoke', () => {
  test('app boots, nav works, quiz happy path', async ({ page }) => {
    // 1) Route stubs FIRST so network is intercepted before navigation
    await installConfigFixtureE2E(page);
    await installQuizMocks(page);

    // Optional debug to see requests while stabilizing
    if (process.env.DEBUG_E2E) {
      page.on('request', r => console.log('>>', r.method(), r.url()));
      page.on('response', r => console.log('<<', r.status(), r.url()));
    }

    // 2) Navigate and wait for /config to finish (avoid race by arming both)
    const [configResp] = await Promise.all([
      page.waitForResponse(r => r.url().includes('/config') && r.ok(), { timeout: 15_000 }),
      page.goto('/'), // baseURL comes from playwright.config.ts
    ]);
    // Optionally assert config shape
    // expect(await configResp.json()).toMatchObject({ content: expect.any(Object) });

    // 3) Verify landing is rendered from config
    const landingTitle = page.getByText(/unlock your inner persona/i);
    await expect(landingTitle).toBeVisible({ timeout: 15_000 });

    // Button text comes from config fixture ("Create My Quiz")
    const createBtn =
      (await page.getByRole('button', { name: /create my quiz/i }).count())
        ? page.getByRole('button', { name: /create my quiz/i })
        : page.getByText(/create my quiz/i);
    await expect(createBtn).toBeVisible();

    // If there is a category input, fill it (placeholder comes from fixture);
    const categoryInput = page.getByPlaceholder(/ancient rome|baking/i);
    if (await categoryInput.count()) {
      await categoryInput.fill('Ancient Rome');
    }

    // 4) Start quiz → our mocks return a synopsis first
    const startRespPromise = page.waitForResponse(
      r => r.url().includes('/api/v1/quiz/start') && r.ok(),
      { timeout: 15_000 }
    );
    await createBtn.click();
    await startRespPromise;

    // Synopsis title from quiz fixture
    await expect(page.getByText(/the world of ancient rome/i)).toBeVisible();

    // 5) Proceed → app calls /quiz/proceed (we stub “processing”)
    const proceedRespPromise = page.waitForResponse(
      r => r.url().includes('/api/v1/quiz/proceed') && r.ok(),
      { timeout: 15_000 }
    );
    const proceedCandidate =
      (await page.getByRole('button', { name: /start|continue|proceed|next/i }).count())
        ? page.getByRole('button', { name: /start|continue|proceed|next/i })
        : page.getByText(/start|continue|proceed|next/i);
    await proceedCandidate.click();
    await proceedRespPromise;

    // 6) Status polling → first returns an active question
    const firstQuestionResp = await page.waitForResponse(
      r => /\/api\/v1\/quiz\/status\/e2e-1/.test(r.url()) && r.ok(),
      { timeout: 15_000 }
    );
    await expect(page.getByText(/which achievement is most impressive\?/i)).toBeVisible();

    // 7) Answer the first question (our fixture provides "Aqueducts", "Roads")
    const aqueducts =
      (await page.getByRole('button', { name: /aqueducts/i }).count())
        ? page.getByRole('button', { name: /aqueducts/i })
        : page.getByText(/aqueducts/i);
    await aqueducts.click();

    // Submit (label may vary; try common options)
    const submit =
      (await page.getByRole('button', { name: /submit|next|continue/i }).count())
        ? page.getByRole('button', { name: /submit|next|continue/i })
        : page.getByText(/submit|next|continue/i);
    const nextRespPromise = page.waitForResponse(
      r => r.url().includes('/api/v1/quiz/next') && r.ok(),
      { timeout: 15_000 }
    );
    await submit.click();
    await nextRespPromise;

    // 8) Next status call returns the result (fixture flips answered=true)
    await page.waitForResponse(
      r => /\/api\/v1\/quiz\/status\/e2e-1/.test(r.url()) && r.ok(),
      { timeout: 15_000 }
    );
    await expect(page.getByText(/the architect/i)).toBeVisible({ timeout: 15_000 });

    // Optional: core nav sanity (home/about if present)
    const homeLink = page.getByRole('link', { name: /home/i });
    if (await homeLink.count()) {
      await homeLink.click();
      await expect(landingTitle).toBeVisible();
    }
  });
});
