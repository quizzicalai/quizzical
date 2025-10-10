// frontend/tests/ct/fixtures/config.ts
import type { Page } from 'playwright';
import { CONFIG_FIXTURE } from '../../fixtures/config.fixture';

export async function installConfigFixtureCT(page: Page) {
  await page.route('**/config*', async (route) => {
    await route.fulfill({
      status: 200,
      contentType: 'application/json',
      body: JSON.stringify(CONFIG_FIXTURE),
    });
  });
}
