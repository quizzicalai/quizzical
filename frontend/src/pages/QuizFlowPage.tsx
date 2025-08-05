import React, { useCallback, useEffect } from 'react';
import { useNavigate } from 'react-router-dom';
import { useConfig } from '../context/ConfigContext';
import { useQuizStore, useQuizView, useQuizProgress } from '../store/quizStore';
import * as api from '../services/apiService';
import { SynopsisView } from '../components/quiz/SynopsisView';
import { QuestionView } from '../components/quiz/QuestionView';
import { Spinner } from '../components/common/Spinner';
import { ErrorPage } from '../components/common/ErrorPage';
import type { Question, Synopsis } from '../types/quiz';

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
    beginPolling,
    setError,
    reset,
  } = useQuizView();
  const { answeredCount, totalTarget } = useQuizProgress();
  const { markAnswered, submitAnswerStart, submitAnswerEnd } = useQuizStore.getState();

  // Guard against rendering before config is loaded
  if (!config) {
    return <Spinner message="Loading configuration..." />;
  }

  const content = config.content ?? {};
  const errorContent = content.errors ?? {};
  const loadingContent = content.loadingStates ?? {};

  // Auto-recover from idle state
  useEffect(() => {
    if (quizId && currentView === 'idle' && !isPolling) {
      beginPolling({ reason: 'idle-recovery' });
    }
  }, [quizId, currentView, isPolling, beginPolling]);

  // Redirect if no quizId
  useEffect(() => {
    if (!quizId && !isPolling) {
      navigate('/', { replace: true });
    }
  }, [quizId, isPolling, navigate]);

  // Navigate to result page when finished
  useEffect(() => {
    if (currentView === 'result') {
      navigate('/result');
    }
  }, [currentView, navigate]);

  const handleProceed = useCallback(async () => {
    await beginPolling({ reason: 'user-advance' });
  }, [beginPolling]);

  const handleSelectAnswer = useCallback(
    async (answerId: string) => {
      if (!quizId) return;

      submitAnswerStart();
      try {
        await api.submitAnswer(quizId, answerId);
        markAnswered();
        await handleProceed();
      } catch (err: any) {
        setError(err.message || 'There was an error submitting your answer.');
      } finally {
        submitAnswerEnd();
      }
    },
    [quizId, submitAnswerStart, markAnswered, handleProceed, submitAnswerEnd, setError]
  );

  const handleResetAndHome = () => {
    reset();
    navigate('/');
  };

  if (currentView === 'error') {
    return (
      <ErrorPage
        title={errorContent.title || 'Something went wrong'}
        message={uiError || errorContent.description || 'An unexpected error occurred.'}
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

  switch (currentView) {
    case 'synopsis':
      return (
        <main>
          <SynopsisView
            synopsis={viewData as Synopsis}
            onProceed={handleProceed}
            isLoading={isPolling}
            inlineError={uiError}
          />
        </main>
      );
    case 'question':
      return (
        <main>
          <QuestionView
            question={viewData as Question}
            onSelectAnswer={handleSelectAnswer}
            isLoading={isSubmittingAnswer}
            progress={{ current: answeredCount + 1, total: totalTarget }}
            inlineError={uiError}
            onRetry={handleProceed}
          />
        </main>
      );
    default:
      return <Spinner message={loadingContent.quiz || 'Preparing your quiz...'} />;
  }
};
