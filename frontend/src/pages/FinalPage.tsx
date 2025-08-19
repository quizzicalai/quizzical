// src/pages/FinalPage.tsx
import React, { useEffect, useState, useCallback } from 'react';
import { useNavigate, useParams, Navigate } from 'react-router-dom';
import { useConfig } from '../context/ConfigContext';
import { useQuizStore } from '../store/quizStore';
import * as api from '../services/apiService';
import { ResultProfile } from '../components/result/ResultProfile';
import { FeedbackIcons } from '../components/result/FeedbackIcons';
import { GlobalErrorDisplay } from '../components/common/GlobalErrorDisplay';
import { Spinner } from '../components/common/Spinner';
import Turnstile from '../components/common/Turnstile';
import type { ResultProfileData } from '../types/result';
import type { ApiError } from '../types/api';
import { getQuizId } from '../utils/session';

export const FinalPage: React.FC = () => {
  const navigate = useNavigate();
  const { resultId } = useParams<{ resultId: string }>();
  const { config } = useConfig();

  // Optimized Selector: Select only the reactive state needed.
  const storeQuizId = useQuizStore((s) => s.quizId);
  // Actions are static and can be retrieved once without causing re-renders.
  const resetQuiz = useQuizStore.getState().reset;

  const [resultData, setResultData] = useState<ResultProfileData | null>(null);
  const [isLoading, setIsLoading] = useState(true);
  const [error, setError] = useState<ApiError | null>(null);
  const [shouldRedirect, setShouldRedirect] = useState(false);
  
  // NOTE: State for the Turnstile token is managed here.
  // To complete the feature, this token needs to be passed to and used by the FeedbackIcons component.
  const [turnstileToken, setTurnstileToken] = useState<string | null>(null);

  const resultLabels = config?.content?.resultPage ?? {};
  const errorLabels = config?.content?.errors ?? {};

  const effectiveResultId = resultId || storeQuizId || getQuizId();

  useEffect(() => {
    let isCancelled = false;
    const controller = new AbortController();

    const fetchResult = async (id: string) => {
      setIsLoading(true);
      setError(null);
      try {
        const data = await api.getResult(id, { signal: controller.signal });
        if (!isCancelled) {
          setResultData(data);
        }
      } catch (err: any) {
        if (isCancelled || err.name === 'AbortError') return;
        
        if (err.status === 404 || err.status === 403) {
          setShouldRedirect(true);
        } else {
          setError({ ...err, message: errorLabels.resultNotFound || err.message });
        }
      } finally {
        if (!isCancelled) setIsLoading(false);
      }
    };

    if (effectiveResultId) {
      fetchResult(effectiveResultId);
    } else {
      setError({ message: errorLabels.resultNotFound || 'No result data found.' });
      setIsLoading(false);
    }

    return () => { 
      isCancelled = true;
      controller.abort();
    };
  }, [effectiveResultId, errorLabels.resultNotFound]);

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

  if (shouldRedirect) {
    return <Navigate to="/" replace />;
  }

  if (isLoading) {
    return <div className="flex h-screen items-center justify-center"><Spinner message="Loading your result..." /></div>;
  }

  if (error) {
    return <GlobalErrorDisplay variant="page" error={error} labels={errorLabels} onHome={handleStartOver} />;
  }

  if (!resultData) {
    return <GlobalErrorDisplay variant="page" error={{ message: errorLabels.resultNotFound || 'No result data found.'}} labels={errorLabels} onHome={handleStartOver} />;
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
          {/* The FeedbackIcons component remains untouched as requested. */}
          {/* A subsequent change will be needed here to pass the token. */}
          <FeedbackIcons quizId={storeQuizId} labels={resultLabels.feedback} />

          {/* The Turnstile widget is rendered here. 
            The ideal implementation would be to place this *inside* the FeedbackIcons component,
            but this fulfills the request of adding it to the page without modifying other components.
          */}
          <div className="flex justify-center mt-4">
            <Turnstile onVerify={setTurnstileToken} />
          </div>
        </section>
      )}
    </main>
  );
};