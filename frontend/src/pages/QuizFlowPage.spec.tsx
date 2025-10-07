/* eslint no-console: ["error", { "allow": ["log", "warn", "error"] }] */
import React from 'react';
import { describe, it, expect, vi, beforeEach, afterEach } from 'vitest';
import type { Mock } from 'vitest';
import { render, screen, cleanup, fireEvent, waitFor } from '@testing-library/react';
import { MemoryRouter } from 'react-router-dom';

import { CONFIG_FIXTURE } from '../../tests/fixtures/config.fixture';

// ---- Mocks ------------------------------------------------------------------

// useNavigate spy
const navigateMock = vi.fn();
vi.mock('react-router-dom', async (orig: any) => {
  const mod = await orig();
  return {
    ...(mod as Record<string, any>),
    useNavigate: () => navigateMock,
  };
});

// Config context -> always return fixture
vi.mock('../context/ConfigContext', () => ({
  useConfig: vi.fn(() => ({ config: CONFIG_FIXTURE })),
}));

// Store: expose replaceable fns/values for each test
const useQuizViewMock = vi.fn();
const useQuizProgressMock = vi.fn();
const beginPollingMock = vi.fn();
const setErrorMock = vi.fn();
const resetMock = vi.fn();
const markAnsweredMock = vi.fn();
const submitAnswerStartMock = vi.fn();
const submitAnswerEndMock = vi.fn();
const hydrateStatusMock = vi.fn();

vi.mock('../store/quizStore', () => ({
  useQuizView: (...args: any[]) => useQuizViewMock(...args),
  useQuizProgress: (...args: any[]) => useQuizProgressMock(...args),
  useQuizActions: () => ({
    beginPolling: beginPollingMock,
    setError: setErrorMock,
    reset: resetMock,
    markAnswered: markAnsweredMock,
    submitAnswerStart: submitAnswerStartMock,
    submitAnswerEnd: submitAnswerEndMock,
    hydrateStatus: hydrateStatusMock,
  }),
}));

// API calls
const proceedQuizMock = vi.fn();
const submitAnswerMock = vi.fn();
vi.mock('../services/apiService', () => ({
  proceedQuiz: (...args: any[]) => proceedQuizMock(...args),
  submitAnswer: (...args: any[]) => submitAnswerMock(...args),
}));

// Child components -> ultra-thin shims to trigger callbacks
vi.mock('../components/quiz/SynopsisView', () => ({
  SynopsisView: (props: any) => (
    <div>
      <div data-testid="synopsis" />
      <button onClick={props.onProceed}>Start Quiz</button>
      {props.inlineError && <div role="alert">{props.inlineError}</div>}
    </div>
  ),
}));

vi.mock('../components/quiz/QuestionView', () => ({
  QuestionView: (props: any) => (
    <div>
      <div data-testid="question" />
      <button onClick={() => props.onSelectAnswer('a1')}>Answer A1</button>
      {props.inlineError && <div role="alert">{props.inlineError}</div>}
      {props.onRetry && <button onClick={props.onRetry}>Retry</button>}
    </div>
  ),
}));

// Spinner (leave simple so we can assert message)
vi.mock('../components/common/Spinner', () => ({
  Spinner: (p: { message?: string }) => <div role="status">{p.message || 'Loading...'}</div>,
}));

// Error page – render real component to assert CTA behavior
import { ErrorPage } from './ErrorPage';
vi.mock('./ErrorPage', async () => {
  const actual = await vi.importActual<typeof import('./ErrorPage')>('./ErrorPage');
  return actual;
});

// ---- SUT --------------------------------------------------------------------
import { QuizFlowPage } from './QuizFlowPage';

// ---- Helpers ----------------------------------------------------------------
function renderPage() {
  return render(
    <MemoryRouter initialEntries={['/quiz']}>
      <QuizFlowPage />
    </MemoryRouter>
  );
}

beforeEach(() => {
  vi.clearAllMocks();

  // sane defaults; override per test
  useQuizViewMock.mockReturnValue({
    quizId: 'q-1',
    currentView: 'idle',
    viewData: null,
    isPolling: false,
    isSubmittingAnswer: false,
    uiError: null,
  });
  useQuizProgressMock.mockReturnValue({
    answeredCount: 0,
    totalTarget: 3,
  });

  proceedQuizMock.mockResolvedValue(undefined);
  submitAnswerMock.mockResolvedValue(undefined);
  beginPollingMock.mockResolvedValue(undefined);
});

afterEach(() => {
  cleanup();
});

// ---- Tests ------------------------------------------------------------------

describe('QuizFlowPage', () => {
  it('redirects home when quizId is missing and not polling', () => {
    useQuizViewMock.mockReturnValue({
      quizId: null,
      currentView: 'idle',
      viewData: null,
      isPolling: false,
      isSubmittingAnswer: false,
      uiError: null,
    });

    renderPage();

    expect(navigateMock).toHaveBeenCalledWith('/', { replace: true });
  });

  it('on mount: recovers by beginPolling when view is idle, quizId exists, and not polling', async () => {
    useQuizViewMock.mockReturnValue({
      quizId: 'q-2',
      currentView: 'idle',
      viewData: null,
      isPolling: false,
      isSubmittingAnswer: false,
      uiError: null,
    });

    renderPage();

    await waitFor(() => {
      expect(beginPollingMock).toHaveBeenCalledWith({ reason: 'idle-recovery' });
    });
  });

  it('shows Spinner when idle or when polling without submission', () => {
    // idle
    useQuizViewMock.mockReturnValue({
      quizId: 'q-3',
      currentView: 'idle',
      viewData: null,
      isPolling: false,
      isSubmittingAnswer: false,
      uiError: null,
    });

    const { rerender } = renderPage();
    expect(screen.getByRole('status')).toHaveTextContent(/preparing your quiz/i);

    // polling background
    useQuizViewMock.mockReturnValue({
      quizId: 'q-3',
      currentView: 'synopsis',
      viewData: {},
      isPolling: true,
      isSubmittingAnswer: false,
      uiError: null,
    });
    rerender(
      <MemoryRouter>
        <QuizFlowPage />
      </MemoryRouter>
    );
    expect(screen.getByRole('status')).toHaveTextContent(/preparing your quiz/i);
  });

  it('navigates to result page when currentView is result (with quizId)', () => {
    useQuizViewMock.mockReturnValue({
      quizId: 'abc-123',
      currentView: 'result',
      viewData: null,
      isPolling: false,
      isSubmittingAnswer: false,
      uiError: null,
    });

    renderPage();

    expect(navigateMock).toHaveBeenCalledWith('/result/abc-123', { replace: true });
  });

  it('synopsis flow: clicking Start triggers proceedQuiz then beginPolling', async () => {
    useQuizViewMock.mockReturnValue({
      quizId: 'abc-123',
      currentView: 'synopsis',
      viewData: { title: 'Baking Basics', summary: 'Let’s bake.' },
      isPolling: false,
      isSubmittingAnswer: false,
      uiError: null,
    });

    renderPage();

    fireEvent.click(screen.getByRole('button', { name: /start quiz/i }));

    await waitFor(() => {
      expect(proceedQuizMock).toHaveBeenCalledWith('abc-123');
    });
    expect(beginPollingMock).toHaveBeenCalledWith({ reason: 'proceed' });
  });

  it('question flow: selecting an answer submits with derived indices and polls; respects store actions', async () => {
    // answeredCount === 1 to test questionIndex calculation
    useQuizProgressMock.mockReturnValue({ answeredCount: 1, totalTarget: 4 });

    useQuizViewMock.mockReturnValue({
      quizId: 'abc-456',
      currentView: 'question',
      viewData: {
        id: 'q1',
        text: 'Pick one',
        answers: [{ id: 'a1', text: 'A' }, { id: 'a2', text: 'B' }],
      },
      isPolling: false,
      isSubmittingAnswer: false,
      uiError: null,
    });

    renderPage();

    fireEvent.click(screen.getByRole('button', { name: /answer a1/i }));

    expect(submitAnswerStartMock).toHaveBeenCalled();

    await waitFor(() => {
      expect(submitAnswerMock).toHaveBeenCalledTimes(1);
    });

    const [quizId, payload] = submitAnswerMock.mock.calls[0];
    expect(quizId).toBe('abc-456');
    // questionIndex equals answeredCount (1)
    expect(payload).toEqual({ questionIndex: 1, optionIndex: 0, answer: 'A' });

    expect(markAnsweredMock).toHaveBeenCalled();
    expect(beginPollingMock).toHaveBeenCalledWith({ reason: 'after-answer' });
    expect(submitAnswerEndMock).toHaveBeenCalled();
  });

  it('question flow: on submit error, sets submissionError and calls setError without crashing', async () => {
    submitAnswerMock.mockRejectedValueOnce(new Error('boom'));

    useQuizViewMock.mockReturnValue({
      quizId: 'abc-789',
      currentView: 'question',
      viewData: {
        id: 'qz',
        text: 'Pick one',
        answers: [{ id: 'a1', text: 'A' }],
      },
      isPolling: false,
      isSubmittingAnswer: false,
      uiError: null,
    });

    renderPage();

    fireEvent.click(screen.getByRole('button', { name: /answer a1/i }));

    // Error bubbled to inline error + setError called
    const alert = await screen.findByRole('alert');
    expect(alert).toHaveTextContent(/there was an error submitting your answer|boom/i);
    expect(setErrorMock).toHaveBeenCalled();
    expect(submitAnswerEndMock).toHaveBeenCalled();
  });

  it('error view: renders ErrorPage with Start Over CTA that resets store and navigates home', () => {
    useQuizViewMock.mockReturnValue({
      quizId: 'abc-err',
      currentView: 'error',
      viewData: null,
      isPolling: false,
      isSubmittingAnswer: false,
      uiError: 'Yikes',
    });

    renderPage();

    // Button label comes from config.errors.startOver
    const btn = screen.getByRole('button', { name: /start over/i });
    fireEvent.click(btn);

    expect(resetMock).toHaveBeenCalled();
    expect(navigateMock).toHaveBeenCalledWith('/');
  });
});
