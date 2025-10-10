// frontend/tests/ct/LandingPage.ct.spec.tsx
import { test, expect } from '@playwright/experimental-ct-react';
import React from 'react';
import { MemoryRouter } from 'react-router-dom';
import { LandingPage } from '../../src/pages/LandingPage';
import {
  __getLastStartQuizCall,
  __resetLastStartQuizCall,
  __setNextStartQuizError,
} from './mocks/quizStore.mock';
import { CONFIG_FIXTURE } from '../fixtures/config.fixture';
import { __setTestConfig } from './mocks/ConfigContext.mock';

test.describe('<LandingPage /> (CT)', () => {
  test.beforeEach(() => {
    // Reset action mocks and ensure any feature-gated UI is enabled for CT runs
    __resetLastStartQuizCall();
    __setTestConfig({
      ...CONFIG_FIXTURE,
      features: { ...(CONFIG_FIXTURE.features ?? {}), turnstileEnabled: false },
    });
  });

  test('happy path: requires turnstile, then submits without backend', async ({ mount, page }) => {
    await mount(
      <MemoryRouter>
        <LandingPage />
      </MemoryRouter>
    );

    const submit = page.getByRole('button', {
      name: new RegExp(CONFIG_FIXTURE.content.landingPage.submitButton, 'i'),
    });
    await expect(submit).toBeDisabled();

    await page
      .getByLabel(/quiz (category )?input|quiz topic/i)
      .fill('coffee personalities');

    // First submit (no token yet) should require Turnstile
    await submit.click();
    await expect(
      page.getByText(/please complete the security verification/i)
    ).toBeVisible();

    // Click the mock Turnstile to set token, then submit again
    await page.getByTestId('turnstile').click();
    await submit.click();

    expect(__getLastStartQuizCall()).toEqual({
      category: 'coffee personalities',
      token: 'ct-token',
    });
  });

  test('error path: category_not_found shows config-driven message', async ({ mount, page }) => {
    await mount(
      <MemoryRouter>
        <LandingPage />
      </MemoryRouter>
    );

    await page
      .getByLabel(/quiz (category )?input|quiz topic/i)
      .fill('unknown');

    const submit = page.getByRole('button', {
      name: new RegExp(CONFIG_FIXTURE.content.landingPage.submitButton, 'i'),
    });

    // Require Turnstile first
    await submit.click();
    await page.getByTestId('turnstile').click();

    // Next submit triggers a single-shot error from the mocked store
    __setNextStartQuizError(
      Object.assign(new Error('not found'), { code: 'category_not_found' })
    );
    await submit.click();

    await expect(
      page.getByText(CONFIG_FIXTURE.content.errors.categoryNotFound)
    ).toBeVisible();
  });
});
