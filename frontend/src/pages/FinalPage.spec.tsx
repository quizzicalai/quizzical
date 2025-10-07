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

  it('Start Over from ResultProfile resets quiz and navigates home', async () => {
    currentParams = {};
    storeState.quizId = 'xyz';
    storeState.status = 'finished';
    storeState.viewData = MOCK_RESULT;

    renderPage('/result');

    const btn = await screen.findByRole('button', { name: /start another quiz/i });
    fireEvent.click(btn);

    expect(resetSpy).toHaveBeenCalled();
    expect(navigateMock).toHaveBeenCalledWith('/');
  });

  it('Copy Link from ResultProfile writes the correct URL to clipboard', async () => {
    currentParams = {};
    storeState.quizId = 'xyz';
    storeState.status = 'finished';
    storeState.viewData = MOCK_RESULT;

    const writeMock = vi.fn().mockResolvedValue(undefined);
    Object.assign(navigator, { clipboard: { writeText: writeMock } });

    renderPage('/result');

    await screen.findByTestId('result-profile');
    expect(screen.getByTestId('share-url')).toHaveTextContent('http://localhost/result/xyz');

    fireEvent.click(screen.getByRole('button', { name: /copy link/i }));
    expect(writeMock).toHaveBeenCalledWith('http://localhost/result/xyz');
  });

  it('when store has finished and route id matches, share URL uses the matching id and FeedbackIcons appear', async () => {
    currentParams = { resultId: 'xyz' };
    storeState.quizId = 'xyz';
    storeState.status = 'finished';
    storeState.viewData = MOCK_RESULT;

    renderPage('/result/xyz');

    await screen.findByTestId('result-profile');
    expect(screen.getByTestId('share-url')).toHaveTextContent('http://localhost/result/xyz');
    expect(screen.getByTestId('feedback-icons')).toHaveTextContent('feedback-xyz');
    expect(getResultMock).not.toHaveBeenCalled();
  });
});
