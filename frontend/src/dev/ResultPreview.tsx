import React from 'react';
import { HeroCard } from '../components/layout/HeroCard';
import { ResultProfile } from '../components/result/ResultProfile';
import { FeedbackIcons } from '../components/result/FeedbackIcons';
import type { ResultProfileData } from '../types/result';

/**
 * Dev-only Result Preview
 * Query params:
 *   ?image=0     -> hide image
 *   ?traits=0    -> hide traits
 *   ?title=...   -> override title
 *   ?summary=... -> override summary
 */
export function ResultPreview() {
  const params = new URLSearchParams(window.location.search);
  const withImage = params.get('image') !== '0';
  const withTraits = params.get('traits') !== '0';

  const sample: ResultProfileData = {
    profileTitle: params.get('title') ?? 'You are The Strategist',
    summary:
      params.get('summary') ??
      `You see the long game. You’re calm under pressure, and you love turning messy situations into clear, executable plans.\n\nCuriosity drives you, but structure keeps you grounded.`,
    imageUrl: withImage
      ? 'https://images.unsplash.com/photo-1517180102446-f3ece451e9d8?q=80&w=1600&auto=format&fit=crop'
      : undefined,
    imageAlt: 'Abstract geometric shapes',
    traits: withTraits
      ? [
          { label: 'Analytical', value: 'You ask great questions' },
          { label: 'Decisive', value: 'You act when it counts' },
          { label: 'Curious', value: 'You explore before you commit' },
          { label: 'Reliable', value: 'People trust your judgment' },
        ]
      : [],
    shareUrl: window.location.href,
  };

  const shareUrl = sample.shareUrl ?? `${window.location.origin}/result/preview`;

  const handleCopy = async () => {
    try {
      await navigator.clipboard.writeText(shareUrl);
    } catch {
      // no-op in dev
    }
  };

  const goHome = () => {
    window.location.href = '/';
  };

  return (
    <main className="flex items-center justify-center flex-grow">
      <div className="lp-wrapper w-full flex items-start justify-center p-4 sm:p-6">
        <HeroCard ariaLabel="Result preview card" showHero={false}>
          <div className="max-w-3xl mx-auto">
            {/* Title centered; content aligns itself as needed */}
            <div className="text-center">
              <ResultProfile
                result={sample}
                labels={{
                  titlePrefix: '',
                  traitListTitle: 'Your Traits',
                  startOverButton: 'Start Another Quiz',
                  shareButton: 'Share your result',
                  shareCopied: 'Link Copied!',
                  shareText: 'Check out my quiz result!',
                  shared: 'Shared!',
                  copyLink: 'Copy link',
                  feedback: {
                    prompt: 'What did you think of your result?',
                    submit: 'Submit Feedback',
                    thanks: 'Thank you for your feedback!',
                    thumbsUp: 'Thumbs up',
                    thumbsDown: 'Thumbs down',
                    commentPlaceholder: 'Add a comment (optional)…',
                    turnstileError: 'Please complete the security check before submitting.',
                  },
                }}
                shareUrl={shareUrl}
                onCopyShare={handleCopy}
                onStartNew={goHome}
              />
            </div>

            {/* Feedback affordance—quiet, at the bottom of the card */}
            <section className="mt-10 pt-8 border-t border-muted/40">
              <h2 className="sr-only">Feedback</h2>
              <FeedbackIcons
                quizId="preview-quiz-id"
                labels={{
                  prompt: 'What did you think of your result?',
                  submit: 'Submit Feedback',
                  thanks: 'Thank you for your feedback!',
                  thumbsUp: 'Thumbs up',
                  thumbsDown: 'Thumbs down',
                  commentPlaceholder: 'Add a comment (optional)…',
                  turnstileError: 'Please complete the security check before submitting.',
                }}
              />
            </section>
          </div>
        </HeroCard>
      </div>
    </main>
  );
}

export default ResultPreview;
