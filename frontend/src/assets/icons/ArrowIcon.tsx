import React from 'react';

// By extending React.SVGProps, our icon can accept any valid SVG attribute
// (like className, fill, stroke, etc.) without us having to list them all.
type IconProps = React.SVGProps<SVGSVGElement>;

export const ArrowIcon = (props: IconProps) => (
  <svg
    xmlns="http://www.w3.org/2000/svg"
    fill="none"
    viewBox="0 0 24 24"
    strokeWidth={2.5}
    stroke="currentColor"
    aria-hidden="true"
    focusable="false"
    {...props}
  >
    <path
      strokeLinecap="round"
      strokeLinejoin="round"
      d="M14 5l7 7m0 0l-7 7m7-7H3"
    />
  </svg>
);