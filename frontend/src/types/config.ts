// ----- Footer -----

export type FooterLink = {
  label: string;
  href: string;
  external?: boolean;
};

export type FooterConfig = {
  about: FooterLink;
  terms: FooterLink;
  privacy: FooterLink;
  donate: FooterLink;
  copyright?: string;
};

// ----- Static Pages / Content Blocks -----

export type StaticBlock =
  | { type: 'p'; text: string }
  | { type: 'h2'; text: string }
  | { type: 'ul'; items: string[] }
  | { type: 'ol'; items: string[] };

export type StaticPageConfig = {
  title: string;
  description?: string;
  blocks?: StaticBlock[];
};

// ----- Result Page -----

export type ResultPageConfig = {
  titlePrefix?: string;
  shareButton?: string;
  shareCopied?: string;
  startOverButton?: string;
  traitListTitle?: string;

  // Direct share + fallback labels
  shareText?: string; // text passed to the Web Share API
  shared?: string;    // acknowledgement after successful native share
  copyLink?: string;  // label for explicit copy fallback

  feedback?: {
    prompt?: string;
    thumbsUp?: string;
    thumbsDown?: string;
    commentPlaceholder?: string;
    submit?: string;
    thanks?: string;
    turnstileError?: string;
  };

  // Optional social metadata
  share?: {
    socialTitle?: string;
    socialDescription?: string;
  };
};

// ----- Errors / Loading / Not Found -----

export type ErrorsConfig = {
  title?: string;
  description?: string;
  retry?: string;
  home?: string;
  startOver?: string;
  categoryNotFound?: string;
  requestTimeout?: string;
  quizCreationFailed?: string;
  resultNotFound?: string;
  hideDetails?: string;
  details?: string;
  submissionFailed?: string;
};

export type LoadingStatesConfig = {
  page?: string;
  question?: string;
  quiz?: string;
};

export type NotFoundPageConfig = {
  heading?: string;
  subheading?: string;
  buttonText?: string;
};

// ----- Theme / Layout -----

export type LandingLayoutTokens = {
  // page paddings
  pagePtSm: string;
  pagePtMd: string;
  pagePtLg: string;

  // card
  cardMaxW: string;
  cardPadSm: string;
  cardPadMd: string;
  cardPadLg: string;
  cardRadius: string;
  cardShadow: string;

  // hero sizes
  heroHSm: string;
  heroHMd: string;
  heroHLg: string;

  // optional: color blob behind hero
  blobSizeSm?: string;
  blobSizeMd?: string;
  blobSizeLg?: string;
  blobOpacity?: string; // e.g. "0.18"

  // spacing + widths
  spaceAfterHeroSm: string;
  spaceAfterHeroMd: string;
  titleMaxW: string;
  subtitleMaxW: string;
  spaceTitleToSubtitleSm: string;
  spaceTitleToSubtitleMd: string;
  spaceSubtitleToFormSm: string;
  spaceSubtitleToFormMd: string;
  formMaxW: string;

  // input pill
  inputHeight: string;
  pillGap: string;
  pillPl: string;
  pillPad: string;
  pillBorder: string; // e.g. "1px solid rgba(var(--color-border), 1)"
  pillBg: string;     // e.g. "rgba(var(--color-bg), 0.7)"
  ringAlpha: string;  // e.g. "0.2"
  submitSize: string;

  // optional: title underline
  underlineWidth?: string;   // e.g. "8rem"
  underlineHeight?: string;  // e.g. "6px"
  underlineRadius?: string;  // e.g. "9999px"
};

export type ThemeConfig = {
  colors: Record<string, string>;
  fonts: Record<string, string>;
  /** Optional map of font-size tokens (e.g., landingTitle, body, etc.) */
  fontSizes?: Record<string, string>;
  /** Layout tokens grouped by surface/page */
  layout?: {
    landing?: LandingLayoutTokens;
  };
  dark?: {
    colors: Record<string, string>;
  };
};

// ----- API Timeouts -----

export type ApiTimeoutsConfig = {
  default: number;
  startQuiz: number;
  poll: {
    total: number;
    interval: number;
    maxInterval: number;
  };
};

// ----- Features (Turnstile alignment) -----

export type FeaturesConfig = {
  /**
   * AUTHORITATIVE flag provided by the backend.
   * If false, the frontend MUST NOT block the UX with Turnstile.
   */
  turnstile: boolean;

  /**
   * Deprecated legacy alias. The backend mirrors this to `turnstile`.
   * Present only for compatibility with older callers. Do not gate UX on this.
   */
  turnstileEnabled?: boolean;

  /** Optional site key used by the client Turnstile widget. */
  turnstileSiteKey?: string;
};

// ----- Content -----

export type ContentConfig = {
  appName: string;
  // For now this remains free-form to match existing configuration shape.
  landingPage: Record<string, any>;
  footer: FooterConfig;
  aboutPage: StaticPageConfig;
  termsPage: StaticPageConfig;
  privacyPolicyPage: StaticPageConfig;
  resultPage?: ResultPageConfig;
  errors?: ErrorsConfig;
  notFoundPage?: NotFoundPageConfig;
  loadingStates?: LoadingStatesConfig;
};

// ----- Top-level App Config -----

export type AppConfig = {
  theme: ThemeConfig;
  content: ContentConfig;
  limits: {
    validation: {
      category_min_length: number;
      category_max_length: number;
    };
  };
  apiTimeouts: ApiTimeoutsConfig;
  /**
   * Optional to avoid breaking older mocks; when present, `features.turnstile`
   * is the single source of truth for Turnstile behavior.
   */
  features?: FeaturesConfig;
};
