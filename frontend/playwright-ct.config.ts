// frontend/playwright-ct.config.ts

import { defineConfig, devices } from '@playwright/experimental-ct-react';

// CT mounts the real app bundle, so seed the same-origin API env that the
// runtime expects before Playwright boots the Vite component server.
process.env.VITE_API_URL ??= 'http://localhost:3100';
process.env.VITE_API_BASE_URL ??= '/api/v1';

export default defineConfig({
  use: {
    trace: 'on-first-retry',
    ctPort: 3100,
  },
  // Only look in src for CT specs
  testDir: './src',

  // Only run files named *.ct.spec.ts or *.ct.spec.tsx
  testMatch: '**/*.ct.spec.{ts,tsx}',

  // Extra guard: ignore e2e directory entirely
  testIgnore: ['tests/e2e/**'],

  snapshotDir: './__snapshots__',
  timeout: 20_000,
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
