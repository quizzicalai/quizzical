// frontend/vitest.config.ts
import { defineConfig } from 'vitest/config';

export default defineConfig({
  test: {
    server: {
      deps: {
        // react-markdown and remark-gfm are ESM-only; inline them so Vitest's
        // CommonJS transform doesn't break the JSDOM test environment.
        inline: ['react-markdown', 'remark-gfm'],
      },
    },
    include: [
      'tests/**/*.spec.ts?(x)',
      'src/**/*.{spec,test}.ts?(x)',
    ],
    exclude: [
      'tests/e2e/**',
      'tests/ct/**',                 // Playwright CT setup
      'src/**/*.ct.spec.ts?(x)',     // ⬅ exclude CT specs (e.g. App.ct.spec.tsx)
      // or just the single file:
      // 'src/App.ct.spec.tsx',
      'node_modules/**',
      'dist/**',
    ],
    environment: 'jsdom',
    setupFiles: ['tests/vitest.setup.ts'],
    coverage: {
      provider: 'v8',
      reportsDirectory: 'coverage',
      reporter: ['text', 'html', 'lcov'],
      include: ['src/**/*.{ts,tsx}'],
      exclude: [
        'src/**/*.d.ts',
        'src/**/__mocks__/**',
        'src/**/*.stories.*',
        'src/main.tsx',
        'src/index.css',
        'src/**/*.ct.spec.ts?(x)',
        'src/assets/icons/**',
        'src/App.tsx', 
      ],
    },
  },
});
