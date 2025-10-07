// src/hooks/useCloseOnRouteChange.spec.ts
import React, { useEffect } from 'react';
import { describe, it, expect, vi } from 'vitest';
import { renderHook, act } from '@testing-library/react';
import { MemoryRouter, useNavigate } from 'react-router-dom';
import { useCloseOnRouteChange } from './useCloseOnRouteChange';

// Build a router harness that supplies a Testing Library wrapper and a navigate helper.
function makeRouterHarness(initialEntry = '/') {
  // We'll capture react-router's navigate and expose it to tests.
  const navRef: { current: (to: string) => void } = {
    current: () => {},
  };

  // Component that captures navigate() and stores it in navRef.
  function NavCapture() {
    const navigate = useNavigate();
    useEffect(() => {
      navRef.current = (to: string) => {
        navigate(to, { replace: true });
      };
    }, [navigate]);
    return null;
  }

  // Conform to Testing Library's wrapper type: a component that only accepts { children }.
  const Wrapper: React.JSXElementConstructor<{ children: React.ReactNode }> = ({ children }) =>
    React.createElement(
      MemoryRouter,
      { initialEntries: [initialEntry] },
      // Use a Fragment to pass multiple children without JSX.
      React.createElement(
        React.Fragment,
        null,
        children,
        React.createElement(NavCapture, null)
      )
    );

  return {
    wrapper: Wrapper,
    navigate: (to: string) => navRef.current(to),
  };
}

describe('useCloseOnRouteChange', () => {
  it('calls close once on mount', () => {
    const close = vi.fn();
    const harness = makeRouterHarness('/');

    renderHook(() => useCloseOnRouteChange(close), {
      wrapper: harness.wrapper,
    });

    expect(close).toHaveBeenCalledTimes(1);
  });

  it('calls close when pathname changes', async () => {
    const close = vi.fn();
    const harness = makeRouterHarness('/');

    renderHook(() => useCloseOnRouteChange(close), { wrapper: harness.wrapper });
    expect(close).toHaveBeenCalledTimes(1);

    await act(async () => {
      harness.navigate('/alpha');
    });
    expect(close).toHaveBeenCalledTimes(2);

    await act(async () => {
      harness.navigate('/beta');
    });
    expect(close).toHaveBeenCalledTimes(3);
  });

  it('calls close when search changes (same pathname)', async () => {
    const close = vi.fn();
    const harness = makeRouterHarness('/items');

    renderHook(() => useCloseOnRouteChange(close), { wrapper: harness.wrapper });
    expect(close).toHaveBeenCalledTimes(1);

    await act(async () => {
      harness.navigate('/items?filter=new');
    });
    expect(close).toHaveBeenCalledTimes(2);

    await act(async () => {
      harness.navigate('/items?filter=old');
    });
    expect(close).toHaveBeenCalledTimes(3);

    await act(async () => {
      harness.navigate('/items');
    });
    expect(close).toHaveBeenCalledTimes(4);
  });

  it('calls close when hash changes (same pathname & search)', async () => {
    const close = vi.fn();
    const harness = makeRouterHarness('/doc?x=1');

    renderHook(() => useCloseOnRouteChange(close), { wrapper: harness.wrapper });
    expect(close).toHaveBeenCalledTimes(1);

    await act(async () => {
      harness.navigate('/doc?x=1#top');
    });
    expect(close).toHaveBeenCalledTimes(2);

    await act(async () => {
      harness.navigate('/doc?x=1#bottom');
    });
    expect(close).toHaveBeenCalledTimes(3);

    await act(async () => {
      harness.navigate('/doc?x=1');
    });
    expect(close).toHaveBeenCalledTimes(4);
  });

  it('does not call close again when route does not change', async () => {
    const close = vi.fn();
    const harness = makeRouterHarness('/same?a=1#h');

    renderHook(() => useCloseOnRouteChange(close), { wrapper: harness.wrapper });
    expect(close).toHaveBeenCalledTimes(1);

    await act(async () => {
      // navigating to the exact same URL should not change pathname/search/hash
      harness.navigate('/same?a=1#h');
    });
    expect(close).toHaveBeenCalledTimes(1);
  });
});
