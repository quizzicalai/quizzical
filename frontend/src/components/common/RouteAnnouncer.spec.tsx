/**
 * AC-FE-A11Y-FOCUS-1..3: Route-change focus + announcement.
 *
 *  - 1: After a route change, focus moves to #main-content.
 *  - 2: A polite live region updates with "Navigated to <route name>".
 *  - 3: First render does NOT steal focus or emit an announcement.
 */
import React from 'react';
import { describe, it, expect, afterEach } from 'vitest';
import { MemoryRouter, useNavigate } from 'react-router-dom';
import { render, screen, act, cleanup } from '@testing-library/react';

import { RouteAnnouncer } from './RouteAnnouncer';

afterEach(() => cleanup());

const Harness: React.FC<{ to: string; trigger: React.MutableRefObject<(p: string) => void> }> = ({
  to: _to,
  trigger,
}) => {
  const navigate = useNavigate();
  trigger.current = (p) => navigate(p);
  return (
    <>
      <main id="main-content" tabIndex={-1}>
        page
      </main>
      <RouteAnnouncer />
    </>
  );
};

describe('RouteAnnouncer (FE-A11Y-FOCUS)', () => {
  it('AC-FE-A11Y-FOCUS-3: first render emits no announcement and does not steal focus', () => {
    render(
      <MemoryRouter initialEntries={['/']}>
        <main id="main-content" tabIndex={-1}>
          page
        </main>
        <RouteAnnouncer />
      </MemoryRouter>,
    );
    const live = screen.getByTestId('route-announcer');
    expect(live.textContent).toBe('');
    // Focus should remain on document body (default).
    expect(document.activeElement).not.toBe(document.getElementById('main-content'));
  });

  it('AC-FE-A11Y-FOCUS-1/2: route change focuses #main-content and updates live region', async () => {
    const trigger = { current: (_p: string) => {} };
    render(
      <MemoryRouter initialEntries={['/']}>
        <Harness to="/" trigger={trigger} />
      </MemoryRouter>,
    );

    await act(async () => {
      trigger.current('/about');
    });

    const live = screen.getByTestId('route-announcer');
    expect(live.textContent).toBe('Navigated to About');
    expect(live.getAttribute('aria-live')).toBe('polite');
    expect(document.activeElement).toBe(document.getElementById('main-content'));
  });

  it('AC-FE-A11Y-FOCUS-2: announces "Quiz" for /quiz/* routes and "Quiz Result" for /result/*', async () => {
    const trigger = { current: (_p: string) => {} };
    render(
      <MemoryRouter initialEntries={['/']}>
        <Harness to="/" trigger={trigger} />
      </MemoryRouter>,
    );

    await act(async () => {
      trigger.current('/quiz/abc-123');
    });
    expect(screen.getByTestId('route-announcer').textContent).toBe('Navigated to Quiz');

    await act(async () => {
      trigger.current('/result/abc-123');
    });
    expect(screen.getByTestId('route-announcer').textContent).toBe(
      'Navigated to Quiz Result',
    );
  });
});
