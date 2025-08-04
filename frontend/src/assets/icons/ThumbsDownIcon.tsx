import React from 'react';

type IconProps = React.SVGProps<SVGSVGElement>;

export const ThumbsDownIcon = (props: IconProps) => (
  <svg 
    xmlns="http://www.w3.org/2000/svg" 
    viewBox="0 0 20 20" 
    fill="currentColor" 
    aria-hidden="true"
    focusable="false"
    {...props}
  >
    <path d="M18 9.5a1.5 1.5 0 11-3 0v-6a1.5 1.5 0 013 0v6zM14 9.667V3a1 1 0 00-1-1H6.242a1 1 0 00-.97 1.22l1.938 5.546A1.5 1.5 0 008.634 9.8H9.5a1 1 0 001-1V7.3a1 1 0 00-1-1H8.242a1 1 0 01-.97-1.22l1.938-5.546A.5.5 0 019.742 3H13a.5.5 0 01.5.5v6.167a1.5 1.5 0 001.5 1.5h2.5" />
  </svg>
);