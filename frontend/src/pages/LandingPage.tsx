// frontend/src/pages/LandingPage.tsx
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
  const { startQuiz } = useQuizActions();

  const [category, setCategory] = useState('');
  const [isSubmitting, setIsSubmitting] = useState(false);
  const [inlineError, setInlineError] = useState<string | null>(null);
  const [turnstileToken, setTurnstileToken] = useState<string | null>(null);
  const [turnstileError, setTurnstileError] = useState<string | null>(null);

  const handleTurnstileVerify = useCallback((token: string) => {
    console.log('[LandingPage] Turnstile token received:', token);
    setTurnstileToken(token);
    setTurnstileError(null);
    setInlineError(null);
  }, []);

  const handleTurnstileError = useCallback(() => {
    setTurnstileError('Verification failed. Please try again.');
    setTurnstileToken(null);
  }, []);

  const handleSubmit = useCallback(async (event: React.FormEvent<HTMLFormElement>) => {
    event.preventDefault();
    console.log('[LandingPage] Form submitted', { category, turnstileToken, isSubmitting });
    
    if (isSubmitting || !category.trim()) {
      console.log('[LandingPage] Submission blocked: isSubmitting or no category');
      return;
    }

    if (!turnstileToken) {
      console.log('[LandingPage] No turnstile token');
      setInlineError('Please complete the security verification.');
      return;
    }

    setInlineError(null);
    setIsSubmitting(true);

    try {
      console.log('[LandingPage] Starting quiz with category:', category);
      await startQuiz(category, turnstileToken);
      navigate('/quiz');
    } catch (err: any) {
      console.error('[LandingPage] Quiz creation failed:', err);
      if ((window as any).resetTurnstile) {
        (window as any).resetTurnstile();
      }
      setTurnstileToken(null);

      const apiError = err as ApiError;
      const userMessage = apiError?.code === 'category_not_found'
        ? config?.content?.errors?.categoryNotFound
        : config?.content?.errors?.quizCreationFailed;
      setInlineError(userMessage || 'Could not create a quiz. Please try again.');
    } finally {
      setIsSubmitting(false);
    }
  }, [isSubmitting, category, turnstileToken, startQuiz, navigate, config]);

  if (!config) {
    return (
      <main className="flex items-center justify-center min-h-[calc(100vh-200px)]">
        <Spinner />
      </main>
    );
  }

  const { content, limits } = config;
  const landingPageContent = content.landingPage ?? {};
  const validationContent = landingPageContent.validation ?? {};

  const minLength = limits.validation.category_min_length ?? 3;
  const maxLength = limits.validation.category_max_length ?? 100;

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

      <form 
        ref={formRef} 
        id="landing-form"
        onSubmit={handleSubmit} 
        className="w-full max-w-lg"
      >
        <InputGroup
          value={category}
          onChange={setCategory}
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
          formId="landing-form"
        />
        <div className="flex justify-center mt-6">
          <Turnstile
            onVerify={handleTurnstileVerify}
            onError={handleTurnstileError}
            theme="auto"
          />
        </div>
        {turnstileError && (
          <p className="text-red-600 text-sm mt-2">{turnstileError}</p>
        )}
      </form>
    </main>
  );
};