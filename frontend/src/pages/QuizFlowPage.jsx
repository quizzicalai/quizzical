import React, { useCallback, useEffect, useState, useRef } from 'react';
import { useNavigate } from 'react-router-dom';
import { useQuizStore } from '../store/useQuizStore';
import * as api from '../services/apiService';
import { SynopsisView } from '../components/quiz/SynopsisView';
import { QuestionView } from '../components/quiz/QuestionView';
import { Spinner } from '../components/common/Spinner';

export function QuizFlowPage() {
  const navigate = useNavigate();

  // Select all necessary state and actions in a single, memoized selector
  const {
    quizId,
    currentView,
    viewData,
    knownQuestionsCount,
    answeredCount,
    totalTarget,
    hydrateStatus,
    markAnswered,
    beginPolling,
    submitAnswerStart,
    submitAnswerEnd,
    setError, // For potential global toasts
  } = useQuizStore((s) => ({
    quizId: s.quizId,
    currentView: s.currentView,
    viewData: s.viewData,
    knownQuestionsCount: s.knownQuestionsCount,
    answeredCount: s.answeredCount,
    totalTarget: s.totalTarget,
    hydrateStatus: s.hydrateStatus,
    markAnswered: s.markAnswered,
    beginPolling: s.beginPolling,
    submitAnswerStart: s.submitAnswerStart,
    submitAnswerEnd: s.submitAnswerEnd,
    setError: s.setError,
  }));

  const [isLoadingNext, setIsLoadingNext] = useState(false);
  const [inlineError, setInlineError] = useState(null);
  const isMountedRef = useRef(true);

  useEffect(() => {
    isMountedRef.current = true;
    return () => { isMountedRef.current = false; };
  }, []);

  // Redirect to landing if there's no active quiz (e.g., on page refresh)
  useEffect(() => {
    if (!quizId) {
      navigate('/', { replace: true });
    }
  }, [quizId, navigate]);

  const handlePoll = useCallback(async () => {
    if (!quizId) return;

    setIsLoadingNext(true);
    setInlineError(null);
    beginPolling();

    try {
      const nextState = await api.pollQuizStatus(quizId, {
        knownQuestionsCount,
      });
      hydrateStatus(nextState); // The store will update currentView/viewData
    } catch (err) {
      const msg = err?.message || 'Could not load the next step. Please try again.';
      setInlineError(msg);
      setError(msg, false); // Optional: also show a toast
    } finally {
      if (isMountedRef.current) {
        setIsLoadingNext(false);
      }
    }
  }, [quizId, knownQuestionsCount, beginPolling, hydrateStatus, setError]);

  const handleSelectAnswer = useCallback(async (answerId) => {
    if (!quizId) return;

    submitAnswerStart();
    setIsLoadingNext(true);
    setInlineError(null);

    try {
      await api.submitAnswer(quizId, answerId);
      markAnswered();
      await handlePoll(); // Poll for the next question/state
    } catch (err) {
      const msg = err?.message || 'There was an error submitting your answer.';
      setInlineError(msg);
      setError(msg, false); // Optional: also show a toast
    } finally {
      submitAnswerEnd();
      // setIsLoadingNext is handled by the poll function's finally block
    }
  }, [quizId, submitAnswerStart, markAnswered, handlePoll, submitAnswerEnd, setError]);
  
  // Navigate to results page when the view changes to 'result'
  useEffect(() => {
    if (currentView === 'result') {
      navigate('/result');
    }
  }, [currentView, navigate]);

  // Render loading state while waiting for the next question
  if (isLoadingNext) {
    return <div className="flex items-center justify-center h-screen"><Spinner message="Thinking..." /></div>;
  }
  
  // Render content based on the current view from the store
  switch (currentView) {
    case 'synopsis':
      return (
        <SynopsisView
          synopsis={viewData}
          onProceed={handlePoll}
          inlineError={inlineError}
        />
      );
    case 'question':
      return (
        <QuestionView
          question={viewData}
          onSelectAnswer={handleSelectAnswer}
          progress={{
            current: answeredCount + 1,
            total: totalTarget,
          }}
          inlineError={inlineError}
          onRetry={handlePoll}
        />
      );
    default:
      // Fallback for initial load or unknown states
      return <div className="flex items-center justify-center h-screen"><Spinner message="Preparing your quiz..." /></div>;
  }
}