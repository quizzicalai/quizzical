// frontend/tests/ct/ct.globals.d.ts
export {};

declare global {
  interface Window {
    // existing helpers (already used in your specs)
    __ct_lastStartQuizCall?: { category: string; token: string } | null;
    __ct_resetLastStartQuizCall?: () => void;
    __ct_setNextStartQuizError?: (err: { code?: string; message?: string }) => void;

    // NEW: startQuiz pending barrier
    __ct_setStartQuizPending?: () => void;
    __ct_resolveStartQuizPending?: () => void;

    // NEW: fast narration overrides for CT
    __ct_loadingLines?: Array<{ atMs: number; text: string }>;
    __ct_loadingTickMs?: number;

    // NEW: QuizFlowPage CT store controls
    __ct_lastStartQuizCall?: { category: string; token: string } | null;
    __ct_resetLastStartQuizCall?: () => void;
    __ct_setNextStartQuizError?: (err: { code?: string; message?: string }) => void;

    __ct_setStartQuizPending?: () => void;
    __ct_resolveStartQuizPending?: () => void;

    __ct_loadingLines?: Array<{ atMs: number; text: string }>;
    __ct_loadingTickMs?: number;

    __ct_quiz_set?: (patch: Partial<any>) => void;
    __ct_quiz_get?: () => any;
    __ct_quiz_reset?: () => void;
  }
}
