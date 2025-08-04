// src/pages/FinalPage.jsx
import React, { useEffect, useState, useCallback } from 'react';
import { useNavigate, useParams } from 'react-router-dom';
import { useConfig } from '../context/ConfigContext';
import { useQuizStore } from '../store/useQuizStore';
import * as api from '../services/apiService';
import { ResultProfile } from '../components/result/ResultProfile';
import { FeedbackIcons } from '../components/quiz/FeedbackIcons';
import { GlobalErrorDisplay } from '../components/common/GlobalErrorDisplay';
import { Spinner } from '../components/common/Spinner';

export function FinalPage() {
  const navigate = useNavigate();
  const { resultId } = useParams();
  const { config } = useConfig();

  const { quizId, storeResult, resetQuiz } = useQuizStore((s) => ({
    quizId: s.quizId,
    storeResult: s.viewData,
    resetQuiz: s.reset,
  }));

  const [resultData, setResultData] = useState(null);
  const [isLoading, setIsLoading] = useState(!!resultId);
  const [error, setError] = useState(null);

  const resultLabels = config?.content?.resultPage ?? {};
  const errorLabels = config?.content?.errors ?? {};

  useEffect(() => {
    let isCancelled = false;
    if (resultId) {
      setIsLoading(true);
      api.getResult(resultId)
        .then(data => { if (!isCancelled) setResultData(data); })
        .catch(err => {
          if (!isCancelled) {
            if (err.status === 403 || err.status === 404) {
              navigate('/', { replace: true });
              return;
            }
            setError({ ...err, message: errorLabels.resultNotFound || err.message });
          }
        })
        .finally(() => { if (!isCancelled) setIsLoading(false); });
    } else {
      setResultData(storeResult);
    }
    return () => { isCancelled = true; };
  }, [resultId, storeResult, navigate, errorLabels.resultNotFound]);

  const handleStartOver = useCallback(() => {
    resetQuiz();
    navigate('/');
  }, [resetQuiz, navigate]);

  if (isLoading) {
    return <div className="flex h-screen items-center justify-center"><Spinner message="Loading your result..." /></div>;
  }
  
  if (error) {
    return <GlobalErrorDisplay variant="page" error={error} labels={errorLabels} onHome={handleStartOver} />;
  }

  if (!resultData) {
    return <div className="flex h-screen items-center justify-center"><Spinner message="No result found. Redirecting..." /></div>;
  }

  return (
    <main className="max-w-3xl mx-auto px-4 py-8">
      <ResultProfile
        result={resultData}
        labels={resultLabels}
        shareUrl={resultId ? window.location.href : resultData.shareUrl}
        onCopyShare={() => navigator.clipboard.writeText(resultId ? window.location.href : resultData.shareUrl)}
        onStartNew={handleStartOver}
      />
      {quizId && (
        <section className="mt-10 pt-8 border-t">
          <FeedbackIcons quizId={quizId} labels={resultLabels.feedback} />
        </section>
      )}
    </main>
  );
}