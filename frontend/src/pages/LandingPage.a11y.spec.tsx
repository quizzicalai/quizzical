// frontend/src/pages/LandingPage.a11y.spec.tsx
/**
 * Accessibility smoke test for the landing page.
 *
 * Why this file exists (AC-A11Y-1):
 * - `jest-axe` / `vitest-axe` are installed but were previously unused.
 * - Without a baseline, accessibility regressions slip in silently —
 *   e.g. missing labels on the topic input, low-contrast text, buttons
 *   without accessible names.
 * - This spec runs axe-core against the rendered landing page (the most
 *   common entry point) and fails CI on any new violation.
 *
 * If you triage a violation as a false positive, prefer adding an
 * explicit `rules: { ... }` override here over deleting the assertion.
 */
/* eslint no-console: ["error", { "allow": ["error", "log"] }] */
import React from 'react';
import { describe, it, expect, vi, beforeEach, afterEach } from 'vitest';
import { render, cleanup, waitFor } from '@testing-library/react';
import { axe } from 'vitest-axe';
// vitest-axe@0.1 exports its matcher from a subpath, not the main module.
import * as axeMatchers from 'vitest-axe/matchers';

import { CONFIG_FIXTURE } from '../../tests/fixtures/config.fixture';

expect.extend(axeMatchers as any);

// --- Mocks (mirror LandingPage.spec.tsx so the page actually renders) ---

vi.mock('../components/common/Spinner', () => ({
  Spinner: ({ message }: { message?: string }) => (
    <div role="status">{message ?? 'Loading'}</div>
  ),
}));

const turnstileMockState = vi.hoisted(() => ({ autoVerify: true }));

vi.mock('../components/common/Turnstile', async () => {
  const ReactMod = await import('react');
  const TurnstileMock = ({
    onVerify,
  }: {
    onVerify: (t: string) => void;
  }) => {
    const fired = ReactMod.useRef(false);
    ReactMod.useEffect(() => {
      if (turnstileMockState.autoVerify && !fired.current) {
        fired.current = true;
        onVerify('tok-123');
      }
    }, [onVerify]);
    return (
      <div data-testid="turnstile-mock" aria-hidden="true" />
    );
  };
  return { __esModule: true, default: TurnstileMock };
});

vi.mock('../context/ConfigContext', () => ({
  useConfig: vi.fn(),
}));

vi.mock('../store/quizStore', () => ({
  useQuizActions: () => ({ startQuiz: vi.fn() }),
}));

vi.mock('react-router-dom', async (orig) => {
  const actual = await (orig() as any);
  return { ...actual, useNavigate: () => vi.fn() };
});

import { LandingPage } from './LandingPage';
import { useConfig } from '../context/ConfigContext';
import { MemoryRouter } from 'react-router-dom';

describe('LandingPage — accessibility (AC-A11Y-1)', () => {
  beforeEach(() => {
    (useConfig as unknown as ReturnType<typeof vi.fn>).mockReturnValue({
      config: CONFIG_FIXTURE,
      loading: false,
      error: null,
    });
    turnstileMockState.autoVerify = true;
  });

  afterEach(() => {
    cleanup();
    vi.clearAllMocks();
  });

  it('has no detectable axe-core violations on first paint', async () => {
    const { container } = render(
      <MemoryRouter>
        <LandingPage />
      </MemoryRouter>,
    );

    // Wait for the form to appear (Turnstile auto-verifies in the mock).
    await waitFor(() => {
      const inputs = container.querySelectorAll('input, button');
      expect(inputs.length).toBeGreaterThan(0);
    });

    const results = await axe(container, {
      // Disable rules that depend on real CSS contrast values (jsdom
      // returns rgba(0,0,0,0) for everything, producing false positives).
      // Real contrast is enforced via Tailwind tokens + manual review.
      rules: {
        'color-contrast': { enabled: false },
        // The landing page is a fragment; document-scoped rules don't apply.
        'region': { enabled: false },
        'landmark-one-main': { enabled: false },
        'page-has-heading-one': { enabled: false },
        'html-has-lang': { enabled: false },
        'document-title': { enabled: false },
      },
    });

    expect(results as any).toHaveNoViolations();
  }, 15_000);
  // 15s timeout: axe + LandingPage's many lazy chips can exceed the 5s
  // default under parallel-load conditions in CI; the scan itself is
  // fast (<800ms) but render + waitFor compete with other suites.
});
