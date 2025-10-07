// frontend/tests/fixtures/errors.fixture.ts

export const ABORT_ERROR = Object.assign(new Error('aborted'), { name: 'AbortError' });

export const NETWORK_ERROR = new Error('socket hang up');

export const makeHttpJsonError = (status = 500) => ({
  status,
  headers: { 'content-type': 'application/json' },
  body: { code: 'boom', message: 'Kaboom' },
});

export const makeHttpTextError = (status = 500) => ({
  status,
  headers: { 'content-type': 'text/plain' },
  body: 'Internal Server Error',
});
