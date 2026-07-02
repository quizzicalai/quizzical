import React, { useEffect, useId, useRef, useState } from 'react';

/**
 * A small, accessible, CLICK-to-reveal info tooltip (not hover) — a round "i"
 * button that toggles a short popover. Closes on Escape, on outside-click, and
 * on a second click. Used e.g. on the landing subtitle to explain that quafel
 * generates all content with AI.
 */
export function InfoTip({
  label,
  children,
  className,
}: {
  /** Accessible name for the trigger (screen readers) — e.g. "How quafel makes quizzes". */
  label: string;
  /** Popover body. */
  children: React.ReactNode;
  className?: string;
}) {
  const [open, setOpen] = useState(false);
  const wrapRef = useRef<HTMLSpanElement>(null);
  const panelId = useId();

  useEffect(() => {
    if (!open) return;
    const onDoc = (e: MouseEvent) => {
      if (wrapRef.current && !wrapRef.current.contains(e.target as Node)) setOpen(false);
    };
    const onKey = (e: KeyboardEvent) => {
      if (e.key === 'Escape') setOpen(false);
    };
    document.addEventListener('mousedown', onDoc);
    document.addEventListener('keydown', onKey);
    return () => {
      document.removeEventListener('mousedown', onDoc);
      document.removeEventListener('keydown', onKey);
    };
  }, [open]);

  return (
    <span ref={wrapRef} className={'relative inline-flex items-center align-middle ' + (className ?? '')}>
      <button
        type="button"
        aria-label={label}
        aria-expanded={open}
        aria-controls={panelId}
        data-testid="info-tip-trigger"
        onClick={() => setOpen((o) => !o)}
        className="inline-flex h-6 w-6 items-center justify-center rounded-full border border-border text-[rgb(var(--color-text-secondary,71_85_105))] transition-[transform,background-color,color] duration-fast ease-out-token hover:bg-card hover:text-fg active:scale-95 focus:outline-none focus-visible:ring-2 focus-visible:ring-primary/50"
      >
        <svg viewBox="0 0 20 20" fill="none" aria-hidden="true" className="h-3.5 w-3.5">
          <circle cx="10" cy="10" r="8.25" stroke="currentColor" strokeWidth="1.5" />
          <circle cx="10" cy="6.2" r="1" fill="currentColor" />
          <path d="M10 9.2v5" stroke="currentColor" strokeWidth="1.6" strokeLinecap="round" />
        </svg>
      </button>
      {open && (
        <span
          id={panelId}
          role="tooltip"
          data-testid="info-tip-panel"
          className="absolute left-1/2 top-full z-20 mt-2 w-64 -translate-x-1/2 rounded-lg border border-border bg-card p-3 text-left text-xs font-normal not-italic leading-relaxed text-[rgb(var(--color-text-secondary,71_85_105))] shadow-md"
        >
          {children}
        </span>
      )}
    </span>
  );
}
