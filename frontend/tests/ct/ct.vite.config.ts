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
        // --- Place SPECIFIC mocks FIRST and cover ../, @/, and /src/ forms ---

        // ConfigContext mock
        { find: /^\.\.\/context\/ConfigContext$/,      replacement: r('./mocks/ConfigContext.mock.ts') },
        { find: /^@\/context\/ConfigContext$/,         replacement: r('./mocks/ConfigContext.mock.ts') },
        { find: /^\/src\/context\/ConfigContext$/,     replacement: r('./mocks/ConfigContext.mock.ts') },

        // quizStore mock
        { find: /^\.\.\/store\/quizStore$/,            replacement: r('./mocks/quizStore.mock.ts') },
        { find: /^@\/store\/quizStore$/,               replacement: r('./mocks/quizStore.mock.ts') },
        { find: /^\/src\/store\/quizStore$/,           replacement: r('./mocks/quizStore.mock.ts') },

        // Turnstile mock
        { find: /^\.\.\/components\/common\/Turnstile$/,  replacement: r('./mocks/Turnstile.mock.tsx') },
        { find: /^@\/components\/common\/Turnstile$/,     replacement: r('./mocks/Turnstile.mock.tsx') },
        { find: /^\/src\/components\/common\/Turnstile$/, replacement: r('./mocks/Turnstile.mock.tsx') },

        // --- THEN the generic "@/..." path resolver ---
        { find: /^@\//,                                replacement: r('../../src/') },
      ],
    },
  };
}

export default makeCtViteConfig;
