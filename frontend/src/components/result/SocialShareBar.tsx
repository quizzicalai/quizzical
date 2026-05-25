import React, { useCallback, useEffect, useMemo, useRef, useState } from 'react';
import clsx from 'clsx';
import { safeImageUrl } from '../../utils/safeImageUrl';
import { XIcon } from '../../assets/icons/social/XIcon';
import { FacebookIcon } from '../../assets/icons/social/FacebookIcon';
import { LinkedInIcon } from '../../assets/icons/social/LinkedInIcon';
import { WhatsAppIcon } from '../../assets/icons/social/WhatsAppIcon';
import { RedditIcon } from '../../assets/icons/social/RedditIcon';
import { EmailIcon } from '../../assets/icons/social/EmailIcon';
import { LinkIcon } from '../../assets/icons/social/LinkIcon';
import { CheckIcon } from '../../assets/icons/CheckIcon';
import { ShareIcon } from '../../assets/icons/ShareIcon';

/**
 * Labels (all optional, fall back to sensible English defaults).
 *
 * The bar is intentionally label-free in the UI itself (each button is an
 * icon with `aria-label` + native tooltip via `title`) — labels here are
 * the source for both. This mirrors how YouTube / Medium present their
 * share trays: a clean row of recognizable brand glyphs.
 */
export type SocialShareLabels = {
  heading?: string;
  preview?: string;
  copyLink?: string;
  copied?: string;
  copyFailed?: string;
  nativeShare?: string;
  shareOnX?: string;
  shareOnFacebook?: string;
  shareOnLinkedIn?: string;
  shareOnWhatsApp?: string;
  shareOnReddit?: string;
  shareViaEmail?: string;
};

export type SocialShareBarProps = {
  /** Absolute URL of the result page being shared. */
  shareUrl: string;
  /** Title for native share / X / email subject. */
  shareTitle: string;
  /** Free-form blurb used by X / WhatsApp / Reddit / email body. */
  shareText?: string;
  /** Optional preview image (square is fine — same one shown on the page). */
  imageUrl?: string;
  /** Short copy shown beside the preview (e.g. summary first line). */
  previewSubtitle?: string;
  labels?: SocialShareLabels;
  /**
   * Optional override for clipboard write. Tests pass a stub; in production
   * we use `navigator.clipboard.writeText` with a `document.execCommand`
   * fallback for older browsers / non-secure contexts.
   */
  writeToClipboard?: (text: string) => Promise<void> | void;
  className?: string;
};

const DEFAULTS: Required<SocialShareLabels> = {
  heading: 'Share your result',
  preview: 'Preview',
  copyLink: 'Copy link',
  copied: 'Link copied',
  copyFailed: 'Could not copy. Long-press the link to copy manually.',
  nativeShare: 'Share via your device',
  shareOnX: 'Share on X',
  shareOnFacebook: 'Share on Facebook',
  shareOnLinkedIn: 'Share on LinkedIn',
  shareOnWhatsApp: 'Share on WhatsApp',
  shareOnReddit: 'Share on Reddit',
  shareViaEmail: 'Share via email',
};

/**
 * Robust clipboard write that survives:
 *  - Browsers without `navigator.clipboard` (older Safari / WebViews).
 *  - Insecure contexts (http://, file://) where the async API is null.
 *  - Permission denial (returns rejected promise).
 *
 * Falls back to a hidden <textarea> + execCommand('copy') sequence.
 */
async function defaultClipboardWrite(text: string): Promise<void> {
  if (typeof navigator !== 'undefined' && navigator.clipboard?.writeText) {
    try {
      await navigator.clipboard.writeText(text);
      return;
    } catch {
      // fall through to legacy path
    }
  }
  if (typeof document === 'undefined') {
    throw new Error('clipboard unavailable');
  }
  const ta = document.createElement('textarea');
  ta.value = text;
  ta.setAttribute('readonly', '');
  ta.style.position = 'fixed';
  ta.style.top = '-1000px';
  ta.style.opacity = '0';
  document.body.appendChild(ta);
  ta.select();
  let ok = false;
  try {
    ok = document.execCommand('copy');
  } finally {
    document.body.removeChild(ta);
  }
  if (!ok) throw new Error('clipboard execCommand failed');
}

type IntentSpec = {
  key: string;
  label: keyof Required<SocialShareLabels>;
  Icon: React.ComponentType<React.SVGProps<SVGSVGElement>>;
  /** Build the target URL given an already-encoded url + text. */
  build: (encodedUrl: string, encodedText: string, encodedTitle: string) => string;
  /** Hex hover color. */
  hover: string;
};

const INTENTS: IntentSpec[] = [
  {
    key: 'x',
    label: 'shareOnX',
    Icon: XIcon,
    build: (u, t) => `https://twitter.com/intent/tweet?url=${u}&text=${t}`,
    hover: '#000000',
  },
  {
    key: 'facebook',
    label: 'shareOnFacebook',
    Icon: FacebookIcon,
    build: (u) => `https://www.facebook.com/sharer/sharer.php?u=${u}`,
    hover: '#1877F2',
  },
  {
    key: 'linkedin',
    label: 'shareOnLinkedIn',
    Icon: LinkedInIcon,
    build: (u) => `https://www.linkedin.com/sharing/share-offsite/?url=${u}`,
    hover: '#0A66C2',
  },
  {
    key: 'whatsapp',
    label: 'shareOnWhatsApp',
    Icon: WhatsAppIcon,
    // wa.me handles both web + native deep-link automatically.
    build: (u, t) => `https://wa.me/?text=${t}%20${u}`,
    hover: '#25D366',
  },
  {
    key: 'reddit',
    label: 'shareOnReddit',
    Icon: RedditIcon,
    build: (u, _t, ti) => `https://www.reddit.com/submit?url=${u}&title=${ti}`,
    hover: '#FF4500',
  },
  {
    key: 'email',
    label: 'shareViaEmail',
    Icon: EmailIcon,
    // mailto: subject + body; trailing url makes it auto-clickable in clients.
    build: (u, t, ti) => `mailto:?subject=${ti}&body=${t}%0A%0A${u}`,
    hover: '#475569',
  },
];

/**
 * Polished share bar inspired by YouTube / Medium / Notion: a small
 * preview card on top, then a row of brand-color icon buttons.
 *
 * - All icon buttons have BOTH `aria-label` (for screen readers) and
 *   `title` (for hover tooltip), with no visible text. This is the
 *   industry pattern for share trays.
 * - The "Copy link" button morphs into a check + "Link copied" pill on
 *   success; recovers automatically after ~2s.
 * - The native Web Share button only renders on devices that expose
 *   `navigator.share` (mobile + a few desktop browsers); otherwise the
 *   existing platform list is the source of truth.
 * - All external-share links use `target="_blank" rel="noopener noreferrer"`.
 */
export function SocialShareBar({
  shareUrl,
  shareTitle,
  shareText = '',
  imageUrl,
  previewSubtitle,
  labels = {},
  writeToClipboard,
  className,
}: SocialShareBarProps) {
  const L: Required<SocialShareLabels> = { ...DEFAULTS, ...labels };
  const [copyState, setCopyState] = useState<'idle' | 'copied' | 'error'>('idle');
  const [nativeShareSupported, setNativeShareSupported] = useState(false);
  const [isOpen, setIsOpen] = useState(false);
  const triggerRef = useRef<HTMLButtonElement | null>(null);
  const closeBtnRef = useRef<HTMLButtonElement | null>(null);

  // Resolve native share availability lazily (SSR-safe).
  React.useEffect(() => {
    setNativeShareSupported(
      typeof navigator !== 'undefined' && typeof navigator.share === 'function',
    );
  }, []);

  const safeImage = safeImageUrl(imageUrl) ?? undefined;

  const intents = useMemo(() => {
    const u = encodeURIComponent(shareUrl);
    const t = encodeURIComponent(shareText || shareTitle);
    const ti = encodeURIComponent(shareTitle);
    return INTENTS.map((i) => ({ ...i, href: i.build(u, t, ti) }));
  }, [shareUrl, shareText, shareTitle]);

  const handleCopy = useCallback(async () => {
    const writer = writeToClipboard ?? defaultClipboardWrite;
    try {
      await writer(shareUrl);
      setCopyState('copied');
      window.setTimeout(() => setCopyState('idle'), 2000);
    } catch {
      setCopyState('error');
      window.setTimeout(() => setCopyState('idle'), 4000);
    }
  }, [shareUrl, writeToClipboard]);

  const handleNativeShare = useCallback(async () => {
    if (typeof navigator === 'undefined' || typeof navigator.share !== 'function') {
      // Defensive: should not be reachable because the button is gated.
      await handleCopy();
      return;
    }
    try {
      await navigator.share({
        title: shareTitle,
        text: shareText || shareTitle,
        url: shareUrl,
      });
    } catch {
      // User cancelled or share failed — silent (matches platform UX).
    }
  }, [handleCopy, shareText, shareTitle, shareUrl]);

  const closeModal = useCallback(() => {
    setIsOpen(false);
    // Restore focus to the trigger so keyboard users land back where
    // they were before opening the dialog.
    window.setTimeout(() => triggerRef.current?.focus(), 0);
  }, []);

  // Close on Escape, and move focus into the dialog when opened.
  useEffect(() => {
    if (!isOpen) return;
    const onKey = (e: KeyboardEvent) => {
      if (e.key === 'Escape') {
        e.stopPropagation();
        closeModal();
      }
    };
    window.addEventListener('keydown', onKey);
    // Focus the close button on open for an obvious keyboard exit path.
    window.setTimeout(() => closeBtnRef.current?.focus(), 0);
    return () => window.removeEventListener('keydown', onKey);
  }, [isOpen, closeModal]);

  return (
    <section
      aria-labelledby="share-heading"
      data-testid="social-share-bar"
      className={clsx('mt-8 flex justify-center', className)}
    >
      <h2 id="share-heading" className="sr-only">
        {L.heading}
      </h2>

      {/* Single primary trigger — YouTube-style. Clicking opens the
          modal containing the preview + every share option. Inline
          backgroundColor with a numeric RGB fallback guarantees the
          brand fill even when --color-primary is missing (same
          documented regression as SynopsisView / IconButton). */}
      <button
        ref={triggerRef}
        type="button"
        onClick={() => setIsOpen(true)}
        aria-haspopup="dialog"
        aria-expanded={isOpen}
        aria-controls="social-share-modal"
        data-testid="social-share-trigger"
        style={{
          backgroundColor: 'rgb(var(--color-primary, 79 70 229))',
          color: 'rgb(255 255 255)',
        }}
        className="inline-flex w-full sm:w-auto min-h-[44px] items-center justify-center gap-2 rounded-xl px-6 py-2.5 text-sm font-semibold shadow-sm transition-opacity hover:opacity-95 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-primary/50"
      >
        <ShareIcon className="h-4 w-4" />
        {L.heading}
      </button>

      {isOpen && (
        <div
          role="dialog"
          aria-modal="true"
          aria-labelledby="share-modal-heading"
          data-testid="social-share-modal"
          id="social-share-modal"
          className="fixed inset-0 z-50 flex items-center justify-center p-4"
        >
          {/* Backdrop */}
          <button
            type="button"
            aria-label="Close share dialog"
            data-testid="social-share-backdrop"
            onClick={closeModal}
            className="absolute inset-0 h-full w-full cursor-default bg-black/50"
          />

          {/* Modal panel */}
          <div className="relative z-10 w-full max-w-md rounded-2xl border border-muted/40 bg-card p-4 sm:p-5 shadow-xl">
            <div className="mb-3 flex items-center justify-between gap-2">
              <h3
                id="share-modal-heading"
                className="flex items-center gap-2 text-base font-semibold text-fg"
              >
                <ShareIcon className="h-4 w-4 text-fg/70" />
                {L.heading}
              </h3>
              <button
                ref={closeBtnRef}
                type="button"
                onClick={closeModal}
                aria-label="Close"
                title="Close"
                data-testid="social-share-close"
                className="rounded-full p-1.5 text-fg/70 hover:bg-bg focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-primary/50"
              >
                <svg
                  className="h-4 w-4"
                  viewBox="0 0 24 24"
                  fill="none"
                  stroke="currentColor"
                  strokeWidth={2}
                  strokeLinecap="round"
                  strokeLinejoin="round"
                  aria-hidden="true"
                  focusable="false"
                >
                  <line x1="18" y1="6" x2="6" y2="18" />
                  <line x1="6" y1="6" x2="18" y2="18" />
                </svg>
              </button>
            </div>

            {/* Preview card */}
            <div
              className="mb-4 flex w-full items-center gap-3 rounded-xl border border-muted/30 bg-bg/60 p-3"
              aria-label={L.preview}
              data-testid="social-share-preview"
            >
              {safeImage ? (
                <img
                  src={safeImage}
                  alt=""
                  loading="lazy"
                  className="h-14 w-14 flex-shrink-0 rounded-lg object-cover"
                />
              ) : (
                <div
                  aria-hidden="true"
                  className="h-14 w-14 flex-shrink-0 rounded-lg bg-gradient-to-br from-primary/20 to-primary/5"
                />
              )}
              <div className="min-w-0 flex-1 text-left">
                <p className="truncate text-sm font-semibold text-fg">{shareTitle}</p>
                {previewSubtitle && (
                  <p className="truncate text-xs text-muted">{previewSubtitle}</p>
                )}
                <p className="mt-1 truncate text-[11px] text-muted/80" title={shareUrl}>
                  {shareUrl}
                </p>
              </div>
            </div>

            {/* Icon row */}
            <ul
              role="list"
              aria-label={L.heading}
              className="flex flex-wrap items-center justify-center gap-2 sm:gap-3"
            >
              {nativeShareSupported && (
                <li>
                  <button
                    type="button"
                    onClick={handleNativeShare}
                    aria-label={L.nativeShare}
                    title={L.nativeShare}
                    data-testid="social-share-native"
                    className="lp-share-btn"
                  >
                    <ShareIcon className="h-5 w-5" />
                  </button>
                </li>
              )}

              <li>
                <button
                  type="button"
                  onClick={handleCopy}
                  aria-label={copyState === 'copied' ? L.copied : L.copyLink}
                  title={copyState === 'copied' ? L.copied : L.copyLink}
                  data-testid="social-share-copy"
                  className={clsx(
                    'lp-share-btn',
                    copyState === 'copied' && 'lp-share-btn--success',
                  )}
                >
                  {copyState === 'copied' ? (
                    <CheckIcon className="h-5 w-5" />
                  ) : (
                    <LinkIcon className="h-5 w-5" />
                  )}
                </button>
              </li>

              {intents.map(({ key, label, Icon, href, hover }) => (
                <li key={key}>
                  <a
                    href={href}
                    target="_blank"
                    rel="noopener noreferrer nofollow"
                    aria-label={L[label]}
                    title={L[label]}
                    data-testid={`social-share-${key}`}
                    className="lp-share-btn"
                    style={{ ['--lp-share-hover' as string]: hover }}
                  >
                    <Icon className="h-5 w-5" />
                  </a>
                </li>
              ))}
            </ul>

            <p
              role="status"
              aria-live="polite"
              className={clsx(
                'mt-3 text-center text-xs',
                copyState === 'copied' && 'text-success',
                copyState === 'error' && 'text-error',
                copyState === 'idle' && 'sr-only',
              )}
            >
              {copyState === 'copied' && L.copied}
              {copyState === 'error' && L.copyFailed}
            </p>
          </div>
        </div>
      )}
    </section>
  );
}

export default SocialShareBar;
