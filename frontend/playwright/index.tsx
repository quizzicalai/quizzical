// frontend/playwright/index.tsx
// This file can stay minimal — it’s the “facade” script CT needs.
// Add global CSS/providers/MSW hooks here if/when you need them.
import React from 'react';
import '../src/index.css';
import { beforeMount, afterMount } from '@playwright/experimental-ct-react/hooks';

// Optional example if you later want per-test hooks config:
// export type HooksConfig = { enableRouting?: boolean };
// beforeMount<HooksConfig>(async ({ App /*, hooksConfig*/ }) => {
//   return <App />; // You could wrap <App/> with providers/router here based on hooksConfig.
// });

beforeMount(async () => {
  // one-time per-test setup if needed
});

afterMount(async () => {
  // per-test cleanup if needed
});
