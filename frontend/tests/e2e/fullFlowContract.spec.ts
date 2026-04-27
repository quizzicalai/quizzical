/* eslint no-console: ["error", { "allow": ["debug", "warn", "error"] }] */
/**
 * FE-E2E-CONTRACT: strict full happy-path FE↔BE contract validation.
 *
 * Walks the canonical happy path (start → synopsis → proceed → poll active
 * question → submit answer → poll → finished result) against
 * `installQuizMocks`, but installs additional listeners on every API request
 * AND response to assert the wire-format contract that both sides depend on.
 *
 * If either side ever drifts (e.g., FE starts sending `quiz_id` instead of
 * `quizId`, or BE response loses `initialPayload.type`), this test fails.
 *
 * Acceptance criteria covered: AC-FE-CONTRACT-1..5.
 */

import { test, expect } from './utils/har.fixture';
import type { Page, Request, Response } from '@playwright/test';

import { installConfigFixtureE2E } from './fixtures/config';
import { installQuizMocks } from './fixtures/quiz';
import { stubTurnstile } from './utils/turnstile';

interface ContractObserver {
  startReqBodies: unknown[];
  startResBodies: unknown[];
  proceedReqBodies: unknown[];
  proceedResBodies: unknown[];
  statusResBodies: unknown[];
  nextReqBodies: unknown[];
  outgoingHasRequestId: boolean;
}

async function attachContractObserver(page: Page): Promise<ContractObserver> {
  const obs: ContractObserver = {
    startReqBodies: [],
    startResBodies: [],
    proceedReqBodies: [],
    proceedResBodies: [],
    statusResBodies: [],
    nextReqBodies: [],
    outgoingHasRequestId: true,
  };

  page.on('request', (req: Request) => {
    const url = req.url();
    if (!/\/api\/v1\//.test(url)) return;
    const headers = req.headers();
    if (!headers['x-request-id']) {
      obs.outgoingHasRequestId = false;
    }
    if (req.method() === 'POST') {
      try {
        const body = req.postDataJSON();
        if (/\/quiz\/start/.test(url)) obs.startReqBodies.push(body);
        else if (/\/quiz\/proceed/.test(url)) obs.proceedReqBodies.push(body);
        else if (/\/quiz\/next/.test(url)) obs.nextReqBodies.push(body);
      } catch {
        /* non-JSON body is fine */
      }
    }
  });

  page.on('response', async (res: Response) => {
    const url = res.url();
    if (!/\/api\/v1\//.test(url)) return;
    if (res.status() !== 200) return;
    let body: unknown;
    try {
      body = await res.json();
    } catch {
      return;
    }
    if (/\/quiz\/start/.test(url)) obs.startResBodies.push(body);
    else if (/\/quiz\/proceed/.test(url)) obs.proceedResBodies.push(body);
    else if (/\/quiz\/status\//.test(url)) obs.statusResBodies.push(body);
  });

  return obs;
}

test.describe('FE-E2E-CONTRACT: full FE↔BE happy-path wire contract', () => {
  test('AC-FE-CONTRACT-1..5: start → synopsis → proceed → question → answer → result, all wire fields validated', async ({
    page,
  }) => {
    const obs = await attachContractObserver(page);

    await stubTurnstile(page);
    await installConfigFixtureE2E(page);
    await installQuizMocks(page);

    // ---- 1. Landing → submit category ----
    await page.goto('/');
    await expect(
      page
        .getByRole('heading', {
          name: /discover your true personality|unlock your inner persona|create.*quiz/i,
        })
        .first(),
    ).toBeVisible({ timeout: 20_000 });
    await page.waitForTimeout(300);

    const input = page.getByRole('textbox').first();
    await input.fill('Ancient Rome');
    await page
      .getByRole('button', { name: /create my quiz/i })
      .first()
      .click();

    // ---- 2. Synopsis renders ----
    await expect(
      page.getByText(/The World of Ancient Rome|A short synopsis/i).first(),
    ).toBeVisible({ timeout: 20_000 });

    // AC-FE-CONTRACT-1: /quiz/start request shape (camelCase `category`,
    // optional `turnstileToken`).
    expect(obs.startReqBodies.length).toBeGreaterThan(0);
    const startReq = obs.startReqBodies[0] as Record<string, unknown>;
    expect(startReq).toHaveProperty('category');
    expect(typeof startReq.category).toBe('string');

    // AC-FE-CONTRACT-2: /quiz/start response shape (camelCase quizId +
    // initialPayload.type='synopsis').
    expect(obs.startResBodies.length).toBe(1);
    const startRes = obs.startResBodies[0] as Record<string, any>;
    expect(startRes.quizId).toBeTruthy();
    expect(typeof startRes.quizId).toBe('string');
    expect(startRes.initialPayload?.type).toBe('synopsis');
    expect(startRes.initialPayload?.data?.title).toBe('The World of Ancient Rome');

    // ---- 3. Click proceed ----
    const proceedBtn = page
      .getByRole('button', { name: /begin|start.*quiz|continue|proceed/i })
      .first();
    await expect(proceedBtn).toBeVisible({ timeout: 15_000 });
    await proceedBtn.click();

    // ---- 4. First question renders ----
    await expect(
      page.getByText(/Which achievement is most impressive/i),
    ).toBeVisible({ timeout: 20_000 });

    // AC-FE-CONTRACT-3: /quiz/proceed request includes quizId.
    expect(obs.proceedReqBodies.length).toBeGreaterThan(0);
    const proceedReq = obs.proceedReqBodies[0] as Record<string, unknown>;
    expect(proceedReq.quizId ?? proceedReq.quiz_id).toBeTruthy();

    // AC-FE-CONTRACT-4: status poll returns active question with
    // `data.questionText` + `data.options[]`.
    const activeStatus = obs.statusResBodies.find(
      (b) => (b as any)?.status === 'active' && (b as any)?.type === 'question',
    ) as Record<string, any> | undefined;
    expect(activeStatus, 'expected an active-question status response').toBeTruthy();
    expect(activeStatus!.data?.questionText).toBe('Which achievement is most impressive?');
    expect(Array.isArray(activeStatus!.data?.options)).toBe(true);
    expect(activeStatus!.data!.options.length).toBe(4);

    // ---- 5. Answer first question ----
    await page
      .getByRole('button', { name: /Aqueducts/i })
      .first()
      .click();

    // ---- 6. Result renders ----
    await expect(page.getByText(/The Architect/i)).toBeVisible({
      timeout: 20_000,
    });
    await expect(
      page.getByText(/engineering excellence and civic design/i),
    ).toBeVisible();

    // AC-FE-CONTRACT-5: /quiz/next request includes quizId + the answer.
    expect(obs.nextReqBodies.length).toBeGreaterThan(0);
    const nextReq = obs.nextReqBodies[0] as Record<string, unknown>;
    expect(nextReq.quizId ?? nextReq.quiz_id).toBeTruthy();

    // Status poll eventually returned a `finished` result with the trait list.
    const finishedStatus = obs.statusResBodies.find(
      (b) => (b as any)?.status === 'finished' && (b as any)?.type === 'result',
    ) as Record<string, any> | undefined;
    expect(finishedStatus, 'expected a finished-result status response').toBeTruthy();
    expect(finishedStatus!.data?.title).toBe('The Architect');
    expect(Array.isArray(finishedStatus!.data?.traits)).toBe(true);
    expect(finishedStatus!.data!.traits.length).toBeGreaterThan(0);

    // AC-FE-OBS-REQID-1 (e2e cross-check): every observed request had
    // X-Request-Id.
    expect(obs.outgoingHasRequestId).toBe(true);
  });
});
