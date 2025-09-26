// src/pages/FinalPage.tsx
import React, { useEffect, useRef, useState, useCallback } from 'react';
import { useNavigate, useParams } from 'react-router-dom';
import { useConfig } from '../context/ConfigContext';
import { useQuizStore } from '../store/quizStore';
import * as api from '../services/apiService';
import { ResultProfile } from '../components/result/ResultProfile';
import { FeedbackIcons } from '../components/result/FeedbackIcons';
import { GlobalErrorDisplay } from '../components/common/GlobalErrorDisplay';
import { Spinner } from '../components/common/Spinner';
import type { ResultProfileData } from '../types/result';
import type { ApiError } from '../types/api';
import { getQuizId } from '../utils/session';

export const FinalPage: React.FC = () => {
  const navigate = useNavigate();
  const { resultId: routeId } = useParams<{ resultId: string }>();
  const { config } = useConfig();

  // Pull from store (avoid constructing new objects here)
  const storeQuizId   = useQuizStore((s) => s.quizId);
  const storeStatus   = useQuizStore((s) => s.status);
  const storeViewData = useQuizStore((s) => s.viewData);
  const resetQuiz     = useQuizStore.getState().reset;

  const [resultData, setResultData] = useState<ResultProfileData | null>(null);
  const [isLoading, setIsLoading]   = useState(true);
  const [error, setError]           = useState<ApiError | null>(null);

  const resultLabels = config?.content?.resultPage ?? {};
  const errorLabels  = config?.content?.errors ?? {};

  const effectiveResultId = routeId || storeQuizId || getQuizId();

  // Prevent re-running the effect for the same id (fixes update depth loop)
  const lastLoadedIdRef = useRef<string | null>(null);

  useEffect(() => {
    // If we don’t have an id, show error once.
    if (!effectiveResultId) {
      setError({
        status: 404,
        code: 'not_found',
        message: errorLabels.resultNotFound || 'No result data found.',
        retriable: false,
      });
      setIsLoading(false);
      return;
    }

    // Only run when the id changes.
    if (lastLoadedIdRef.current === effectiveResultId) return;
    lastLoadedIdRef.current = effectiveResultId;

    const controller = new AbortController();

    // Fast path: if this is our current quiz and we already have the finished result in memory.
    if (storeQuizId === effectiveResultId && storeStatus === 'finished' && storeViewData) {
      setResultData(storeViewData as ResultProfileData);
      setIsLoading(false);
      setError(null);
      return;
    }

    // Cold path: fetch from API (v0 tries /quiz/status cache via api.getResult)
    setIsLoading(true);
    setError(null);
    api
      .getResult(effectiveResultId, { signal: controller.signal })
      .then((data) => {
        setResultData(data);
      })
      .catch(() => {
        setError({
          status: 404,
          code: 'not_found',
          message: errorLabels.resultNotFound || 'No result data found.',
          retriable: false,
        });
      })
      .finally(() => {
        setIsLoading(false);
      });

    return () => controller.abort();
  }, [effectiveResultId, storeQuizId, storeStatus, storeViewData, errorLabels.resultNotFound]);

  const handleStartOver = useCallback(() => {
    resetQuiz();
    navigate('/');
  }, [resetQuiz, navigate]);

  const handleCopyShare = useCallback(() => {
    if (effectiveResultId) {
      const shareUrl = `${window.location.origin}/result/${effectiveResultId}`;
      navigator.clipboard.writeText(shareUrl);
    }
  }, [effectiveResultId]);

  if (isLoading) {
    return (
      <div className="flex h-screen items-center justify-center">
        <Spinner message="Loading your result..." />
      </div>
    );
  }

  if (error) {
    return (
      <GlobalErrorDisplay
        variant="page"
        error={error}
        labels={errorLabels}
        onHome={handleStartOver}
      />
    );
  }

  if (!resultData) {
    return (
      <GlobalErrorDisplay
        variant="page"
        error={{ message: errorLabels.resultNotFound || 'No result data found.' }}
        labels={errorLabels}
        onHome={handleStartOver}
      />
    );
  }

  return (
    <main className="max-w-3xl mx-auto px-4 py-8">
      <ResultProfile
        result={resultData}
        labels={resultLabels}
        shareUrl={effectiveResultId ? `${window.location.origin}/result/${effectiveResultId}` : undefined}
        onCopyShare={handleCopyShare}
        onStartNew={handleStartOver}
      />
      {storeQuizId && storeQuizId === effectiveResultId && (
        <section className="mt-10 pt-8 border-t">
          <FeedbackIcons quizId={storeQuizId} labels={resultLabels.feedback} />
        </section>
      )}
    </main>
  );
};
