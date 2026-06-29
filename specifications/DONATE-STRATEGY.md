# Quafel — Donation Strategy & Setup Guide

A research-backed plan for adding donations to **Quafel**, an open, free, no-auth AI personality-quiz web app. The audience is thousands of casual visitors who just received a fun, shareable result — so the strategy is built around a single high-quality moment, not a nag.

> **Scope note:** This is an authoring/strategy document only. It does not change any app code. Code snippets below are illustrative copy-paste starting points for whoever implements the CTA.

---

## TL;DR (Summary)

- **Recommended primary platform: [Ko-fi](https://ko-fi.com/).** It charges **0% platform fee on tips/donations**, donors can give with **just an email + card (no Ko-fi account required)**, setup takes minutes with no backend, and a hosted "tip" page fits a minimalist aesthetic. You only pay the underlying Stripe/PayPal processing fee (~2.9% + $0.30). ([Owelet: Ko-fi fees 2026](https://owelet.app/blog/ko-fi-fees-2026), [Talks.co: Ko-fi vs BMC](https://talks.co/p/kofi-vs-buy-me-a-coffee/))
- **Recommended fallback: [Stripe Payment Links](https://stripe.com/payments/payment-links) (Customers-choose-what-to-pay).** Fully Stripe-hosted, no backend, no third-party platform cut — you pay only Stripe's standard 2.9% + $0.30. Use this if you want zero intermediary and full brand control of the checkout, or if Ko-fi is ever unavailable. ([Stripe: pay-what-you-want](https://docs.stripe.com/payments/checkout/pay-what-you-want), [Stripe pricing](https://stripe.com/pricing))
- **Recommended placement: a tasteful, dismissible post-result CTA** shown *after* the quiz result renders (the "peak-end" moment of delight), below the result and its share buttons — never blocking the result, never a modal on load. ([Laws of UX: Peak-End Rule](https://lawsofux.com/peak-end-rule/), [NN/g: Peak-End Rule](https://www.nngroup.com/articles/peak-end-rule/))

---

## 1. Platform research (real specifics)

All platforms below let donors give **one-time** unless noted. "Processing fee" everywhere refers to the card processor (usually Stripe or PayPal) at roughly **2.9% + $0.30** in the US. ([checkoutpage.com: Stripe fees 2026](https://checkoutpage.com/blog/stripe-processing-fees), [Stripe pricing](https://stripe.com/pricing))

### Ko-fi — RECOMMENDED PRIMARY
- **Platform fee:** **0% on tips/donations** on the free plan (and on Gold). 5% on shop sales/memberships/commissions on free; Ko-fi Gold ($12/mo) removes that 5% — irrelevant for pure tipping. ([Owelet](https://owelet.app/blog/ko-fi-fees-2026), [KnowYourCut: Ko-fi fees 2026](https://knowyourcut.com/blog/kofi-fees-2026))
- **Processing fee:** Stripe/PayPal charge ~2.9% + $0.30 separately; Ko-fi never touches the money — it goes straight to your connected account. ([Owelet](https://owelet.app/blog/ko-fi-fees-2026))
- **Donor friction:** Supporters can tip with **just an email + card — no Ko-fi account needed.** ([Talks.co](https://talks.co/p/kofi-vs-buy-me-a-coffee/))
- **One-time vs recurring:** Excellent for one-time tips ("Buy me a coffee"-style); also supports monthly memberships if you ever want them.
- **Backend needed?** No. Hosted page at `ko-fi.com/yourname`, plus copy-paste button/widget embeds (no server code). ([Ko-fi Help: tip widget](https://help.ko-fi.com/hc/en-us/articles/360018381678-Ko-fi-tip-widget))
- **Payouts:** Instant via connected Stripe/PayPal. ([Talks.co](https://talks.co/p/kofi-vs-buy-me-a-coffee/))
- **Pros for Quafel:** Lowest effective fee for tips; donor-friendly (no login); minutes to set up; minimalist hosted page; widgets match a clean UI.
- **Cons:** Off-site checkout (donor leaves your domain unless you embed the overlay); Ko-fi branding on the page.

### Buy Me a Coffee
- **Platform fee:** **5% on every transaction**, with no tier to remove it. On $1,000 of tips that's $50 vs Ko-fi's $0. ([Talks.co](https://talks.co/p/kofi-vs-buy-me-a-coffee/), [SchoolMaker: BMC pricing](https://www.schoolmaker.com/blog/buy-me-a-coffee-pricing))
- **Donor friction:** Low — one-click style giving, no donor account required.
- **Backend needed?** No.
- **Payouts:** Batched (slower than Ko-fi's instant). ([Talks.co](https://talks.co/p/kofi-vs-buy-me-a-coffee/))
- **Verdict:** Nearly identical UX to Ko-fi but strictly worse on fees. Only pick it if you already have an audience there.

### Stripe Payment Links — RECOMMENDED FALLBACK
- **Platform fee:** None — Stripe is the processor, not a middleman platform. You pay only **2.9% + $0.30** per US card payment. ([Stripe pricing](https://stripe.com/pricing))
- **"Customers choose what to pay":** Built-in pay-what-you-want pricing ideal for donations/tips; create it in the Dashboard with no pre-made product and no code. ([Stripe: pay-what-you-want](https://docs.stripe.com/payments/checkout/pay-what-you-want), [Stripe: create a payment link](https://docs.stripe.com/payment-links/create))
- **One-time vs recurring:** Pay-what-you-want supports **one-time only** (no recurring on that mode). Fixed-amount recurring is possible separately. ([Stripe: pay-what-you-want](https://docs.stripe.com/payments/checkout/pay-what-you-want))
- **Backend needed?** No — Stripe hosts the checkout page (PCI, card validation, receipt emails handled for you). Optional webhook if you want to log/track donations. ([Stripe: how to accept donations](https://support.stripe.com/questions/how-to-accept-donations-through-stripe))
- **Pros for Quafel:** No third-party cut; cleanest, most on-brand hosted checkout; "donor covers fees" toggle available. ([Stripe: how to accept donations](https://support.stripe.com/questions/how-to-accept-donations-through-stripe))
- **Cons:** You must complete Stripe account onboarding (business/identity/bank details) — more setup than Ko-fi; no built-in "supporter wall"/social proof.

### GitHub Sponsors
- **Platform fee:** **0% from personal accounts** (100% to you); up to 6% from organization sponsors. ([GitHub Docs: fees & taxes](https://docs.github.com/en/sponsors/sponsoring-open-source-contributors/about-sponsorships-fees-and-taxes))
- **Fit:** Great for an *open-source maintainer* audience (developers with GitHub accounts). **Poor fit for casual quiz-takers**, who mostly don't have GitHub and won't log in to sponsor.
- **Backend needed?** No, but requires sponsor onboarding/approval and donor GitHub login.
- **Verdict:** Keep as a secondary "support the project" link in the README/footer for the dev crowd; not the main consumer CTA.

### Open Collective
- **Fees:** Fiscal-host fee **5–10%** plus ~3% processing → **~8–13% total**; via GitHub Sponsors + Open Source Collective it's **10%**. ([Open Source Collective docs](https://docs.oscollective.org/campaigns-and-partnerships/github-sponsors), [Open Collective: GitHub Sponsors](https://opencollective.com/github-sponsors))
- **Fit:** Strong for *transparent community budgets* (public ledger of income/expenses), weak for low-friction casual tipping. Higher fees than Ko-fi/Stripe.
- **Verdict:** Consider only if radical financial transparency is a core brand value.

### Patreon
- **Fees:** Membership-platform fee plus processing (transaction fees apply on top). ([SaaSHub: Patreon vs GitHub Sponsors](https://www.saashub.com/compare-patreon-vs-github-sponsors))
- **Fit:** Built for **ongoing memberships with tiers/perks**, not spontaneous one-time tips from anonymous visitors. Heavy for this use case; donors typically need a Patreon account.
- **Verdict:** Not recommended for a one-shot quiz result moment.

### Quick comparison

| Platform | Platform fee (tips) | + Processing | Donor account needed? | Backend? | Best for | One-time fit |
|---|---|---|---|---|---|---|
| **Ko-fi** | **0%** | ~2.9% + $0.30 | **No** | No | Casual tips, fast setup | Excellent |
| Buy Me a Coffee | 5% | included/processing | No | No | Existing BMC audience | Good |
| **Stripe Payment Links** | **0% (none)** | 2.9% + $0.30 | No | No (webhook optional) | On-brand hosted checkout | Excellent (PWYW one-time) |
| GitHub Sponsors | 0% (personal) | handled | **Yes (GitHub)** | No | Developer/OSS audience | Good |
| Open Collective | 5–10% | ~3% | No | No | Transparent budgets | Good |
| Patreon | Membership fee | + processing | Usually | No | Recurring tiers/perks | Poor |

---

## 2. UX tactics that actually drive donations (with evidence)

1. **Ask at the peak-end "moment of delight."** People judge an experience by its peak and its end; the post-result screen is both. Place the ask *after* the result is delivered and savored — not before, and never as a load-time popup. ([Laws of UX: Peak-End Rule](https://lawsofux.com/peak-end-rule/), [NN/g](https://www.nngroup.com/articles/peak-end-rule/), [AFP: critical moments timing](https://afpglobal.org/news/tapping-your-talents-critical-moments-timing-guides-donor-behavior))
2. **Suggest a few anchored amounts.** Suggested amounts reduce decision friction and raise the average gift — one venue went from a **$1 to a $4** average within 30 days after adding suggested amounts. Use ~3 options plus a custom field. ([WPSimplePay: suggested amounts](https://wpsimplepay.com/how-to-set-suggested-donation-amounts-that-drive-conversions/))
3. **Put your preferred amount in the middle.** Donors avoid the cheapest option but don't want to overreach, so they gravitate to the middle/highlighted tier. Highlight one amount visually. ([WPSimplePay](https://wpsimplepay.com/how-to-set-suggested-donation-amounts-that-drive-conversions/))
4. **Always allow a custom amount.** Without it, donors who want a different number simply leave. ([WPSimplePay](https://wpsimplepay.com/how-to-set-suggested-donation-amounts-that-drive-conversions/))
5. **Cost transparency ("a quiz costs us ~$X in AI").** Concrete, honest unit-cost framing makes the ask legible and fair. Wikipedia's per-use framing ("less than a penny per visit") is the canonical example — keep it *honest* (don't imply the lights go off), since overstated appeals breed distrust. ([Wikipedia: donation appeal ideas](https://en.wikipedia.org/wiki/Wikipedia:Donation_appeal_ideas), [MakeUseOf: Wikipedia appeals](https://www.makeuseof.com/tag/wikipedia-millions-bank-beg/))
6. **Reciprocity.** They just got something fun and free; a light "if it made you smile, you can chip in" leverages the reciprocity norm (people give back after receiving). ([Invesp: psychological triggers](https://www.invespcro.com/blog/12-psychological-tricks-to-increase-your-conversion-rate/), [FasterCapital: social proof & CRO](https://fastercapital.com/content/Conversion-rate-optimization--CRO---Social-Proof-Integration--Building-Trust-with-Social-Proof-to-Enhance-CRO.html))
7. **Social proof.** A subtle "joined by N supporters this month" or a supporter count builds trust and a sense of momentum. Add it only once real numbers exist — Ko-fi's page provides a supporter feed for free. ([FasterCapital](https://fastercapital.com/content/Conversion-rate-optimization--CRO---Social-Proof-Integration--Building-Trust-with-Social-Proof-to-Enhance-CRO.html), [BlueWing: donation CRO](https://bluewing.co/blog/boosting-donations-effective-conversion-rate-optimization-strategies/))
8. **Low-friction, hosted, one-tap checkout.** Send donors straight to a hosted page (Ko-fi / Stripe) that handles cards, Apple Pay, and receipts — no account creation. Fewer steps = higher completion. ([Stripe: pay-what-you-want](https://docs.stripe.com/payments/checkout/pay-what-you-want), [Talks.co](https://talks.co/p/kofi-vs-buy-me-a-coffee/))
9. **Gentle, non-nagging cadence.** Make the CTA dismissible and remember the dismissal (e.g., `localStorage`) so repeat quiz-takers aren't pestered. One clean ask beats repeated interruptions.
10. **Thank-you / impact framing.** After a gift, show a genuine thank-you and tie it to impact ("you just covered ~N quizzes for other people"). Acknowledgment increases the chance of giving again. ([Giving USA: giving moment to retention](https://givingusa.org/how-fundraisers-can-turn-a-giving-moment-into-lifetime-retention/), [NumberAnalytics: donor behavior](https://www.numberanalytics.com/blog/donor-behavior-analytics-econ))

---

## 3. Recommendation & rationale

**Primary: Ko-fi.** It wins on every priority that matters for Quafel:

- **Low friction** — donors give with email + card, no account. ([Talks.co](https://talks.co/p/kofi-vs-buy-me-a-coffee/))
- **Low fees** — 0% platform fee on tips; you keep everything minus the unavoidable card processing. ([Owelet](https://owelet.app/blog/ko-fi-fees-2026))
- **Fast setup** — a usable page and a link/widget in minutes, no backend. ([Ko-fi Help](https://help.ko-fi.com/hc/en-us/articles/360018381678-Ko-fi-tip-widget))
- **Minimalist aesthetic** — clean hosted page + embeddable overlay that won't clutter the result screen.

**Fallback: Stripe Payment Links (pay-what-you-want).** Choose this if you want the donor to stay fully on-brand, want no third-party platform at all, or if Ko-fi is unavailable. Same ~2.9% + $0.30 economics, slightly more onboarding. ([Stripe: pay-what-you-want](https://docs.stripe.com/payments/checkout/pay-what-you-want))

**Secondary (dev audience only):** a "Sponsor on GitHub" link in the README/footer for the open-source crowd (0% from personal sponsors). ([GitHub Docs](https://docs.github.com/en/sponsors/sponsoring-open-source-contributors/about-sponsorships-fees-and-taxes))

---

## 4. Setup guide

### Option A — Ko-fi (primary, ~15 minutes)

1. **Create the page.** Go to [ko-fi.com](https://ko-fi.com/), click **Start a page**, claim your URL (e.g. `ko-fi.com/quafel`), register with email + password. ([Ko-fi setup overview](https://medium.com/@datatomas/adding-ko-fi-to-your-own-platform-a-complete-guide-4e2eb116f908))
2. **Connect a payout method.** In **Settings → Payments**, connect **Stripe** (cards + Apple Pay) and/or **PayPal**. Money flows directly to that account; Ko-fi takes 0% on tips. ([Owelet](https://owelet.app/blog/ko-fi-fees-2026))
3. **Brand the page.** Set the page name, a one-line description ("Quafel makes fun AI quizzes — free for everyone"), a cover image, and rename the tip button label/amount step to fit (e.g., a "coffee" = a small fixed unit).
4. **Get your link.** Your public page is `https://ko-fi.com/<yourname>` — this is the simplest CTA target.
5. **(Optional) Get a no-code widget.** In **Widgets**, choose **Floating Button** (expands to a tip panel) or **Tip Panel** (inline embed), customize, and copy the script. For Quafel, prefer an **inline button you control** that opens the Ko-fi overlay so it only appears on the result screen — don't use a site-wide floating button. ([Ko-fi Help: tip widget](https://help.ko-fi.com/hc/en-us/articles/360018381678-Ko-fi-tip-widget))
6. **(Optional) Thank-you.** Enable Ko-fi's automatic thank-you message/email so donors get acknowledgment immediately.

> Implementation note: the lowest-effort integration is a plain link/button on the result screen that opens `https://ko-fi.com/<yourname>` in a new tab. The Ko-fi overlay widget keeps donors closer to the app but adds a script tag.

### Option B — Stripe Payment Links (fallback, ~20–30 minutes)

1. **Create/onboard a Stripe account** at [stripe.com](https://stripe.com) (business/identity + bank details). ([Stripe: how to accept donations](https://support.stripe.com/questions/how-to-accept-donations-through-stripe))
2. **Dashboard → Payment Links → New.** For the price, choose **"Customers choose what to pay"** so donors enter any amount; optionally set a suggested/preset and a minimum. No product needs to be pre-created. ([Stripe: pay-what-you-want](https://docs.stripe.com/payments/checkout/pay-what-you-want), [Stripe: create a payment link](https://docs.stripe.com/payment-links/create))
3. **(Optional) Let donors cover the fee** with the optional fee-coverage setting; keep it clearly labeled and optional. ([Stripe: how to accept donations](https://support.stripe.com/questions/how-to-accept-donations-through-stripe))
4. **Copy the hosted link** and use it as the CTA target. Stripe hosts checkout, receipts, and PCI. ([Stripe: create a payment link](https://docs.stripe.com/payment-links/create))
5. **(Optional) Webhook for tracking.** If you want to count donations or trigger an in-app thank-you, add a Stripe webhook (`checkout.session.completed`) to a small endpoint on Quafel's existing backend. Not required to accept money.

---

## 5. In-app placement & copy

### Where
On the **result screen only**, rendered **after** the personality result and **after / alongside the share buttons** (share first — sharing is the user's own peak action; the donate ask rides that momentum). It must be:
- **Inline, not a modal/popup**, and never block or precede the result.
- **Dismissible**, with the dismissal remembered (e.g., `localStorage` flag) so repeat takers aren't nagged.
- **Visually quiet** — one line of copy, ~3 amount chips, a custom field, one button — matching Quafel's minimalism.

### Suggested amounts
Show **three small, clean chips plus "Other"**, with the **middle option highlighted/preselected** (donors drift to the middle): e.g. **$3 · $5 · $10 · Other**, default **$5**. ($3 reads as "a coffee"; keep amounts small so processing fees don't dominate, since 30¢ is a big bite on a $1 gift.) Tune values via A/B testing. ([WPSimplePay](https://wpsimplepay.com/how-to-set-suggested-donation-amounts-that-drive-conversions/), [checkoutpage.com: Stripe fees](https://checkoutpage.com/blog/stripe-processing-fees))

### Copy variations (pick one to start, A/B the rest)

- **Cost-transparency (lead candidate):**
  > "Loved your result? Each quiz costs us about a few cents in AI. If Quafel made you smile, you can chip in to keep it free for everyone. ☕"
- **Reciprocity / delight:**
  > "Enjoyed that? Quafel is free and ad-free. Buy us a coffee to keep the quizzes coming."
- **Social proof (once you have numbers):**
  > "Joined by 320+ supporters this month — keeping Quafel free for everyone. Want to chip in?"
- **Minimal / understated:**
  > "Quafel is free. Tip if you'd like. ❤️"

**Button label:** "Buy us a coffee" or "Support Quafel". **Dismiss label:** "Maybe later" (low-pressure, not "No").

### Thank-you / impact
On return from a successful donation (or via Ko-fi's auto thank-you), show:
> "Thank you! You just helped cover ~N free quizzes for other people. 💛"

### Illustrative markup (starting point, not production)

```html
<!-- Shown on the result screen, after share buttons; hidden if localStorage flag set -->
<section class="donate-cta" aria-label="Support Quafel">
  <p>Loved your result? Each quiz costs us ~a few cents in AI.
     Chip in to keep Quafel free for everyone. ☕</p>
  <div class="amounts" role="group" aria-label="Donation amount">
    <button data-amt="3">$3</button>
    <button data-amt="5" aria-pressed="true">$5</button>   <!-- preselected middle -->
    <button data-amt="10">$10</button>
    <button data-amt="other">Other</button>
  </div>
  <a class="donate-go" href="https://ko-fi.com/quafel" target="_blank" rel="noopener">
    Buy us a coffee
  </a>
  <button class="donate-dismiss">Maybe later</button>
</section>
```
> With Ko-fi, the amount chips set the preset on the linked Ko-fi page (or just open the page). With Stripe Payment Links using "customers choose what to pay," the chips can map to amount query params / preset links. Keep it to one screenful and one tap.

---

## 6. Metrics & A/B test

### Track
- **CTA view-through / donation conversion %** = donations ÷ result screens that showed the CTA. (Primary KPI.)
- **Average donation amount** (and median).
- **Click-through rate** on the CTA (clicked donate ÷ saw CTA) vs **completion rate** (paid ÷ clicked) — separates "interest" from "checkout friction."
- **Repeat-donor rate** (share of donors who give more than once).
- **Dismiss rate** (how often "Maybe later" is clicked) — watch this to ensure the ask isn't annoying.
- **Revenue per 1,000 results** — the bottom-line number that ties it all together.

(These mirror standard donation-CRO metrics. ([BlueWing: donation CRO](https://bluewing.co/blog/boosting-donations-effective-conversion-rate-optimization-strategies/), [NumberAnalytics: donor behavior analytics](https://www.numberanalytics.com/blog/donor-behavior-analytics-econ)))

### Simple A/B idea
Split result-screen traffic 50/50:
- **A (control):** minimal copy — "Quafel is free. Tip if you'd like."
- **B (treatment):** cost-transparency copy — "Each quiz costs us ~a few cents in AI… chip in to keep it free."

Run until each arm has enough donations to compare (aim for a few hundred conversions per arm before trusting the result). **Primary metric: donation conversion %; guardrail metric: dismiss rate.** A natural second test is **suggested-amount sets** (e.g., $3/$5/$10 vs $5/$10/$20), since higher anchors raise average gift but can lower frequency — test, don't assume. ([WPSimplePay](https://wpsimplepay.com/how-to-set-suggested-donation-amounts-that-drive-conversions/))

---

## Sources

- Ko-fi fees 2026 — https://owelet.app/blog/ko-fi-fees-2026
- Ko-fi fees (Free + Gold) — https://knowyourcut.com/blog/kofi-fees-2026
- Ko-fi vs Buy Me a Coffee (fees, donor account, payouts) — https://talks.co/p/kofi-vs-buy-me-a-coffee/
- Buy Me a Coffee pricing — https://www.schoolmaker.com/blog/buy-me-a-coffee-pricing
- Ko-fi tip widget / embed setup — https://help.ko-fi.com/hc/en-us/articles/360018381678-Ko-fi-tip-widget
- Adding Ko-fi to your platform (setup walkthrough) — https://medium.com/@datatomas/adding-ko-fi-to-your-own-platform-a-complete-guide-4e2eb116f908
- Stripe pricing (2.9% + $0.30) — https://stripe.com/pricing
- Stripe fees explained 2026 — https://checkoutpage.com/blog/stripe-processing-fees
- Stripe Payment Links (overview) — https://stripe.com/payments/payment-links
- Stripe: create a payment link — https://docs.stripe.com/payment-links/create
- Stripe: customers choose what to pay (PWYW) — https://docs.stripe.com/payments/checkout/pay-what-you-want
- Stripe: how to accept donations — https://support.stripe.com/questions/how-to-accept-donations-through-stripe
- GitHub Sponsors fees & taxes — https://docs.github.com/en/sponsors/sponsoring-open-source-contributors/about-sponsorships-fees-and-taxes
- Open Source Collective / GitHub Sponsors fees — https://docs.oscollective.org/campaigns-and-partnerships/github-sponsors
- Open Collective: GitHub Sponsors — https://opencollective.com/github-sponsors
- Patreon vs GitHub Sponsors — https://www.saashub.com/compare-patreon-vs-github-sponsors
- Laws of UX: Peak-End Rule — https://lawsofux.com/peak-end-rule/
- NN/g: Peak-End Rule — https://www.nngroup.com/articles/peak-end-rule/
- AFP: critical moments / timing of the ask — https://afpglobal.org/news/tapping-your-talents-critical-moments-timing-guides-donor-behavior
- Giving USA: turning a giving moment into retention — https://givingusa.org/how-fundraisers-can-turn-a-giving-moment-into-lifetime-retention/
- WPSimplePay: suggested donation amounts that convert — https://wpsimplepay.com/how-to-set-suggested-donation-amounts-that-drive-conversions/
- BlueWing: donation conversion-rate optimization — https://bluewing.co/blog/boosting-donations-effective-conversion-rate-optimization-strategies/
- FasterCapital: social proof & CRO — https://fastercapital.com/content/Conversion-rate-optimization--CRO---Social-Proof-Integration--Building-Trust-with-Social-Proof-to-Enhance-CRO.html
- Invesp: psychological triggers for conversion — https://www.invespcro.com/blog/12-psychological-tricks-to-increase-your-conversion-rate/
- NumberAnalytics: donor behavior analytics — https://www.numberanalytics.com/blog/donor-behavior-analytics-econ
- Wikipedia: donation appeal ideas — https://en.wikipedia.org/wiki/Wikipedia:Donation_appeal_ideas
- MakeUseOf: Wikipedia appeals / cost transparency caveats — https://www.makeuseof.com/tag/wikipedia-millions-bank-beg/

---

## Skeptical Review — Verification

**Reviewed 2026-06-29 against current (2026) sources. Verdict: every load-bearing factual claim holds; the Ko-fi-primary / Stripe-fallback recommendation stands. One small wording tightening and a couple of "be honest about the asterisk" caveats below.**

### Fees & terms — re-verified (all CONFIRMED accurate for 2026)

| Claim in doc | Verified current fact | Source |
|---|---|---|
| Ko-fi **0%** platform fee on tips/donations (free + Gold) | Confirmed. Free plan: 0% on tips/donations, 5% on shop/memberships/commissions; Gold ($12/mo or $96/yr) removes the 5%. | [KnowYourCut: Ko-fi fees 2026](https://knowyourcut.com/blog/kofi-fees-2026), [ko-fi.com](https://ko-fi.com/) |
| Ko-fi donors need **no account** (email + card) | Confirmed — "no accounts required for supporters." | [KnowYourCut](https://knowyourcut.com/blog/kofi-fees-2026) |
| Ko-fi payouts **instant** via connected Stripe/PayPal | Confirmed — money goes straight to your Stripe/PayPal. | [KnowYourCut](https://knowyourcut.com/blog/kofi-fees-2026) |
| Buy Me a Coffee **5%** every transaction, no removal tier | Confirmed — flat 5%, single free plan. | [SchoolMaker: BMC pricing](https://www.schoolmaker.com/blog/buy-me-a-coffee-pricing) |
| Stripe standard **2.9% + $0.30** (US card), no platform middleman | Confirmed standard rate. | [Stripe pricing](https://stripe.com/pricing) |
| Stripe Payment Links / PWYW = **one-time only**, donor **no account** (guest checkout) | Confirmed — PWYW doesn't support recurring; one-time payments use a guest customer (no account/login). | [Stripe: pay-what-you-want](https://docs.stripe.com/payments/checkout/pay-what-you-want), [Stripe: guest customers](https://docs.stripe.com/payments/checkout/guest-customers) |
| GitHub Sponsors **0% from personal**, up to 6% from orgs | Confirmed — 0% personal (100% to dev), up to 6% org; donor needs a GitHub login. | [GitHub Docs: fees & taxes](https://docs.github.com/en/sponsors/sponsoring-open-source-contributors/about-sponsorships-fees-and-taxes) |
| Open Collective **~8–13%**; via OSC + GitHub Sponsors **10%** | Confirmed — OSC host fee 10% on GitHub-Sponsors funds (GitHub adds nothing). | [Open Source Collective docs](https://docs.oscollective.org/campaigns-and-partnerships/github-sponsors) |
| Patreon membership fee + processing; donors usually need an account | Confirmed in spirit — membership-platform fee tiers + processing, built for recurring; not one-shot tipping. | [SchoolMaker / talks.co comparisons (2026)] |

**Nothing material is stale, wrong, or overstated.** Two honest-footnote tightenings worth adding so the doc can't be accused of cherry-picking:

1. **"0% fee" is platform-only, not all-in.** The doc already says you still pay ~2.9% + $0.30 processing — keep that adjacent to every "0%" so a reader doesn't think Ko-fi is literally free. On a $3 tip the 30¢ + ~2.9% ≈ **$0.39 (≈13%)** goes to the processor regardless of platform; that is why the doc's "keep amounts small but not tiny / 30¢ is a big bite on $1" guidance (§5) is exactly right and should stay prominent.
2. **BMC's "5%" is also platform-only** — BMC layers its 5% *on top of* the same Stripe processing (and a 0.5% payout fee + 1% international), so BMC is strictly worse than Ko-fi by ~5 pts, not "5% total." The doc's "strictly worse on fees" verdict is correct; the table's "+Processing: included/processing" cell for BMC is slightly muddled — BMC processing is **additive**, same as Ko-fi, so the only real delta vs Ko-fi is the 5% platform cut. Minor wording nit, not a factual error.

### Recommendation sanity-check — Ko-fi STILL STANDS

For a free, no-auth, minimalist, high-volume-casual app optimizing **low friction + low fees**, the ranking is unchanged and well-justified:
- **Ko-fi wins on both axes simultaneously:** lowest effective fee for pure tipping (0% platform) **and** lowest donor friction (no account). No competitor beats it on both — BMC matches friction but loses 5%; Stripe matches fees but adds creator onboarding and lacks a supporter feed; GitHub Sponsors/Patreon both impose donor accounts; Open Collective is higher-fee and built for a different (transparency) use case.
- **Stripe Payment Links is the correct fallback** — same processing economics, zero platform cut, fully on-brand hosted checkout, guest checkout (no donor account). The only cost is more creator-side onboarding. Sound.
- **GitHub Sponsors as a dev-audience-only secondary** in README/footer is the right call (donor GitHub login kills it for casual quiz-takers).

One forward-looking caveat (not a correction): Ko-fi's 0%-on-tips is its core differentiator and a known business risk — if it ever changes, the doc's Stripe fallback already covers it. Worth a one-line "re-check Ko-fi's fee page before launch" note since fee pages do change.

### In-app placement — REASONABLE, does not harm minimalism

The proposed placement is sound and consistent with the app's minimalist ethos:
- **Post-result, after/alongside share, inline (never a modal/load-time popup), dismissible with the dismissal remembered** — this is the textbook peak-end placement and is the *least* intrusive way to ask. It rides the user's own share momentum rather than interrupting the result.
- **Visual budget is tight on purpose** (one line of copy, ~3 chips + Other, one button) — this is compatible with minimalism; it adds one quiet section to a screen the user has already reached, not a persistent site-wide widget. The doc explicitly rejects the site-wide floating button, which is the right instinct.
- **Honesty guardrail is already baked in:** the cost-transparency copy ("a few cents in AI") is kept deliberately vague/honest, and the doc warns against overstated appeals — good, because an inflated claim would do more brand damage than the donations are worth.

The only placement nuance to watch: don't let the chips imply a stronger commitment than a one-tap link delivers (with a plain Ko-fi link the chips are decorative unless you wire amount params). The doc already flags this in the markup note — keep the copy honest about what tapping a chip does.

**Bottom line:** No factual corrections required beyond the two minor wording footnotes above. Prices, fee structures, no-account claims, and payout terms are all current as of 2026-06-29. Ko-fi primary + Stripe fallback + GitHub-Sponsors-for-devs remains the right recommendation, and the dismissible post-result CTA is a minimalism-preserving placement.
