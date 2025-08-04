// src/pages/QuizFlowPage.tsx
import React, { useCallback, useEffect, useState, useRef } from 'react';
import { useNavigate } from 'react-router-dom';
import { useConfig } from '../context/ConfigContext';
import { useQuizStore } from '../store/quizStore';
import * as api from '../services/apiService';
import { SynopsisView } from '../components/quiz/SynopsisView';
import { QuestionView } from '../components/quiz/QuestionView';
import { Spinner } from '../components/common/Spinner';
import type { Question, Synopsis } from '../types/quiz';
import { ApiError } from '../types/api';

export const QuizFlowPage: React.FC = () => {
  const navigate = useNavigate();
  const { config } = useConfig();

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
  }));

  const [isLoadingNext, setIsLoadingNext] = useState(false);
  const [inlineError, setInlineError] = useState<string | null>(null);
  const isMountedRef = useRef(true);

  const errorContent = config?.content?.errors ?? {};

  useEffect(() => {
    isMountedRef.current = true;
    return () => { isMountedRef.current = false; };
  }, []);

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
      const nextState = await api.pollQuizStatus(quizId, { knownQuestionsCount });
      hydrateStatus(nextState);
    } catch (err) {
      const apiError = err as ApiError;
      const msg = apiError?.code === 'poll_timeout' 
        ? errorContent.requestTimeout 
        : (apiError?.message || errorContent.description);
      setInlineError(msg || 'An error occurred while fetching the next step.');
    } finally {
      if (isMountedRef.current) setIsLoadingNext(false);
    }
  }, [quizId, knownQuestionsCount, beginPolling, hydrateStatus, errorContent]);

  const handleSelectAnswer = useCallback(async (answerId: string) => {
    if (!quizId) return;

    submitAnswerStart();
    setIsLoadingNext(true);
    setInlineError(null);

    try {
      await api.submitAnswer(quizId, answerId);
      markAnswered();
      await handlePoll();
    } catch (err: any) {
      setInlineError(err?.message || 'There was an error submitting your answer.');
    } finally {
      submitAnswerEnd();
    }
  }, [quizId, submitAnswerStart, markAnswered, handlePoll, submitAnswerEnd]);

  useEffect(() => {
    if (currentView === 'result') {
      navigate('/result');
    }
  }, [currentView, navigate]);

  if (isLoadingNext) {
    return <div className="flex items-center justify-center h-screen"><Spinner message="Thinking..." /></div>;
  }
  
  switch (currentView) {
    case 'synopsis':
      return (
        <main><SynopsisView synopsis={viewData as Synopsis} onProceed={handlePoll} isLoading={isLoadingNext} inlineError={inlineError} /></main>
      );
    case 'question':
      return (
        <main><QuestionView question={viewData as Question} onSelectAnswer={handleSelectAnswer} isLoading={isLoadingNext} progress={{ current: answeredCount + 1, total: totalTarget }} inlineError={inlineError} onRetry={handlePoll} /></main>
      );
    default:
      return <div className="flex items-center justify-center h-screen"><Spinner message="Preparing your quiz..." /></div>;
  }
}
