import { test, expect } from '@playwright/experimental-ct-react';
import React from 'react';
import { LoadingNarration } from '../../src/components/loading/LoadingNarration';

// Scaled timings for stability under load
const TEST_LINES = [
  { atMs:   0, text: 'Thinking…' },
  { atMs: 150, text: 'Researching topic…' },
  { atMs: 300, text: 'Determining characters…' },
  { atMs: 450, text: 'Writing character profiles…' },
  { atMs: 600, text: 'Preparing topic…' },
];

test.describe('<LoadingNarration />', () => {
  test.beforeEach(async ({ page }) => {
    await page.emulateMedia({ reducedMotion: 'reduce' });
  });

  test('transitions at 0/150/300/450/600ms (scaled)', async ({ mount, page }) => {
    await mount(<LoadingNarration lines={TEST_LINES} tickMs={25} ariaLabel="Loading" />);

    // Target the visible text node specifically
    const text = page.getByTestId('loading-narration-text');

    await expect(text).toHaveText('Thinking…');

    await page.waitForTimeout(170);
    await expect(text).toHaveText('Researching topic…');

    await page.waitForTimeout(170);
    await expect(text).toHaveText('Determining characters…');

    await page.waitForTimeout(170);
    await expect(text).toHaveText('Writing character profiles…');

    await page.waitForTimeout(220);
    await expect(text).toHaveText('Preparing topic…');
  });

  test('a11y contract: role=status + polite live region', async ({ mount, page }) => {
    await mount(<LoadingNarration lines={TEST_LINES} tickMs={25} ariaLabel="Loading quiz" />);
    const region = page.getByRole('status', { name: 'Loading quiz' });
    await expect(region).toBeVisible();
    await expect(region).toHaveAttribute('aria-live', 'polite');
  });
});
