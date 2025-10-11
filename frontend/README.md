# PLAN FOR LOADING STATE

Awesome — let’s ship this in small, safe slices with Playwright-CT only (no API calls), and end up with a reusable “loading narration” that slots into both the Landing and Quiz pages.

# Plan (small, testable steps)

## Phase 0 — Prep (no UX change)

**Goal:** Extract the landing “card” shell so we can reuse it on `/quiz`.

1. **Extract `HeroCard`** from `LandingPage` (keeps hero + padding + tokens).
2. Keep existing behavior; all tests still pass.

**CT tests**

* `HeroCard renders hero and centers content` (visual snapshot with reduced motion).
* `No layout shift across breakpoints` (measure height/width at sm/md/lg).

---

## Phase 1 — Add Loading Narration (component only)

**Goal:** Implement a self-contained loading strip: whimsical sprite (constant cadence) + time-based text (0–3 “Thinking…”, 3–6 “Researching…”, 6–9 “Determining…”, 9–12 “Writing…”, 13+ “Preparing…”).
**Important:** Sprite animation is independent of text timing. Honors `prefers-reduced-motion`.

### New components (drop-in)

#### `components/loading/WhimsySprite.tsx`

```tsx
import React from 'react';
import clsx from 'clsx';

/** Decorative sprite: independent loop; respects reduced motion. */
export function WhimsySprite({ className }: { className?: string }) {
  return (
    <div
      aria-hidden="true"
      className={clsx(
        'relative h-10 w-10 shrink-0',
        'rounded-full bg-[rgb(var(--color-primary)/0.12)]',
        'before:absolute before:inset-0 before:m-auto before:h-6 before:w-6 before:rounded-full before:bg-[rgb(var(--color-primary))]',
        'motion-safe:animate-wobble',
        className
      )}
      data-testid="whimsy-sprite"
    />
  );
}
```

Add a tiny keyframe to your CSS (respects `prefers-reduced-motion` automatically via `motion-safe`):

```css
@keyframes wobble {
  0%   { transform: translateY(0) rotate(0deg) scale(1); }
  25%  { transform: translateY(-2px) rotate(-3deg) scale(1.02); }
  50%  { transform: translateY(0) rotate(0deg) scale(1); }
  75%  { transform: translateY(2px) rotate(3deg) scale(0.98); }
  100% { transform: translateY(0) rotate(0deg) scale(1); }
}
.motion-safe\:animate-wobble { animation: wobble 1200ms ease-in-out infinite; }
```

#### `components/loading/LoadingNarration.tsx`

```tsx
import React from 'react';

type Line = { atMs: number; text: string };

const DEFAULT_LINES: Line[] = [
  { atMs:    0, text: 'Thinking…' },
  { atMs: 3000, text: 'Researching topic…' },
  { atMs: 6000, text: 'Determining characters…' },
  { atMs: 9000, text: 'Writing character profiles…' },
  { atMs:13000, text: 'Preparing topic…' },
];

export type LoadingNarrationProps = {
  /** Millisecond schedule; last line repeats until done */
  lines?: Line[];
  /** Called once when text changes (optional; handy for analytics) */
  onChangeText?: (t: string) => void;
  /** For tests: tick period; default 250ms */
  tickMs?: number;
};

export function LoadingNarration({ lines = DEFAULT_LINES, onChangeText, tickMs = 250 }: LoadingNarrationProps) {
  const startRef = React.useRef<number>(performance.now());
  const [text, setText] = React.useState(lines[0]?.text ?? 'Loading…');

  React.useEffect(() => {
    let last = '';
    const i = setInterval(() => {
      const elapsed = performance.now() - startRef.current;
      const current = lines.reduce((acc, l) => (elapsed >= l.atMs ? l : acc), lines[0]!);
      if (current.text !== last) {
        last = current.text;
        setText(current.text);
        onChangeText?.(current.text);
      }
    }, tickMs);
    return () => clearInterval(i);
  }, [lines, onChangeText, tickMs]);

  return (
    <div className="flex items-center gap-3" role="status" aria-live="polite" data-testid="loading-narration">
      {/* sprite lives beside text, not bound to text transitions */}
      <span className="sr-only">Loading</span>
      {/* The sprite itself is decorative */}
      {/* Consumers place <WhimsySprite/> beside this component */}
      <span className="text-sm text-[rgb(var(--color-muted))]">{text}</span>
    </div>
  );
}
```

#### `components/loading/LoadingCard.tsx`

```tsx
import React from 'react';
import { WizardCatIcon } from '../../assets/icons/WizardCatIcon';
import { WhimsySprite } from './WhimsySprite';
import { LoadingNarration } from './LoadingNarration';

export function HeroCard({ children }: { children: React.ReactNode }) {
  return (
    <div className="w-full mx-auto lp-card flex flex-col justify-center pt-4 sm:pt-6 md:pt-8 lg:pt-10 pb-12 sm:pb-16 md:pb-20 lg:pb-24 min-h-[50vh] sm:min-h-[55vh] md:min-h-[60vh] lg:min-h-[66vh]">
      <div className="text-center">
        <div className="flex justify-center lp-space-after-hero">
          <span className="lp-hero-wrap">
            <span className="lp-hero-blob" />
            <WizardCatIcon className="lp-hero" aria-label="Wizard cat reading a book" />
          </span>
        </div>
        {children}
      </div>
    </div>
  );
}

export function LoadingCard() {
  return (
    <HeroCard>
      <div className="flex items-center justify-center">
        <div className="inline-flex items-center gap-3">
          <WhimsySprite />
          <LoadingNarration />
        </div>
      </div>
    </HeroCard>
  );
}
```

**CT tests (component-only)**

* `LoadingNarration transitions at 0/3/6/9/13s` (use fake timers; assert text).
* `Sprite runs independently` (emulate reduced motion → narration still changes).
* `A11y contract`: `role="status"` + polite live region; no Axe violations.
* `Visual snapshot` of `LoadingCard` with motion disabled.

---

## Phase 2 — Use LoadingCard on **LandingPage** submit (no backend change)

**Goal:** After the user submits (and passes Turnstile), replace the title/subtitle/form with the `LoadingCard`’s center strip **inside the same card** until navigation occurs. No layout jump.

**Minimal diff in `LandingPage.tsx`** (replace the inline `<Spinner className="mt-4" />`):

```tsx
{/* ...inside the card... */}
{isSubmitting ? (
  <div className="flex justify-center mt-8" data-testid="lp-loading-inline">
    <div className="inline-flex items-center gap-3">
      <WhimsySprite />
      <LoadingNarration />
    </div>
  </div>
) : (
  /* existing title/subtitle/form block */
)}
```

**CT tests**

* `LandingPage submit → shows inline narration until navigate` (mount with MemoryRouter, fake timers, assert text changes; then simulate startQuiz resolve → expect navigation intent call already covered by your mock).

---

## Phase 3 — Use LoadingCard on **QuizFlowPage** while “processing”

**Goal:** Long wait happens here. Swap the current spinner branch for `LoadingCard`. We’ll key off `currentView === 'idle' || (isPolling && !isSubmittingAnswer)` exactly where you render `<Spinner />`.

**Minimal diff in `QuizFlowPage.tsx`**:

```tsx
// replace:
if (currentView === 'idle' || (isPolling && !isSubmittingAnswer)) {
  // return <Spinner message={loadingContent.quiz || 'Preparing your quiz...'} />;
  return (
    <main className="flex items-center justify-center flex-grow" data-testid="quiz-loading-card">
      <div className="lp-wrapper w-full flex items-start justify-center p-4 sm:p-6">
        <LoadingCard />
      </div>
    </main>
  );
}
```

**CT tests (store mocked, no API)**

* `Shows LoadingCard while isPolling=true` → narration ticks 0→3→6s.
* `Stops immediately when backend state flips` → simulate store change to `synopsis` at 5s; narration stops and view switches.
* `No CLS between loading and synopsis` → measure `.lp-card` height during loading vs after synopsis render (diff ≤ 2px).
* `Reduced motion respects animation` → emulate and snapshot.

---

## Phase 4 — Guardrails & Telemetry (optional, tiny)

* Add a `useLoadingTelemetry('quiz-initial')` hook (as sketched earlier) to record `time_to_first_label_ms` and `duration_until_content_ms`.
* Budget SLOs later; not required for v0.

---

## Phase 5 — Remove legacy spinner paths (cleanup)

* Replace any remaining `<Spinner/>` in these two flows with narration; keep `<Spinner/>` generic for background mini-loads elsewhere.

---

# Playwright-CT examples (ready to paste)

### 1) Unit-ish: narration schedule

```ts
import { test, expect } from '@playwright/experimental-ct-react';
import React from 'react';
import { LoadingNarration } from '../../src/components/loading/LoadingNarration';

test.describe('<LoadingNarration />', () => {
  test('transitions 0/3/6/9/13s', async ({ mount, page }) => {
    await page.emulateMedia({ reducedMotion: 'reduce' });
    await mount(<LoadingNarration tickMs={50} />);

    const label = page.getByTestId('loading-narration');

    await expect(label).toContainText('Thinking…');
    await page.waitForTimeout(3100);
    await expect(label).toContainText('Researching topic…');
    await page.waitForTimeout(3000);
    await expect(label).toContainText('Determining characters…');
    await page.waitForTimeout(3000);
    await expect(label).toContainText('Writing character profiles…');
    await page.waitForTimeout(4000);
    await expect(label).toContainText('Preparing topic…');
  });
});
```

### 2) Component visual: `LoadingCard`

```ts
import { test, expect } from '@playwright/experimental-ct-react';
import React from 'react';
import { LoadingCard } from '../../src/components/loading/LoadingCard';

test('LoadingCard visual (reduced motion)', async ({ mount, page }) => {
  await page.emulateMedia({ reducedMotion: 'reduce' });
  const cmp = await mount(<LoadingCard />);
  await expect(cmp).toHaveScreenshot('loading-card.png');
});
```

### 3) Integration on LandingPage

```ts
import { test, expect } from '@playwright/experimental-ct-react';
import React from 'react';
import { MemoryRouter } from 'react-router-dom';
import { LandingPage } from '../../src/pages/LandingPage';
import './fixtures/config';

test('submit → inline narration appears before navigate', async ({ mount, page }) => {
  await page.emulateMedia({ reducedMotion: 'reduce' });
  await mount(<MemoryRouter><LandingPage /></MemoryRouter>);

  await page.getByLabel(/quiz topic/i).fill('cats');
  await page.getByRole('button', { name: /create|generate/i }).click();
  await page.getByTestId('turnstile').click(); // satisfy mock

  await page.getByRole('button', { name: /create|generate/i }).click();
  await expect(page.getByTestId('lp-loading-inline')).toBeVisible();
  await expect(page.getByTestId('loading-narration')).toContainText(/Thinking/i);
});
```

### 4) Integration on QuizFlowPage (store mocked)

(You already mock `useQuiz*` in CT. Extend the mock to expose a test handle that flips `currentView`.)

```ts
import { test, expect } from '@playwright/experimental-ct-react';
import React from 'react';
import { MemoryRouter } from 'react-router-dom';
import { QuizFlowPage } from '../../src/pages/QuizFlowPage';
import * as store from '../mocks/quizStore.view.mock'; // new: mock that starts in { currentView:'idle', isPolling:true }

test('QuizFlowPage shows narration while polling, then flips to synopsis', async ({ mount, page }) => {
  await page.emulateMedia({ reducedMotion: 'reduce' });
  await mount(<MemoryRouter><QuizFlowPage /></MemoryRouter>);

  await expect(page.getByTestId('quiz-loading-card')).toBeVisible();
  await expect(page.getByTestId('loading-narration')).toContainText(/Thinking/i);

  // Advance fake time in the browser env (or just wait)
  await page.waitForTimeout(3200);
  await expect(page.getByTestId('loading-narration')).toContainText(/Researching/i);

  // Flip store to synopsis (no API)
  await page.evaluate(() => (window as any).__ct_setQuizView?.({ currentView: 'synopsis', isPolling: false, viewData: { title: 't', summary: 's' } }));
  await expect(page.getByTestId('quiz-loading-card')).toBeHidden();
  await expect(page.getByText(/start|proceed/i)).toBeVisible(); // synopsis view affordance visible
});
```

> If you prefer not to create another mock file, you can extend your existing `quizStore.mock.ts` with a `__ct_setQuizView` bridge similar to your `__ct_setNextStartQuizError`.

---

# Why this works

* **No backend calls in tests.** All timing is driven by the component’s internal clock; store transitions are simulated.
* **No duplication.** One `LoadingCard` used on Landing and Quiz pages yields consistent visuals.
* **Deterministic visuals.** We snapshot with `reducedMotion` so the sprite doesn’t flake the diffs.
* **A11y-safe.** `role="status"` + polite live region; sprite is decorative.
* **Layout safe.** The card shell is identical between states → near-zero CLS; we assert it.

If you want, I can also provide the tiny `quizStore.view.mock.ts` helper used in test #4 and a one-liner analytics hook to log label transitions (`onChangeText`).



