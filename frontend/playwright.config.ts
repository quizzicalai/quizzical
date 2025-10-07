// playwright.config.ts
import { defineConfig, devices } from '@playwright/test';
import dotenv from 'dotenv';
import { fileURLToPath } from 'node:url';
import { dirname, resolve } from 'node:path';

const __filename = fileURLToPath(import.meta.url);
const __dirname = dirname(__filename);

// Load .env.e2e
dotenv.config({ path: resolve(__dirname, '.env.e2e') });

export default defineConfig({
  // Only run E2E specs
  testDir: './tests/e2e',
  testMatch: '**/*.spec.ts',
  testIgnore: ['**/*.ct.spec.*'],

  fullyParallel: true,
  forbidOnly: !!process.env.CI,
  retries: process.env.CI ? 2 : 0,
  workers: process.env.CI ? 1 : undefined,

  // Single reporter (no duplicates)
  reporter: [
    ['line'],
    ['html', { open: 'never', outputFolder: 'test-artifacts/report' }],
  ],

  // Global artifacts
  outputDir: 'test-artifacts/output',

  use: {
    baseURL: 'http://localhost:5173',
    trace: 'on',         // use 'on-first-retry' once stable
    video: 'on',         // use 'retain-on-failure' once stable
    screenshot: 'on',    // use 'only-on-failure' once stable
  },

  projects: [
    { name: 'chromium', use: { ...devices['Desktop Chrome'] } },
    { name: 'firefox',  use: { ...devices['Desktop Firefox'] } },
    { name: 'webkit',   use: { ...devices['Desktop Safari'] } },
  ],

  webServer: {
    command: 'npm run dev:e2e',
    url: 'http://localhost:5173',
    reuseExistingServer: !process.env.CI,
    timeout: 120_000,
  },
});
