// frontend/src/App.spec.tsx
import '../tests/ct/setup'; // registers test.beforeEach to stub /api/v1/config

import { test, expect } from '@playwright/experimental-ct-react';
import App from './App';

test('renders the app (with config loaded)', async ({ mount, page }) => {
  // The setup file already installed the /config stub before each test.
  const component = await mount(<App />);

  // T1 (2026-07-02): the landing page now gates the form on Turnstile token
  // readiness — resolve the (mocked) widget first, then the config-driven
  // form copy appears.
  await page.getByTestId('turnstile').click();

  // Assert something stable from config-driven UI
  await expect(component).toContainText(
    /answer a few questions and let our ai reveal a surprising profile of you\./i,
  );
  await expect(component.getByRole('button', { name: /start quiz/i })).toBeVisible();
});
