// frontend/src/pages/LandingPage.tsx

import React, { useState, useCallback } from 'react';
import { useNavigate } from 'react-router-dom';
import { useConfig } from '../context/ConfigContext';
import { useQuizActions } from '../store/quizStore';
import type { ApiError } from '../types/api';
import { Spinner } from '../components/common/Spinner';
import Turnstile from '../components/common/Turnstile';
import IconButton from '../components/common/IconButton';
import { ArrowIcon } from '../assets/icons/ArrowIcon';
import { WizardCatIcon } from '../assets/icons/WizardCatIcon';

export const LandingPage: React.FC = () => {
  const navigate = useNavigate();
  const { config } = useConfig();
  const { startQuiz } = useQuizActions();

  const [category, setCategory] = useState('');
  const [isSubmitting, setIsSubmitting] = useState(false);
  const [inlineError, setInlineError] = useState<string | null>(null);
  const [turnstileToken, setTurnstileToken] = useState<string | null>(null);
  const [showTurnstile, setShowTurnstile] = useState(false);

  const handleTurnstileVerify = useCallback((token: string) => {
    setTurnstileToken(token);
    setInlineError(null);
  }, []);

  const handleTurnstileError = useCallback(() => {
    setInlineError('Verification failed. Please try again.');
    setTurnstileToken(null);
  }, []);

  const handleSubmit = useCallback(async (event: React.FormEvent<HTMLFormElement>) => {
    event.preventDefault();
    if (isSubmitting || !category.trim()) return;

    if (!turnstileToken) {
      setShowTurnstile(true);
      setInlineError('Please complete the security verification to continue.');
      return;
    }

    setInlineError(null);
    setIsSubmitting(true);

    try {
      await startQuiz(category, turnstileToken);
      navigate('/quiz');
    } catch (err: any) {
      if ((window as any).resetTurnstile) (window as any).resetTurnstile();
      setTurnstileToken(null);
      setShowTurnstile(true);

      const apiError = err as ApiError;
      const userMessage =
        apiError?.code === 'category_not_found'
          ? config?.content?.errors?.categoryNotFound
          : config?.content?.errors?.quizCreationFailed;
      setInlineError(userMessage || 'Could not create a quiz. Please try again.');
    } finally {
      setIsSubmitting(false);
    }
  }, [isSubmitting, category, turnstileToken, startQuiz, navigate, config]);

  if (!config) {
    return (
      <div className="flex-grow flex items-center justify-center">
        <Spinner />
      </div>
    );
  }

  const lp = config.content.landingPage ?? {};
  const examples: string[] = Array.isArray(lp.examples)
    ? (lp.examples as string[]).filter((s) => typeof s === 'string' && s.trim() !== '')
    : [];

  const placeholder =
    typeof lp.placeholder === 'string' && lp.placeholder.trim()
      ? lp.placeholder
      : (examples.length
          ? `e.g., ${examples.slice(0, 2).map((e: string) => `"${e}"`).join(', ')}`
          : 'e.g., "Gilmore Girls", "Myers Briggs"');

  return (
    <div className="flex-grow flex items-start justify-center p-4 sm:p-6 lp-wrapper">
      <div
        className="
          w-full mx-auto lp-card
          flex flex-col justify-center
          pt-4  sm:pt-6  md:pt-8  lg:pt-10
          pb-12 sm:pb-16 md:pb-20 lg:pb-24
          min-h-[50vh] sm:min-h-[55vh] md:min-h-[60vh] lg:min-h-[66vh]
        "
      >
        <div className="text-center">

          {/* Hero with soft color halo */}
          <div className="flex justify-center lp-space-after-hero">
            <span className="lp-hero-wrap">
              <span className="lp-hero-blob" />
              <WizardCatIcon className="lp-hero" aria-label="Wizard cat reading a book" />
            </span>
          </div>

          {/* Title uses display font (fonts.serif -> --font-display) + gradient underline */}
          <h1 className="lp-title font-bold text-fg tracking-tight leading-tight lp-title-maxw mx-auto lp-title-underline">
            {lp.title || 'Discover Your True Personality.'}
          </h1>

          <p className="text-muted lp-subtitle lp-subtitle-maxw mx-auto lp-space-title-sub">
            {lp.subtitle || 'Pick a topic. Our AI will craft a custom quiz to reveal a surprising side of you.'}
          </p>

          <div className="lp-form-maxw mx-auto lp-space-sub-form">
            <form onSubmit={handleSubmit} className="w-full">
              <div
                className="lp-pill"
                style={
                  {
                    // consumed by .lp-pill:focus-within ring
                    ['--tw-ring-color' as any]: `rgba(var(--color-ring, 129 140 248), var(--lp-ring-alpha, 0.2))`,
                  } as React.CSSProperties
                }
              >
                <input
                  type="text"
                  value={category}
                  onChange={(e) => setCategory(e.target.value)}
                  className="lp-input placeholder-muted flex-1"
                  placeholder={placeholder}
                  aria-label={lp.inputAriaLabel || 'Quiz Topic'}
                  disabled={isSubmitting}
                />

                {/* Solid primary circular submit for a tasteful color pop */}
                <IconButton
                  type="submit"
                  Icon={ArrowIcon}
                  label={lp.submitButton || lp.buttonText || 'Generate quiz'}
                  disabled={isSubmitting || !category.trim()}
                  size="md"
                  className="lp-submit lp-submit-colored shrink-0"
                  style={{ fontSize: 'var(--font-size-button, 1rem)' }}
                />
              </div>

              {isSubmitting && <Spinner className="mt-4" />}

              {inlineError && (
                <p className="text-red-600 text-sm mt-2">{inlineError}</p>
              )}

              {showTurnstile && (
                <div className="flex justify-center mt-6">
                  <Turnstile
                    onVerify={handleTurnstileVerify}
                    onError={handleTurnstileError}
                    theme="auto"
                  />
                </div>
              )}
            </form>
          </div>

        </div>
      </div>
    </div>
  );
};
