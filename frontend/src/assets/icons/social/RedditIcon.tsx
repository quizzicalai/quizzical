import React from 'react';

type IconProps = React.SVGProps<SVGSVGElement>;

/** Reddit alien glyph, filled, single-color. */
export const RedditIcon = (props: IconProps) => (
  <svg
    xmlns="http://www.w3.org/2000/svg"
    viewBox="0 0 24 24"
    fill="currentColor"
    aria-hidden="true"
    focusable="false"
    {...props}
  >
    <path d="M22 12.14a2.14 2.14 0 0 0-3.62-1.55 10.45 10.45 0 0 0-5.7-1.81l.97-4.57 3.18.68a1.53 1.53 0 1 0 .15-.92l-3.55-.75a.46.46 0 0 0-.55.36l-1.08 5.1a10.45 10.45 0 0 0-5.78 1.8 2.14 2.14 0 1 0-2.36 3.5 4.27 4.27 0 0 0-.05.66c0 3.36 3.91 6.09 8.74 6.09s8.74-2.73 8.74-6.09a4.3 4.3 0 0 0-.05-.65A2.13 2.13 0 0 0 22 12.14ZM6.71 13.66a1.53 1.53 0 1 1 1.53 1.53 1.53 1.53 0 0 1-1.53-1.53Zm8.6 4.05a4.85 4.85 0 0 1-3.31.97 4.85 4.85 0 0 1-3.31-.97.41.41 0 0 1 .58-.58 4.13 4.13 0 0 0 2.73.74 4.13 4.13 0 0 0 2.73-.74.41.41 0 0 1 .58.58Zm-.27-2.52a1.53 1.53 0 1 1 1.53-1.53 1.53 1.53 0 0 1-1.53 1.53Z" />
  </svg>
);
