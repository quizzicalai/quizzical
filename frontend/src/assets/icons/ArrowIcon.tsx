// src/assets/icons/ArrowIcon.tsx
import React from 'react';

export type IconProps = React.SVGProps<SVGSVGElement>;

export const ArrowIcon: React.FC<IconProps> = (props) => (
  <svg
    xmlns="http://www.w3.org/2000/svg"
    viewBox="0 0 24 24"
    fill="none"
    stroke="currentColor"
    strokeWidth={2.5}            // slightly less thick than before
    strokeLinecap="round"
    strokeLinejoin="round"
    vectorEffect="non-scaling-stroke"
    aria-hidden={props['aria-hidden'] ?? true}
    focusable="false"
    {...props}                   // width/height are provided by IconButton
  >
    <line x1="5" y1="12" x2="19" y2="12" />
    <polyline points="12 5 19 12 12 19" />
  </svg>
);