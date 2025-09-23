import React, { useCallback, useEffect, useState } from 'react';
import { useNavigate } from 'react-router-dom';
import { useConfig } from '../context/ConfigContext';
import { useQuizView, useQuizProgress, useQuizActions } from '../store/quizStore';
import * as api from '../services/apiService';
import { SynopsisView } from '../components/quiz/SynopsisView';
import { QuestionView } from '../components/quiz/QuestionView';
import { Spinner } from '../components/common/Spinner';
import { ErrorPage } from './ErrorPage';
import type { Question, Synopsis, CharacterProfile } from '../types/quiz';

const IS_DEV = import.meta.env.DEV === true;

export const QuizFlowPage: React.FC = () => {
  const navigate = useNavigate();
  const { config } = useConfig();

  // Optimized Selectors: Each hook subscribes to a specific slice of the state.
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
    hydrateStatus,
  } = useQuizActions();
  
  const [submissionError, setSubmissionError] = useState<string | null>(null);
  const [selectedAnswer, setSelectedAnswer] = useState<string | null>(null);

  // This check is safe; config is guaranteed to be loaded by the router layout.
  const content = config!.content;
  const errorContent = content.errors ?? {};
  const loadingContent = content.loadingStates ?? {};

  // Effect to recover polling state if the component re-mounts
  useEffect(() => {
    if (quizId && currentView === 'idle' && !isPolling) {
      if (IS_DEV) console.log('[QuizFlowPage] idle-recovery beginPolling', { quizId });
      beginPolling({ reason: 'idle-recovery' });
    }
  }, [quizId, currentView, isPolling, beginPolling]);

  // Effect to redirect to home if the quiz session is lost
  useEffect(() => {
    if (!quizId && !isPolling) {
      if (IS_DEV) console.warn('[QuizFlowPage] Missing quizId; navigating home');
      navigate('/', { replace: true });
    }
  }, [quizId, isPolling, navigate]);

  /**
   * Proceed flow CHANGE:
   * - Call /quiz/proceed once when leaving the synopsis to begin question generation.
   * - Then delegate polling to the store, which uses knownQuestionsCount and backoff.
   */
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
        // Determine the current question index (0-based) and selected option index/text.
        // Current question index equals the number of answers already submitted.
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
            // Fallback: try parsing "opt-{n}" pattern
            const m = /^opt-(\d+)$/.exec(answerId);
            if (m) {
              optionIndex = Number(m[1]);
              answerText = question.answers[optionIndex]?.text ?? undefined;
            }
          }
        }

        if (IS_DEV) console.log('[QuizFlowPage] submitAnswer', { quizId, answerId, questionIndex, optionIndex, answerText });

        // UPDATED: send questionIndex (+ optional optionIndex/answer) per backend contract.
        await api.submitAnswer(quizId, { questionIndex, optionIndex, answer: answerText });

        markAnswered();
        if (IS_DEV) console.log('[QuizFlowPage] answer submitted; beginPolling(reason=after-answer)');
        await beginPolling({ reason: 'after-answer' }); // just poll; do NOT call proceed again
        setSelectedAnswer(null); 
      } catch (err: any) {
        if (IS_DEV) console.error('[QuizFlowPage] submitAnswer error', err);
        const message = err.message || errorContent.submissionFailed || 'There was an error submitting your answer.';
        setSubmissionError(message);
        setError(message, false);
      } finally {
        submitAnswerEnd();
      }
    },
    [quizId, isSubmittingAnswer, submitAnswerStart, markAnswered, beginPolling, submitAnswerEnd, setError, errorContent.submissionFailed, answeredCount, currentView, viewData]
  );
  
  const handleRetrySubmission = useCallback(() => {
    if (selectedAnswer) {
      if (IS_DEV) console.log('[QuizFlowPage] retry submission with same answer', { selectedAnswer });
      handleSelectAnswer(selectedAnswer);
    }
  }, [selectedAnswer, handleSelectAnswer]);

  const handleResetAndHome = () => {
    if (IS_DEV) console.log('[QuizFlowPage] reset and navigate home');
    reset();
    navigate('/');
  };

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

  if (currentView === 'idle' || (isPolling && !isSubmittingAnswer)) {
    return <Spinner message={loadingContent.quiz || 'Preparing your quiz...'} />;
  }

  // If the store included characters separately with the synopsis, surface them.
  // Align with backend: characters are returned as a separate payload; some store
  // implementations merge them onto the synopsis for convenience. Be tolerant.
  const synopsis = currentView === 'synopsis'
    ? ((viewData as (Synopsis & { characters?: CharacterProfile[] })) || null)
    : null;
  const extraCharacters = synopsis?.characters;

  switch (currentView) {
    case 'synopsis':
      return (
        <main className="flex items-center justify-center flex-grow">
          <SynopsisView
            synopsis={synopsis as Synopsis | null}
            characters={extraCharacters}
            onProceed={handleProceed}
            isLoading={isPolling}
            inlineError={submissionError || uiError}
          />
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
    default:
      if (IS_DEV) console.warn('[QuizFlowPage] Unknown currentView, showing spinner', { currentView });
      return <Spinner message={loadingContent.quiz || 'Preparing your quiz...'} />;
  }
};
