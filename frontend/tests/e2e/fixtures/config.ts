// frontend/tests/e2e/fixtures/config.ts
import type { Page } from '@playwright/test';
import { CONFIG_FIXTURE } from '../../fixtures/config.fixture';

export async function installConfigFixtureE2E(page: Page) {
  // Match any origin + any base path + /config (+ optional query)
  await page.route('**/config*', async (route) => {
    // Debug: confirm we intercepted
    // console.log('[E2E] fulfilling', route.request().url());
    await route.fulfill({
      status: 200,
      contentType: 'application/json',
      body: JSON.stringify(CONFIG_FIXTURE),
    });
  });
}
