// frontend/playwright-ct.config.ts

import { defineConfig, devices } from '@playwright/experimental-ct-react';

export default defineConfig({
  // Only look in src for CT specs
  testDir: './src',

  // Only run files named *.ct.spec.ts or *.ct.spec.tsx
  testMatch: '**/*.ct.spec.{ts,tsx}',

  // Extra guard: ignore e2e directory entirely
  testIgnore: ['tests/e2e/**'],

  snapshotDir: './__snapshots__',
  timeout: 10_000,
  fullyParallel: true,
  forbidOnly: !!process.env.CI,
  retries: process.env.CI ? 2 : 0,
  workers: process.env.CI ? 1 : undefined,
  reporter: 'html',
  use: {
    trace: 'on-first-retry',
    ctPort: 3100,
  },
  projects: [
    { name: 'chromium', use: { ...devices['Desktop Chrome'] } },
    { name: 'firefox',  use: { ...devices['Desktop Firefox'] } },
    { name: 'webkit',   use: { ...devices['Desktop Safari'] } },
  ],
});
