// frontend/src/types/types.spec.ts
import { describe, it, expectTypeOf } from 'vitest';

// api.ts
import type { ApiError } from './api';

// components.ts
import type {
  Answer as CAnswer,
  Question as CQuestion,
  Synopsis as CSynopsis,
} from './components';

// config.ts
import type {
  FooterLink,
  FooterConfig,
  StaticBlock,
  StaticPageConfig,
  ResultPageConfig,
  ErrorsConfig,
  LoadingStatesConfig,
  NotFoundPageConfig,
  ContentConfig,
  ThemeConfig,
  ApiTimeoutsConfig,
  FeaturesConfig,
  AppConfig,
} from './config';

// pages.ts (derivatives over ContentConfig)
import type {
  StaticPageKey,
  StaticContentBlock,
  PageLink,
} from './pages';

// quiz.ts
import type {
  Answer,
  Question,
  Synopsis,
  Character,
  CharacterProfile,
  QuizStatus,
} from './quiz';

// result.ts
import type { Trait, ResultProfileData, FinalResultApi } from './result';

// turnstile.d.ts
import type { TurnstileOptions } from './turnstile.d.ts';


describe('types: compile-time integrity checks', () => {
  it('ApiError: all fields optional and well-typed', () => {
    const partial: ApiError = { message: 'oops' };
    const full: ApiError = {
      status: 400,
      code: 'E_BAD',
      message: 'Nope',
      retriable: true,
      details: { any: 'thing' },
    };
    void partial; void full;

    expectTypeOf<ApiError['status']>().toEqualTypeOf<number | undefined>();
    expectTypeOf<ApiError>().not.toBeAny();
  });

  it('components: Answer / Question / Synopsis shapes', () => {
    const a: CAnswer = { id: 'a1', text: 'Hello' };
    const q: CQuestion = { id: 'q1', text: 'Q', answers: [a] };
    const s: CSynopsis = { title: 'Title', summary: 'Summary' };
    void q; void s;

    expectTypeOf<CQuestion['answers'][number]>().toMatchTypeOf<CAnswer>();
  });

  it('config: FooterLink/FooterConfig requireds & optionals', () => {
    const link: FooterLink = { label: 'About', href: '/about' };
    const footer: FooterConfig = {
        about: link,
        terms: link,
        privacy: link,
        donate: link,
        // copyright optional
    };
    void footer;

    // Type-only assertions (no runtime identifier confusion)
    expectTypeOf<FooterLink['external']>().toEqualTypeOf<boolean | undefined>();
    expectTypeOf<FooterConfig['about']>().toEqualTypeOf<FooterLink>();
    expectTypeOf<FooterConfig['copyright']>().toEqualTypeOf<string | undefined>();
    });

  it('config: StaticBlock discriminated union + alias parity', () => {
    const p: StaticBlock = { type: 'p', text: 'x' };
    const ul: StaticBlock = { type: 'ul', items: ['a'] };
    void p; void ul;

    // StaticContentBlock in pages.ts mirrors StaticBlock
    expectTypeOf<StaticContentBlock>().toEqualTypeOf<StaticBlock>();
  });

  it('ContentConfig minimal shape w/ required static pages', () => {
    const page: StaticPageConfig = {
      title: 'T',
      blocks: [{ type: 'p', text: 'x' }],
    };

    const content: ContentConfig = {
      appName: 'Quizzical',
      landingPage: {},
      footer: {
        about: { label: 'a', href: '#' },
        terms: { label: 't', href: '#' },
        privacy: { label: 'p', href: '#' },
        donate: { label: 'd', href: '#' },
      },
      aboutPage: page,
      termsPage: page,
      privacyPolicyPage: page,
      // resultPage/errors/notFound/loadingStates optional
    };
    void content;

    expectTypeOf<StaticPageKey>()
      .toEqualTypeOf<'aboutPage' | 'termsPage' | 'privacyPolicyPage'>();
  });

  it('ThemeConfig/AppConfig composition is consistent', () => {
    const theme: ThemeConfig = {
      colors: { primary: '#000' },
      fonts: { body: 'Inter' },
      dark: { colors: { primary: '#111' } },
    };

    const apiTimeouts: ApiTimeoutsConfig = {
      default: 1000,
      startQuiz: 2000,
      poll: { total: 3000, interval: 100, maxInterval: 200 },
    };

    const app: AppConfig = {
      theme,
      content: {
        appName: 'Q',
        landingPage: {},
        footer: {
          about: { label: 'a', href: '#' },
          terms: { label: 't', href: '#' },
          privacy: { label: 'p', href: '#' },
          donate: { label: 'd', href: '#' },
        },
        aboutPage: { title: 'About', blocks: [{ type: 'p', text: 'x' }] },
        termsPage: { title: 'Terms', blocks: [{ type: 'p', text: 'y' }] },
        privacyPolicyPage: { title: 'Privacy', blocks: [{ type: 'p', text: 'z' }] },
      },
      limits: { validation: { category_min_length: 1, category_max_length: 10 } },
      apiTimeouts,
      features: { turnstileEnabled: false },
    };
    void app;

    expectTypeOf<AppConfig['features']>().toEqualTypeOf<FeaturesConfig | undefined>();
  });

  it('quiz: alias CharacterProfile === Character, and QuizStatus union', () => {
    expectTypeOf<CharacterProfile>().toEqualTypeOf<Character>();

    const finished: QuizStatus = {
      status: 'finished',
      type: 'result',
      data: { title: 't', imageUrl: null, description: 'desc' },
    };
    void finished;

    expectTypeOf<QuizStatus>().toMatchTypeOf<
      | { status: 'processing' | 'pending'; type: 'status' }
      | { status: 'finished'; type: 'result'; data: FinalResultApi }
    >();
  });

  it('result: FinalResultApi imageUrl is string|null', () => {
    const rp: ResultProfileData = { profileTitle: 'A', summary: 'B' };
    const fr: FinalResultApi = { title: 't', imageUrl: null, description: 'd' };
    void rp; void fr;

    expectTypeOf<FinalResultApi['imageUrl']>().toEqualTypeOf<string | null>();
  });

  it('pages: PageLink is a simple {href,label}', () => {
    const pl: PageLink = { href: '/', label: 'Home' };
    void pl;

    expectTypeOf<PageLink['href']>().toEqualTypeOf<string>();
    expectTypeOf<PageLink['label']>().toEqualTypeOf<string>();
  });

  it('turnstile.d.ts: global augmentation exists with expected signatures (type-only)', () => {
    // Assert the shape of Window['turnstile'] without touching runtime values
    expectTypeOf<Window['turnstile']>().toMatchTypeOf<{
        render: (element: HTMLElement | string, options: TurnstileOptions) => string;
        reset: (widgetId: string) => void;
        remove: (widgetId: string) => void;
        getResponse: (widgetId: string) => string | undefined;
    }>();

    // A couple of focused assertions if you want them:
    expectTypeOf<Window['turnstile']['render']>().parameters.toEqualTypeOf<
        [HTMLElement | string, TurnstileOptions]
    >();
    expectTypeOf<ReturnType<Window['turnstile']['render']>>().toEqualTypeOf<string>();
    });
});
