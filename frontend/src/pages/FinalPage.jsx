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

  // Select only the necessary data and actions from the store
  const { quizId, storeResult, resetQuiz } = useQuizStore((s) => ({
    quizId: s.quizId,
    storeResult: s.viewData,
    resetQuiz: s.reset,
  }));

  const [resultData, setResultData] = useState(null);
  const [isLoading, setIsLoading] = useState(!!resultId); // Only load if there's a resultId in the URL
  const [error, setError] = useState(null);

  useEffect(() => {
    let isCancelled = false;
    
    // If a resultId is in the URL, fetch it. This is the "shared link" path.
    if (resultId) {
      setIsLoading(true);
      api.getResult(resultId)
        .then(data => {
          if (!isCancelled) setResultData(data);
        })
        .catch(err => {
          if (!isCancelled) {
            // Per spec, redirect home on 403/404 for shared links
            if (err.status === 403 || err.status === 404) {
              navigate('/', { replace: true });
              return;
            }
            setError(err);
          }
        })
        .finally(() => {
          if (!isCancelled) setIsLoading(false);
        });
    } else {
      // Otherwise, use the result from the store. This is the "just finished quiz" path.
      setResultData(storeResult);
    }
    
    return () => { isCancelled = true; };
  }, [resultId, storeResult, navigate]);

  const handleStartOver = useCallback(() => {
    resetQuiz();
    navigate('/');
  }, [resetQuiz, navigate]);

  const labels = config?.content?.resultPage ?? {};
  
  if (isLoading) {
    return <div className="flex h-screen items-center justify-center"><Spinner message={labels.loading ?? 'Loading your result...'} /></div>;
  }
  
  if (error) {
    return (
      <GlobalErrorDisplay
        variant="page"
        error={error}
        labels={config?.content?.errors}
        onHome={handleStartOver}
      />
    );
  }

  if (!resultData) {
    // If there's no data for any reason, send the user home.
    return <div className="flex h-screen items-center justify-center"><Spinner message="No result found. Redirecting..." /></div>;
  }

  return (
    <main className="max-w-3xl mx-auto px-4 py-8">
      <ResultProfile
        result={resultData}
        labels={config?.content?.resultProfile}
        shareUrl={resultId ? window.location.href : resultData.shareUrl}
        onCopyShare={() => navigator.clipboard.writeText(resultId ? window.location.href : resultData.shareUrl)}
        onStartNew={handleStartOver}
      />
      {quizId && (
        <section className="mt-10 pt-8 border-t">
          <FeedbackIcons quizId={quizId} labels={config?.content?.feedback} />
        </section>
      )}
    </main>
  );
}