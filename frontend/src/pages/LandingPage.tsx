// src/pages/LandingPage.tsx
import React, { useState, useCallback } from 'react';
import { useNavigate } from 'react-router-dom';
import { useConfig } from '../context/ConfigContext';
import { useQuizStore } from '../store/quizStore';
import * as api from '../services/apiService';
import { InputGroup } from '../components/common/InputGroup';
import { Logo } from '../assets/icons/Logo';
import { ApiError } from '../types/api';
import { Spinner } from '../components/common/Spinner';

export const LandingPage: React.FC = () => {
  const navigate = useNavigate();
  const { config } = useConfig();

  const startQuizInStore = useQuizStore((state) => state.startQuiz);
  const hydrateFromStart = useQuizStore((state) => state.hydrateFromStart);

  const [category, setCategory] = useState('');
  const [isSubmitting, setIsSubmitting] = useState(false);
  const [inlineError, setInlineError] = useState<string | null>(null);

  // Add a guard clause: If config is not yet loaded, show a spinner.
  // This ensures that 'config' is of type AppConfig in the rest of the component.
  if (!config) {
    return (
      <main className="flex items-center justify-center min-h-[calc(100vh-200px)]">
        <Spinner />
      </main>
    );
  }

  const content = config.content.landingPage ?? {};
  const errorContent = config.content.errors ?? {};
  // Now we can safely access nested properties without optional chaining or fallbacks.
  const limits = config.limits.validation;
  const minLength = limits.category_min_length ?? 3;
  const maxLength = limits.category_max_length ?? 100;

  const handleSubmit = useCallback(async (submittedCategory: string) => {
    if (isSubmitting) return;

    setInlineError(null);
    setIsSubmitting(true);
    startQuizInStore();

    try {
      const { quizId, initialPayload } = await api.startQuiz(submittedCategory);
      hydrateFromStart({ quizId, initialPayload });
      navigate('/quiz');
    } catch (err: any) {
      const apiError = err as ApiError;
      const userMessage = apiError?.code === 'category_not_found'
        ? errorContent.categoryNotFound
        : errorContent.quizCreationFailed;
      setInlineError(userMessage || 'Could not create a quiz. Please try again.');
    } finally {
      setIsSubmitting(false);
    }
  }, [isSubmitting, navigate, startQuizInStore, hydrateFromStart, errorContent]);

  return (
    <main className="flex flex-col items-center justify-center min-h-[calc(100vh-200px)] text-center px-4">
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
      />
    </main>
  );
}
