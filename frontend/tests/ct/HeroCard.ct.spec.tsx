// frontend/tests/ct/HeroCard.ct.spec.tsx
import { test, expect } from '@playwright/experimental-ct-react';
import React from 'react';
import { HeroCard } from '../../src/components/layout/HeroCard';

test.describe('<HeroCard /> (CT)', () => {
  test.beforeEach(async ({ page }) => {
    await page.emulateMedia({ reducedMotion: 'reduce' }); // stable screenshots
  });

  test('renders hero and centers children', async ({ mount, page }) => {
    const cmp = await mount(
      <HeroCard>
        {/* Use a plain element, not a test-defined component */}
        <div data-testid="probe" style={{ display: 'inline-block' }}>Probe</div>
      </HeroCard>
    );

    await expect(page.getByTestId('hero-card-hero')).toBeVisible();
    await expect(cmp).toHaveScreenshot('herocard-default.png');

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

    async function measure() {
      return page.evaluate(() => {
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
    }

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

    expect(md.cardW).toBeGreaterThanOrEqual(sm.cardW * 0.95);
    expect(lg.cardW).toBeGreaterThanOrEqual(md.cardW * 0.95);
  });

  test('can hide the hero (API surface only, no UX change today)', async ({ mount, page }) => {
    await mount(
      <HeroCard showHero={false}>
        <div data-testid="probe" style={{ display: 'inline-block' }}>Probe</div>
      </HeroCard>
    );

    await expect(page.getByTestId('hero-card-hero')).toHaveCount(0);
  });
});
