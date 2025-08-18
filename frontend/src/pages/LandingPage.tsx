// src/pages/LandingPage.tsx
import React, { useState, useCallback, useRef } from 'react';
import { useNavigate } from 'react-router-dom';
import { useConfig } from '../context/ConfigContext';
import { useQuizActions } from '../store/quizStore';
import { InputGroup } from '../components/common/InputGroup';
import { Logo } from '../assets/icons/Logo';
import { ApiError } from '../types/api';
import { Spinner } from '../components/common/Spinner';
import Turnstile from '../components/common/Turnstile';

export const LandingPage: React.FC = () => {
  const navigate = useNavigate();
  const { config } = useConfig();
  const formRef = useRef<HTMLFormElement>(null);
  const { startQuiz } = useQuizActions(); // Use the dedicated actions hook

  const [category, setCategory] = useState('');
  const [isSubmitting, setIsSubmitting] = useState(false);
  const [inlineError, setInlineError] = useState<string | null>(null);

  if (!config) {
    return (
      <main className="flex items-center justify-center min-h-[calc(100vh-200px)]">
        <Spinner />
      </main>
    );
  }

  const { content, limits } = config;
  const landingPageContent = content.landingPage ?? {};
  const errorContent = content.errors ?? {};
  const validationContent = landingPageContent.validation ?? {};

  const minLength = limits.validation.category_min_length ?? 3;
  const maxLength = limits.validation.category_max_length ?? 100;

  const handleSubmit = useCallback(async (event: React.FormEvent<HTMLFormElement>) => {
    event.preventDefault();
    if (isSubmitting || !category.trim()) return;

    const formData = new FormData(formRef.current!);
    const turnstileToken = formData.get('cf-turnstile-response')?.toString();

    if (!turnstileToken) {
      setInlineError('Please complete the security check to continue.');
      return;
    }

    setInlineError(null);
    setIsSubmitting(true);

    try {
      await startQuiz(category, turnstileToken);
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
  }, [isSubmitting, category, startQuiz, navigate, errorContent]);

  return (
    <main className="flex flex-col items-center justify-center min-h-[calc(100vh-200px)] text-center px-4">
      <header className="mb-8">
        <Logo className="h-16 w-16 mx-auto mb-4 text-primary" />
        {landingPageContent.title && (
          <h1 className="text-4xl font-bold text-fg tracking-tight">
            {landingPageContent.title}
          </h1>
        )}
        {landingPageContent.subtitle && (
          <p className="mt-2 text-lg text-muted max-w-xl">
            {landingPageContent.subtitle}
          </p>
        )}
      </header>

      <form ref={formRef} onSubmit={handleSubmit} className="w-full max-w-lg">
        <InputGroup
          value={category}
          onChange={setCategory}
          // The onSubmit prop is not needed as the parent form handles submission
          placeholder={landingPageContent.examples?.[0] ?? landingPageContent.inputPlaceholder}
          errorText={inlineError}
          minLength={minLength}
          maxLength={maxLength}
          isSubmitting={isSubmitting}
          ariaLabel={landingPageContent.inputAriaLabel ?? 'Quiz category input'}
          buttonText={landingPageContent.submitButton ?? 'Create My Quiz'}
          validationMessages={{
            minLength: validationContent.minLength,
            maxLength: validationContent.maxLength,
            patternMismatch: validationContent.patternMismatch,
          }}
        />
        <div className="flex justify-center mt-6">
          <Turnstile />
        </div>
      </form>
    </main>
  );
};
