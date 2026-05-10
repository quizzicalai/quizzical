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
import { defineConfig, type Plugin } from 'vite';
import react from '@vitejs/plugin-react';

/**
 * UX audit H11 / M21: turn the relative `/og-image.png` and `/__SELF__/`
 * placeholder into absolute URLs at build time so social-card crawlers
 * (Twitter / LinkedIn / Facebook / Slack) can fetch them. The site origin
 * is read from `VITE_PUBLIC_URL`; if unset, the relative paths are left
 * intact (current behaviour, fine for dev).
 */
function htmlAbsoluteUrls(): Plugin {
  return {
    name: 'quizzical-html-absolute-urls',
    transformIndexHtml(html) {
      const origin = (process.env.VITE_PUBLIC_URL || '').replace(/\/$/, '');
      if (!origin) return html;
      return html
        .replace(
          /<meta property="og:image" content="\/og-image\.png" \/>/,
          `<meta property="og:image" content="${origin}/og-image.png" />`,
        )
        .replace(
          /<\/head>/,
          `    <link rel="canonical" href="${origin}/" />\n  </head>`,
        );
    },
  };
}

export default defineConfig({
  plugins: [react(), htmlAbsoluteUrls()],
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
