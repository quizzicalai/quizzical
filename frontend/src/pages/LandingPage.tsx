// frontend/src/pages/LandingPage.tsx
import React, { useState, useCallback, useRef } from 'react';
import { useNavigate } from 'react-router-dom';
import { useConfig } from '../context/ConfigContext';
import { useQuizActions } from '../store/quizStore';
import type { ApiError } from '../types/api';
import { Spinner } from '../components/common/Spinner';
import Turnstile from '../components/common/Turnstile';
import { HeroCard } from '../components/layout/HeroCard';
import TopicSuggestionExplorer from '../components/landing/TopicSuggestionExplorer';
import { validateCategory } from '../utils/categoryValidation';
import { usePlaceholderRotation } from '../hooks/usePlaceholderRotation';

// Inline loading strip
import { WhimsySprite } from '../components/loading/WhimsySprite';
import { LoadingNarration, LANDING_PREPARING_LINES } from '../components/loading/LoadingNarration';

export const LandingPage: React.FC = () => {
  const navigate = useNavigate();
  const { config } = useConfig();
  const { startQuiz } = useQuizActions();

  const [category, setCategory] = useState('');
  const [isSubmitting, setIsSubmitting] = useState(false);
  const [inlineError, setInlineError] = useState<string | null>(null);
  const [turnstileToken, setTurnstileToken] = useState<string | null>(null);
  // Tracks whether the most recent failure was a Turnstile rejection so
  // we can transparently auto-retry once a fresh token arrives from the
  // invisible widget. Without this the user sees the friendly
  // "Security check needs to refresh…" toast but the next submit only
  // succeeds if they click again — most users don't realise that's
  // required and abandon. We bound retries to 1 so a persistent
  // Cloudflare-level failure can't loop.
  const pendingTurnstileRetryRef = useRef<{ topic: string } | null>(null);
  // Latch: once we've had a token at least once, keep the form visible even
  // if the token is later reset (after a backend error or expiry). The
  // submit button is still `disabled` until a fresh token arrives, so we
  // can't accidentally submit without one — but we don't punish the user
  // by collapsing the entire form back into a loading state mid-flow.
  const [hasEverHadToken, setHasEverHadToken] = useState(false);
  const topicInputRef = useRef<HTMLInputElement | null>(null);
  const errorTextId = 'landing-topic-error';

  const handleTurnstileVerify = useCallback((token: string) => {
    setTurnstileToken(token);
    setHasEverHadToken(true);
    setInlineError(null);
    // P0b auto-retry: if the most recent submission failed because
    // Cloudflare rejected our token (typically a stale token replay after
    // a back-navigation), retry it once with the fresh token we just got.
    const pending = pendingTurnstileRetryRef.current;
    if (pending) {
      pendingTurnstileRetryRef.current = null;
      // Defer one tick so React commits the token state before the
      // submit guard in submitCategory re-reads it.
      setTimeout(() => { void submitCategoryRef.current?.(pending.topic); }, 0);
    }
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
      } else if (apiError?.code === 'turnstile_failed') {
        // P0b: queue a single auto-retry; the friendly toast bridges the
        // ~1–2s gap while the invisible widget mints a fresh token, and
        // handleTurnstileVerify will re-fire submitCategory on arrival.
        userMessage = apiError.message;
        if (!pendingTurnstileRetryRef.current) {
          pendingTurnstileRetryRef.current = { topic: validation.sanitized };
        }
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

  // Stable ref so handleTurnstileVerify (declared above submitCategory)
  // can invoke the latest submitCategory closure when a fresh token
  // arrives after a P0b auto-retry queue.
  const submitCategoryRef = useRef(submitCategory);
  React.useEffect(() => {
    submitCategoryRef.current = submitCategory;
  }, [submitCategory]);

  const handleSelectSuggestedTopic = useCallback((topic: string) => {
    void submitCategory(topic);
  }, [submitCategory]);

  const handleSubmit = useCallback(async (event: React.FormEvent<HTMLFormElement>) => {
    event.preventDefault();
    await submitCategory(category);
  }, [submitCategory, category]);

  const lp = config?.content?.landingPage ?? {};
  // UX audit M3: client-side cap mirroring backend validation; counter shows
  // remaining-of-max once the user is within ~30% of the limit.
  const categoryMaxLength = config?.limits?.validation?.category_max_length ?? 80;
  const counterId = 'lp-category-counter';
  const showCounter = category.length >= Math.floor(categoryMaxLength * 0.7);
  const counterAtLimit = category.length >= categoryMaxLength;
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

  // Gate the visible form on having a Turnstile token (real or bypass). The
  // submit button is also `disabled` until the token arrives, but rendering
  // an enabled-looking input + greyed button before the invisible widget
  // resolves the round-trip looks broken to users — they click and nothing
  // happens. Showing an explicit "Securing your session…" spinner instead
  // makes the wait honest and prevents any chance of submitting without a
  // token. The Turnstile widget itself is mounted unconditionally below so
  // the token can resolve while the loader is showing.
  const tokenReady = !!turnstileToken;
  const showPreparing = !tokenReady && !isSubmitting && !hasEverHadToken;

  return (
    <HeroCard ariaLabel="Landing hero card">
      {/* Invisible Turnstile runs on page load; mounted regardless of which
          view (preparing / submitting / form) is active so token resolution
          and the form are decoupled. */}
      <Turnstile
        size="invisible"
        autoExecute
        onVerify={handleTurnstileVerify}
        onError={handleTurnstileError}
        onExpire={handleTurnstileExpire}
      />

      {/* Inline errors (e.g., Turnstile failure) are rendered above the
          gated views so they are visible while the loader is showing.
          When the form is visible the form-side error is used instead, so
          we don't render two copies of the same message. */}
      {inlineError && showPreparing && (
        <p
          id={errorTextId}
          role="alert"
          className="mt-3 mx-auto max-w-md rounded-lg border border-error-border bg-error-soft px-3 py-2 text-sm text-error text-center"
        >
          {inlineError}
        </p>
      )}

      {isSubmitting ? (
        <div className="flex justify-center mt-8" data-testid="lp-loading-inline">
          <div className="inline-flex items-center gap-3">
            <WhimsySprite spinning />
            <LoadingNarration />
          </div>
        </div>
      ) : showPreparing ? (
        <div
          className="flex flex-col items-center justify-center gap-3 mt-8"
          data-testid="lp-preparing"
          aria-busy="true"
        >
          {/* AC-UX-2026-05-12 — friendlier "Loading…" headline with a
              rotating sub-message that telegraphs the breadth of Quafel
              topics while invisible Turnstile resolves. */}
          <div className="flex items-center gap-3">
            <WhimsySprite spinning />
            <span className="text-lg font-semibold text-fg">Loading…</span>
          </div>
          <div className="max-w-md text-center text-sm text-muted">
            <LoadingNarration
              lines={LANDING_PREPARING_LINES}
              ariaLabel={lp.preparingMessage || 'Preparing your quiz'}
            />
          </div>
        </div>
      ) : (
        <>
          {/* AC-UX-2026-05-13 — stationary sprite to the left of the
              tagline matches the in-progress loading affordance, and the
              tagline is now action-oriented ("You pick the topic, I'll
              generate the quiz!") to make the call-to-action obvious
              before the user reads the input. Italic styling lives in
              .lp-subtitle CSS and was removed in the same audit.

              Rendered as a <div role="paragraph"> rather than a real <p>
              because WhimsySprite mounts <ldrs/> web-component-style
              <div> children, which are invalid descendants of <p> and
              produce a React 18 hydration warning. The role keeps the
              accessibility semantics identical for screen readers. */}
          <div
            role="paragraph"
            className="text-muted/90 lp-subtitle lp-subtitle-maxw mx-auto inline-flex items-center justify-center gap-2"
          >
            <WhimsySprite />
            <span>{lp.subtitle || 'A personality quiz for\u2026 everything.'}</span>
          </div>

          <div className="lp-form-maxw lg:max-w-3xl mx-auto lp-space-sub-form-tight">
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
                    aria-required="true"
                    aria-describedby={
                      [
                        inlineError ? errorTextId : null,
                        showCounter ? counterId : null,
                      ]
                        .filter(Boolean)
                        .join(' ') || undefined
                    }
                    maxLength={categoryMaxLength}
                    disabled={isSubmitting}
                  />
                </div>

                <span className="lp-question-word" aria-hidden="true">am I?</span>
              </div>
              {/* Submit moved below the input. Disabled (light grey)
                  until the user has typed something AND the invisible
                  Turnstile token has resolved; enabled fills with the
                  primary brand colour. Full-width on phones, auto-width
                  on larger screens — same pattern as the FinalPage CTAs.
                  Inline style with numeric RGB fallback guarantees the
                  brand fill even when --color-primary is unset (e.g.
                  before ThemeInjector runs, or if the backend config
                  omits theme.colors.primary). Without it the bare
                  `bg-primary` Tailwind token resolves to
                  `rgba(var(--color-primary), 1)` with NO fallback and the
                  button renders white-on-white — same documented
                  regression already fixed in SynopsisView / IconButton. */}
              {(() => {
                const startDisabled =
                  isSubmitting || !category.trim() || !turnstileToken;
                return (
                  <div className="mt-4 flex justify-center">
                    <button
                      type="submit"
                      data-testid="lp-submit"
                      disabled={startDisabled}
                      data-state={startDisabled ? 'disabled' : 'enabled'}
                      style={
                        startDisabled
                          ? {
                              // A2 (UI-REVIEW-2026-06-29): bump the disabled
                              // label legibility (fg/0.7 on muted/0.30 = ~5.88:1)
                              // and correct the stale 203 213 225 fallback to the
                              // runtime-injected muted (148 163 184). Still
                              // `disabled` — no behavior change.
                              backgroundColor:
                                'rgb(var(--color-muted, 148 163 184) / 0.30)',
                              color: 'rgb(var(--color-fg, 15 23 42) / 0.7)',
                            }
                          : {
                              backgroundColor:
                                'rgb(var(--color-primary, 79 70 229))',
                              color: 'rgb(255 255 255)',
                            }
                      }
                      className="inline-flex w-full sm:w-auto min-h-[44px] items-center justify-center rounded-xl px-6 py-2.5 text-sm font-semibold shadow-sm transition-colors transition-opacity hover:opacity-95 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-primary/50 disabled:cursor-not-allowed disabled:hover:opacity-100"
                    >
                      {lp.submitButton || lp.buttonText || 'Start Quiz'}
                    </button>
                  </div>
                );
              })()}

              {/* AC-UX-2026-05-25-PART3 item 1 \u2014 the explicit hint
                  "Enter any topic to start your quiz" was removed per
                  user feedback. The primary-tinted input border (item 3,
                  see .lp-pill in index.css) now carries the affordance
                  visually. This spacer preserves the vertical rhythm
                  between the Start Quiz button and the Popular/Random
                  chip explorer so the form still feels balanced. */}
              <div
                aria-hidden="true"
                data-testid="lp-topic-hint-spacer"
                className="mt-6"
              />

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

              {/* UX audit M3: visible char counter once user nears the limit. */}
              {showCounter && (
                <p
                  id={counterId}
                  data-testid="lp-category-counter"
                  aria-live="polite"
                  className={
                    'mt-2 text-right text-xs tabular-nums ' +
                    (counterAtLimit ? 'text-error' : 'text-muted')
                  }
                >
                  {category.length}/{categoryMaxLength}
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
