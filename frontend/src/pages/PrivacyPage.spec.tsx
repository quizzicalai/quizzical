// src/pages/PrivacyPage.spec.tsx
/* eslint no-console: ["error", { "allow": ["error"] }] */
import React from 'react';
import { describe, it, expect, beforeEach, afterEach, vi } from 'vitest';
import { render, screen, cleanup } from '@testing-library/react';

// Mock StaticPage inline in the factory to avoid hoisting issues
vi.mock('./StaticPage', () => {
  // use the top-level React import inside the mock factory
  return {
    StaticPage: ({ pageKey }: { pageKey: string }) => (
      <div data-testid="static-page">key:{pageKey}</div>
    ),
  };
});

import { PrivacyPage } from './PrivacyPage';

describe('PrivacyPage', () => {
  beforeEach(() => {
    vi.clearAllMocks();
  });

  afterEach(() => {
    cleanup();
  });

  it('renders and passes the correct pageKey to StaticPage', () => {
    render(<PrivacyPage />);

    const staticPage = screen.getByTestId('static-page');
    expect(staticPage).toBeInTheDocument();
    expect(staticPage).toHaveTextContent('key:privacyPolicyPage');
  });
});
