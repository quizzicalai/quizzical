// src/pages/QuizFlowPage.tsx
import React, { useCallback, useEffect, useState } from 'react';
import { useNavigate } from 'react-router-dom';
import { useConfig } from '../context/ConfigContext';
import { useQuizStore, useQuizView, useQuizProgress } from '../store/quizStore';
import * as api from '../services/apiService';
import { SynopsisView } from '../components/quiz/SynopsisView';
import { QuestionView } from '../components/quiz/QuestionView';
import { Spinner } from '../components/common/Spinner';
import { ErrorPage } from './ErrorPage';
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
  
  const [submissionError, setSubmissionError] = useState<string | null>(null);
  const [selectedAnswer, setSelectedAnswer] = useState<string | null>(null);

  if (!config) {
    return <Spinner message="Loading configuration..." />;
  }

  const content = config.content ?? {};
  const errorContent = content.errors ?? {};
  const loadingContent = content.loadingStates ?? {};

  useEffect(() => {
    if (quizId && currentView === 'idle' && !isPolling) {
      beginPolling({ reason: 'idle-recovery' });
    }
  }, [quizId, currentView, isPolling, beginPolling]);

  useEffect(() => {
    if (!quizId && !isPolling) {
      navigate('/', { replace: true });
    }
  }, [quizId, isPolling, navigate]);

  useEffect(() => {
    if (currentView === 'result') {
      navigate('/result');
    }
  }, [currentView, navigate]);

  const handleProceed = useCallback(async () => {
    setSubmissionError(null);
    await beginPolling({ reason: 'user-advance' });
  }, [beginPolling]);

  const handleSelectAnswer = useCallback(
    async (answerId: string) => {
      if (!quizId || isSubmittingAnswer) return;

      setSelectedAnswer(answerId);
      submitAnswerStart();
      setSubmissionError(null);

      try {
        await api.submitAnswer(quizId, answerId);
        markAnswered();
        await handleProceed();
        setSelectedAnswer(null); // Clear selection on success
      } catch (err: any) {
        const message = err.message || errorContent.submissionFailed || 'There was an error submitting your answer.';
        setSubmissionError(message);
        setError(message, false); // Set non-fatal error
      } finally {
        submitAnswerEnd();
      }
    },
    [quizId, isSubmittingAnswer, submitAnswerStart, markAnswered, handleProceed, submitAnswerEnd, setError, errorContent.submissionFailed]
  );
  
  const handleRetrySubmission = useCallback(() => {
    if (selectedAnswer) {
      handleSelectAnswer(selectedAnswer);
    }
  }, [selectedAnswer, handleSelectAnswer]);


  const handleResetAndHome = () => {
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

  switch (currentView) {
    case 'synopsis':
      return (
        <main>
          <SynopsisView
            synopsis={viewData as Synopsis}
            onProceed={handleProceed}
            isLoading={isPolling}
            inlineError={submissionError || uiError}
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
            selectedAnswerId={selectedAnswer}
            progress={{ current: answeredCount + 1, total: totalTarget }}
            inlineError={submissionError || uiError}
            onRetry={submissionError ? handleRetrySubmission : handleProceed}
          />
        </main>
      );
    default:
      return <Spinner message={loadingContent.quiz || 'Preparing your quiz...'} />;
  }
};