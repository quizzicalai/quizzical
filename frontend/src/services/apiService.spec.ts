/* eslint no-console: ["error", { "allow": ["debug", "warn", "error"] }] */
import { describe, it, expect, beforeEach, afterEach, vi } from 'vitest';
import {
  installFetchMock,
  setEnv,
  loadModule,
  silenceConsole,
  advance,
  runAllTimers,
} from '../../tests/fixtures/testHarness';
import { CONFIG_FIXTURE } from '../../tests/fixtures/config.fixture';

// NOTE: Use a Vite-resolvable root-relative path (no "frontend/..." prefix)
const MOD_PATH = 'src/services/apiService.ts';

type ApiModule = typeof import('./apiService');

function makeTimeouts() {
  return { ...CONFIG_FIXTURE.apiTimeouts };
}

/**
 * Initializes env + fetch mock + loads module and initializes the api service.
 * Defaults ensure FULL_BASE_URL is absolute in all tests (https://api.test/api/v1)
 */
async function setupInitialized(overrides: Record<string, any> = {}) {
  setEnv({
    VITE_API_URL: 'https://api.test',
    VITE_API_BASE_URL: '/api/v1',
    VITE_USE_DB_RESULTS: 'false',
    ...overrides,
  });
  const fetchMock = installFetchMock();
  silenceConsole();
  const mod = (await loadModule<ApiModule>(MOD_PATH));
  mod.initializeApiService(makeTimeouts());
  return { mod, fetchMock };
}

describe('apiService: apiFetch core wrapper', () => {
  afterEach(() => {
    vi.restoreAllMocks();
  });

  it('throws if called before initializeApiService (except /config)', async () => {
    setEnv({
      VITE_API_URL: 'https://api.test',
      VITE_API_BASE_URL: '/api/v1',
    });
    const { fetchMock } = { fetchMock: installFetchMock() };
    silenceConsole();
    const mod = await loadModule<ApiModule>(MOD_PATH);

    fetchMock.mockJsonOnce(200, CONFIG_FIXTURE);

    // /config is allowed pre-init
    const cfg = await mod.apiFetch('/config');
    expect(cfg).toBeTruthy();

    // any other path should throw
    await expect(mod.apiFetch('/somewhere')).rejects.toThrow(
      /apiService has not been initialized/i
    );
  });

  it('builds the correct URL and defaults (method, headers, credentials)', async () => {
    const { mod, fetchMock } = await setupInitialized();

    fetchMock.mockJsonOnce(200, { ok: true }, { 'content-type': 'application/json' });

    await mod.apiFetch('/ok', {
      method: 'POST',
      body: { a: 1 },
      headers: { 'X-Token': 't' },
    });

    const call = fetchMock.calls[0];
    expect(call.url).toBe('https://api.test/api/v1/ok');
    expect(call.method).toBe('POST');
    // Default header + custom header merge
    expect(call.headers['Content-Type']).toBe('application/json');
    expect(call.headers['X-Token']).toBe('t');
    // Body was JSON.stringified then parsed by harness
    expect(call.body).toEqual({ a: 1 });
    // Default credentials
    expect(call.credentials).toBe('same-origin');
  });

  it('merges and allows overriding Content-Type header', async () => {
    const { mod, fetchMock } = await setupInitialized();

    fetchMock.mockTextOnce(200, 'ok', { 'content-type': 'text/plain' });

    await mod.apiFetch('/override-header', {
      method: 'POST',
      headers: { 'Content-Type': 'text/plain' },
      body: 'hi',
    });

    const call = fetchMock.calls[0];
    expect(call.headers['Content-Type']).toBe('text/plain');
  });

  it('passes credentials option through', async () => {
    const { mod, fetchMock } = await setupInitialized();

    fetchMock.mockJsonOnce(200, { ok: 1 });

    await mod.apiFetch('/creds', { credentials: 'include' });

    expect(fetchMock.calls[0].credentials).toBe('include');
  });

  it('appends query params (skips null/undefined) and stringifies values', async () => {
    const { mod, fetchMock } = await setupInitialized();

    fetchMock.mockJsonOnce(200, { ok: true });

    await mod.apiFetch('/with-query', {
      query: { a: 1, b: 'x', c: true, d: null, e: undefined },
    });

    const u = new URL(fetchMock.calls[0].url);
    expect(u.pathname).toBe('/api/v1/with-query');
    expect(u.searchParams.get('a')).toBe('1');
    expect(u.searchParams.get('b')).toBe('x');
    expect(u.searchParams.get('c')).toBe('true');
    expect(u.searchParams.has('d')).toBe(false);
    expect(u.searchParams.has('e')).toBe(false);
  });

  it('returns text when response is not JSON', async () => {
    const { mod, fetchMock } = await setupInitialized();

    fetchMock.mockTextOnce(200, 'hello', { 'content-type': 'text/plain' });

    const res = await mod.apiFetch<string>('/text');
    expect(res).toBe('hello');
  });

  it('normalizes AbortError rejections as benign "canceled"', async () => {
    const { mod, fetchMock } = await setupInitialized();

    fetchMock.mockRejectOnce({ name: 'AbortError' });

    await expect(mod.apiFetch('/aborted')).rejects.toMatchObject({
      code: 'canceled',
      message: 'Request was aborted',
      retriable: false,
    });
  });

  it('normalizes network errors (non-Abort) with retriable=true', async () => {
    const { mod, fetchMock } = await setupInitialized();

    fetchMock.mockRejectOnce(new Error('boom'));

    await expect(mod.apiFetch('/net')).rejects.toMatchObject({
      code: 'network_error',
      retriable: true,
    });
  });

  it('normalizes non-2xx JSON error with code/message from payload', async () => {
    const { mod, fetchMock } = await setupInitialized();

    fetchMock.mockJsonOnce(400, { code: 'bad', message: 'oops' });

    await expect(mod.apiFetch('/err-json')).rejects.toMatchObject({
      status: 400,
      code: 'bad',
      message: 'oops',
      retriable: false,
    });
  });

  it('normalizes non-2xx text error with default http_error code and retriable when 5xx', async () => {
    const { mod, fetchMock } = await setupInitialized();

    fetchMock.mockTextOnce(500, 'server down', { 'content-type': 'text/plain' });

    await expect(mod.apiFetch('/err-text')).rejects.toMatchObject({
      status: 500,
      code: 'http_error',
      message: 'HTTP 500',
      retriable: true,
    });
  });

  it('allows /config before init and uses FULL_BASE_URL', async () => {
    setEnv({
      VITE_API_URL: 'https://api.test',
      VITE_API_BASE_URL: '/api/v1',
    });
    const fetchMock = installFetchMock();
    silenceConsole();
    const mod = await loadModule<ApiModule>(MOD_PATH);

    fetchMock.mockJsonOnce(200, CONFIG_FIXTURE, { 'content-type': 'application/json' });
    const cfg = await mod.apiFetch('/config');

    expect(cfg).toEqual(CONFIG_FIXTURE);
    expect(fetchMock.calls[0].url).toBe('https://api.test/api/v1/config');
  });
});

describe('apiService: startQuiz', () => {
  afterEach(() => {
    vi.restoreAllMocks();
  });

  it('normalizes question initial payload and characters', async () => {
    const { mod, fetchMock } = await setupInitialized();

    fetchMock.mockJsonOnce(200, {
      quizId: 'q1',
      initialPayload: {
        type: 'question',
        data: {
          type: 'question',
          questionText: 'What?',
          options: [{ text: 'A' }, { text: 'B' }],
        },
      },
      charactersPayload: {
        type: 'characters',
        data: [{ name: 'Alice', shortDescription: 'a', profileText: 'p', imageUrl: null }],
      },
    });

    const res = await mod.startQuiz('History', 'turnstile-token');

    // Request body
    expect(JSON.stringify((fetchMock.calls[0].body ?? {}))).toContain('"category":"History"');
    expect(JSON.stringify((fetchMock.calls[0].body ?? {}))).toContain('"cf-turnstile-response":"turnstile-token"');

    // Normalized response
    expect(res.quizId).toBe('q1');
    expect(res.initialPayload?.type).toBe('question');
    expect(res.initialPayload && 'data' in res.initialPayload && (res.initialPayload as any).data.answers.length).toBe(2);
    expect(res.charactersPayload?.data?.[0]?.shortDescription).toBe('a');
  });

  it('normalizes synopsis initial payload', async () => {
    const { mod, fetchMock } = await setupInitialized();

    fetchMock.mockJsonOnce(200, {
      quizId: 'q2',
      initialPayload: {
        type: 'synopsis',
        data: { type: 'synopsis', title: 'Title', summary: 'Summary' },
      },
    });

    const res = await mod.startQuiz('Cooking', 'ts');
    expect(res.quizId).toBe('q2');
    expect(res.initialPayload?.type).toBe('synopsis');
    expect((res.initialPayload as any).data.title).toBe('Title');
  });

  it('falls back to legacy fields when initialPayload is missing', async () => {
    const { mod, fetchMock } = await setupInitialized();

    // initialPayload missing (null), but legacy `question` exists on raw object
    fetchMock.mockJsonOnce(200, {
      quizId: 'legacy',
      initialPayload: null,
      question: {
        text: 'Legacy?',
        options: ['x', 'y'],
      },
    });

    const res = await mod.startQuiz('Legacy', 'ts');
    expect(res.quizId).toBe('legacy');
    expect(res.initialPayload?.type).toBe('question');
    expect((res.initialPayload as any).data.answers.map((a: any) => a.text)).toEqual(['x', 'y']);
  });
});

describe('apiService: proceedQuiz', () => {
  afterEach(() => {
    vi.restoreAllMocks();
  });

  it('posts quizId and returns server shape', async () => {
    const { mod, fetchMock } = await setupInitialized();
    fetchMock.mockJsonOnce(200, { status: 'processing', quiz_id: 'abc' });

    const res = await mod.proceedQuiz('abc');
    expect(fetchMock.calls[0].url).toBe('https://api.test/api/v1/quiz/proceed');
    expect(fetchMock.calls[0].method).toBe('POST');
    expect(fetchMock.calls[0].body).toEqual({ quizId: 'abc' });
    expect(res).toEqual({ status: 'processing', quiz_id: 'abc' });
  });
});

describe('apiService: getQuizStatus', () => {
  afterEach(() => {
    vi.restoreAllMocks();
  });

  it('normalizes processing status (quizId -> quiz_id)', async () => {
    const { mod, fetchMock } = await setupInitialized();
    fetchMock.mockJsonOnce(200, { status: 'processing', quizId: 'id123' });

    const res = await mod.getQuizStatus('id123', { knownQuestionsCount: 2 });
    expect(res).toEqual({ status: 'processing', type: 'wait', quiz_id: 'id123' });
    const url = new URL(fetchMock.calls[0].url);
    expect(url.searchParams.get('known_questions_count')).toBe('2');
  });

  it('normalizes active question payload to UI Question', async () => {
    const { mod, fetchMock } = await setupInitialized();
    fetchMock.mockJsonOnce(200, {
      status: 'active',
      type: 'question',
      data: { text: 'Hi', options: [{ text: 'A' }] },
    });

    const res = await mod.getQuizStatus('s1');
    expect(res.status).toBe('active');
    expect(res.type).toBe('question');
    expect((res as any).data.answers?.length).toBe(1);
  });

  it('normalizes finished result payload', async () => {
    const { mod, fetchMock } = await setupInitialized();
    fetchMock.mockJsonOnce(200, {
      status: 'finished',
      type: 'result',
      data: { title: 'Winner', description: 'desc', imageUrl: null },
    });

    const res = await mod.getQuizStatus('s2');
    expect(res.status).toBe('finished');
    expect(res.type).toBe('result');
    expect((res as any).data.profileTitle).toBe('Winner');
    // null -> undefined mapping for imageUrl is handled by toUiResult; no strict assert needed here
  });
});

describe('apiService: pollQuizStatus', () => {
  beforeEach(() => {
    vi.useFakeTimers();
    vi.spyOn(Math, 'random').mockReturnValue(0); // deterministic jitter
  });
  afterEach(() => {
    vi.useRealTimers();
    vi.restoreAllMocks();
  });

  it('returns when question or finished status arrives; calls onTick each time', async () => {
    const { mod, fetchMock } = await setupInitialized({
      // speed up polling
      VITE_API_URL: 'https://api.test',
      VITE_USE_DB_RESULTS: 'false',
    });

    // Replace timeouts with tiny ones for fast test
    mod.initializeApiService({
      default: 15000,
      startQuiz: 15000,
      poll: { total: 5000, interval: 0, maxInterval: 100 },
    });

    // First call → processing
    fetchMock.mockJsonOnce(200, { status: 'processing', quiz_id: 'q' });
    // Second call → active question
    fetchMock.mockJsonOnce(200, {
      status: 'active',
      type: 'question',
      data: { text: 'Q1', options: [{ text: 'A' }] },
    });

    const onTick = vi.fn();

    const p = mod.pollQuizStatus('q', { onTick });

    // Let first loop tick (interval=0 so it immediately requests)
    await advance(1); // progress microtasks
    // Sleep nextDelay (attempt=1 => ~500ms, but with jitter=0, maxInterval=100 => 100)
    await advance(100);
    await runAllTimers();

    const res = await p;

    expect(res.status).toBe('active');
    expect(onTick).toHaveBeenCalledTimes(2); // processing + active
    expect((res as any).data.answers.length).toBe(1);
  });

  it('continues on retriable errors and eventually returns', async () => {
    const { mod, fetchMock } = await setupInitialized();
    mod.initializeApiService({
      default: 15000,
      startQuiz: 15000,
      poll: { total: 4000, interval: 0, maxInterval: 50 },
    });

    // First call -> 500 server error (retriable)
    fetchMock.mockTextOnce(500, 'fail');
    // Second call -> processing
    fetchMock.mockJsonOnce(200, { status: 'processing', quiz_id: 'q' });
    // Third call -> finished
    fetchMock.mockJsonOnce(200, {
      status: 'finished',
      type: 'result',
      data: { title: 'Done', description: 'ok', imageUrl: null },
    });

    const onTick = vi.fn();
    const p = mod.pollQuizStatus('q', { onTick });

    await advance(1);
    await advance(50);
    await runAllTimers();

    const res = await p;
    expect(res.status).toBe('finished');
    expect(onTick).toHaveBeenCalledTimes(2); // processing + finished (retriable error does not call onTick)
  });

  it('throws immediately on non-retriable getQuizStatus error', async () => {
    const { mod, fetchMock } = await setupInitialized();
    mod.initializeApiService({
      default: 15000,
      startQuiz: 15000,
      poll: { total: 2000, interval: 0, maxInterval: 50 },
    });

    // 400 error -> non-retriable
    fetchMock.mockJsonOnce(400, { code: 'bad', message: 'no' });

    await expect(mod.pollQuizStatus('q')).rejects.toMatchObject({
      status: 400,
      code: 'bad',
    });
  });

  it('times out with poll_timeout error when total time elapses', async () => {
    const { mod, fetchMock } = await setupInitialized();
    mod.initializeApiService({
      default: 15000,
      startQuiz: 15000,
      poll: { total: 600, interval: 0, maxInterval: 100 },
    });

    // Keep returning processing so it never resolves
    fetchMock.mockJsonOnce(200, { status: 'processing', quiz_id: 'q' });
    fetchMock.mockJsonOnce(200, { status: 'processing', quiz_id: 'q' });
    fetchMock.mockJsonOnce(200, { status: 'processing', quiz_id: 'q' });
    fetchMock.mockJsonOnce(200, { status: 'processing', quiz_id: 'q' });

    const p = mod.pollQuizStatus('q');
    // attach an immediate catch to avoid Node's "rejection handled asynchronously" warning
    p.catch(() => {});

    // Progress time beyond total
    await advance(100);
    await advance(100);
    await advance(100);
    await advance(100);
    await advance(200);
    await runAllTimers();

    await expect(p).rejects.toMatchObject({
      code: 'poll_timeout',
      status: 408,
    });
  });
});

describe('apiService: submitAnswer', () => {
  afterEach(() => {
    vi.restoreAllMocks();
  });

  it('POSTs /quiz/next with the correct body', async () => {
    const { mod, fetchMock } = await setupInitialized();

    fetchMock.mockJsonOnce(200, { status: 'processing' });

    await mod.submitAnswer('qid', { questionIndex: 0, optionIndex: 2 });

    const call = fetchMock.calls[0];
    expect(call.url).toBe('https://api.test/api/v1/quiz/next');
    expect(call.method).toBe('POST');
    expect(call.body).toEqual({
      quizId: 'qid',
      questionIndex: 0,
      optionIndex: 2,
      answer: undefined,
    });
  });
});

describe('apiService: submitFeedback', () => {
  afterEach(() => {
    vi.restoreAllMocks();
  });

  it('POSTs /feedback with snake_case, rating, comment, and turnstile token', async () => {
    const { mod, fetchMock } = await setupInitialized();

    fetchMock.mockTextOnce(200, '');

    await mod.submitFeedback(
      'qid',
      { rating: 'up', comment: 'nice' },
      'turnstile',
    );

    const call = fetchMock.calls[0];
    expect(call.url).toBe('https://api.test/api/v1/feedback');
    expect(call.method).toBe('POST');
    expect(call.body).toEqual({
      quiz_id: 'qid',
      rating: 'up',
      text: 'nice',
      'cf-turnstile-response': 'turnstile',
    });
  });
});

describe('apiService: getResult', () => {
  afterEach(() => {
    vi.restoreAllMocks();
  });

  it('when USE_DB_RESULTS=false: returns from status if finished', async () => {
    const { mod, fetchMock } = await setupInitialized({ VITE_USE_DB_RESULTS: 'false' });

    fetchMock.mockJsonOnce(200, {
      status: 'finished',
      type: 'result',
      data: { title: 'R', description: 'D', imageUrl: null },
    });

    const res = await mod.getResult('rid');
    expect(res.profileTitle).toBe('R');
  });

  it('when USE_DB_RESULTS=false: throws not_found if not finished in status', async () => {
    const { mod, fetchMock } = await setupInitialized({ VITE_USE_DB_RESULTS: 'false' });

    fetchMock.mockJsonOnce(200, { status: 'processing', quiz_id: 'rid' });

    await expect(mod.getResult('rid')).rejects.toMatchObject({
      status: 404,
      code: 'not_found',
    });
  });

  it('when USE_DB_RESULTS=true: returns from DB on success', async () => {
    // Must re-import module to pick up new env (constant evaluated at import)
    setEnv({
      VITE_API_URL: 'https://api.test',
      VITE_API_BASE_URL: '/api/v1',
      VITE_USE_DB_RESULTS: 'true',
    });
    const fetchMock = installFetchMock();
    silenceConsole();
    const mod = await loadModule<ApiModule>(MOD_PATH);
    mod.initializeApiService(makeTimeouts());

    fetchMock.mockJsonOnce(200, {
      title: 'DBTitle',
      description: 'DBDesc',
      imageUrl: null,
      traits: [{ label: 't1' }],
      shareUrl: 'https://x',
    });

    const res = await mod.getResult('rid');
    expect(res.profileTitle).toBe('DBTitle');
    expect(res.traits?.[0]?.label).toBe('t1');
  });

  it('when USE_DB_RESULTS=true: 404/403 falls back to status result', async () => {
    // Re-import with USE_DB_RESULTS=true
    setEnv({
      VITE_API_URL: 'https://api.test',
      VITE_API_BASE_URL: '/api/v1',
      VITE_USE_DB_RESULTS: 'true',
    });
    const fetchMock = installFetchMock();
    silenceConsole();
    const mod = await loadModule<ApiModule>(MOD_PATH);
    mod.initializeApiService(makeTimeouts());

    // DB 404
    fetchMock.mockJsonOnce(404, { code: 'not_found' });
    // Status finished
    fetchMock.mockJsonOnce(200, {
      status: 'finished',
      type: 'result',
      data: { title: 'FromStatus', description: 'S', imageUrl: null },
    });

    const res = await mod.getResult('rid');
    expect(res.profileTitle).toBe('FromStatus');
  });

  it('when USE_DB_RESULTS=true: non-404/403 DB failure rethrows', async () => {
    setEnv({
      VITE_API_URL: 'https://api.test',
      VITE_API_BASE_URL: '/api/v1',
      VITE_USE_DB_RESULTS: 'true',
    });
    const fetchMock = installFetchMock();
    silenceConsole();
    const mod = await loadModule<ApiModule>(MOD_PATH);
    mod.initializeApiService(makeTimeouts());

    // DB 500 -> should throw; no status fallback attempted
    fetchMock.mockTextOnce(500, 'fail');

    await expect(mod.getResult('rid')).rejects.toMatchObject({
      status: 500,
      retriable: true,
    });
  });
});
