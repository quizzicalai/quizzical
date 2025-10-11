// frontend/tests/ct/HeroCard.ct.spec.tsx
import { test, expect } from '@playwright/experimental-ct-react';
import React from 'react';
import { HeroCard } from '../../src/components/layout/HeroCard';

test.describe('<HeroCard /> (CT)', () => {
  test.beforeEach(async ({ page }) => {
    // Make animations predictable & avoid motion jitter
    await page.emulateMedia({ reducedMotion: 'reduce' });
  });

  test('renders hero and centers children', async ({ mount, page }) => {
    await page.setViewportSize({ width: 1024, height: 800 });

    const cmp = await mount(
      <HeroCard>
        <div data-testid="probe" style={{ display: 'inline-block' }}>Probe</div>
      </HeroCard>
    );

    // Hero area is present
    await expect(page.getByTestId('hero-card-hero')).toBeVisible();

    // Visual regression of the card container (stable target)
    const card = page.getByTestId('hero-card');
    await expect(card).toBeVisible();
    await expect(card).toHaveScreenshot('herocard-default.png', { animations: 'disabled' });

    // Child should be horizontally centered within the card
    const { cardCx, probeCx } = await page.evaluate(() => {
      const cardEl = document.querySelector('[data-testid="hero-card"]') as HTMLElement;
      const probeEl = document.querySelector('[data-testid="probe"]') as HTMLElement;
      const cb = cardEl.getBoundingClientRect();
      const pb = probeEl.getBoundingClientRect();
      return { cardCx: cb.left + cb.width / 2, probeCx: pb.left + pb.width / 2 };
    });
    expect(Math.abs(cardCx - probeCx)).toBeLessThanOrEqual(1);
  });

  test('no lateral layout shift across breakpoints (sm → md → lg)', async ({ mount, page }) => {
    await mount(
      <HeroCard>
        <div data-testid="probe" style={{ display: 'inline-block' }}>Probe</div>
      </HeroCard>
    );

    const measure = async () => page.evaluate(() => {
      const cardEl = document.querySelector('[data-testid="hero-card"]') as HTMLElement;
      const probeEl = document.querySelector('[data-testid="probe"]') as HTMLElement;
      const cb = cardEl.getBoundingClientRect();
      const pb = probeEl.getBoundingClientRect();
      return {
        cardCx: cb.left + cb.width / 2,
        probeCx: pb.left + pb.width / 2,
        cardW: cb.width,
      };
    });

    // sm
    await page.setViewportSize({ width: 640, height: 900 });
    const sm = await measure();
    expect(Math.abs(sm.cardCx - sm.probeCx)).toBeLessThanOrEqual(1);

    // md
    await page.setViewportSize({ width: 820, height: 900 });
    const md = await measure();
    expect(Math.abs(md.cardCx - md.probeCx)).toBeLessThanOrEqual(1);

    // lg
    await page.setViewportSize({ width: 1024, height: 900 });
    const lg = await measure();
    expect(Math.abs(lg.cardCx - lg.probeCx)).toBeLessThanOrEqual(1);

    // Card width should not collapse between breakpoints
    expect(md.cardW).toBeGreaterThanOrEqual(sm.cardW * 0.95);
    expect(lg.cardW).toBeGreaterThanOrEqual(md.cardW * 0.95);
  });

  test('can hide the hero via prop', async ({ mount, page }) => {
    await mount(
      <HeroCard showHero={false}>
        <div data-testid="probe" style={{ display: 'inline-block' }}>Probe</div>
      </HeroCard>
    );
    await expect(page.getByTestId('hero-card-hero')).toHaveCount(0);
  });
});
