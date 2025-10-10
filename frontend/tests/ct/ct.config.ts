// frontend/tests/ct/ct.config.ts
import { defineConfig, devices } from '@playwright/experimental-ct-react';
import { makeCtViteConfig } from './ct.vite.config';
import path from 'node:path';
import { fileURLToPath } from 'node:url';

// 🔽 Add these two lines to compute an absolute path to the template file
const __filename = fileURLToPath(import.meta.url);
const __dirname  = path.dirname(__filename);
const CT_TEMPLATE_DIR = '../../playwright';

export default defineConfig({
  testDir: '.',
  testMatch: '**/*.ct.spec.{ts,tsx}',
  snapshotDir: './__snapshots__',
  fullyParallel: true,
  forbidOnly: !!process.env.CI,
  retries: process.env.CI ? 2 : 0,
  workers: process.env.CI ? 1 : undefined,
  reporter: process.env.CI ? 'list' : 'html',
  use: {
    ctPort: 3100,
    viewport: { width: 900, height: 700 },
    ctTemplateDir: CT_TEMPLATE_DIR,            // 🔽 use it here
    ctViteConfig: makeCtViteConfig() as any,
    launchOptions: { headless: !!process.env.CI },
    trace: process.env.CI ? 'on-first-retry' : 'on',
  },
  projects: [
    { name: 'chromium', use: { ...devices['Desktop Chrome'] } },
    { name: 'firefox',  use: { ...devices['Desktop Firefox'] } },
    { name: 'webkit',   use: { ...devices['Desktop Safari'] } },
  ],
});
