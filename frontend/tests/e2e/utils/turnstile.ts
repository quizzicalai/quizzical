// frontend/tests/e2e/utils/turnstile.ts
import type { Page } from '@playwright/test'

/**
 * Injects a minimal turnstile stub that immediately invokes the "callback"
 * with a fake token, so the app can proceed in tests.
 */
export async function stubTurnstile(page: Page, token = 'e2e-fake-turnstile-token') {
  await page.addInitScript(({ token: t }) => {
    // basic global stub Playwright will inject before any page script runs
    ;(window as any).turnstile = {
      render: (_el: any, opts: any) => {
        // Immediately call the callback as if the challenge passed
        if (opts && typeof opts.callback === 'function') {
          setTimeout(() => opts.callback(t), 0)
        }
        return 'turnstile-widget-id'
      },
      reset: () => {},
      remove: () => {},
    }
  }, { token })
}
