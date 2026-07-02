// frontend/playwright-ct.config.ts

import { defineConfig, devices } from '@playwright/experimental-ct-react';
import { makeCtViteConfig } from './tests/ct/ct.vite.config';

// CT mounts the real app bundle, so seed the same-origin API env that the
// runtime expects before Playwright boots the Vite component server.
process.env.VITE_API_URL ??= 'http://localhost:3100';
process.env.VITE_API_BASE_URL ??= '/api/v1';

export default defineConfig({
  // Visual snapshots are compared across environments (win32/darwin dev
  // machines, the GH ubuntu runner, and the Playwright container that
  // generates the linux baselines). Identical chromium versions still differ
  // by ~0.5-1% of pixels in font antialiasing, so allow a 2% ratio — small
  // enough to catch real layout/styling regressions, large enough to absorb
  // rasterizer noise (the observed cross-env diff was 140px ≈ 1%).
  expect: {
    toHaveScreenshot: { maxDiffPixelRatio: 0.02 },
  },

  use: {
    trace: 'on-first-retry',
    ctPort: 3100,
    // T1 (2026-07-02): the CT specs under tests/ct mount real pages
    // (LandingPage / QuizFlowPage) whose imports (ConfigContext / quizStore /
    // Turnstile) are aliased to deterministic mocks by the shared CT Vite
    // config, which also provides the generic `@/` → `src/` resolver.
    ctViteConfig: makeCtViteConfig(),
  },

  // T1 (2026-07-02): collect CT specs from BOTH homes. `testDir: './src'`
  // previously orphaned the six specs under tests/ct — no config that
  // actually ran ever collected them.
  testDir: '.',
  testMatch: [
    'src/**/*.ct.spec.{ts,tsx}',
    'tests/ct/**/*.ct.spec.{ts,tsx}',
  ],

  // Extra guard: never pick up e2e specs.
  testIgnore: ['tests/e2e/**', 'node_modules/**'],

  snapshotDir: './__snapshots__',
  // 30s: the first spec pays the CT Vite cold-build cost; 20s flaked locally.
  timeout: 30_000,
  fullyParallel: false,
  forbidOnly: !!process.env.CI,
  retries: process.env.CI ? 2 : 0,
  workers: 1,
  reporter: 'html',
  projects: [
    { name: 'chromium', use: { ...devices['Desktop Chrome'] } },
    { name: 'firefox',  use: { ...devices['Desktop Firefox'] } },
    { name: 'webkit',   use: { ...devices['Desktop Safari'] } },
  ],
});
