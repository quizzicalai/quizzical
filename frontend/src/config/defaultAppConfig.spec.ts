// Regression: DEFAULT_APP_CONFIG must require Turnstile by default (safe-by-default).
import { describe, it, expect } from 'vitest';
import { DEFAULT_APP_CONFIG } from './defaultAppConfig';

describe('DEFAULT_APP_CONFIG safe-by-default', () => {
  it('enables Turnstile by default to fail-closed when backend config is missing', () => {
    expect(DEFAULT_APP_CONFIG.features?.turnstile).toBe(true);
    expect(DEFAULT_APP_CONFIG.features?.turnstileEnabled).toBe(true);
  });
});
