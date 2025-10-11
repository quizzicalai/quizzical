import { test, expect } from '@playwright/experimental-ct-react';
import React from 'react';

test('CT host is reachable (sanity via mount)', async ({ mount, page }) => {
  const component = await mount(<div data-testid="ct-ok">ok</div>);
  // Root itself has the test id — use the page-scoped locator or assert the component itself:
  await expect(page.getByTestId('ct-ok')).toBeVisible();
  // or: await expect(component).toBeVisible();

  // Also assert CT facade’s #root is there
  await expect(page.locator('#root')).toBeVisible();
});