// frontend/src/utils/configValidation.spec.ts
import { describe, it, expect, vi, beforeEach, afterEach } from 'vitest';
import { validateAndNormalizeConfig } from './configValidation';

/** Minimal-but-valid raw config (can be freely mutated per test). */
function makeRawValid(): any {
  return {
    theme: {
      colors: { primary: '#000000', secondary: '#ffffff' },
      fonts: { body: 'Inter', heading: 'Georgia' },
      dark: { colors: { primary: '#111111' } }, // covers optional dark branch
    },
    content: {
      appName: 'Quizzical AI',
      footer: {
        about: { label: 'About', href: '/about' },
        terms: { label: 'Terms', href: '/terms' },
        privacy: { label: 'Privacy', href: '/privacy' },
        donate: { label: 'Donate', href: 'https://example.com', external: true },
      },
      // leave landingPage/loadingStates/errors unset to exercise defaults merger
    },
    limits: {
      validation: {
        category_min_length: 3,
        category_max_length: 32,
      },
    },
    apiTimeouts: {
      default: 15_000,
      startQuiz: 20_000,
      poll: { total: 60_000, interval: 0, maxInterval: 2_000 }, // 0 allowed (nonnegative)
    },
    // features is optional
  };
}

/** Toggle Vite-style DEV flag (Vitest sets import.meta.env). */
const DEV_GET = () => (import.meta as any).env?.DEV;
const SET_DEV = (value: boolean) => {
  (import.meta as any).env = { ...(import.meta as any).env, DEV: value };
};

let originalDEV: boolean | undefined;
let consoleErrorSpy: ReturnType<typeof vi.spyOn>;

beforeEach(() => {
  originalDEV = DEV_GET();
  consoleErrorSpy = vi.spyOn(console, 'error').mockImplementation(() => {});
  // Ensure tests start in non-DEV mode unless a test explicitly enables it.
  SET_DEV(false);
});

afterEach(() => {
  if (typeof originalDEV !== 'undefined') SET_DEV(originalDEV);
  consoleErrorSpy.mockRestore();
  vi.restoreAllMocks();
});

describe('validateAndNormalizeConfig (configValidation.ts)', () => {
  it('parses a valid config and merges defaults for optional content (landingPage, loadingStates, errors)', () => {
    const raw = makeRawValid();
    const normalized = validateAndNormalizeConfig(raw);

    // types preserved & merged
    expect(normalized.theme.colors.primary).toBe('#000000');
    expect(normalized.theme.dark?.colors.primary).toBe('#111111');

    // landingPage defaults from DEFAULT_APP_CONFIG (assert shapes, not exact text)
    expect(normalized.content.landingPage).toMatchObject({
      title: expect.any(String),
      subtitle: expect.any(String),
      placeholder: expect.any(String),
      buttonText: expect.any(String),
      validation: {
        minLength: expect.any(String),
        maxLength: expect.any(String),
      },
    });

    // defaults injected for loadingStates and errors
    expect(normalized.content.loadingStates?.page).toEqual(expect.any(String));
    expect(normalized.content.errors?.title).toEqual(expect.any(String));
  });

  it('merges landingPage overrides while keeping unspecified defaults (including nested validation)', () => {
    const raw = makeRawValid();
    raw.content.landingPage = {
      title: 'Custom Title',
      buttonText: 'Start!',
      validation: { minLength: 'At least {min} chars' },
    };

    const normalized = validateAndNormalizeConfig(raw);

    // override took effect
    expect((normalized.content.landingPage as any).title).toBe('Custom Title');
    expect((normalized.content.landingPage as any).buttonText).toBe('Start!');

    // partial nested override: minLength overridden; maxLength from defaults
    expect((normalized.content.landingPage as any).validation.minLength).toBe('At least {min} chars');
    expect((normalized.content.landingPage as any).validation.maxLength).toEqual(expect.any(String));
  });

  it('merges loadingStates/errors overrides while preserving default keys', () => {
    const raw = makeRawValid();
    raw.content.loadingStates = { page: 'Please wait…' };
    raw.content.errors = { title: 'Oops' };

    const normalized = validateAndNormalizeConfig(raw);
    expect(normalized.content.loadingStates?.page).toBe('Please wait…'); // override
    expect(normalized.content.loadingStates?.quiz).toEqual(expect.any(String)); // default preserved
    expect(normalized.content.errors?.title).toBe('Oops'); // override
    expect(normalized.content.errors?.home).toEqual(expect.any(String)); // default preserved
  });

  it('passes through theme.fontSizes when provided', () => {
    const raw = makeRawValid();
    raw.theme.fontSizes = {
      landingTitle: '2.25rem',
      button: '1rem',
    };

    const normalized = validateAndNormalizeConfig(raw);
    expect(normalized.theme.fontSizes).toBeDefined();
    expect(normalized.theme.fontSizes?.landingTitle).toBe('2.25rem');
    expect(normalized.theme.fontSizes?.button).toBe('1rem');
  });

  it('coerces string numbers for limits and apiTimeouts; allows poll.interval=0; keeps ints positive/nonnegative as required', () => {
    const raw = makeRawValid();
    raw.limits.validation = { category_min_length: '5', category_max_length: '40' };
    raw.apiTimeouts = {
      default: '10000',
      startQuiz: '25000',
      poll: { total: '60000', interval: '0', maxInterval: '3000' },
    };

    const normalized = validateAndNormalizeConfig(raw);

    // coerced to numbers
    expect(normalized.limits.validation.category_min_length).toBe(5);
    expect(normalized.limits.validation.category_max_length).toBe(40);
    expect(normalized.apiTimeouts.default).toBe(10_000);
    expect(normalized.apiTimeouts.poll.interval).toBe(0); // nonnegative allowed
    expect(normalized.apiTimeouts.poll.maxInterval).toBe(3_000);
  });

  it('fails when category_max_length < category_min_length (refine), logs field errors in DEV', () => {
    SET_DEV(true);
    const raw = makeRawValid();
    raw.limits.validation = { category_min_length: 10, category_max_length: 3 };

    // Fails at strict merged validation
    expect(() => validateAndNormalizeConfig(raw)).toThrowError(
      'Merged application configuration failed validation.'
    );

    // DEV branch: logs flattened field errors
    expect(consoleErrorSpy).toHaveBeenCalledTimes(1);
    const [msg] = consoleErrorSpy.mock.calls[0];
    expect(String(msg)).toMatch(
      /Merged configuration failed strict validation|Merged application configuration failed/i
    );
  });

  it('fails when poll.interval is negative (partial nonnegative), and when maxInterval < interval (strict refine)', () => {
    // Case A: negative interval hits the *partial* schema (nonnegative)
    const raw1 = makeRawValid();
    raw1.apiTimeouts.poll.interval = -1;

    expect(() => validateAndNormalizeConfig(raw1)).toThrowError(
      'Application configuration is invalid and could not be parsed.'
    );

    // Case B: maxInterval < interval passes partial but fails *strict* refine
    const raw2 = makeRawValid();
    raw2.apiTimeouts.poll.interval = 5000;
    raw2.apiTimeouts.poll.maxInterval = 1000;

    expect(() => validateAndNormalizeConfig(raw2)).toThrowError(
      'Merged application configuration failed validation.'
    );
  });

  it('ignores unknown keys at non-strict partial levels (e.g., theme.extra) instead of throwing', () => {
    const raw = makeRawValid();
    (raw.theme as any).extra = 'nope'; // unknown in partial theme

    expect(() => validateAndNormalizeConfig(raw)).not.toThrow();
    const normalized = validateAndNormalizeConfig(raw);
    expect((normalized.theme as any).extra).toBeUndefined();
  });

  it('rejects unknown keys inside LinkSchema (footer.about), due to .strict()', () => {
    const raw = makeRawValid();
    raw.content.footer.about = { label: 'About', href: '/about', foo: 'bar' } as any;

    // Strict link object is validated during partial parse
    expect(() => validateAndNormalizeConfig(raw)).toThrowError(
      'Application configuration is invalid and could not be parsed.'
    );
  });

  it('accepts features when valid, rejects when present but invalid', () => {
    // valid features
    const ok = makeRawValid();
    ok.features = { turnstileEnabled: true, turnstileSiteKey: 'site-key' };
    expect(() => validateAndNormalizeConfig(ok)).not.toThrow();

    // features optional: completely absent is fine
    const none = makeRawValid();
    expect(() => validateAndNormalizeConfig(none)).not.toThrow();

    // invalid: features present but missing required boolean (passes partial, fails strict)
    const bad = makeRawValid();
    bad.features = { turnstileSiteKey: 'missing-required-flag' } as any;
    expect(() => validateAndNormalizeConfig(bad)).toThrowError(
      'Merged application configuration failed validation.'
    );
  });

  it('top-level unknown key is rejected because AppConfigSchema is strict (partial parse)', () => {
    const raw = makeRawValid();
    (raw as any).unexpected = true;

    expect(() => validateAndNormalizeConfig(raw)).toThrowError(
      'Application configuration is invalid and could not be parsed.'
    );
  });

  it('landingPage can be an empty object (record schema) and still gets defaulted', () => {
    const raw = makeRawValid();
    raw.content.landingPage = {}; // allowed by z.record(...).optional()

    const normalized = validateAndNormalizeConfig(raw);
    expect((normalized.content.landingPage as any).title).toEqual(expect.any(String));
    expect((normalized.content.landingPage as any).buttonText).toEqual(expect.any(String));
  });
});
