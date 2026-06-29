// PROTOTYPE-ONLY — Q&A image-enrichment (branch: prototype/qa-image-enrichment).
// Renders a precomputed, brand-recolored icon for a question/answer.
//
// Design properties (matching IMAGE-ENRICHMENT-PLAN.md §6 + the skeptical review):
//  * Routing is PRECOMPUTED upstream (router.py). This component only renders
//    the already-resolved iconId — ZERO embedding/NN/network at render time.
//  * Inline SVG sprite => ZERO extra HTTP requests (stronger than the <img>
//    path) and the icon bytes ship with the JS bundle, already cached.
//  * FIXED-SIZE with reserved space => ZERO layout shift (CLS-safe). The slot
//    occupies its box whether or not an icon is bound.
//  * Decorative => aria-hidden + no alt text (a11y: meaningful content stays
//    in the question/answer TEXT; the icon never carries information).
//  * Fail-open to NOTHING (renders an empty reserved box) — never a broken
//    image, never a cross-origin placeholder (avoids the Image.tsx footgun
//    the review called out).
//
// Entirely gated behind VITE_PROTO_QA_ICONS — when off, renders nothing.

import React from 'react';
import { QA_ICON_SVG } from './qaIconSprite';

export const QA_ICONS_ENABLED =
  String(import.meta.env.VITE_PROTO_QA_ICONS ?? '') === '1' ||
  String(import.meta.env.VITE_PROTO_QA_ICONS ?? '').toLowerCase() === 'true';

type QaIconProps = {
  iconId?: string | null;
  /** rendered px size; the reserved box is sizePx + padding */
  sizePx?: number;
  className?: string;
  /** when true, always reserve the box even with no icon (prevents CLS) */
  reserve?: boolean;
};

/**
 * A tiny brand icon badge. The outer span is a fixed-size box so layout never
 * shifts; the inner SVG is injected via dangerouslySetInnerHTML from the
 * build-time sprite (the SVG is our own recolored asset, not user content).
 */
export function QaIcon({ iconId, sizePx = 28, className, reserve = true }: QaIconProps) {
  if (!QA_ICONS_ENABLED) return null;

  const svg = iconId ? QA_ICON_SVG[iconId] : undefined;
  if (!svg && !reserve) return null;

  const box = sizePx + 8; // 4px padding each side
  return (
    <span
      aria-hidden="true"
      data-proto-qa-icon={iconId || 'none'}
      className={className}
      style={{
        display: 'inline-flex',
        alignItems: 'center',
        justifyContent: 'center',
        width: box,
        height: box,
        flex: `0 0 ${box}px`,
        lineHeight: 0,
      }}
    >
      {svg ? (
        <span
          style={{ width: sizePx, height: sizePx, display: 'inline-block', lineHeight: 0 }}
          dangerouslySetInnerHTML={{ __html: sizeSvg(svg, sizePx) }}
        />
      ) : null}
    </span>
  );
}

/** Force the inline SVG to the target px size (sprite SVGs ship at 24px). */
function sizeSvg(svg: string, px: number): string {
  return svg
    .replace(/width="\d+"/, `width="${px}"`)
    .replace(/height="\d+"/, `height="${px}"`);
}
