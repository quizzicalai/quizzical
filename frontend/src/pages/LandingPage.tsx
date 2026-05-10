// frontend/src/pages/LandingPage.tsx
import React, { useState, useCallback, useRef } from 'react';
import { useNavigate } from 'react-router-dom';
import { useConfig } from '../context/ConfigContext';
import { useQuizActions } from '../store/quizStore';
import type { ApiError } from '../types/api';
import { Spinner } from '../components/common/Spinner';
import Turnstile from '../components/common/Turnstile';
import IconButton from '../components/common/IconButton';
import { ArrowIcon } from '../assets/icons/ArrowIcon';
import { HeroCard } from '../components/layout/HeroCard';
import TopicSuggestionExplorer from '../components/landing/TopicSuggestionExplorer';
import { validateCategory } from '../utils/categoryValidation';
import { usePlaceholderRotation } from '../hooks/usePlaceholderRotation';

// Inline loading strip
import { WhimsySprite } from '../components/loading/WhimsySprite';
import { LoadingNarration } from '../components/loading/LoadingNarration';

export const LandingPage: React.FC = () => {
  const navigate = useNavigate();
  const { config } = useConfig();
  const { startQuiz } = useQuizActions();

  const [category, setCategory] = useState('');
  const [isSubmitting, setIsSubmitting] = useState(false);
  const [inlineError, setInlineError] = useState<string | null>(null);
  const [turnstileToken, setTurnstileToken] = useState<string | null>(null);
  const topicInputRef = useRef<HTMLInputElement | null>(null);
  const errorTextId = 'landing-topic-error';

  const handleTurnstileVerify = useCallback((token: string) => {
    setTurnstileToken(token);
    setInlineError(null);
  }, []);

  const handleTurnstileError = useCallback(() => {
    setTurnstileToken(null);
    setInlineError('Verification failed. Please try again.');
  }, []);

  const handleTurnstileExpire = useCallback(() => {
    // Token expired; our Turnstile component auto re-executes.
    // Clear until we receive a fresh one.
    setTurnstileToken(null);
  }, []);

  const submitCategory = useCallback(async (rawCategory: string) => {
    if (isSubmitting || !rawCategory.trim() || !turnstileToken) return;

    // FE-IN-PROD-1..5: client-side validation mirroring BE category rules.
    const validation = validateCategory(rawCategory);
    if (!validation.ok) {
      setInlineError(validation.message);
      return;
    }

    setInlineError(null);
    setIsSubmitting(true);

    try {
      await startQuiz(validation.sanitized, turnstileToken);
      navigate('/quiz');
    } catch (err: any) {
      // Get a fresh token immediately after any backend failure
      (window as any).resetTurnstile?.();
      setTurnstileToken(null);

      const apiError = err as ApiError;
      // FE-ERR-PROD-3: surface the canonical 413 message verbatim.
      let userMessage: string | undefined;
      if (apiError?.code === 'payload_too_large') {
        userMessage = apiError.message;
      } else if (apiError?.code === 'rate_limited') {
        // FE-ERR-PROD-1: rate-limited start; suggest a short wait.
        const secs = apiError.retryAfterMs ? Math.max(1, Math.round(apiError.retryAfterMs / 1000)) : 0;
        userMessage = secs
          ? `Too many attempts. Please try again in ${secs} second${secs === 1 ? '' : 's'}.`
          : 'Too many attempts. Please wait a moment and try again.';
      } else if (apiError?.code === 'service_unavailable' || apiError?.code === 'gateway_timeout') {
        // FE-ERR-PROD-6: differentiated 503/504 surface their canonical messages.
        userMessage = apiError.message;
      } else if (apiError?.code === 'category_not_found') {
        userMessage = config?.content?.errors?.categoryNotFound;
      } else {
        userMessage = config?.content?.errors?.quizCreationFailed;
      }
      setInlineError(userMessage || 'Could not create a quiz. Please try again.');
    } finally {
      setIsSubmitting(false);
    }
  }, [isSubmitting, turnstileToken, startQuiz, navigate, config]);

  const handleSelectSuggestedTopic = useCallback((topic: string) => {
    void submitCategory(topic);
  }, [submitCategory]);

  const handleSubmit = useCallback(async (event: React.FormEvent<HTMLFormElement>) => {
    event.preventDefault();
    await submitCategory(category);
  }, [submitCategory, category]);

  const lp = config?.content?.landingPage ?? {};
  const examples: string[] = Array.isArray(lp.examples)
    ? (lp.examples as string[]).filter((s) => typeof s === 'string' && s.trim() !== '')
    : [];

  const configuredPlaceholder =
    typeof lp.placeholder === 'string' && lp.placeholder.trim()
      ? lp.placeholder
      : (examples.length
          ? `e.g., ${examples.slice(0, 2).map((e: string) => `"${e}"`).join(', ')}`
          : 'Hogwarts house');

  // Pause rotation while the user is interacting with the field.
  const isInputBusy = category.length > 0 || isSubmitting;
  const rotatingPlaceholder = usePlaceholderRotation({
    paused: isInputBusy,
    fallback: configuredPlaceholder,
  });
  const placeholder = isInputBusy ? configuredPlaceholder : (rotatingPlaceholder || configuredPlaceholder);

  if (!config) {
    return (
      <div className="flex-grow flex items-center justify-center">
        <Spinner />
      </div>
    );
  }

  return (
    <HeroCard ariaLabel="Landing hero card">
      {/* Invisible Turnstile runs on page load; shows nothing unless there's an error */}
      <Turnstile
        size="invisible"
        autoExecute
        onVerify={handleTurnstileVerify}
        onError={handleTurnstileError}
        onExpire={handleTurnstileExpire}
      />

      {isSubmitting ? (
        <div className="flex justify-center mt-8" data-testid="lp-loading-inline">
          <div className="inline-flex items-center gap-3">
            <WhimsySprite />
            <LoadingNarration />
          </div>
        </div>
      ) : (
        <>
          <p className="text-muted/90 lp-subtitle lp-subtitle-maxw mx-auto">
            {lp.subtitle || 'A personality quiz for any subject'}
          </p>

          <div className="lp-form-maxw mx-auto lp-space-sub-form">
            <form onSubmit={handleSubmit} className="w-full">
              <div className="lp-question-frame" data-testid="lp-question-frame">
                <span className="lp-question-word" aria-hidden="true">Which</span>

                <div
                  className="lp-pill lp-pill-question"
                  style={
                    {
                      ['--tw-ring-color' as any]: `rgba(var(--color-ring, 129 140 248), var(--lp-ring-alpha, 0.2))`,
                    } as React.CSSProperties
                  }
                >
                  <input
                    ref={topicInputRef}
                    type="text"
                    value={category}
                    onChange={(e) => setCategory(e.target.value)}
                    className="lp-input lp-input-question placeholder-muted flex-1"
                    placeholder={placeholder}
                    aria-label={lp.inputAriaLabel || 'Quiz Topic'}
                    aria-describedby={inlineError ? errorTextId : undefined}
                    disabled={isSubmitting}
                  />

                  <IconButton
                    type="submit"
                    Icon={ArrowIcon}
                    label={lp.submitButton || lp.buttonText || 'Generate quiz'}
                    disabled={isSubmitting || !category.trim() || !turnstileToken}
                    size="md"
                    className="lp-submit lp-submit-colored shrink-0"
                    style={{ fontSize: 'var(--font-size-button, 1rem)' }}
                  />
                </div>

                <span className="lp-question-word" aria-hidden="true">am I?</span>
              </div>

              {/* Plain text error only (Turnstile or server) */}
              {inlineError && (
                <p
                  id={errorTextId}
                  role="alert"
                  className="mt-3 rounded-lg border border-error-border bg-error-soft px-3 py-2 text-sm text-error"
                >
                  {inlineError}
                </p>
              )}
            </form>

            <TopicSuggestionExplorer onSelectTopic={handleSelectSuggestedTopic} />
          </div>
        </>
      )}
    </HeroCard>
  );
};
