// src/assets/icons/ShuffleIcon.tsx
import React from 'react';

export type IconProps = React.SVGProps<SVGSVGElement>;

/**
 * Shuffle / re-roll glyph. Two arrows that cross over a pair of horizontal
 * rails — the standard "shuffle" affordance familiar from media players.
 *
 * Stroke / sizing match the rest of `src/assets/icons/*` (currentColor +
 * non-scaling-stroke) so the icon picks up the parent button's text color
 * and stays visually consistent with ArrowIcon, SendIcon, etc.
 */
export const ShuffleIcon: React.FC<IconProps> = (props) => (
  <svg
    xmlns="http://www.w3.org/2000/svg"
    viewBox="0 0 24 24"
    fill="none"
    stroke="currentColor"
    strokeWidth={2}
    strokeLinecap="round"
    strokeLinejoin="round"
    vectorEffect="non-scaling-stroke"
    aria-hidden={props['aria-hidden'] ?? true}
    focusable="false"
    {...props}
  >
    {/* Top rail: enters left, crosses to bottom-right; arrowhead at right end. */}
    <path d="M3 7h4l10 10h4" />
    <polyline points="17 21 21 17 17 13" />
    {/* Bottom rail: enters left, crosses to top-right; arrowhead at right end. */}
    <path d="M3 17h4l4-4" />
    <path d="M13 11l4-4h4" />
    <polyline points="17 11 21 7 17 3" />
  </svg>
);
