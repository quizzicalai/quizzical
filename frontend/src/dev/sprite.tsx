// frontend/src/dev/sprite.tsx

import React from 'react';
import { createRoot } from 'react-dom/client';

// Pull in your global styles so Tailwind + keyframes are available
import '../index.css';

import { SpritePlayground } from './SpritePlayground';

// Flag so you can branch if you ever need to (optional)
;(window as any).__SPRITE_DEV__ = true;

const root = createRoot(document.getElementById('root')!);
root.render(<SpritePlayground />);
