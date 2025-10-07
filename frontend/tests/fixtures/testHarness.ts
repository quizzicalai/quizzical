// frontend/tests/fixtures/testHarness.ts
import { vi } from 'vitest';

/**
 * Minimal local types (avoid DOM lib types so ESLint doesn't complain in Node)
 */
type Credentials = 'omit' | 'same-origin' | 'include';

type HeadersLike =
  | Record<string, string>
  | Array<[string, string]>
  | { [k: string]: string }
  | null
  | undefined;

type FetchInitLike = {
  method?: string;
  headers?: HeadersLike | any;
  body?: any;
  credentials?: Credentials;
  signal?: unknown;
};

type FetchCall = {
  url: string;
  init?: FetchInitLike;
  method: string;
  headers: Record<string, string>;
  body: unknown;
  credentials?: Credentials;
};

type Enqueued =
  | { kind: 'json'; status: number; json: any; headers?: Record<string, string> }
  | { kind: 'text'; status: number; text: string; headers?: Record<string, string> }
  | { kind: 'reject'; error: any };

type ResponseLike = {
  ok: boolean;
  status: number;
  headers: { get(name: string): string | null };
  json(): Promise<any>;
  text(): Promise<string>;
};

/* -----------------------------------------------------------------------------
 * Helpers: headers / body / response
 * ---------------------------------------------------------------------------*/

/** Normalize headers of various shapes to a plain object. */
function normalizeHeaders(h: HeadersLike | any): Record<string, string> {
  if (!h) return {};
  // Headers instance (duck-type)
  if (typeof h?.forEach === 'function' && typeof h?.get === 'function') {
    const obj: Record<string, string> = {};
    try {
      h.forEach((v: string, k: string) => {
        obj[k] = v;
      });
    } catch {
      // ignore
    }
    return obj;
  }
  if (Array.isArray(h)) {
    try {
      return Object.fromEntries(h as Array<[string, string]>);
    } catch {
      const out: Record<string, string> = {};
      for (const pair of h as Array<[string, string]>) {
        if (pair && pair.length >= 2) out[String(pair[0])] = String(pair[1]);
      }
      return out;
    }
  }
  if (typeof h === 'object') return { ...(h as Record<string, string>) };
  return {};
}

/** Parse fetch body (only handles JSON strings for convenience in tests). */
function parseBody(body: any): unknown {
  if (typeof body === 'string') {
    try {
      return JSON.parse(body);
    } catch {
      return body;
    }
  }
  return body ?? undefined;
}

/**
 * Build a Response-like object.
 * If global Response is present (jsdom), use it. Otherwise, return a small polyfill.
 */
function makeResponse(
  status: number,
  headers: Record<string, string>,
  payload: { kind: 'json'; json: any } | { kind: 'text'; text: string }
): ResponseLike {
  const mergedHeaders = { ...headers };
  const hasDomResponse = typeof (globalThis as any).Response === 'function';

  if (hasDomResponse) {
    if (payload.kind === 'json') {
      // eslint-disable-next-line new-cap
      const r = new (globalThis as any).Response(JSON.stringify(payload.json), {
        status,
        headers: mergedHeaders,
      });
      return r as ResponseLike;
    } else {
      // eslint-disable-next-line new-cap
      const r = new (globalThis as any).Response(payload.text, {
        status,
        headers: mergedHeaders,
      });
      return r as ResponseLike;
    }
  }

  // Polyfill
  const headerStore = Object.fromEntries(
    Object.entries(mergedHeaders).map(([k, v]) => [k.toLowerCase(), String(v)])
  );

  const textData = payload.kind === 'json' ? JSON.stringify(payload.json) : payload.text;

  return {
    ok: status >= 200 && status < 300,
    status,
    headers: {
      get(name: string) {
        if (!name) return null;
        return headerStore[String(name).toLowerCase()] ?? null;
      },
    },
    async json() {
      if (payload.kind === 'json') return payload.json;
      try {
        return JSON.parse(textData);
      } catch {
        // mimic real fetch.json() throwing on invalid json
        throw new Error('Invalid JSON');
      }
    },
    async text() {
      return textData;
    },
  };
}

/* -----------------------------------------------------------------------------
 * Env control
 * ---------------------------------------------------------------------------*/

/**
 * Set Vite-style env vars used by the module under test.
 * Must be called BEFORE importing the module (loadModule).
 *
 * We use vi.stubEnv so that Vite/Vitest inlines the values into `import.meta.env.*`
 * at transform time. We also mirror the values onto (import.meta as any).env
 * for any runtime checks (e.g. DEV booleans).
 */
export function setEnv(overrides: Partial<Record<string, any>> = {}): void {
  // Clear any previous stubs so tests don't leak into each other
  const anyVi = vi as any;
  if (typeof anyVi.unstubAllEnvs === 'function') {
    anyVi.unstubAllEnvs();
  }

  // Defaults guarantee ABSOLUTE FULL_BASE_URL (https://api.test/api/v1) and predictable behavior
  const base: Record<string, string> = {
    VITE_API_URL: 'https://api.test',
    VITE_API_BASE_URL: '/api/v1',
    VITE_USE_DB_RESULTS: 'false',
  };

  const merged: Record<string, string> = {
    ...base,
    ...Object.fromEntries(
      Object.entries(overrides).map(([k, v]) => [k, v === undefined ? '' : String(v)])
    ),
  };

  // Stub for transform-time inlining
  for (const [k, v] of Object.entries(merged)) {
    vi.stubEnv(k, v);
  }

  // Also set a runtime object for direct runtime reads (e.g., DEV)
  (import.meta as any).env = {
    ...(import.meta as any).env,
    DEV: true,
    ...merged,
  };
}

/* -----------------------------------------------------------------------------
 * Fetch mock
 * ---------------------------------------------------------------------------*/

/**
 * Install a global fetch mock with a FIFO queue of responses.
 */
export function installFetchMock() {
  const queue: Enqueued[] = [];
  const calls: FetchCall[] = [];

  const fetchMock = vi.fn((input: any, init?: FetchInitLike) => {
    const url = String(input);
    const headers = normalizeHeaders(init?.headers);
    const method = (init?.method || 'GET').toUpperCase();
    const body = parseBody(init?.body);

    calls.push({
      url,
      init,
      method,
      headers,
      body,
      credentials: init?.credentials,
    });

    const next = queue.shift();
    if (!next) {
      return Promise.reject(new Error('No fetch mock queued for: ' + url));
    }

    if (next.kind === 'reject') {
      return Promise.reject(next.error);
    }

    if (next.kind === 'json') {
      const respHeaders = {
        'content-type': 'application/json',
        ...(next.headers || {}),
      };
      return Promise.resolve(
        makeResponse(next.status, respHeaders, { kind: 'json', json: next.json })
      );
    }

    const respHeaders = {
      'content-type': 'text/plain',
      ...(next.headers || {}),
    };
    return Promise.resolve(
      makeResponse(next.status, respHeaders, { kind: 'text', text: next.text })
    );
  });

  (globalThis as any).fetch = fetchMock;

  return {
    /** Enqueue a JSON response for the next fetch call */
    mockJsonOnce(status: number, json: any, headers?: Record<string, string>) {
      queue.push({ kind: 'json', status, json, headers });
    },
    /** Enqueue a text response for the next fetch call */
    mockTextOnce(status: number, text: string, headers?: Record<string, string>) {
      queue.push({ kind: 'text', status, text, headers });
    },
    /** Enqueue a rejection (e.g., network error or AbortError) */
    mockRejectOnce(error: any) {
      queue.push({ kind: 'reject', error });
    },
    /** Recorded fetch calls */
    get calls(): FetchCall[] {
      return calls;
    },
    /** Direct access to the vi mock */
    get fn() {
      return fetchMock;
    },
    /** Clear queued responses and recorded calls */
    reset() {
      queue.splice(0, queue.length);
      calls.splice(0, calls.length);
      fetchMock.mockClear();
    },
  };
}

/* -----------------------------------------------------------------------------
 * Module loader
 * ---------------------------------------------------------------------------*/

/**
 * Import a module AFTER setting env and installing fetch mocks.
 * Ensures module-level constants see the right environment.
 */
export async function loadModule<T = any>(modulePath: string): Promise<T> {
  vi.resetModules();
  const mod = await import(/* @vite-ignore */ modulePath);
  return mod as T;
}

/* -----------------------------------------------------------------------------
 * Misc helpers
 * ---------------------------------------------------------------------------*/

export function createAbort() {
  const controller = new AbortController();
  return { controller, signal: controller.signal };
}

/** Timer helpers (Vitest exposes async variants in newer versions). */
export async function runAllTimers() {
  const anyVi = vi as any;
  if (typeof anyVi.runAllTimersAsync === 'function') {
    await anyVi.runAllTimersAsync();
    return;
  }
  vi.runAllTimers();
}

export async function advance(ms: number) {
  const anyVi = vi as any;
  if (typeof anyVi.advanceTimersByTimeAsync === 'function') {
    await anyVi.advanceTimersByTimeAsync(ms);
    return;
  }
  vi.advanceTimersByTime(ms);
}

/** Silence console noise during tests (spies still available). */
export function silenceConsole() {
  vi.spyOn(console, 'debug').mockImplementation(() => {});
  vi.spyOn(console, 'warn').mockImplementation(() => {});
  vi.spyOn(console, 'error').mockImplementation(() => {});
}

/** Small async tick helper. */
export const nextTick = () => new Promise<void>((r) => setTimeout(r, 0));
