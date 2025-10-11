import { test, expect } from '@playwright/experimental-ct-react';
import React from 'react';
import { LoadingCard } from '../../src/components/loading/LoadingCard';
import { LoadingNarration } from '../../src/components/loading/LoadingNarration';

const TEST_LINES = [
  { atMs:   0, text: 'Thinking…' },
  { atMs: 150, text: 'Researching topic…' },
  { atMs: 300, text: 'Determining characters…' },
];

test.describe('<LoadingCard />', () => {
  test.beforeEach(async ({ page }) => {
    await page.emulateMedia({ reducedMotion: 'reduce' });
  });

  test('visual snapshot (reduced motion)', async ({ mount, page }) => {
    await page.setViewportSize({ width: 1024, height: 800 });
    const cmp = await mount(<LoadingCard />);
    await expect(cmp).toHaveScreenshot('loading-card.png', { animations: 'disabled' });
  });

  test('narration changes over time (sprite animation independent)', async ({ mount, page }) => {
    // Mount just the strip so we can inject a fast schedule
    await mount(
      <div className="inline-flex items-center gap-3">
        <div data-testid="sprite-proxy" />
        <LoadingNarration lines={TEST_LINES} tickMs={25} />
      </div>
    );

    const text = page.getByTestId('loading-narration-text');

    await expect(text).toHaveText('Thinking…');
    await page.waitForTimeout(170);
    await expect(text).toHaveText('Researching topic…');
    await page.waitForTimeout(170);
    await expect(text).toHaveText('Determining characters…');
  });

  test('includes an a11y live region via LoadingNarration', async ({ mount, page }) => {
    await mount(<LoadingCard />);
    const region = page.getByRole('status', { name: 'Loading' });
    await expect(region).toBeVisible();
    await expect(region).toHaveAttribute('aria-live', 'polite');
  });
});
