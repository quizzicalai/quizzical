import React, { useCallback, useEffect, useState } from 'react';
import { useNavigate } from 'react-router-dom';
import { useConfig } from '../context/ConfigContext';
import { useQuizView, useQuizProgress, useQuizActions } from '../store/quizStore';
import * as api from '../services/apiService';
import { SynopsisView } from '../components/quiz/SynopsisView';
import { QuestionView } from '../components/quiz/QuestionView';
import { Spinner } from '../components/common/Spinner';
import { ErrorPage } from './ErrorPage';
import { LoadingCard } from '../components/loading/LoadingCard';
import { HeroCard } from '../components/layout/HeroCard';
import type { Question, Synopsis, CharacterProfile } from '../types/quiz';

const IS_DEV = import.meta.env.DEV === true;

export const QuizFlowPage: React.FC = () => {
  const navigate = useNavigate();
  const { config } = useConfig();

  const {
    quizId,
    currentView,
    viewData,
    isPolling,
    isSubmittingAnswer,
    uiError,
  } = useQuizView();

  const { answeredCount, totalTarget } = useQuizProgress();

  const {
    beginPolling,
    setError,
    reset,
    markAnswered,
    submitAnswerStart,
    submitAnswerEnd,
    hydrateStatus, // retained
  } = useQuizActions();

  const [submissionError, setSubmissionError] = useState<string | null>(null);
  const [selectedAnswer, setSelectedAnswer] = useState<string | null>(null);

  // Config is guaranteed by router layout
  const content = config!.content;
  const errorContent = content.errors ?? {};
  const loadingContent = content.loadingStates ?? {};

  // Recover polling if remounts on idle
  useEffect(() => {
    if (quizId && currentView === 'idle' && !isPolling) {
      if (IS_DEV) console.log('[QuizFlowPage] idle-recovery beginPolling', { quizId });
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
      if (IS_DEV) console.log('[QuizFlowPage] finished; redirecting to result page', { quizId });
      if (quizId) navigate(`/result/${encodeURIComponent(quizId)}`, { replace: true });
      else navigate('/result', { replace: true });
    }
  }, [currentView, quizId, navigate]);

  const handleProceed = useCallback(async () => {
    setSubmissionError(null);
    if (!quizId) return;
    try {
      if (IS_DEV) console.log('[QuizFlowPage] handleProceed -> /quiz/proceed', { quizId });
      await api.proceedQuiz(quizId);
      if (IS_DEV) console.log('[QuizFlowPage] proceed acknowledged; beginPolling(reason=proceed)');
      await beginPolling({ reason: 'proceed' });
    } catch (err: any) {
      if (IS_DEV) console.error('[QuizFlowPage] handleProceed error', err);
      setError(err.message || 'Polling for the next question failed.');
    }
  }, [quizId, beginPolling, setError]);

  const handleSelectAnswer = useCallback(
    async (answerId: string) => {
      if (!quizId || isSubmittingAnswer) return;

      setSelectedAnswer(answerId);
      submitAnswerStart();
      setSubmissionError(null);

      try {
        // questionIndex equals answeredCount
        const questionIndex = answeredCount;

        const question = (currentView === 'question' ? (viewData as Question) : null);
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
      errorContent.submissionFailed,
      answeredCount,
      currentView,
      viewData,
    ]
  );

  const handleRetrySubmission = useCallback(() => {
    if (selectedAnswer) handleSelectAnswer(selectedAnswer);
  }, [selectedAnswer, handleSelectAnswer]);

  const handleResetAndHome = () => {
    reset();
    navigate('/');
  };

  // Error view
  if (currentView === 'error' && uiError) {
    return (
      <ErrorPage
        title={errorContent.title || 'Something went wrong'}
        message={uiError}
        primaryCta={{
          label: errorContent.startOver || 'Start Over',
          onClick: handleResetAndHome,
        }}
      />
    );
  }

  // Loading/processing → LoadingCard inside landing-style wrapper
  if (currentView === 'idle' || (isPolling && !isSubmittingAnswer)) {
    return (
      <main className="flex items-center justify-center flex-grow" data-testid="quiz-loading-card">
        <div className="lp-wrapper w-full flex items-start justify-center p-4 sm:p-6">
          <LoadingCard />
        </div>
      </main>
    );
  }

  // Synopsis + optional characters
  const synopsis = currentView === 'synopsis'
    ? ((viewData as (Synopsis & { characters?: CharacterProfile[] })) || null)
    : null;
  const extraCharacters = synopsis?.characters;

  // Main routing
  switch (currentView) {
    case 'synopsis':
      // Wrap in HeroCard so geometry matches LoadingCard → minimal CLS
      return (
        <main className="flex items-center justify-center flex-grow">
          <div className="lp-wrapper w-full flex items-start justify-center p-4 sm:p-6">
            <HeroCard ariaLabel="Quiz hero card">
              <SynopsisView
                synopsis={synopsis as Synopsis | null}
                characters={extraCharacters}
                onProceed={handleProceed}
                isLoading={isPolling}
                inlineError={submissionError || uiError}
              />
            </HeroCard>
          </div>
        </main>
      );

    case 'question':
      return (
        <main className="flex items-center justify-center flex-grow">
          <QuestionView
            question={viewData as Question}
            onSelectAnswer={handleSelectAnswer}
            isLoading={isSubmittingAnswer}
            selectedAnswerId={selectedAnswer}
            progress={{ current: answeredCount + 1, total: totalTarget }}
            inlineError={submissionError || uiError}
            onRetry={submissionError ? handleRetrySubmission : handleProceed}
          />
        </main>
      );

    // transient while redirecting
    case 'result':
      return <Spinner message="Loading your result..." />;

    default:
      if (IS_DEV) console.warn('[QuizFlowPage] Unknown currentView, showing spinner', { currentView });
      return <Spinner message={loadingContent.quiz || 'Preparing your quiz...'} />;
  }
};
