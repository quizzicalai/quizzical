// src/types/config.ts

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
  | { type: "p"; text: string }
  | { type: "h2"; text: string }
  | { type: "ul"; items: string[] }
  | { type: "ol"; items: string[] };

// The configuration for a single static page (About, Terms, etc.)
export type StaticPageConfig = {
  title: string;
  blocks: StaticBlock[];
};

// Configuration for all labels and text on the result page
export type ResultPageConfig = {
  titlePrefix?: string;
  shareButton?: string;
  shareCopied?: string;
  startOverButton?: string;
  traitListTitle?: string;
  // UPDATE THIS SECTION: Make feedback properties optional
  feedback?: {
    prompt?: string;
    thumbsUp?: string;
    thumbsDown?: string;
    commentPlaceholder?: string;
    submit?: string;
    thanks?: string;
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
};

// The structure for the 'theme' part of the config
export type ThemeConfig = {
    colors: Record<string, string>;
    fonts: Record<string, string>;
    dark?: {
        colors: Record<string, string>;
    }
};

// The top-level application configuration object
export type AppConfig = {
  theme: ThemeConfig;
  content: ContentConfig;
  limits: {
    validation: {
        category_min_length: number;
        category_max_length: number;
    }
  }
};