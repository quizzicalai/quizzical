import { test, expect } from '@playwright/experimental-ct-react';

test('CT host is reachable', async ({ page }) => {
  await page.goto('/playwright/index.html');
  await expect(page.locator('#root')).toBeVisible();
});