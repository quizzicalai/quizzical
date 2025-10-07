/* eslint no-console: ["error", { "allow": ["debug", "warn", "error"] }] */
import React from 'react';
import { describe, it, expect, beforeEach, afterEach, vi } from 'vitest';
import { render, screen, cleanup, act } from '@testing-library/react';
import { MemoryRouter } from 'react-router-dom';
import { CONFIG_FIXTURE } from '../../tests/fixtures/config.fixture';

// ---------- Mocks we can control in tests ----------
let __config: any = null;
let __quizId: string | null = null;

// useConfig mock with a setter
vi.mock('/src/context/ConfigContext', () => {
  return {
    __setConfig: (c: any) => (__config = c),
    useConfig: () => ({
      config: __config,
      isLoading: false,
      error: null,
      reload: vi.fn(),
    }),
  };
});

// Minimal store mock: Zustand-like selector usage
vi.mock('/src/store/quizStore', () => ({
  __setQuizId: (id: string | null) => (__quizId = id),
  useQuizStore: (selector: (s: any) => any) => selector({ quizId: __quizId }),
}));

// Spinner mock
vi.mock('/src/components/common/Spinner', () => ({
  Spinner: ({ message }: { message?: string }) =>
    React.createElement('div', { 'data-testid': 'spinner' }, message || 'Loading...'),
}));

// Layout mocks (Header/Footer)
vi.mock('/src/components/layout/Header', () => ({
  Header: () => <header data-testid="hdr">header</header>,
}));
vi.mock('/src/components/layout/Footer', () => ({
  Footer: ({ variant }: { variant?: 'landing' | 'quiz' }) => (
    <footer data-testid="ftr" data-variant={variant}>
      footer
    </footer>
  ),
}));

// Non-lazy pages
vi.mock('/src/pages/AboutPage', () => ({
  AboutPage: () => <main data-testid="about">About</main>,
}));
vi.mock('/src/pages/TermsPage', () => ({
  TermsPage: () => <main data-testid="terms">Terms</main>,
}));
vi.mock('/src/pages/PrivacyPage', () => ({
  PrivacyPage: () => <main data-testid="privacy">Privacy</main>,
}));
vi.mock('/src/pages/NotFoundPage', () => ({
  default: () => <main data-testid="notfound">Not Found</main>,
}));

// Lazy pages (resolved immediately but still suspend for one tick)
vi.mock('/src/pages/LandingPage', () => ({
  LandingPage: () => <main data-testid="landing">Landing</main>,
}));
vi.mock('/src/pages/QuizFlowPage', () => ({
  QuizFlowPage: () => <main data-testid="quiz">Quiz</main>,
}));
vi.mock('/src/pages/FinalPage', () => ({
  FinalPage: () => <main data-testid="final">Final</main>,
}));

// ----- Utilities to control mocks -----
const { __setConfig } = (await import('../context/ConfigContext')) as any;
const { __setQuizId } = (await import('../store/quizStore')) as any;

// Module under test (import after mocks)
const MOD_PATH = '/src/router/AppRouter';

async function renderAt(pathname: string) {
  const { AppRouter } = await import(MOD_PATH);
  const ui = render(
    <MemoryRouter initialEntries={[pathname]}>
      <AppRouter />
    </MemoryRouter>
  );
  // IMPORTANT: allow React.lazy + useEffect to flush
  await act(async () => {});
  return ui;
}

let scrollSpy: ReturnType<typeof vi.spyOn> | null = null;

beforeEach(() => {
  cleanup();
  __setConfig(CONFIG_FIXTURE);
  __setQuizId(null);
  document.title = 'initial';

  // Mock scrollTo globally so JSDOM doesn’t complain
  scrollSpy = vi.spyOn(window, 'scrollTo').mockImplementation(() => {});
});

afterEach(() => {
  scrollSpy?.mockRestore();
  scrollSpy = null;
});

// ------------------------------------------------------

describe('AppRouter', () => {
  it('renders Landing route with Header/Footer; Footer has landing variant', async () => {
    await renderAt('/');

    // Wait for the lazy LandingPage to resolve (Suspense fallback disappears)
    await screen.findByTestId('landing');

    // Now the layout is rendered
    expect(screen.getByTestId('hdr')).toBeInTheDocument();
    expect(screen.getByTestId('landing')).toBeInTheDocument();

    const footer = screen.getByTestId('ftr');
    expect(footer).toBeInTheDocument();
    expect(footer).toHaveAttribute('data-variant', 'landing');
    });

  it('Footer variant switches to "quiz" on /quiz when allowed (quizId present)', async () => {
    __setQuizId('qid-123'); // ensure guard allows /quiz
    await renderAt('/quiz');

    expect(screen.getByTestId('hdr')).toBeInTheDocument();
    expect(screen.getByTestId('quiz')).toBeInTheDocument();
    const footer = screen.getByTestId('ftr');
    expect(footer).toHaveAttribute('data-variant', 'quiz');
  });

  it('RequireQuiz: redirects to "/" when no quizId (navigating to /quiz)', async () => {
    __setQuizId(null); // guard should redirect
    await renderAt('/quiz');

    expect(screen.getByTestId('landing')).toBeInTheDocument();
    expect(screen.queryByTestId('quiz')).toBeNull();
  });

  it('DocumentTitleUpdater sets document.title per route + config', async () => {
    await renderAt('/');
    expect(document.title).toBe(
      CONFIG_FIXTURE.content.landingPage.title ?? CONFIG_FIXTURE.content.appName
    );

    cleanup();
    __setQuizId('qid-123'); // <-- needed so /quiz isn’t redirected to '/'
    await renderAt('/quiz');
    expect(document.title).toBe(`Quiz - ${CONFIG_FIXTURE.content.appName}`);

    cleanup();
    await renderAt('/result');
    expect(document.title).toBe(`Result - ${CONFIG_FIXTURE.content.appName}`);

    cleanup();
    await renderAt('/about');
    expect(document.title).toBe(
      CONFIG_FIXTURE.content.aboutPage?.title ?? `About - ${CONFIG_FIXTURE.content.appName}`
    );

    cleanup();
    await renderAt('/terms');
    expect(document.title).toBe(
      CONFIG_FIXTURE.content.termsPage?.title ?? `Terms - ${CONFIG_FIXTURE.content.appName}`
    );

    cleanup();
    await renderAt('/privacy');
    expect(document.title).toBe(
      CONFIG_FIXTURE.content.privacyPolicyPage?.title ?? `Privacy - ${CONFIG_FIXTURE.content.appName}`
    );
  });

  it('ScrollAndFocusManager: scrolls to top and focuses main on navigation', async () => {
    await renderAt('/about'); // effect runs after mount (await act above)

    expect(scrollSpy).toHaveBeenCalledWith(0, 0);

    const main = screen.getByTestId('about');
    // give focus() a tick (already did in renderAt, but keep this to be explicit)
    await act(async () => {});
    expect(document.activeElement).toBe(main);
  });

  it('renders NotFound for unknown paths', async () => {
    await renderAt('/something-unknown');
    expect(screen.getByTestId('notfound')).toBeInTheDocument();
  });

  it('shows Suspense fallback spinner while a lazy page is still loading', async () => {
    vi.resetModules();

    // reinstall critical mocks that AppRouter depends on
    vi.mock('/src/context/ConfigContext', () => ({
      __setConfig: (c: any) => (__config = c),
      useConfig: () => ({ config: __config, isLoading: false, error: null, reload: vi.fn() }),
    }));
    vi.mock('/src/store/quizStore', () => ({
      __setQuizId: (id: string | null) => (__quizId = id),
      useQuizStore: (selector: (s: any) => any) => selector({ quizId: __quizId }),
    }));
    vi.mock('/src/components/common/Spinner', () => ({
      Spinner: ({ message }: { message?: string }) =>
        React.createElement('div', { 'data-testid': 'spinner' }, message || 'Loading...'),
    }));
    vi.mock('/src/components/layout/Header', () => ({
      Header: () => <header data-testid="hdr">header</header>,
    }));
    vi.mock('/src/components/layout/Footer', () => ({
      Footer: ({ variant }: { variant?: 'landing' | 'quiz' }) => (
        <footer data-testid="ftr" data-variant={variant}>
          footer
        </footer>
      ),
    }));
    vi.mock('/src/pages/NotFoundPage', () => ({
      default: () => <main data-testid="notfound">Not Found</main>,
    }));
    vi.mock('/src/pages/QuizFlowPage', () => ({
      QuizFlowPage: () => <main data-testid="quiz">Quiz</main>,
    }));
    vi.mock('/src/pages/FinalPage', () => ({
      FinalPage: () => <main data-testid="final">Final</main>,
    }));
    vi.mock('/src/pages/AboutPage', () => ({ AboutPage: () => <main data-testid="about">About</main> }));
    vi.mock('/src/pages/TermsPage', () => ({ TermsPage: () => <main data-testid="terms">Terms</main> }));
    vi.mock('/src/pages/PrivacyPage', () => ({ PrivacyPage: () => <main data-testid="privacy">Privacy</main> }));

    // Async LandingPage to force Suspense fallback
    vi.mock('/src/pages/LandingPage', async () => {
      let resolve!: (m: any) => void;
      const p = new Promise<any>((r) => (resolve = r));
      setTimeout(() => resolve({ LandingPage: () => <main data-testid="landing">Landing</main> }), 0);
      return p;
    });

    const { __setConfig: setConfig2 } = (await import('../context/ConfigContext')) as any;
    setConfig2(CONFIG_FIXTURE);

    const { AppRouter } = await import('./AppRouter');

    render(
      <MemoryRouter initialEntries={['/']}>
        <AppRouter />
      </MemoryRouter>
    );

    // Spinner is present while the async LandingPage resolves
    expect(screen.getByTestId('spinner')).toBeInTheDocument();

    await act(async () => {
      await new Promise((r) => setTimeout(r, 1));
    });

    expect(screen.getByTestId('landing')).toBeInTheDocument();
  });
});
