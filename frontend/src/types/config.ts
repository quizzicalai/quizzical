// frontend/src/types/config.ts

// A single link object for the footer
export type FooterLink = {
  label: string;
  href: string;
  external?: boolean;
};

// The complete footer configuration
export type FooterConfig = {
  about: FooterLink;
  terms: FooterLink;
  privacy: FooterLink;
  donate: FooterLink;
  copyright?: string;
};

// A block of content for a static page (e.g., a paragraph, a heading)
export type StaticBlock =
  | { type: 'p'; text: string }
  | { type: 'h2'; text: string }
  | { type: 'ul'; items: string[] }
  | { type: 'ol'; items: string[] };

// The configuration for a single static page (About, Terms, etc.)
// NOTE: description and blocks are optional to match defaultAppConfig
export type StaticPageConfig = {
  title: string;
  description?: string;
  blocks?: StaticBlock[];
};

// Configuration for all labels and text on the result page
export type ResultPageConfig = {
  titlePrefix?: string;
  shareButton?: string;
  shareCopied?: string;
  startOverButton?: string;
  traitListTitle?: string;
  feedback?: {
    prompt?: string;
    thumbsUp?: string;
    thumbsDown?: string;
    commentPlaceholder?: string;
    submit?: string;
    thanks?: string;
    turnstileError?: string;
  };
  share?: {
    socialTitle?: string;
    socialDescription?: string;
  };
};

// Configuration for all user-facing error messages
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

// Configuration for loading state messages
export type LoadingStatesConfig = {
  page?: string;
  question?: string;
  quiz?: string;
};

// Configuration for the 404 Not Found page
export type NotFoundPageConfig = {
  heading?: string;
  subheading?: string;
  buttonText?: string;
};

// ---- NEW: Layout tokens for the Landing page ----
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
  pillBorder: string; // full CSS value (e.g. "1px solid rgba(var(--color-border), 1)")
  pillBg: string;     // full CSS value (e.g. "rgba(var(--color-bg), 0.7)")
  ringAlpha: string;  // e.g. "0.2"
  submitSize: string;

  // optional: title underline
  underlineWidth?: string;   // e.g. "8rem"
  underlineHeight?: string;  // e.g. "6px"
  underlineRadius?: string;  // e.g. "9999px"
};

// The structure for the 'theme' part of the config
export type ThemeConfig = {
  colors: Record<string, string>;
  fonts: Record<string, string>;
  /** Optional map of font-size tokens (e.g., landingTitle, body, etc.) */
  fontSizes?: Record<string, string>;
  /** NEW: layout tokens grouped by surface/page */
  layout?: {
    landing?: LandingLayoutTokens;
  };
  dark?: {
    colors: Record<string, string>;
  };
};

// New: Defines the shape for all API timeout configurations
export type ApiTimeoutsConfig = {
  default: number;
  startQuiz: number;
  poll: {
    total: number;
    interval: number;
    maxInterval: number;
  };
};

// New: Feature flags/config coming from the backend /config endpoint
export type FeaturesConfig = {
  turnstileEnabled: boolean;
  turnstileSiteKey?: string;
};

// The complete structure for the 'content' part of the config
export type ContentConfig = {
  appName: string;
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

// The top-level application configuration object
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
  features?: FeaturesConfig; // kept optional to avoid breaking older configs
};
