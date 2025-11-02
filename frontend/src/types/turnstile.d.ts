// frontend/src/types/turnstile.d.ts
export {};

declare global {
  /**
   * Cloudflare Turnstile global loaded by their script.
   * It only exists after the script has loaded.
   */
  interface CloudflareTurnstile {
    render: (element: HTMLElement | string, options: TurnstileOptions) => string;
    reset: (widgetId: string) => void;
    remove: (widgetId: string) => void;
    /** Optional; not always present in every build/integration. */
    getResponse?: (widgetId: string) => string | undefined;
    /** For invisible widgets: programmatically fetch a token. */
    execute: (widgetId: string) => void;
  }

  interface Window {
    /** Optional because the script attaches it at runtime. */
    turnstile?: CloudflareTurnstile;

    /** Optional helper your component can attach for convenience. */
    resetTurnstile?: () => void;
  }
}

export interface TurnstileOptions {
  sitekey: string;
  callback?: (token: string) => void;
  'error-callback'?: (errorCode?: string) => void;
  'expired-callback'?: () => void;
  theme?: 'light' | 'dark' | 'auto';
  size?: 'normal' | 'compact' | 'invisible';
}

export interface TurnstileProps {
  onVerify: (token: string) => void;
  onError?: () => void;
  onExpire?: () => void;
  theme?: 'light' | 'dark' | 'auto';
  size?: 'normal' | 'compact' | 'invisible';
  /** When size="invisible", auto-executes after render to fetch a token. */
  autoExecute?: boolean;
}
