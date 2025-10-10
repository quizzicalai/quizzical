// frontend/tests/ct/ct.vite.config.ts
import react from '@vitejs/plugin-react';
import path from 'path';
import { fileURLToPath } from 'url';

const __filename = fileURLToPath(import.meta.url);
const __dirname  = path.dirname(__filename);
const r = (p: string) => path.resolve(__dirname, p);

export function makeCtViteConfig(): any {
  return {
    plugins: [react()],
    resolve: {
      alias: [
        // General app alias so "@/..." works in CT
        { find: /^@\//, replacement: r('../../src/') },

        // ✅ Match the FULL specifier for relative imports from app files
        { find: /^\.\.\/context\/ConfigContext$/, replacement: r('./mocks/ConfigContext.mock.ts') },
        { find: /^\.\.\/store\/quizStore$/,       replacement: r('./mocks/quizStore.mock.ts') },
        { find: /^\.\.\/components\/common\/Turnstile$/, replacement: r('./mocks/Turnstile.mock.tsx') },

        // ✅ Also match the FULL "@/..." variants (if any app files use them)
        { find: /^@\/context\/ConfigContext$/, replacement: r('./mocks/ConfigContext.mock.ts') },
        { find: /^@\/store\/quizStore$/,       replacement: r('./mocks/quizStore.mock.ts') },
        { find: /^@\/components\/common\/Turnstile$/, replacement: r('./mocks/Turnstile.mock.tsx') },
      ],
    },
  };
}

export default makeCtViteConfig;
