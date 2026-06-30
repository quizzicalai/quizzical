import React, { useMemo, useState } from 'react';
import { useConfig } from '../../context/ConfigContext';

/**
 * DonateCTA — a tasteful, dismissible post-result "support us" ask.
 *
 * Strategy (see DONATE-STRATEGY.md):
 *  - Shown ONLY on the result screen, AFTER the result + share buttons render
 *    (the peak-end "moment of delight"). It is inline, never a modal/popup,
 *    and never blocks or precedes the result.
 *  - One line of honest cost-transparency copy + three suggested amount chips
 *    ($3 / $5 / $10) with the middle ($5) visually preselected, one donate
 *    button, and a low-pressure "Maybe later" dismiss.
 *  - The dismissal is remembered in localStorage so repeat takers aren't nagged.
 *
 * CRITICAL: this renders ONLY when a donation URL is configured. It reads the
 * URL from the app config content via optional chaining (`content?.donationUrl`).
 * If that key is absent or empty, the component renders nothing — it must
 * degrade to hidden, never a broken link. (The backend adds the key + the owner
 * sets the real Ko-fi/Stripe URL later.)
 */

const DISMISS_KEY = 'quafel:donate-cta:dismissed';

type AmountChip = { value: number; label: string };

const AMOUNTS: AmountChip[] = [
  { value: 3, label: '$3' },
  { value: 5, label: '$5' }, // middle — preselected
  { value: 10, label: '$10' },
];

const DEFAULT_AMOUNT = 5;

/** SSR-safe localStorage read. Never throws (private mode / disabled storage). */
function readDismissed(): boolean {
  try {
    if (typeof window === 'undefined' || !window.localStorage) return false;
    return window.localStorage.getItem(DISMISS_KEY) === '1';
  } catch {
    return false;
  }
}

/** SSR-safe localStorage write. Never throws. */
function writeDismissed(): void {
  try {
    if (typeof window === 'undefined' || !window.localStorage) return;
    window.localStorage.setItem(DISMISS_KEY, '1');
  } catch {
    /* storage unavailable — dismissal just won't persist; harmless */
  }
}

/**
 * Append the selected amount as a best-effort `?amount=N` query param. Hosted
 * tip pages (Ko-fi / Stripe Payment Links) ignore unknown params gracefully,
 * so this never produces a broken link. Falls back to the raw URL if it can't
 * be parsed.
 */
function buildDonateHref(donationUrl: string, amount: number): string {
  try {
    const url = new URL(donationUrl, window.location.origin);
    url.searchParams.set('amount', String(amount));
    return url.toString();
  } catch {
    return donationUrl;
  }
}

export type DonateCTAProps = {
  /**
   * Optional explicit donation URL. When omitted, it is read from the app
   * config (`content?.donationUrl`). Exposed primarily for tests/storybook.
   */
  donationUrl?: string;
  className?: string;
};

export function DonateCTA({ donationUrl, className }: DonateCTAProps) {
  const { config } = useConfig();
  // Read via optional chaining; empty/whitespace is treated as "not configured".
  const configuredUrl = (donationUrl ?? config?.content?.donationUrl ?? '').trim();

  const [dismissed, setDismissed] = useState<boolean>(() => readDismissed());
  const [selected, setSelected] = useState<number>(DEFAULT_AMOUNT);

  const href = useMemo(
    () => (configuredUrl ? buildDonateHref(configuredUrl, selected) : ''),
    [configuredUrl, selected],
  );

  // Degrade to hidden when no URL is configured or the user dismissed it.
  if (!configuredUrl || dismissed) return null;

  const handleDismiss = () => {
    writeDismissed();
    setDismissed(true);
  };

  return (
    <section
      aria-label="Support Quafel"
      data-testid="donate-cta"
      className={
        'mt-8 mx-auto w-full max-w-md rounded-token-lg border border-muted/30 bg-card/80 px-5 py-4 text-center ' +
        (className ? ` ${className}` : '')
      }
    >
      {/* Secondary/body text at AA contrast via the A1 --color-text-secondary
          token (slate-600 = 7.58:1 on the white card). Consumed as a raw CSS
          var (no Tailwind utility is exposed for it) with a numeric fallback,
          matching the codebase's defensive inline-RGB pattern. */}
      <p
        className="text-sm"
        style={{ color: 'rgb(var(--color-text-secondary, 71 85 105))' }}
      >
        Each quiz costs us a few cents in AI. If Quafel made you smile, you can
        chip in to keep it free for everyone.
      </p>

      <div
        role="group"
        aria-label="Suggested donation amount"
        className="mt-3 flex items-center justify-center gap-2"
      >
        {AMOUNTS.map(({ value, label }) => {
          const isSelected = value === selected;
          return (
            <button
              key={value}
              type="button"
              onClick={() => setSelected(value)}
              aria-pressed={isSelected}
              data-testid={`donate-amount-${value}`}
              className={
                'min-h-[40px] min-w-[3rem] rounded-token-pill border px-4 py-1.5 text-sm font-semibold transition-colors duration-fast focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-primary/60 ' +
                (isSelected
                  ? 'border-primary bg-primary text-white'
                  : 'border-muted/40 bg-card text-fg hover:border-primary/50')
              }
            >
              {label}
            </button>
          );
        })}
      </div>

      <div className="mt-4 flex flex-col items-center gap-2">
        {/* UX-2026-06-29 (quiz-ux-polish item 2) — DEMOTED from the solid
            indigo `bg-primary` fill to a quiet OUTLINED secondary so the
            donate ask no longer competes with the Share trigger for the
            single primary emphasis on the result screen. Per
            DONATE-STRATEGY.md the donate CTA is "visually quiet" and rides
            the user's own share momentum; Share is the peak-end primary.
            Functionality + a11y unchanged (still a >=44px focusable link
            opening the hosted tip page). */}
        <a
          href={href}
          target="_blank"
          rel="noopener noreferrer"
          data-testid="donate-go"
          className="inline-flex min-h-[44px] w-full sm:w-auto items-center justify-center rounded-xl border border-muted/40 bg-card px-6 py-2.5 text-sm font-semibold text-fg shadow-sm transition-[border-color,box-shadow,opacity] hover:border-primary/50 hover:shadow-md focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-primary/50"
        >
          Buy us a coffee
        </a>
        <button
          type="button"
          onClick={handleDismiss}
          data-testid="donate-dismiss"
          className="text-xs text-muted underline-offset-2 hover:underline focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-primary/40 rounded"
        >
          Maybe later
        </button>
      </div>
    </section>
  );
}

export default DonateCTA;
