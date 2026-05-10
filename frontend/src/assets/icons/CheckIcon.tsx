import React from 'react';

type IconProps = React.SVGProps<SVGSVGElement>;

/** Generic check mark used as success affordance after copy. */
export const CheckIcon = (props: IconProps) => (
  <svg
    xmlns="http://www.w3.org/2000/svg"
    viewBox="0 0 24 24"
    fill="none"
    stroke="currentColor"
    strokeWidth={2.5}
    strokeLinecap="round"
    strokeLinejoin="round"
    aria-hidden="true"
    focusable="false"
    {...props}
  >
    <path d="M5 12.5 9.5 17 19 7" />
  </svg>
);
