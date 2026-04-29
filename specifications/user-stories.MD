# Quizzical User Stories

This document captures the business objectives and user value outcomes enabled by Quizzical.

Format standard:
- Every story uses: "As a user, I can ... so that ..."
- Each story includes supporting bullet points to clarify value, constraints, and acceptance nuance.

## Discovery and Onboarding

### 1. Start from a clear landing experience
As a user, I can arrive on a clear landing page that explains what Quizzical does so that I quickly understand the product value and decide to try a quiz.
- The page communicates the product purpose in plain language.
- The core action (start a quiz) is immediately visible without hunting through navigation.
- Supporting pages like About, Terms, and Privacy are available for trust and context.

### 2. Enter my own quiz topic
As a user, I can provide a custom topic or category so that the quiz feels personally relevant instead of generic.
- Topic entry supports free-form language, not only preset templates.
- The app validates required input before submission.
- Error messages are understandable if input is missing or invalid.

### 3. Understand loading state during quiz creation
As a user, I can see progress/loading feedback while my quiz is being prepared so that I know the app is actively working and I should wait.
- Loading components reduce uncertainty and abandonment.
- The app avoids blank screens during generation.
- Transitions from loading to quiz content are clear and predictable.

### 3A. Pick from diverse suggested topics
As a user, in addition to entering any topic I choose, I can also see a randomly generated and highly diverse set of clickable topics so that I immediately understand what kinds of quizzes I can create and can launch one quickly from an example.
- The suggestions span distinct domains (for example: pop culture, careers, places, objects, personality systems, hobbies, food, and abstract prompts).
- Suggestions are randomized per load and support reshuffling so repeated visits feel fresh.
- Suggestions are directly actionable (click to populate the input and reduce effort).
- The suggestion UI remains clear and touch-friendly on mobile while still dense and scannable on desktop.

## Trust, Safety, and Abuse Prevention

### 4. Be protected from bot-driven abuse
As a user, I can complete a Turnstile challenge when required so that service quality is protected from automated abuse and spam.
- Human verification can be enabled or disabled via runtime config.
- Invisible mode can protect the flow with minimal friction.
- In development environments, controlled bypass behavior supports testing speed.

### 5. Use a service that respects security boundaries
As a user, I can use a quiz experience with secure request handling so that my interactions are less likely to be impacted by common web security issues.
- CORS is explicitly controlled by allowed origins.
- Security headers are present on live responses.
- Request metadata includes trace identifiers for incident triage.

### 6. Trust data usage expectations
As a user, I can access terms and privacy pages so that I can decide whether I am comfortable using the product.
- Policy pages are first-class routes, not hidden links.
- Legal content can be runtime-configured to stay current.
- Access to policy pages does not require quiz participation.

## Quiz Experience and Engagement

### 7. Receive an immediate synopsis before questions
As a user, I can get an initial synopsis at quiz start so that I understand the framing and tone before answering questions.
- The synopsis confirms the app understood my requested theme.
- The synopsis can be delivered even if deeper generation takes longer.
- The experience avoids waiting for all downstream generation to finish.

### 8. Progress from synopsis into active quiz mode
As a user, I can explicitly proceed from synopsis to questions so that I control when interactive questioning begins.
- Proceed action is intentional and user-driven.
- Backend work continues asynchronously after proceed.
- The UI reflects that processing may continue in the background.

### 9. Answer questions one step at a time
As a user, I can answer one active question at a time so that the flow stays simple and I always know what is expected next.
- The app prevents duplicate or out-of-order answer handling.
- The current question context remains visible while selecting an option.
- Answer submission provides feedback when complete.

### 10. Experience adaptive question generation
As a user, I can receive follow-up questions informed by prior answers so that the final result feels tailored to me.
- The backend can add new questions after initial baseline questions.
- Polling-based progression supports asynchronous generation without freezing the interface.
- The question stream can end naturally when confidence is sufficient.

### 11. Resume an in-progress quiz
As a user, I can recover my active quiz session after refresh or accidental navigation so that I do not lose progress.
- Session state is stored in browser session storage for short-lived recovery.
- Recovery logic validates against backend status rather than trusting stale client-only state.
- Expired sessions are handled gracefully with clear fallback behavior.

### 12. Handle temporary processing delays
As a user, I can continue through a processing state while content is generated so that occasional backend latency does not feel like a crash.
- Polling returns explicit processing status.
- UI distinguishes waiting states from fatal failures.
- The flow transitions automatically when new data is ready.

## Result Value and Shareability

### 13. Receive a coherent final profile result
As a user, I can receive a final quiz result with title and explanation so that I get a satisfying payoff for completing the quiz.
- Result output includes interpretable text, not only a label.
- Category context is preserved for relevance.
- Result pages support both warm state and cold reload retrieval.

### 14. Access result via direct link
As a user, I can open a shareable result URL so that I can revisit or share my outcome with others.
- Result routes support direct access by result/session identifier.
- Retrieval works when opening links in a fresh browser session.
- Missing or unauthorized result IDs fail gracefully.

### 15. Copy and share my result
As a user, I can copy a share link from the result page so that I can distribute my quiz outcome quickly on social or chat platforms.
- Copy behavior uses the current browser origin for correctness.
- Feedback confirms copy success.
- The feature remains available without requiring account creation.

### 16. See only relevant feedback controls
As a user, I can provide feedback only for my active session result so that feedback quality stays meaningful and abuse is reduced.
- Feedback controls are scoped to the local active session context.
- Shared or historical views do not incorrectly allow unrelated feedback submission.
- Feedback sentiment options are clear and low-friction.

## Feedback and Product Learning

### 17. Submit sentiment on result quality
As a user, I can rate a result positively or negatively so that the product team can measure perceived result quality.
- Submission is fast and lightweight.
- The API stores normalized sentiment values.
- The response path is resilient and does not block normal browsing if feedback fails.

### 18. Add optional comments to feedback
As a user, I can include optional text with feedback so that I can explain why a result felt accurate or off-target.
- Comment field is optional, reducing effort for users who only want quick sentiment.
- Comment payload supports qualitative analysis for iteration.
- Input handling protects system stability and validation integrity.

### 19. Trust that feedback is tied to the correct quiz
As a user, I can submit feedback against a quiz identifier so that my feedback is attached to the right result context.
- Feedback payload references quiz/session identity.
- Backend persistence links sentiment to historical session records.
- Invalid IDs are rejected with clear API errors.

## Reliability and Error Resilience

### 20. Recover from runtime frontend errors
As a user, I can be shown a controlled fallback UI if a render error occurs so that I am not left with a broken blank screen.
- Error boundaries catch rendering failures.
- Users are offered retry/reset behavior where possible.
- Critical errors are surfaced without exposing internal stack traces.

### 21. Understand configuration load failures
As a user, I can see explicit configuration load errors and retry options so that startup failures are actionable rather than silent.
- Config bootstrap has a dedicated error path.
- Retry controls avoid mandatory hard refreshes.
- Local development can use guarded fallback defaults where appropriate.

### 22. Continue with stable API error handling
As a user, I can receive consistent error responses and UI handling so that failures are understandable and less frustrating.
- API errors are normalized to a predictable shape.
- Timeout and cancellation behavior is distinguished from server errors.
- Retriable conditions can be surfaced to support recovery.

### 23. Use a service with health and readiness protections
As a user, I can rely on infrastructure that checks health/readiness so that unstable releases are less likely to impact my session.
- Liveness and readiness endpoints support deployment gating.
- Dependency failures can block unhealthy rollout states.
- Observability metadata supports faster incident response.

## Performance and UX Quality

### 24. Use a responsive interface across routes
As a user, I can navigate quickly between pages and states so that the app feels modern and trustworthy.
- Route transitions preserve context where appropriate.
- Scroll and focus management improves accessibility and perceived polish.
- Content rendering is structured to avoid jarring layout shifts.

### 25. Experience runtime theming and content personalization
As a user, I can see app theme/content adjusted by backend configuration so that branding and messaging can be improved without redeploying frontend code.
- Theme tokens are injected at runtime through CSS variables.
- Content strings can be managed centrally.
- Feature flags can be toggled without rebuilding the entire frontend.

### 26. Use the app on shared/public deployments safely
As a user, I can access the frontend and API through production domains so that I can use the product reliably outside local development.
- Deployment pipelines verify live health and readiness.
- CORS and environment settings are validated against real frontend origin.
- Post-deploy smoke checks catch regressions before users do.

## Operational and Business Outcomes (User-Visible Effects)

### 27. Benefit from continuous quality improvements
As a user, I can use an app that continuously improves based on telemetry, tests, and feedback so that each release is more reliable and relevant.
- Unit, integration, and smoke checks reduce regressions.
- Deployment checks validate production-like behavior, not only local assumptions.
- Feedback loops drive feature prioritization based on real user value.

### 28. Rely on stable share and revisit behavior
As a user, I can return to prior results through durable storage-backed retrieval so that quiz outcomes remain useful beyond a single session.
- Completed results are persisted in the backend.
- Retrieval supports cold starts and external link opens.
- Session persistence and result persistence complement each other.

### 29. Feel confident the app protects my interaction flow
As a user, I can complete the quiz journey with guardrails against common failure modes so that my effort is not wasted by preventable technical issues.
- Session recovery reduces accidental loss.
- Async polling and background generation maintain progress under load.
- Deployment controls reduce chances of environment/config drift impacting users.

### 30. Get entertaining, personalized outcomes quickly
As a user, I can turn an idea into a personalized quiz result in a short flow so that I receive immediate entertainment value and social sharing potential.
- Topic-to-result loop is optimized for low friction.
- Result quality is informed by adaptive questioning.
- Sharing and feedback paths extend product engagement beyond one completion.

## Story Coverage Notes

The stories above represent value delivered across:
- Acquisition and first-run activation
- Core quiz interaction and completion
- Result retention and sharing
- Feedback-driven product iteration
- Reliability, security, and deployment quality as experienced by end users

These stories should be used as a living product-value baseline and updated whenever routes, session behavior, config loading, API contracts, or deployment guarantees change.

## Spring 2026 UX Refinement Stories

### 31. Keep orientation with a stable top identity bar
As a user, I can always see a lightweight app header while scrolling so that I keep context and can restart quickly without hunting for navigation.
- The header stays visible without stealing too much vertical space on mobile.
- The header remains legible over changing backgrounds.
- The home action remains reachable by keyboard and touch.

### 32. Feel visual depth without visual noise
As a user, I can experience subtle layered backgrounds and card separation so that the app feels modern and premium while still easy to scan.
- Background treatments stay low-contrast and do not reduce text readability.
- Primary cards remain visually dominant over decorative layers.
- Decorative effects are non-blocking and purely cosmetic.

### 33. Quickly recover from category input mistakes
As a user, I can clear my topic input with one tap so that I can correct mistakes quickly on mobile keyboards.
- A clear control appears only when there is text.
- The clear action has an explicit accessible label.
- Clearing preserves focus in the input for rapid retry.

### 34. Understand trust and friction expectations before submit
As a user, I can see a short helper line near quiz creation so that I know there is no signup and what verification to expect.
- Helper copy is concise and non-alarming.
- The line does not distract from the primary CTA.
- It remains readable at small viewport widths.

### 35. Understand question progress at a glance
As a user, I can see both current step and total steps with a compact progress bar so that I can estimate remaining effort.
- Progress communicates both numeric and visual state.
- Progress updates immediately when the next question loads.
- Progress remains accessible to assistive technologies.

### 36. Confidently identify selected answers
As a user, I can see a clear selected-answer indicator (not color alone) so that I know what will be submitted.
- Selected state includes iconographic reinforcement.
- Disabled/loading selected states remain distinguishable.
- Selection signifiers work with keyboard and screen readers.

### 37. Read and resolve input errors faster
As a user, I receive inline errors in a dedicated alert treatment so that issues are noticeable and understandable without scanning the whole page.
- Error text uses `role="alert"` semantics.
- Error visual treatment is distinct but not hostile.
- Retry pathways remain obvious and nearby.

### 38. Get immediate share-result feedback
As a user, I can hear/see status updates when sharing or copying my result so that I know whether the action succeeded.
- Share status is announced via assistive technology-friendly semantics.
- Temporary confirmation states automatically settle back.
- Primary actions remain available after confirmation fades.

### 39. Tap controls comfortably on small screens
As a user, interactive controls maintain at least mobile-friendly target sizes so that accidental taps are reduced.
- Critical controls remain >= 44px in at least one dimension.
- Dense areas preserve separation between adjacent controls.
- Focus indicators remain visible and unobscured.

### 40. Experience consistent micro-interactions across routes
As a user, buttons/chips/tiles animate and focus consistently so that the interface feels coherent and trustworthy.
- Hover/active/focus states use consistent timing and visual language.
- Reduced-motion preference remains honored.
- Interaction styling never overrides readability.

## Spring 2026 Landing Refresh Stories

### 41. Read a sophisticated, mascot-free landing
As a user, I arrive on a landing page that feels modern and adult so that I trust the product before I type anything.
- AC-LANDING-41-1: The header renders only a wordmark; no `WizardCatIcon` or other illustration appears in the header chrome.
- AC-LANDING-41-2: The hero card renders no mascot, no decorative blob, and no gradient title underline.
- AC-LANDING-41-3: The header is at most 56px tall on desktop; the wordmark button preserves a 44px tap target.

### 42. See exactly what to type and what I will get back
As a user, the landing page tells me both what kind of input it expects and what kind of output I will receive so that I do not have to guess.
- AC-LANDING-42-1: The subtitle explicitly identifies the product as a "personality quiz" and describes the input as "a noun" or "a subject".
- AC-LANDING-42-2: The subtitle does not promise a specific completion time (e.g., no "in seconds" / "under a minute" claim).
- AC-LANDING-42-3: The input field is wrapped in a visible `Which [____] am I?` composition so the question shape is self-evident.

### 43. Pick a starting point from question-shaped suggestions
As a user, I can pick from a cloud of full-question suggestion chips so that I see real examples of what the input is for.
- AC-LANDING-43-1: At least 12 chips render directly below the input pill, with no intervening label, helper text, or shuffle button.
- AC-LANDING-43-2: Each chip reads `Which {noun phrase} am I?` rather than a bare keyword.
- AC-LANDING-43-3: Clicking a chip populates the input with the bare noun phrase only (e.g., `Hogwarts house`, not `Which Hogwarts house am I?`).
- AC-LANDING-43-4: Suggested noun phrases are personality-quiz prompts (characters, archetypes, types) rather than single-word topics.

### 44. Trust that the visible title carries weight
As a user, I am not shown decorative headlines that add no information so that the page reads as deliberate, not generic.
- AC-LANDING-44-1: No visible `<h1>` headline ("Discover Your True Personality" or otherwise) is rendered above the question frame.
- AC-LANDING-44-2: The configured `landingPage.title` value remains available in config for SEO/document metadata but is not painted as visible body content.

### 45. See breadth of possible topics through a rotating placeholder
As a user, I see the input placeholder cycle through a wide variety of personality-quiz prompts so that I immediately understand the breadth of supported subjects.
- AC-LANDING-45-1: A pool of at least 1,000 unique personality-quiz noun phrases backs the placeholder rotation.
- AC-LANDING-45-2: The first placeholder shown on each page load is a random selection from the pool (random start).
- AC-LANDING-45-3: Each rotation tick selects a new pool entry that is not equal to the immediately previous value (random next, no repeat).
- AC-LANDING-45-4: Rotation pauses when the input is focused, contains text, or the page is submitting; the configured `landingPage.placeholder` is shown as a calm fallback during pause.
- AC-LANDING-45-5: Rotation respects `prefers-reduced-motion: reduce` by holding a single random pick for the duration of the visit.

### 46. Read the landing page comfortably at any device size
As a user on a small phone, the question composition, subtitle, and input remain legible and tappable without horizontal scrolling.
- AC-LANDING-46-1: The subtitle and `Which __ am I?` composition use fluid `clamp()` typography so font size scales smoothly between mobile and desktop.
- AC-LANDING-46-2: On viewports under 480px the input pill wraps onto its own line below the "Which" word, preserving a touch target of at least 44px in height.
- AC-LANDING-46-3: No horizontal overflow occurs at viewport widths down to 320px.
- AC-LANDING-46-4: The number of suggested-topic chips visible scales with viewport width: ≤8 chips below 480px, ≤12 chips at ≥480px, ≤24 chips at ≥640px, ≤36 chips at ≥1024px, and ≤48 chips at ≥1280px.

### 47. Recognise the subtitle as a deliberate design element
As a user, I see the subtitle styled as playful display text with personality so the page feels fun and current rather than generic.
- AC-LANDING-47-1: The subtitle is rendered in a playful rounded display family (Baloo 2) distinct from the rest of the UI body type.
- AC-LANDING-47-2: Suggested-topic chips and rotating placeholder examples use standardized capitalization: every entry begins with an uppercase letter (or non-letter character such as `9`/`&`); proper nouns are preserved, and remaining words are lowercase.
- AC-LANDING-47-3: The input pill border is visibly darker than the surrounding card background in the idle state so the input is immediately discoverable.

### 48. See a calmer, denser landing page chrome
As a user on a desktop, the page chrome stays out of the way: the header is slim, the hero card has tight padding, and the suggestion grid is dense enough to fit three or more chips per row.
- AC-LANDING-48-1: The site header is at most 40px tall on all viewports and does not render a dark bottom divider line.
- AC-LANDING-48-2: The landing hero card uses tight horizontal padding (≤2rem) and a max width of at least 60rem so suggestion chips comfortably fit at least three per row at viewport widths ≥1024px.
- AC-LANDING-48-3: The page background uses a vertical (top-to-bottom) gradient, not a left-to-right or corner-anchored gradient.
- AC-LANDING-48-4: The hero card border is a light slate/neutral grey (not black) and is paired with a soft layered drop shadow that elevates the surface from the background.

### 49. See fresh high-volume topic ideas on every visit
As a user, I see a fresh set of topic examples each time the landing page loads, drawn from a very large catalog, so the page feels broad and alive.
- AC-LANDING-49-1: Suggestion chips are sampled from a deduplicated pool of at least 2,000 topics.
- AC-LANDING-49-2: The sampled chip set is randomized once per page load so two reloads are very likely to show different examples.
- AC-LANDING-49-3: Clicking any chip still writes only the bare noun phrase into the input field.
