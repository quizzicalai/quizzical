import { test } from '@playwright/experimental-ct-react';
import { installConfigFixtureCT } from './fixtures/config';

// Runs before each CT test (in files that import this module)
test.beforeEach(async ({ page }) => {
  await installConfigFixtureCT(page);
});

// (no exports needed; just being imported registers the hook)
