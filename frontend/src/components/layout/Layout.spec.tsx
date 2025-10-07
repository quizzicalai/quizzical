// src/components/layout/Layout.spec.tsx
/* eslint no-console: ["error", { "allow": ["debug", "warn", "error"] }] */
import React from 'react';
import { describe, it, expect, vi, beforeEach, afterEach } from 'vitest';
import { MemoryRouter, Routes, Route } from 'react-router-dom';
import { render, screen, cleanup } from '@testing-library/react';

vi.mock('/src/components/layout/Header', () => ({
  Header: () => <header data-testid="hdr">Header</header>,
}));

vi.mock('/src/components/layout/Footer', () => ({
  Footer: ({ variant }: { variant?: 'landing' | 'quiz' }) => (
    <footer data-testid="ftr" data-variant={variant}>
      Footer
    </footer>
  ),
}));

describe('Layout', () => {
  const MOD_PATH = '/src/components/layout/Layout';

  const renderWithRoute = async (initialEntry: string, element: React.ReactNode) => {
    const mod = await import(MOD_PATH);
    const Layout = mod.Layout;

    return render(
      <MemoryRouter initialEntries={[initialEntry]}>
        <Routes>
          <Route path="/" element={<Layout />}>
            {element}
          </Route>
        </Routes>
      </MemoryRouter>
    );
  };

  beforeEach(() => {
    cleanup();
  });

  afterEach(() => {
    cleanup();
    vi.resetModules();
    vi.clearAllMocks();
  });

  it('renders Header, main, child via <Outlet>, and Footer', async () => {
    await renderWithRoute('/', <Route index element={<div data-testid="child">Home</div>} />);

    expect(screen.getByTestId('hdr')).toBeInTheDocument();
    expect(screen.getByRole('main')).toBeInTheDocument();
    expect(screen.getByTestId('child')).toHaveTextContent('Home');
    expect(screen.getByTestId('ftr')).toBeInTheDocument();
  });

  it('uses Footer variant "landing" on the root ("/")', async () => {
    await renderWithRoute('/', <Route index element={<div>Root</div>} />);
    const footer = screen.getByTestId('ftr');
    expect(footer).toHaveAttribute('data-variant', 'landing');
  });

  it('uses Footer variant "quiz" on non-root paths', async () => {
    await renderWithRoute('/quiz', <Route path="quiz" element={<div data-testid="child2">Quiz</div>} />);
    expect(screen.getByTestId('child2')).toHaveTextContent('Quiz');
    const footer = screen.getByTestId('ftr');
    expect(footer).toHaveAttribute('data-variant', 'quiz');
  });

  it('keeps a proper landmark for main content', async () => {
    await renderWithRoute('/', <Route index element={<div>Anything</div>} />);
    const main = screen.getByRole('main');
    expect(main).toBeInTheDocument();
  });
});
