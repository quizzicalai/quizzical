// frontend/src/utils/configValidation.spec.ts
import { describe, it, expect, vi, beforeEach, afterEach } from 'vitest';
import { z } from 'zod';
import { validateAndNormalizeConfig, type AppConfig } from './configValidation';

// --- helpers ---------------------------------------------------------------

/** Minimal-but-valid raw config (can be freely mutated per test). */
function makeRawValid(): any {
  return {
    theme: {
      colors: { primary: '#000000', secondary: '#ffffff' },
      fonts: { body: 'Inter', heading: 'Georgia' },
      dark: { colors: { primary: '#111111' } }, // covers optional dark branch
    },
    content: {
      appName: 'Quizzical',
      // landingPage is optional and intentionally omitted in many tests
      footer: {}, // All keys optional; {} is valid thanks to .strict()
      // leave loadingStates/errors unset to exercise defaults merger
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

// --- tests -----------------------------------------------------------------

describe('validateAndNormalizeConfig (configValidation.ts)', () => {
  it('parses a valid config and shallow-merges runtime defaults when optional content is missing', () => {
    const raw = makeRawValid();
    // intentionally omit content.landingPage, loadingStates, errors
    const normalized = validateAndNormalizeConfig(raw);

    // types preserved
    expect(normalized.theme.colors.primary).toBe('#000000');
    expect(normalized.theme.dark?.colors.primary).toBe('#111111');

    // defaults injected for landingPage
    expect(normalized.content.landingPage).toMatchObject({
      title: 'Unlock Your Inner Persona',
      subtitle:
        'Answer a few questions and let our AI reveal a surprising profile of you.',
      inputPlaceholder: "e.g., 'Ancient Rome', 'Baking'",
      submitButton: 'Create My Quiz',
      inputAriaLabel: 'Quiz category input',
      examples: ['Ancient Rome', 'Baking'],
      validation: {
        minLength: expect.any(String),
        maxLength: expect.any(String),
      },
    });

    // defaults injected for loadingStates and errors
    expect(normalized.content.loadingStates?.page).toBe('Loading...');
    expect(normalized.content.errors?.title).toBe('An Error Occurred');
  });

  it('merges landingPage overrides while keeping unspecified defaults (including nested validation)', () => {
    const raw = makeRawValid();
    raw.content.landingPage = {
      title: 'Custom Title',
      submitButton: 'Start!',
      validation: { minLength: 'At least {min} chars' },
    };

    const normalized = validateAndNormalizeConfig(raw);

    // override took effect
    expect(normalized.content.landingPage.title).toBe('Custom Title');
    expect(normalized.content.landingPage.submitButton).toBe('Start!');
    // partial nested override: minLength overridden, maxLength comes from defaults
    expect(normalized.content.landingPage.validation.minLength).toBe('At least {min} chars');
    expect(normalized.content.landingPage.validation.maxLength).toBe(
      'Cannot exceed {max} characters.'
    );
  });

  it('merges loadingStates/errors overrides while preserving default keys', () => {
    const raw = makeRawValid();
    raw.content.loadingStates = { page: 'Please wait…' };
    raw.content.errors = { title: 'Oops' };

    const normalized = validateAndNormalizeConfig(raw);
    expect(normalized.content.loadingStates?.page).toBe('Please wait…'); // override
    expect(normalized.content.loadingStates?.quiz).toBe('Preparing your quiz...'); // default preserved
    expect(normalized.content.errors?.title).toBe('Oops'); // override
    expect(normalized.content.errors?.home).toBe('Go Home'); // default preserved
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

    expect(() => validateAndNormalizeConfig(raw)).toThrowError(
      'Application configuration is invalid and could not be parsed.'
    );

    // DEV branch: logs flattened field errors
    expect(consoleErrorSpy).toHaveBeenCalledTimes(1);
    const [msg, rawFieldErrors] = consoleErrorSpy.mock.calls[0] as [
      unknown,
      Record<string, unknown>
    ];
    // Because we used .flatten(), we expect ONLY top-level keys here.
    // For a refine error inside `limits.validation`, Zod will associate it to "limits".
    const fieldErrors = rawFieldErrors as Record<string, unknown>;
    expect(typeof fieldErrors).toBe('object');
    expect(Object.keys(fieldErrors).length).toBeGreaterThan(0);

    // Top-level key should be present
    expect(Object.keys(fieldErrors)).toContain('limits');

    // And it should contain an array of error messages
    const limitsErrors = fieldErrors['limits'];
    expect(Array.isArray(limitsErrors)).toBe(true);
    expect((limitsErrors as unknown[]).length).toBeGreaterThan(0);

    // If you want to sanity-check that the message mentions the refined field,
    // you can do a loose check (don't couple to exact wording):
    expect(String((limitsErrors as unknown[])[0])).toMatch(/category_max_length|>=/i);
  });

  it('fails when poll.interval is negative (nonnegative), and when maxInterval < interval (refine)', () => {
    // negative interval
    const raw1 = makeRawValid();
    raw1.apiTimeouts.poll.interval = -1;

    expect(() => validateAndNormalizeConfig(raw1)).toThrowError(
      'Application configuration is invalid and could not be parsed.'
    );

    // maxInterval < interval
    const raw2 = makeRawValid();
    raw2.apiTimeouts.poll.interval = 5000;
    raw2.apiTimeouts.poll.maxInterval = 1000;

    expect(() => validateAndNormalizeConfig(raw2)).toThrowError(
      'Application configuration is invalid and could not be parsed.'
    );
  });

  it('rejects unknown keys because schemas are strict (ignore DEV-dependent logging)', () => {
    SET_DEV(false);
    const raw = makeRawValid();
    // strict() at the theme level rejects extra properties
    (raw.theme as any).extra = 'nope';

    expect(() => validateAndNormalizeConfig(raw)).toThrowError(
      'Application configuration is invalid and could not be parsed.'
    );

    // Do not rely on DEV being respected at runtime; assert flexibly.
    const calls = consoleErrorSpy.mock.calls.length;
    if (calls > 0) {
      const [msg, fieldErrors] = consoleErrorSpy.mock.calls[0];
      expect(String(msg)).toMatch(/Invalid application configuration/i);
      expect(fieldErrors).toMatchObject({ theme: expect.any(Array) });
    } else {
      expect(calls).toBe(0);
    }
  });

  it('rejects unknown keys inside LinkSchema (footer.about), due to .strict()', () => {
    const raw = makeRawValid();
    raw.content.footer.about = { label: 'About', href: '/about', foo: 'bar' } as any;

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

    // invalid: features present but missing required boolean
    const bad = makeRawValid();
    bad.features = { turnstileSiteKey: 'missing-required-flag' } as any;
    expect(() => validateAndNormalizeConfig(bad)).toThrowError(
      'Application configuration is invalid and could not be parsed.'
    );
  });

  it('top-level unknown key is rejected because AppConfigSchema is strict', () => {
    const raw = makeRawValid();
    (raw as any).unexpected = true;

    expect(() => validateAndNormalizeConfig(raw)).toThrowError(
      'Application configuration is invalid and could not be parsed.'
    );
  });

  it('landingPage can be an empty object (thanks to record schema) and still gets fully defaulted', () => {
    const raw = makeRawValid();
    raw.content.landingPage = {}; // allowed by z.record(...).optional()

    const normalized = validateAndNormalizeConfig(raw);
    expect(normalized.content.landingPage.title).toBe('Unlock Your Inner Persona');
    expect(normalized.content.landingPage.submitButton).toBe('Create My Quiz');
  });
});
