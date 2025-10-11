// frontend/src/types/turnstile.d.ts

declare global {
  interface Window {
    turnstile: {
      render: (element: HTMLElement | string, options: TurnstileOptions) => string;
      reset: (widgetId: string) => void;
      remove: (widgetId: string) => void;
      getResponse: (widgetId: string) => string | undefined;
      execute: (widgetId: string) => void; // <-- used for invisible widgets
    };

    // Helper you attach in Turnstile component
    resetTurnstile?: () => void;
  }
}

export interface TurnstileOptions {
  sitekey: string;
  callback?: (token: string) => void;
  'error-callback'?: (errorCode?: string) => void;
  'expired-callback'?: () => void;
  theme?: 'light' | 'dark' | 'auto';
  size?: 'normal' | 'compact' | 'invisible'; // <-- support invisible
}

export interface TurnstileProps {
  onVerify: (token: string) => void;
  onError?: () => void;
  onExpire?: () => void;
  theme?: 'light' | 'dark' | 'auto';
  size?: 'normal' | 'compact' | 'invisible'; // <-- support invisible
  /** When size="invisible", auto-executes after render to fetch a token. */
  autoExecute?: boolean;
}

export {};
