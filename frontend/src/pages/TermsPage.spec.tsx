// src/pages/TermsPage.spec.tsx
/* eslint no-console: ["error", { "allow": ["error"] }] */
import React from 'react';
import { describe, it, expect, beforeEach, afterEach, vi } from 'vitest';
import { render, screen, cleanup } from '@testing-library/react';

// Mock StaticPage inline within the factory to avoid hoisting issues
vi.mock('./StaticPage', () => {
  return {
    StaticPage: ({ pageKey }: { pageKey: string }) => (
      <div data-testid="static-page">key:{pageKey}</div>
    ),
  };
});

import { TermsPage } from './TermsPage';

describe('TermsPage', () => {
  beforeEach(() => {
    vi.clearAllMocks();
  });

  afterEach(() => {
    cleanup();
  });

  it('renders and passes the correct pageKey to StaticPage', () => {
    render(<TermsPage />);

    const staticPage = screen.getByTestId('static-page');
    expect(staticPage).toBeInTheDocument();
    expect(staticPage).toHaveTextContent('key:termsPage');
  });
});
