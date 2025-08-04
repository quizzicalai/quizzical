import React, { useState, useCallback } from 'react';
import { useNavigate } from 'react-router-dom';
import { useConfig } from '../context/ConfigContext';
import { useQuizStore } from '../store/useQuizStore';
import * as api from '../services/apiService';
import { InputGroup } from '../components/common/InputGroup';
import { Logo } from '../components/common/Logo';

export function LandingPage() {
  const navigate = useNavigate();
  const { config } = useConfig();
  
  // Select specific actions/state from the store to prevent unnecessary re-renders
  const startQuizInStore = useQuizStore((state) => state.startQuiz);
  const hydrateFromStart = useQuizStore((state) => state.hydrateFromStart);

  const [category, setCategory] = useState('');
  const [isSubmitting, setIsSubmitting] = useState(false);
  const [inlineError, setInlineError] = useState(null);

  const content = config?.content?.landingPage ?? {};
  const limits = config?.limits?.validation ?? {};
  const minLength = limits.category_min_length ?? 3;
  const maxLength = limits.category_max_length ?? 100;

  const handleSubmit = useCallback(async (submittedCategory) => {
    if (isSubmitting) return;

    setInlineError(null);
    setIsSubmitting(true);
    startQuizInStore(); // Set global state to 'loading'

    try {
      // The InputGroup component now handles trim/normalization
      const { quizId, initialPayload } = await api.startQuiz(submittedCategory);
      
      // Hydrate the store with the initial data to avoid a redundant fetch
      hydrateFromStart({ quizId, initialPayload });
      
      navigate('/quiz');
    } catch (err) {
      const userMessage = err?.message || content?.errorMessages?.creationFailed || 'Could not create a quiz. Please try another category.';
      setInlineError(userMessage);
    } finally {
      setIsSubmitting(false);
    }
  }, [isSubmitting, navigate, startQuizInStore, hydrateFromStart, content?.errorMessages]);

  return (
    <div className="flex flex-col items-center justify-center min-h-[calc(100vh-150px)] text-center px-4">
      <header className="mb-8">
        <Logo className="h-16 w-16 mx-auto mb-4 text-primary" />
        {content.title && (
          <h1 className="text-4xl font-bold text-fg tracking-tight">
            {content.title}
          </h1>
        )}
        {content.subtitle && (
          <p className="mt-2 text-lg text-muted max-w-xl">
            {content.subtitle}
          </p>
        )}
      </header>

      <main className="w-full">
        <InputGroup
          value={category}
          onChange={setCategory}
          onSubmit={handleSubmit}
          placeholder={content.inputPlaceholder}
          errorText={inlineError}
          minLength={minLength}
          maxLength={maxLength}
          isSubmitting={isSubmitting}
          ariaLabel="Quiz category input"
          enterKeyHint="go"
        />
      </main>
    </div>
  );
}