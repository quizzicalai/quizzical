// frontend/tests/e2e/utils/turnstile.ts
import type { Page } from '@playwright/test';

export async function stubTurnstile(page: Page, token = 'e2e-fake-turnstile-token') {
  await page.addInitScript(({ token: t }) => {
    (window as any).turnstile = {
      render: (_el: any, opts: any) => {
        if (opts && typeof opts.callback === 'function') {
          setTimeout(() => opts.callback(t), 0);
        }
        return 'turnstile-widget-id';
      },
      reset: () => {},
      remove: () => {},
      ready: (cb: () => void) => setTimeout(cb, 0),
      execute: (_el?: any, _opts?: any) => Promise.resolve(t),
    };

    // FE landing page calls this after failures
    (window as any).resetTurnstile = () => {
      (window as any).turnstile?.reset?.();   // no try/catch needed here
    };
  }, { token });

  await page.route('**/*turnstile*/v0/**', r =>
    r.fulfill({ status: 204, contentType: 'application/javascript', body: '' })
  );
}
