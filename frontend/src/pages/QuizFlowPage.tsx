// frontend/src/pages/QuizFlowPage.tsx
import React, { useCallback, useEffect, useState } from 'react';
import { useNavigate } from 'react-router-dom';
import { useConfig } from '../context/ConfigContext';
import {
  useQuizView,
  useQuizProgress,
  useQuizActions,
  useQuizMediaStore,
} from '../store/quizStore';
import * as api from '../services/apiService';
import { SynopsisView } from '../components/quiz/SynopsisView';
import { QuestionView } from '../components/quiz/QuestionView';
import { Spinner } from '../components/common/Spinner';
import { ErrorPage } from './ErrorPage';
import { LoadingCard } from '../components/loading/LoadingCard';
import { HeroCard } from '../components/layout/HeroCard';
import { QUIZ_PROGRESS_LINES } from '../components/loading/LoadingNarration'; // NEW
import type { Question, Synopsis, CharacterProfile } from '../types/quiz';
import { useQuizMedia } from '../hooks/useQuizMedia';
import { clearQuizId } from '../utils/session';
import type { ApiError } from '../types/api';

const IS_DEV = import.meta.env.DEV === true;

export const QuizFlowPage: React.FC = () => {
  const navigate = useNavigate();
  const { config } = useConfig();

  const {
    quizId,
    currentView,
    viewData,
    status,
    isPolling,
    isSubmittingAnswer,
    uiError,
    uiErrorCode,
    uiErrorTraceId,
  } = useQuizView();

  const { answeredCount } = useQuizProgress();

  const {
    beginPolling,
    setError,
    reset,
    markAnswered,
    submitAnswerStart,
    submitAnswerEnd,
    mergeMediaSnapshot,
    hydrateStatus: _hydrateStatus, // retained
  } = useQuizActions();

  // Blackbox fix #4(b) — persisted async image URLs (survive a view change).
  const { characterImages: storedCharacterImages, synopsisImageUrl: storedSynopsisUrl } =
    useQuizMediaStore();

  const [submissionError, setSubmissionError] = useState<string | null>(null);
  const [selectedAnswer, setSelectedAnswer] = useState<string | null>(null);
  // Deep-review #26: client-side in-flight guard for the synopsis "Start Quiz"
  // CTA. Without it, a double-click double-fires /quiz/proceed — two paid
  // background agent runs (the server lock only covers overlapping handler
  // windows, not a fast second click that lands after the first returns).
  const [isProceeding, setIsProceeding] = useState(false);

  // NEW: flag for post-synopsis loading narration
  const [useQuizProgressLines, setUseQuizProgressLines] = useState(false);

  // Config is guaranteed by router layout
  const content = config!.content;
  const errorContent = content.errors ?? {};
  const loadingContent = content.loadingStates ?? {};

  // Recover polling if remounts on idle
  useEffect(() => {
    if (quizId && currentView === 'idle' && !isPolling) {
      if (IS_DEV) console.warn('[QuizFlowPage] idle-recovery beginPolling', { quizId });
      beginPolling({ reason: 'idle-recovery' });
    }
  }, [quizId, currentView, isPolling, beginPolling]);

  // If session lost, go home
  useEffect(() => {
    if (!quizId && !isPolling) {
      if (IS_DEV) console.warn('[QuizFlowPage] Missing quizId; navigating home');
      navigate('/', { replace: true });
    }
  }, [quizId, isPolling, navigate]);

  // When result arrives, redirect
  useEffect(() => {
    if (currentView === 'result') {
      if (IS_DEV) console.warn('[QuizFlowPage] finished; redirecting to result page', { quizId });
      if (quizId) navigate(`/result/${encodeURIComponent(quizId)}`, { replace: true });
      else navigate('/result', { replace: true });
    }
  }, [currentView, quizId, navigate]);

  // Reset the special narration once we leave the loading state
  useEffect(() => {
    if (currentView === 'question' || currentView === 'result' || currentView === 'synopsis') {
      setUseQuizProgressLines(false);
    }
  }, [currentView]);

  // Deep-review #12: a 404/403 on /quiz/next or /quiz/proceed means the session
  // was expired or evicted — it is TERMINAL, not retriable. Mirror the poll
  // path: flip to a fatal error (so the ErrorPage renders with a working Start
  // Over) and clear the persisted quizId so a refresh cannot resurrect the dead
  // session. Returns true when it handled the error so the caller can bail out
  // of its normal (retriable) error handling.
  const handleTerminalSessionError = useCallback(
    (err: unknown): boolean => {
      const status = (err as ApiError | undefined)?.status;
      if (status === 404 || status === 403) {
        const apiErr = err as ApiError | undefined;
        setError(
          apiErr?.whimsical || 'Your session has expired. Please start a new quiz.',
          true,
          { code: apiErr?.qfCode ?? null, traceId: apiErr?.traceId ?? null },
        );
        clearQuizId();
        return true;
      }
      return false;
    },
    [setError],
  );

  const handleProceed = useCallback(async () => {
    setSubmissionError(null);
    // Deep-review #26: ignore re-entrant clicks while a proceed is in flight so
    // a double-click cannot launch two paid agent runs.
    if (!quizId || isProceeding) return;
    setIsProceeding(true);
    try {
      if (IS_DEV) console.warn('[QuizFlowPage] handleProceed -> /quiz/proceed', { quizId });
      // NEW: switch narration to post-synopsis script
      setUseQuizProgressLines(true);

      await api.proceedQuiz(quizId);
      if (IS_DEV) console.warn('[QuizFlowPage] proceed acknowledged; beginPolling(reason=proceed)');
      await beginPolling({ reason: 'proceed' });
    } catch (err: any) {
      if (IS_DEV) console.error('[QuizFlowPage] handleProceed error', err);
      // AC-FE-LOCK-PROD-1: 409 SESSION_BUSY -> specific message + skip narration revert.
      if (err?.code === 'session_busy' || err?.errorCode === 'SESSION_BUSY') {
        setSubmissionError('We are still preparing your quiz. Hang tight…');
        // The BE has the session locked; just begin polling so we surface the
        // next state when the lock releases.
        await beginPolling({ reason: 'proceed-busy' });
        return;
      }
      // Deep-review #12: expired/evicted session is terminal — do not leave a
      // retriable inline error that loops /quiz/proceed (a paid agent run).
      if (handleTerminalSessionError(err)) {
        setUseQuizProgressLines(false);
        return;
      }
      setError(err.message || 'Polling for the next question failed.');
      setUseQuizProgressLines(false);
    } finally {
      // Deep-review #26: always release the in-flight guard. Polling continues
      // to own the "loading" surface after this returns, so re-enabling the CTA
      // here is safe (the view has already moved past the synopsis by the time
      // proceed resolves in the happy path).
      setIsProceeding(false);
    }
  }, [quizId, isProceeding, beginPolling, setError, handleTerminalSessionError]);

  const handleSelectAnswer = useCallback(
    async (answerId: string) => {
      if (!quizId || isSubmittingAnswer) return;

      setSelectedAnswer(answerId);
      submitAnswerStart();
      setSubmissionError(null);

      try {
        const question = (currentView === 'question' ? (viewData as Question) : null);

        // Deep-review #4: submit the SERVED ordinal, not the FE-local
        // `answeredCount`. The server owns the question position; `questionNumber`
        // is the 1-based ordinal of the question on screen, so its 0-based index
        // is `questionNumber - 1`. Deriving the index from `answeredCount` meant a
        // silently-dropped duplicate `/quiz/next` (BE 202s duplicates) shifted all
        // later history by one. Fall back to `answeredCount` only if the BE did
        // not surface an ordinal.
        const servedNumber = question?.questionNumber;
        const questionIndex =
          typeof servedNumber === 'number' && servedNumber >= 1
            ? servedNumber - 1
            : answeredCount;

        let optionIndex: number | undefined;
        let answerText: string | undefined;

        if (question && Array.isArray(question.answers)) {
          const byId = question.answers.findIndex(a => a.id === answerId);
          if (byId >= 0) {
            optionIndex = byId;
            answerText = question.answers[byId]?.text ?? undefined;
          } else {
            const m = /^opt-(\d+)$/.exec(answerId);
            if (m) {
              optionIndex = Number(m[1]);
              answerText = question.answers[optionIndex]?.text ?? undefined;
            }
          }
        }

        await api.submitAnswer(quizId, { questionIndex, optionIndex, answer: answerText });

        markAnswered();
        await beginPolling({ reason: 'after-answer' }); // just poll; don't proceed again
        setSelectedAnswer(null);
      } catch (err: any) {
        // AC-FE-LOCK-PROD-1: 409 SESSION_BUSY on /quiz/next -> friendly message,
        // poll instead of surfacing a hard error.
        if (err?.code === 'session_busy' || err?.errorCode === 'SESSION_BUSY') {
          setSubmissionError('Still scoring your previous answer—one moment…');
          await beginPolling({ reason: 'answer-busy' });
          setSelectedAnswer(null);
          return;
        }
        // Deep-review #12: a 404/403 here means the session expired/was evicted.
        // Make it terminal (ErrorPage + Start Over) instead of an endless inline
        // "Try Again" 404 loop.
        if (handleTerminalSessionError(err)) {
          setSelectedAnswer(null);
          return;
        }
        const message =
          err.message || errorContent.submissionFailed || 'There was an error submitting your answer.';
        setSubmissionError(message);
        setError(message, false);
      } finally {
        submitAnswerEnd();
      }
    },
    [
      quizId,
      isSubmittingAnswer,
      submitAnswerStart,
      markAnswered,
      beginPolling,
      submitAnswerEnd,
      setError,
      handleTerminalSessionError,
      errorContent.submissionFailed,
      answeredCount,
      currentView,
      viewData,
    ]
  );

  // Deep-review #7: the question-view retry must NEVER call /quiz/proceed (a
  // paid agent run). If the user has a failed answer submission pending, retry
  // THAT submission; otherwise the error is a transient poll error, so simply
  // re-poll for the next state via beginPolling. Both are free/idempotent.
  const handleQuestionRetry = useCallback(() => {
    if (submissionError && selectedAnswer) {
      handleSelectAnswer(selectedAnswer);
      return;
    }
    setSubmissionError(null);
    beginPolling({ reason: 'manual-retry' });
  }, [submissionError, selectedAnswer, handleSelectAnswer, beginPolling]);

  const handleResetAndHome = () => {
    reset();
    navigate('/');
  };

  // ---------------------------------------------------------------------------
  // IMPORTANT: All hooks (incl. useQuizMedia) must be called unconditionally,
  // BEFORE any early returns. A previous version called useQuizMedia after the
  // `idle/isPolling` early return, which violated the Rules of Hooks: the hook
  // count changed between renders (synopsis → polling → question), causing
  // React to throw "Rendered more hooks than during the previous render"
  // which surfaced as a generic crash on the question screen in production.
  // ---------------------------------------------------------------------------

  // Synopsis + optional characters (only meaningful when currentView==='synopsis',
  // but the derivation is cheap and the hook below must run on every render).
  const synopsis = currentView === 'synopsis'
    ? ((viewData as (Synopsis & { characters?: CharacterProfile[] })) || null)
    : null;
  const extraCharacters = synopsis?.characters;

  // Asynchronously-generated images: poll the backend snapshot endpoint while
  // the synopsis is on screen and merge in URLs as they become available. This
  // never blocks rendering — the page works (and remains interactive) even if
  // the hook never returns anything. The hook itself short-circuits when
  // `enabled` is false, so calling it on non-synopsis renders is free.
  const characterListForMedia: readonly CharacterProfile[] =
    (Array.isArray(synopsis?.characters) && synopsis.characters.length > 0
      ? synopsis.characters
      : (Array.isArray(extraCharacters) ? extraCharacters : [])) || [];
  const expectedCharacterNames = characterListForMedia.map(c => c.name);
  // Blackbox fix #4(b) — keep polling the media snapshot while the quiz is live
  // (quizId set AND not finished), NOT only on the synopsis screen. The cast +
  // synopsis images often arrive AFTER the user has proceeded to the questions;
  // continuing to poll lets them resolve, and we persist every resolved URL into
  // the store so it survives the view change and shows through to the result.
  const { snapshot: mediaSnapshot, characterImageMap } = useQuizMedia(quizId, {
    enabled: !!quizId && status !== 'finished' && currentView !== 'result',
    expectedCharacterNames,
    expectSynopsisImage: true,
    // Cover a slow hero render across the whole question phase.
    maxDurationMs: 120_000,
  });

  // Persist resolved URLs into the store as they arrive (additive, survives view
  // changes). FinalPage reads these so late cast/result images aren't discarded.
  useEffect(() => {
    if (mediaSnapshot) mergeMediaSnapshot(mediaSnapshot);
  }, [mediaSnapshot, mergeMediaSnapshot]);

  // Error view
  if (currentView === 'error' && uiError) {
    return (
      <ErrorPage
        title={errorContent.title || 'Something went wrong'}
        message={uiError}
        code={uiErrorCode ?? undefined}
        traceId={uiErrorTraceId ?? undefined}
        primaryCta={{
          label: errorContent.startOver || 'Start Over',
          onClick: handleResetAndHome,
        }}
      />
    );
  }

  // Loading/processing → LoadingCard inside landing-style wrapper.
  // AC-PROD-R8-LOAD-2 — once we already have a question on screen, do not
  // swap to the global LoadingCard while polling for the next one.
  // Stay on the question view so the upper-right ThinkingIndicator can
  // surface the in-flight "thinking" state.
  if (
    currentView === 'idle' ||
    (isPolling && !isSubmittingAnswer && currentView !== 'question')
  ) {
    return (
      <div className="flex items-center justify-center flex-grow" data-testid="quiz-loading-card">
        <div className="lp-wrapper w-full flex items-start justify-center p-4 sm:p-6">
          <LoadingCard
            lines={useQuizProgressLines ? QUIZ_PROGRESS_LINES : undefined}
            onStartOver={handleResetAndHome}
          />
        </div>
      </div>
    );
  }

  // Blackbox fix #4(b) — resolve each image URL from (1) the data itself, (2) the
  // freshest live poll snapshot, then (3) the persisted store (which retains URLs
  // that resolved earlier, even before a view change). This makes the cast images
  // sticky across synopsis → question and through to the result page.
  const resolveCharUrl = (c: CharacterProfile): string | undefined =>
    c.imageUrl ?? characterImageMap[c.name] ?? storedCharacterImages[c.name] ?? undefined;
  const resolvedSynopsisUrl = (url?: string) =>
    url ?? mediaSnapshot?.synopsisImageUrl ?? storedSynopsisUrl ?? undefined;

  const synopsisWithImage: (Synopsis & { characters?: CharacterProfile[] }) | null = synopsis
    ? {
        ...synopsis,
        imageUrl: resolvedSynopsisUrl(synopsis.imageUrl),
        characters: Array.isArray(synopsis.characters)
          ? synopsis.characters.map(c => ({
              ...c,
              imageUrl: resolveCharUrl(c),
            }))
          : synopsis.characters,
      }
    : null;
  const extraCharactersWithImages = Array.isArray(extraCharacters)
    ? extraCharacters.map(c => ({
        ...c,
        imageUrl: resolveCharUrl(c),
      }))
    : extraCharacters;

  // Main routing
  switch (currentView) {
    case 'synopsis':
      // Wrap in HeroCard so geometry matches LoadingCard → minimal CLS
      return (
        <div className="flex items-center justify-center flex-grow">
          <div className="lp-wrapper w-full flex items-start justify-center p-4 sm:p-6">
            <HeroCard ariaLabel="Quiz hero card">
              <SynopsisView
                synopsis={synopsisWithImage as Synopsis | null}
                characters={extraCharactersWithImages}
                onProceed={handleProceed}
                onStartOver={handleResetAndHome}
                // Deep-review #26: disable the CTA for the WHOLE proceed window
                // (before polling even starts) so a double-click cannot double-
                // fire /quiz/proceed.
                isLoading={isPolling || isProceeding}
                inlineError={submissionError || uiError}
              />
            </HeroCard>
          </div>
        </div>
      );

    case 'question':
      // NEW: Use HeroCard, but hide the wizard cat (showHero={false})
      return (
        <div className="flex items-center justify-center flex-grow">
          <div className="lp-wrapper w-full flex items-start justify-center p-4 sm:p-6">
            <HeroCard ariaLabel="Question card" showHero={false}>
              <QuestionView
                question={viewData as Question}
                onSelectAnswer={handleSelectAnswer}
                // AC-PROD-R8-LOAD-2 — keep the spinner up for the entire
                // gap between the user's submit and the next question's
                // arrival, not just the brief POST window.
                isLoading={isSubmittingAnswer || isPolling}
                selectedAnswerId={selectedAnswer}
                // The agent ends the quiz on either max-questions OR a
                // confidence threshold, so we pass the running ordinal but
                // deliberately omit any total. Phrase comes from the BE per
                // question and is shown in the upper-right pill in place of
                // a misleading "% complete" indicator.
                questionNumber={
                  (viewData as Question | null)?.questionNumber ?? answeredCount + 1
                }
                progressPhrase={(viewData as Question | null)?.progressPhrase}
                confidence={(viewData as Question | null)?.confidence ?? null}
                // AC-PROD-R7-TW-POOL-2 — once the user has answered enough
                // questions that the agent is likely to finalize on this
                // submission (rather than ask another), switch the
                // placeholder pool to the profile-writing variant. The
                // agent's confidence threshold typically fires by Q8.
                mode={(isSubmittingAnswer || isPolling) && answeredCount >= 7 ? 'finalizing' : 'thinking'}
                inlineError={submissionError || uiError}
                // Deep-review #7: retry re-polls (or re-submits a failed answer),
                // NEVER /quiz/proceed — that fired an extra paid agent run from
                // a transient poll error.
                onRetry={handleQuestionRetry}
              />
            </HeroCard>
          </div>
        </div>
      );

    // transient while redirecting
    case 'result':
      return <Spinner message="Loading your result..." />;

    default:
      if (IS_DEV) console.warn('[QuizFlowPage] Unknown currentView, showing spinner', { currentView });
      return <Spinner message={loadingContent.quiz || 'Preparing your quiz...'} />;
  }
};
