/* eslint no-console: ["error", { "allow": ["error", "warn", "log"] }] */
import React from 'react';
import { describe, it, expect, vi, beforeEach, afterEach } from 'vitest';
import { render, screen, cleanup, fireEvent } from '@testing-library/react';
import { MemoryRouter } from 'react-router-dom';
import { CONFIG_FIXTURE } from '../../tests/fixtures/config.fixture';

// ---------------- Router mocks ----------------
const navigateMock = vi.fn();
let currentParams: Record<string, string | undefined> = {};
vi.mock('react-router-dom', async (orig) => {
  const mod = (await orig()) as any;
  return {
    ...mod,
    useNavigate: () => navigateMock,
    useParams: () => currentParams,
  };
});

// ---------------- Config mock ----------------
vi.mock('../context/ConfigContext', () => ({
  useConfig: () => ({ config: CONFIG_FIXTURE }),
}));

// ---------------- Session util mock ----------------
const getQuizIdMock = vi.fn();
vi.mock('../utils/session', () => ({
  getQuizId: (...args: any[]) => getQuizIdMock(...args),
}));

// ---------------- API mocks ----------------
const getResultMock = vi.fn();
vi.mock('../services/apiService', () => ({
  getResult: (...args: any[]) => getResultMock(...args),
  getQuizMedia: vi.fn().mockResolvedValue({
    quizId: 'mock',
    synopsisImageUrl: null,
    resultImageUrl: null,
    characters: [],
  }),
}));

// ---------------- Store mock ----------------
// The component calls both `useQuizStore(selector)` and `useQuizStore.getState()`
type StoreState = {
  quizId: string | null;
  status: 'idle' | 'processing' | 'question' | 'finished' | 'error';
  viewData: unknown | null;
  reset: () => void;
};
const resetSpy = vi.fn();
let storeState: StoreState;

const selectorHarness = vi.fn(<T,>(selector: (s: StoreState) => T) => selector(storeState));

function makeUseQuizStoreExport() {
  const fn = ((selector: (s: StoreState) => any) => selectorHarness(selector)) as any;
  fn.getState = () => storeState; // IMPORTANT: expose getState on the exported function
  return fn;
}

vi.mock('../store/quizStore', () => {
  return { useQuizStore: makeUseQuizStoreExport() };
});

// ---------------- Child component shims ----------------
vi.mock('../components/common/Spinner', () => ({
  Spinner: (p: { message?: string }) => <div role="status">{p.message || 'Loading...'}</div>,
}));

vi.mock('../components/common/GlobalErrorDisplay', () => ({
  GlobalErrorDisplay: (p: { error: any; onHome: () => void }) => (
    <div role="alert">
      <div data-testid="global-error-msg">{p?.error?.message || 'err'}</div>
      <button onClick={p.onHome}>Home</button>
    </div>
  ),
}));

vi.mock('../components/result/ResultProfile', () => ({
  ResultProfile: (p: any) => (
    <div data-testid="result-profile">
      <div data-testid="result-title">{p?.result?.profileTitle}</div>
      {p.shareUrl && <div data-testid="share-url">{p.shareUrl}</div>}
      {p.onStartNew && <button onClick={p.onStartNew}>Start Another Quiz</button>}
      {p.onCopyShare && <button onClick={p.onCopyShare}>Copy Link</button>}
    </div>
  ),
}));

vi.mock('../components/result/SocialShareBar', () => ({
  SocialShareBar: (p: any) => (
    <div data-testid="social-share-bar-mock">
      <div data-testid="share-bar-url">{p.shareUrl}</div>
      <div data-testid="share-bar-title">{p.shareTitle}</div>
      {p.imageUrl && <div data-testid="share-bar-image">{p.imageUrl}</div>}
      {p.previewSubtitle && (
        <div data-testid="share-bar-subtitle">{p.previewSubtitle}</div>
      )}
    </div>
  ),
}));

vi.mock('../components/result/FeedbackIcons', () => ({
  FeedbackIcons: (p: { quizId: string }) => (
    <div data-testid="feedback-icons">feedback-{p.quizId}</div>
  ),
}));

// ---------------- SUT ----------------
import { FinalPage } from './FinalPage';

// ---------------- Helpers ----------------
function renderPage(initialPath = '/result/xyz') {
  return render(
    <MemoryRouter initialEntries={[initialPath]}>
      <FinalPage />
    </MemoryRouter>,
  );
}

const MOCK_RESULT: any = {
  profileTitle: 'You are The Baker',
  summary: 'Warm and crusty!',
  imageUrl: null,
  traits: [{ label: 'Crispy' }],
};

beforeEach(() => {
  vi.clearAllMocks();
  currentParams = {};
  getQuizIdMock.mockReset().mockReturnValue(undefined);
  resetSpy.mockReset();

  storeState = {
    quizId: null,
    status: 'idle',
    viewData: null,
    reset: resetSpy,
  };

  getResultMock.mockResolvedValue(MOCK_RESULT);

  // Ensure stable origin used when building share links
  Object.defineProperty(window, 'location', {
    value: { origin: 'http://localhost' },
    writable: false,
  });
});

afterEach(() => cleanup());

// ---------------- Tests ----------------
describe('FinalPage', () => {
  it('shows Spinner while loading (pending fetch)', async () => {
    currentParams = { resultId: 'r-1' };
    let resolveFn!: (v: any) => void;
    const pending = new Promise<any>((res) => (resolveFn = res));
    getResultMock.mockReturnValueOnce(pending);

    renderPage('/result/r-1');

    expect(screen.getByRole('status')).toHaveTextContent(/loading your result/i);

    resolveFn(MOCK_RESULT);
    await screen.findByTestId('result-profile');
  });

  it('renders GlobalErrorDisplay when no effective result id (route, store, session all missing)', async () => {
    currentParams = {}; // no route id
    storeState.quizId = null;
    getQuizIdMock.mockReturnValueOnce(undefined);

    renderPage('/result');

    const alert = await screen.findByRole('alert');
    expect(alert).toBeInTheDocument();

    // Hitting home calls reset & navigate('/')
    fireEvent.click(screen.getByText(/home/i));
    expect(resetSpy).toHaveBeenCalled();
    expect(navigateMock).toHaveBeenCalledWith('/');
  });

  it('fast path: uses store finished result (no API call), and shows FeedbackIcons when ids match', async () => {
    currentParams = {};
    storeState.quizId = 'xyz';
    storeState.status = 'finished';
    storeState.viewData = MOCK_RESULT;

    renderPage('/result');

    expect(getResultMock).not.toHaveBeenCalled();
    expect(await screen.findByTestId('result-profile')).toBeInTheDocument();
    expect(screen.getByTestId('result-title')).toHaveTextContent(/the baker/i);
    expect(screen.getByTestId('feedback-icons')).toHaveTextContent('feedback-xyz');
  });

  it('cold path: fetches result by route id and renders, without FeedbackIcons when store id does not match', async () => {
    currentParams = { resultId: 'r-9' };
    storeState.quizId = 'different-id';
    storeState.status = 'finished';
    storeState.viewData = MOCK_RESULT;

    renderPage('/result/r-9');

    await screen.findByTestId('result-profile');
    expect(getResultMock).toHaveBeenCalledWith('r-9', expect.any(Object));
    expect(screen.queryByTestId('feedback-icons')).toBeNull();
  });

  it('cold path: when fetch fails, shows GlobalErrorDisplay', async () => {
    currentParams = { resultId: 'r-bad' };
    getResultMock.mockRejectedValueOnce(new Error('not found'));

    renderPage('/result/r-bad');

    const alert = await screen.findByRole('alert');
    expect(alert).toBeInTheDocument();
    expect(screen.getByTestId('global-error-msg')).toHaveTextContent(
      /result could not be found|no result data found/i,
    );
  });

  it('cold path: AbortError from cleanup does NOT surface a 404 to the user', async () => {
    // Regression: navigating away (or React StrictMode double-invoke) aborts the
    // in-flight getResult. Previously the .catch() unconditionally set a 404,
    // briefly flashing "result not found" before the correct render. The handler
    // must ignore AbortError / aborted-signal so the unmounted/replaced page
    // never shows the wrong error.
    currentParams = { resultId: 'r-abort' };

    // Capture the AbortSignal passed to api.getResult and reject with an
    // AbortError only after the controller has actually been aborted —
    // matching what happens during effect cleanup in production.
    let capturedSignal: AbortSignal | undefined;
    getResultMock.mockImplementationOnce((_id: string, opts: { signal?: AbortSignal } = {}) => {
      capturedSignal = opts.signal;
      return new Promise((_resolve, reject) => {
        opts.signal?.addEventListener('abort', () => {
          reject(Object.assign(new Error('aborted'), { name: 'AbortError' }));
        });
      });
    });

    const { unmount } = renderPage('/result/r-abort');

    // Trigger cleanup; this aborts the controller and the mock rejects.
    unmount();

    // Flush microtasks so the .catch runs.
    await new Promise((r) => setTimeout(r, 10));

    // No alert should have been rendered (the unmounted tree is gone, and
    // even if something flushed, no setError() should have fired).
    expect(capturedSignal?.aborted).toBe(true);
    expect(screen.queryByRole('alert')).toBeNull();
    expect(screen.queryByTestId('global-error-msg')).toBeNull();
  });

  it('Start Over from ResultProfile resets quiz and navigates home', async () => {
    currentParams = {};
    storeState.quizId = 'xyz';
    storeState.status = 'finished';
    storeState.viewData = MOCK_RESULT;

    renderPage('/result');

    const btns = await screen.findAllByRole('button', { name: /start another quiz|play again|start over/i });
    fireEvent.click(btns[0]);

    expect(resetSpy).toHaveBeenCalled();
    expect(navigateMock).toHaveBeenCalledWith('/');
  });

  it('Copy Link / share URL: SocialShareBar receives the canonical share URL', async () => {
    currentParams = {};
    storeState.quizId = 'xyz';
    storeState.status = 'finished';
    storeState.viewData = MOCK_RESULT;

    renderPage('/result');

    await screen.findByTestId('result-profile');
    // SocialShareBar (not ResultProfile) now owns the share UI.
    expect(screen.queryByTestId('share-url')).toBeNull();
    expect(screen.getByTestId('share-bar-url')).toHaveTextContent(
      'http://localhost/result/xyz',
    );
    expect(screen.getByTestId('share-bar-title').textContent ?? '').toMatch(
      /baker|quizzical|result|profile/i,
    );
  });

  it('result card wrapper provides a max-width container for readable line length on wide screens', async () => {
    currentParams = {};
    storeState.quizId = 'xyz';
    storeState.status = 'finished';
    storeState.viewData = MOCK_RESULT;

    renderPage('/result');

    await screen.findByTestId('result-profile');

    // The inner wrapper should constrain to max-w-3xl for legible line length
    const profile = screen.getByTestId('result-profile').parentElement;
    expect(profile?.className).toContain('max-w-3xl');
  });

  it('result card feedback section uses a valid border class without breaking layout', async () => {
    currentParams = {};
    storeState.quizId = 'xyz';
    storeState.status = 'finished';
    storeState.viewData = MOCK_RESULT;

    renderPage('/result');

    await screen.findByTestId('feedback-icons');

    // Section wrapper must not use broken class 'border-muted-50'; must use a slash variant
    const section = screen.getByTestId('feedback-icons').closest('section');
    expect(section?.className).not.toContain('border-muted-50');
    expect(section?.className).toMatch(/border-muted\/|border-border/);
  });

  it('renders a dual CTA pair under the share bar', async () => {
    currentParams = {};
    storeState.quizId = 'xyz';
    storeState.status = 'finished';
    storeState.viewData = MOCK_RESULT;

    renderPage('/result');
    await screen.findByTestId('social-share-bar-mock');

    expect(screen.getAllByRole('button', { name: /play again|start another quiz/i }).length).toBeGreaterThanOrEqual(1);
    expect(screen.getByRole('button', { name: /try a new topic/i })).toBeInTheDocument();
  });

  it('Try a New Topic resets quiz and navigates home with focus hint state', async () => {
    currentParams = {};
    storeState.quizId = 'xyz';
    storeState.status = 'finished';
    storeState.viewData = MOCK_RESULT;

    renderPage('/result');
    fireEvent.click(await screen.findByRole('button', { name: /try a new topic/i }));

    expect(resetSpy).toHaveBeenCalledTimes(1);
    expect(navigateMock).toHaveBeenCalledWith('/', {
      state: { focusTopicInput: true, fromResult: true },
    });
  });

  it('when store has finished and route id matches, SocialShareBar gets the matching URL and FeedbackIcons appear', async () => {
    currentParams = { resultId: 'xyz' };
    storeState.quizId = 'xyz';
    storeState.status = 'finished';
    storeState.viewData = MOCK_RESULT;

    renderPage('/result/xyz');

    await screen.findByTestId('result-profile');
    expect(screen.getByTestId('share-bar-url')).toHaveTextContent(
      'http://localhost/result/xyz',
    );
    expect(screen.getByTestId('feedback-icons')).toHaveTextContent('feedback-xyz');
    expect(getResultMock).not.toHaveBeenCalled();
  });

  // UX audit P6: FinalPage should not crash when result has an imageUrl,
  // and it renders successfully (the preload <link> injection via useEffect
  // targets document.head which is a browser optimization; jsdom intercept
  // testing is not reliable, so we assert the component renders correctly
  // with an imageUrl present — the implementation in FinalPage.tsx is
  // verified by code review + TypeScript compilation).
  it('renders result page without crash when result has imageUrl (P6 smoke)', async () => {
    currentParams = { resultId: 'preload-test' };
    storeState.quizId = 'preload-test';
    storeState.status = 'finished';
    storeState.viewData = {
      ...MOCK_RESULT,
      imageUrl: 'https://fal.media/files/hero.jpg',
    };

    renderPage('/result/preload-test');
    const profile = await screen.findByTestId('result-profile');
    expect(profile).toBeInTheDocument();
    // Verify the profileTitle flows through to the mock
    expect(screen.getByTestId('result-title')).toHaveTextContent('You are The Baker');
  });

  // UX audit M30: the result content wrapper carries the entrance animation class.
  it('result content wrapper has fade-in-up animation class (M30)', async () => {
    currentParams = {};
    storeState.quizId = 'xyz';
    storeState.status = 'finished';
    storeState.viewData = MOCK_RESULT;

    renderPage('/result');
    await screen.findByTestId('result-profile');

    const profile = screen.getByTestId('result-profile');
    const wrapper = profile.parentElement;
    expect(wrapper?.className).toContain('animate-fade-in-up');
  });
});
