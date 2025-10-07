// frontend/src/utils/configNormalizer.spec.ts
import { describe, it, expect, vi, beforeEach, afterEach } from 'vitest';
import { validateAndNormalizeConfig, AppConfigSchema, type AppConfig } from './configNormalizer';

function makeValidConfig(): AppConfig {
  return {
    theme: {
      colors: {
        primary: '#123456',
        secondary: '#abcdef',
      },
      fonts: {
        body: 'Inter, sans-serif',
        heading: 'Georgia, serif',
      },
      dark: {
        colors: {
          primary: '#0a0a0a',
          secondary: '#1a1a1a',
        },
      },
    },
    content: {
      appName: 'Quizzical',
      landingPage: {
        heroTitle: 'Unlock your inner persona',
        ctaText: 'Create my quiz',
      },
      footer: {
        about:   { label: 'About',   href: '/about' },                  // external omitted (optional)
        terms:   { label: 'Terms',   href: '/terms',   external: false },
        privacy: { label: 'Privacy', href: '/privacy', external: true  },
        donate:  { label: 'Donate',  href: 'https://example.com' },     // external omitted (optional)
        copyright: '© 2025 Quizzical Inc.',
      },
      // Each StaticPage exercises a different branch of StaticBlockSchema union
      aboutPage: {
        title: 'About',
        blocks: [
          { type: 'p',  text: 'About our app.' },
          { type: 'h2', text: 'Mission' },
        ],
      },
      termsPage: {
        title: 'Terms',
        blocks: [
          { type: 'ul', items: ['Be nice', 'No spam'] },
        ],
      },
      privacyPolicyPage: {
        title: 'Privacy',
        blocks: [
          { type: 'ol', items: ['We collect X', 'We store Y'] },
        ],
      },
      // optional records present to exercise those paths
      resultPage: { titlePrefix: 'Your Persona:' },
      errors:     { resultNotFound: 'No result data found.' },
      notFoundPage: { message: 'Page not found' },
    },
    limits: {
      validation: {
        category_min_length: 3,
        category_max_length: 40,
      },
    },
  };
}

const DEV_GET = () => (import.meta as any).env?.DEV;
const SET_DEV = (value: boolean) => { (import.meta as any).env = { ...import.meta.env, DEV: value }; };

let originalDEV: boolean | undefined;
let consoleErrorSpy: ReturnType<typeof vi.spyOn>;

beforeEach(() => {
  originalDEV = DEV_GET();
  consoleErrorSpy = vi.spyOn(console, 'error').mockImplementation(() => {});
});

afterEach(() => {
  // restore env + spies
  if (typeof originalDEV !== 'undefined') SET_DEV(originalDEV);
  consoleErrorSpy.mockRestore();
  vi.restoreAllMocks();
});

describe('validateAndNormalizeConfig', () => {
  it('parses a fully valid config and preserves values', () => {
    const valid = makeValidConfig();

    const parsed = validateAndNormalizeConfig(valid);

    // Deep structural equality (zod may create new objects, but structure should match)
    expect(parsed).toStrictEqual(valid);

    // Spot-check a few fields to guard against accidental schema drift
    expect(parsed.theme.dark?.colors.primary).toBe('#0a0a0a');
    expect(parsed.content.footer.privacy.external).toBe(true);
    expect(parsed.content.aboutPage.blocks[0]).toEqual({ type: 'p', text: 'About our app.' });
    expect(parsed.limits.validation.category_max_length).toBe(40);
  });

  it('throws a friendly error and logs field errors in DEV mode', () => {
    // Force DEV true to exercise the logging branch
    SET_DEV(true);

    const invalid = makeValidConfig();
    // Break a required field (min(1)) to produce a ZodError
    invalid.content.footer.about.href = '';

    expect(() => validateAndNormalizeConfig(invalid)).toThrowError(
      'Application configuration is invalid and could not be parsed.'
    );

    // In DEV, a ZodError should trigger logging of flattened field errors
    expect(consoleErrorSpy).toHaveBeenCalledTimes(1);
    const [firstArg, secondArg] = consoleErrorSpy.mock.calls[0];
    expect(String(firstArg)).toMatch(/Invalid application configuration/i);
    expect(secondArg).toBeTypeOf('object'); // fieldErrors object from Zod flatten()
  });

  it('rethrows the friendly error but does not log for non-Zod errors (defensive branch)', () => {
  // Force a non-Zod error coming from parse()
  const parseSpy = vi
    .spyOn(AppConfigSchema, 'parse')
    // eslint-disable-next-line @typescript-eslint/no-explicit-any
    .mockImplementation((_: any) => { throw new Error('boom'); });

  expect(() => validateAndNormalizeConfig({} as unknown))
    .toThrowError('Application configuration is invalid and could not be parsed.');

  // Should NOT log because error is not a ZodError
  expect(consoleErrorSpy).not.toHaveBeenCalled();

  parseSpy.mockRestore();
  });
});
