/**
 * FE-E2E-RESULT-SHARE: result page social-share bar + profile-quality contract.
 *
 * Walks the full happy path against `installQuizMocks` and then validates the
 * result-page share affordance against industry-standard expectations:
 *   1. The SocialShareBar mounts and shows a preview card with the result title.
 *   2. All six social-intent anchors (X, Facebook, LinkedIn, WhatsApp, Reddit,
 *      Email) render with `target="_blank"` + `rel` set to `noopener noreferrer`
 *      and contain the canonical share URL in the encoded href.
 *   3. The "Copy link" button writes the canonical share URL to the OS clipboard
 *      (verified via `navigator.clipboard.readText()` after granting clipboard
 *      permissions to the browser context).
 *   4. The rendered profile description on the page contains at least
 *      `MIN_FINAL_PARAGRAPHS = 3` blank-line-separated paragraphs (the same
 *      quality floor enforced by the backend tool).
 *
 * Acceptance criteria covered:
 *   - AC-FE-SHARE-1..4 (FE-side share UX).
 *   - AC-QUALITY-FINALPROFILE-2 (cross-cuts the BE quality gate).
 */

import { test, expect } from './utils/har.fixture';

import { installConfigFixtureE2E } from './fixtures/config';
import { installQuizMocks } from './fixtures/quiz';
import { stubTurnstile } from './utils/turnstile';

const MIN_FINAL_PARAGRAPHS = 3;
const SOCIAL_TESTIDS = [
  'social-share-x',
  'social-share-facebook',
  'social-share-linkedin',
  'social-share-whatsapp',
  'social-share-reddit',
  'social-share-email',
] as const;

test.describe('FE-E2E-RESULT-SHARE: final page share bar + profile quality', () => {
  test('renders share bar, exposes 6 brand intents, copies canonical URL, and shows ≥3-paragraph profile', async ({
    page,
    context,
    browserName,
  }) => {
    // `clipboard-read` / `clipboard-write` are Chromium-only permission
    // strings. Firefox + WebKit either don't model the permission at all
    // (the API just works) or use a different permission model. Granting
    // them on those browsers throws "Unknown permission".
    if (browserName === 'chromium') {
      await context.grantPermissions(['clipboard-read', 'clipboard-write']);
    }

    await stubTurnstile(page);
    await installConfigFixtureE2E(page);
    await installQuizMocks(page);

    // ---- Walk landing → quiz → result ----
    await page.goto('/');
    await expect(page.getByTestId('lp-question-frame')).toBeVisible({
      timeout: 20_000,
    });
    await page.waitForTimeout(300);

    await page.getByRole('textbox').first().fill('Ancient Rome');
    await page
      .getByRole('button', { name: /create my quiz/i })
      .first()
      .click();

    await expect(
      page.getByText(/The World of Ancient Rome|A short synopsis/i).first(),
    ).toBeVisible({ timeout: 20_000 });

    await page
      .getByRole('button', { name: /begin|start.*quiz|continue|proceed/i })
      .first()
      .click();

    await expect(
      page.getByText(/Which achievement is most impressive/i),
    ).toBeVisible({ timeout: 20_000 });

    await page
      .getByRole('button', { name: /Aqueducts/i })
      .first()
      .click();

    // ---- Result page lands ----
    await expect(page.getByText(/The Architect/i).first()).toBeVisible({
      timeout: 20_000,
    });

    // ---- 1. Share bar mounts with a preview ----
    const shareBar = page.getByTestId('social-share-bar');
    await expect(shareBar).toBeVisible();
    await expect(page.getByTestId('social-share-preview')).toBeVisible();

    // The preview should reference the canonical share path (/result/<id>).
    // We don't pin the id — we just require the URL chip / preview text to
    // contain the expected path segment.
    const previewText = (await shareBar.innerText()).toLowerCase();
    expect(previewText).toMatch(/architect/);
    expect(previewText).toMatch(/\/result\//);

    // ---- 2. Six brand-intent anchors with safe rel + share URL in href ----
    for (const tid of SOCIAL_TESTIDS) {
      const link = page.getByTestId(tid);
      await expect(link).toBeVisible();
      await expect(link).toHaveAttribute('target', '_blank');
      const rel = (await link.getAttribute('rel')) ?? '';
      expect(rel).toContain('noopener');
      expect(rel).toContain('noreferrer');
      const href = (await link.getAttribute('href')) ?? '';
      // mailto: encodes the URL inside the body; every other intent
      // includes the share URL directly. In all 6 cases the encoded
      // `/result/` segment must be present.
      expect(href.toLowerCase()).toContain('%2fresult%2f');
    }

    // ---- 3. Copy-link writes the canonical URL to the clipboard ----
    const copyBtn = page.getByTestId('social-share-copy');
    await expect(copyBtn).toBeVisible();
    await copyBtn.click();

    // The copy button is icon-only — its accessible name swaps from
    // "Copy link" → "Link copied" once the clipboard write succeeds.
    await expect
      .poll(async () => (await copyBtn.getAttribute('aria-label')) ?? '', {
        timeout: 3_000,
      })
      .toMatch(/copied/i);

    const clipboardContents = await page
      .evaluate(async () => {
        try {
          return await navigator.clipboard.readText();
        } catch {
          return null;
        }
      });
    // WebKit (and Firefox in some configurations) refuse programmatic
    // clipboard reads even after the write succeeded; in that case we
    // assert the FE-visible success state and trust the write path.
    if (clipboardContents !== null) {
      expect(clipboardContents).toMatch(/^https?:\/\//);
      expect(clipboardContents).toContain('/result/');
    }

    // ---- 4. Profile description has ≥3 blank-line-separated paragraphs ----
    // The rendered description lives in ResultProfile; we count distinct
    // <p> elements within the result body, falling back to a regex split
    // on the page's text if the markup uses a single block.
    const paragraphCount = await page.evaluate(() => {
      // Prefer a stable testid if the component exposes one; otherwise
      // fall back to scanning every <p> under <main>.
      const root =
        document.querySelector('[data-testid="result-profile-description"]') ??
        document.querySelector('main') ??
        document.body;
      const ps = root.querySelectorAll('p');
      if (ps.length > 0) return ps.length;
      const text = (root.textContent ?? '').trim();
      return text.split(/\n\s*\n+/).filter((b) => b.trim().length > 0).length;
    });
    expect(paragraphCount).toBeGreaterThanOrEqual(MIN_FINAL_PARAGRAPHS);
  });

  test('feedback widget: choose rating → comment → submit → success card', async ({
    page,
  }) => {

    await stubTurnstile(page);
    await installConfigFixtureE2E(page);
    await installQuizMocks(page);

    // Register the feedback observer AFTER installQuizMocks so this
    // handler wins (Playwright matches routes in reverse registration
    // order — last registered handler runs first).
    const feedbackRequests: Array<Record<string, unknown>> = [];
    await page.route('**/api/v1/feedback', async (route, request) => {
      try {
        const body = request.postDataJSON();
        if (body && typeof body === 'object') {
          feedbackRequests.push(body as Record<string, unknown>);
        }
      } catch {
        /* non-JSON body — ignore */
      }
      await route.fulfill({
        status: 200,
        contentType: 'application/json',
        body: JSON.stringify({ ok: true }),
      });
    });

    // Walk landing → quiz → result (same as the other test).
    await page.goto('/');
    await expect(page.getByTestId('lp-question-frame')).toBeVisible({
      timeout: 20_000,
    });
    await page.waitForTimeout(300);
    await page.getByRole('textbox').first().fill('Ancient Rome');
    await page
      .getByRole('button', { name: /create my quiz/i })
      .first()
      .click();
    await expect(
      page.getByText(/The World of Ancient Rome|A short synopsis/i).first(),
    ).toBeVisible({ timeout: 20_000 });
    await page
      .getByRole('button', { name: /begin|start.*quiz|continue|proceed/i })
      .first()
      .click();
    await expect(
      page.getByText(/Which achievement is most impressive/i),
    ).toBeVisible({ timeout: 20_000 });
    await page
      .getByRole('button', { name: /Aqueducts/i })
      .first()
      .click();
    await expect(page.getByText(/The Architect/i).first()).toBeVisible({
      timeout: 20_000,
    });

    // Feedback widget mounts in the idle state.
    const widget = page.getByTestId('feedback-icons');
    await expect(widget).toBeVisible();
    await expect(widget).toHaveAttribute('data-state', 'idle');

    // Choose thumbs up → state transitions to 'rating-chosen' and the
    // comment textarea + submit button appear.
    await page.getByTestId('feedback-up').click();
    await expect(widget).toHaveAttribute('data-state', 'rating-chosen');

    const textarea = widget.getByRole('textbox');
    await expect(textarea).toBeVisible();
    await textarea.fill('Loved the depth of the reading.');

    // Turnstile auto-verifies via the test stub; submit becomes enabled.
    const submit = page.getByTestId('feedback-submit');
    await expect(submit).toBeEnabled({ timeout: 5_000 });
    await submit.click();

    // Success card replaces the form with the green checkmark animation.
    await expect(widget).toHaveAttribute('data-state', 'submitted', {
      timeout: 5_000,
    });
    await expect(widget).toContainText(/thank you|appreciate/i);

    // Wire payload assertion — the FE sent a structured body to the BE.
    // The wire shape (defined in apiService.submitFeedback) is:
    //   { quiz_id, rating, text, 'cf-turnstile-response' }
    expect(feedbackRequests.length).toBeGreaterThanOrEqual(1);
    const payload = feedbackRequests[0];
    expect(payload).toMatchObject({ rating: 'up' });
    expect(typeof payload.quiz_id).toBe('string');
    expect(typeof payload.text).toBe('string');
    expect((payload.text as string).toLowerCase()).toContain('depth');
    expect(typeof payload['cf-turnstile-response']).toBe('string');
  });
});
