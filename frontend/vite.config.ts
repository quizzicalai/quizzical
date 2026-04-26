// frontend/vite.config.ts
//
// Build-only Vite config (no dev/HMR overrides needed for this repo).
//
// Why this file exists:
// - Splits long-tail vendor code into stable chunks so that one app code change
//   doesn't bust the whole vendor cache for returning users.
// - Caps per-chunk warning size so that large bundles surface in CI logs.
//
// The `manualChunks` strategy is intentionally conservative: only the largest,
// most-stable third-party libraries get their own chunks. Everything else stays
// in the default vendor split that Vite computes.
import { defineConfig } from 'vite';
import react from '@vitejs/plugin-react';

export default defineConfig({
  plugins: [react()],
  build: {
    sourcemap: false,
    chunkSizeWarningLimit: 500, // KB; warn (not fail) above this per chunk
    rollupOptions: {
      output: {
        manualChunks(id: string) {
          if (!id.includes('node_modules')) return;
          if (id.includes('/react/') || id.includes('/react-dom/') || id.includes('/scheduler/')) {
            return 'react-vendor';
          }
          if (id.includes('/react-router') || id.includes('/@remix-run/')) {
            return 'router-vendor';
          }
          if (id.includes('/zod/') || id.includes('/zustand/')) {
            return 'state-vendor';
          }
        },
      },
    },
  },
});
