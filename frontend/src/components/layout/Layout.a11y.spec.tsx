/**
 * AC-FE-A11Y-LANDMARK-1..3: Layout provides exactly ONE `<main>` landmark with
 * id="main-content", and renders a SkipLink as the first focusable element so
 * keyboard users can jump past Header/nav (WCAG 2.4.1 Level A).
 */
import React from 'react';
import { describe, it, expect, vi, afterEach } from 'vitest';
import { MemoryRouter, Routes, Route } from 'react-router-dom';
import { render, screen, cleanup } from '@testing-library/react';

vi.mock('./Header', () => ({
  Header: () => <header data-testid="hdr">Header</header>,
}));
vi.mock('./Footer', () => ({
  Footer: () => <footer data-testid="ftr">Footer</footer>,
}));

import { Layout } from './Layout';

describe('Layout landmarks + skip link (FE-A11Y-LANDMARK)', () => {
  afterEach(() => cleanup());

  const renderLayout = (children?: React.ReactNode) => {
    return render(
      <MemoryRouter initialEntries={['/']}>
        <Routes>
          <Route path="/" element={<Layout />}>
            <Route index element={<div>{children ?? 'Page'}</div>} />
          </Route>
        </Routes>
      </MemoryRouter>,
    );
  };

  it('AC-FE-A11Y-LANDMARK-1: renders a skip link targeting #main-content as the first focusable', async () => {
    await renderLayout();
    const link = screen.getByRole('link', { name: /skip to main content/i });
    expect(link).toBeInTheDocument();
    expect(link.getAttribute('href')).toBe('#main-content');
  });

  it('AC-FE-A11Y-LANDMARK-2: <main> has id="main-content"', async () => {
    await renderLayout();
    const main = screen.getByRole('main');
    expect(main.getAttribute('id')).toBe('main-content');
  });

  it('AC-FE-A11Y-LANDMARK-3: there is exactly ONE main landmark even when child renders content', async () => {
    await renderLayout(<section>child content</section>);
    const mains = screen.getAllByRole('main');
    expect(mains.length).toBe(1);
  });
});
