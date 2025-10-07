// frontend/tests/e2e/fixtures/config.ts
/* eslint no-console: ["error", { "allow": ["debug", "warn", "error"] }] */
import type { Page } from '@playwright/test';

export async function installConfigFixtureE2E(page: Page) {
  await page.route('**/api/v1/config', async route => {
    const url = route.request().url();
    console.debug('[MOCK:/config] match ->', url);

    const body = {
      theme: {
        colors: {
          primary: '79 70 229',
          secondary: '30 41 59',
          accent: '234 179 8',
          bg: '248 250 252',
          fg: '15 23 42',
          border: '226 232 240',
          muted: '100 116 139',
          ring: '129 140 248',
        },
        fonts: { sans: 'Inter, sans-serif', serif: 'serif' },
        dark: {
          colors: {
            primary: '129 140 248',
            secondary: '226 232 240',
            accent: '250 204 21',
            bg: '15 23 42',
            fg: '248 250 252',
            border: '30 41 59',
            muted: '148 163 184',
            ring: '129 140 248',
          },
        },
      },
      content: {
        appName: 'Quizzical',
        landingPage: {}, // backend sends minimal; FE normalizer fills defaults
        footer: {
          about:   { label: 'About',   href: '/about'   },
          terms:   { label: 'Terms',   href: '/terms'   },
          privacy: { label: 'Privacy', href: '/privacy' },
          donate:  { label: 'Donate',  href: '#'        },
        },
        aboutPage:         { title: 'About',   blocks: [] },
        termsPage:         { title: 'Terms',   blocks: [] },
        privacyPolicyPage: { title: 'Privacy', blocks: [] },
        errors: {
          title: 'Error',
          retry: 'Retry',
          home: 'Home',
          startOver: 'Start Over',
        },
      },
      limits:      { validation: { category_min_length: 3, category_max_length: 100 } },
      apiTimeouts: { default: 15000, startQuiz: 60000, poll: { total: 60000, interval: 1000, maxInterval: 5000 } },
      features:    { turnstileEnabled: false, turnstileSiteKey: '' },
    };

    try {
      await route.fulfill({
        status: 200,
        headers: { 'content-type': 'application/json' },
        body: JSON.stringify(body),
      });
      console.debug('[MOCK:/config] fulfilled 200 with normalized body');
    } catch (e) {
      console.error('[MOCK:/config] fulfill error', e);
      throw e;
    }
  });
}
