// frontend/src/assets/icons/ShareIcon.tsx
import React from 'react';

type IconProps = React.SVGProps<SVGSVGElement>;

export const ShareIcon = (props: IconProps) => (
  <svg
    xmlns="http://www.w3.org/2000/svg"
    fill="none"
    viewBox="0 0 100 100"
    stroke="currentColor"
    aria-hidden="true"
    focusable="false"
    {...props}
  >
    {/* Rotate CCW 90° around (50, 50) — the center of the viewBox */}
    <g transform="rotate(-90 50 50)">
      <g transform="translate(-1222.248 -664)">
        <path d="M1255.5,731.8a15.685,15.685,0,1,1-13.5-13.5A15.705,15.705,0,0,1,1255.5,731.8Z" fill="none" stroke="currentColor" strokeMiterlimit={10} strokeWidth={8}/>
        <path d="M1292.4,687.1a12.38,12.38,0,0,1-13.9,13.9,12.517,12.517,0,0,1-10.7-10.7,12.4,12.4,0,1,1,24.6-3.2Z" fill="none" stroke="currentColor" strokeLinecap="round" strokeMiterlimit={10} strokeWidth={8}/>
        <path d="M1313.7,732a11.163,11.163,0,1,1-9.1-9.1A10.93,10.93,0,0,1,1313.7,732Z" fill="none" stroke="currentColor" strokeLinecap="round" strokeMiterlimit={10} strokeWidth={8}/>
        <line y1="26.6" x2="17.9" transform="translate(1250.6 694.5)" stroke="currentColor" strokeLinecap="round" strokeMiterlimit={10} strokeWidth={8}/>
        <line x1="10.3" y1="23.5" transform="translate(1287.4 699.2)" stroke="currentColor" strokeLinecap="round" strokeMiterlimit={10} strokeWidth={8}/>
        <path d="M1254.8,686.5s-3.3-20.5,16.7-20.5" fill="none" stroke="currentColor" strokeLinecap="round" strokeMiterlimit={10} strokeWidth={8}/>
      </g>
    </g>
  </svg>
);
