# backend/app/agent/progress_phrases.py
"""Curated short status phrases shown in the upper-right of the quiz UI in
place of "% complete" / "Question N of M".

Why a curated pool?
-------------------
The agent finishes the quiz when it either (a) reaches the configured maximum
number of questions OR (b) reaches a confidence threshold for a single
outcome. That makes "Question 3 of 20" and "15% complete" actively misleading
because the quiz can end at any time. Instead we surface a short phrase that
conveys roughly *where the user is in the journey*:

  * baseline / information-gathering — low confidence, exploratory tone
  * narrowing — medium confidence, "starting to see a pattern"
  * closing in — high confidence, "almost there"
  * decision imminent — very high confidence, "I think I've got it"

How the agent uses these
------------------------
- For BASELINE questions the agent does not yet have any information, so the
  baseline phrases are picked deterministically by question index. This keeps
  the early experience predictable and avoids LLM cost on a screen that
  changes every couple of seconds.
- For ADAPTIVE / narrowing questions the LLM is shown the relevant pool plus
  the user's history and asked to either pick verbatim from the pool or
  compose its own short phrase that fits the topic's tone. The BE always
  validates the returned phrase (length cap, no character/winner reveal) and
  falls back to a deterministic pool entry when validation fails.

Constraints (enforced in `pick_progress_phrase` and the FE):
- Plain text, no markdown.
- Hard cap of 60 characters (the FE pill is small).
- Must not contain a winner / character name (the agent prompt is told this,
  and the BE strips obvious leaks).
"""
from __future__ import annotations

import random
from typing import Iterable, Literal

# A confidence band roughly maps to where the user is in the journey. The
# numeric edges deliberately overlap the agent's `early_finish_confidence`
# threshold so the "decision imminent" pool only fires when the agent itself
# is very close to finalising.
ConfidenceBand = Literal["baseline", "exploring", "narrowing", "closing", "imminent"]

# Hard cap on the rendered phrase. The FE pill is small; anything longer
# wraps awkwardly.
MAX_PHRASE_LEN = 60

# Deterministic baseline phrases — used for the first N questions before the
# LLM has any signal. Order matters: question index 0 → first phrase, etc.
# Wraps with modulo if there are more baseline questions than phrases.
BASELINE_PHRASES: tuple[str, ...] = (
    "Just getting to know you…",
    "Still learning…",
    "Gathering a few clues…",
    "Listening carefully…",
    "Building a picture…",
    "Tell me more…",
    "Filing that away…",
    "Interesting start…",
    "Getting warmer…",
    "Still wide open…",
)

# Narrowing pool — used by the LLM (and as a fallback) once the agent starts
# discriminating between candidates. ~50 short, varied phrases ranging from
# very low to very high confidence. The bands below partition this pool.
EXPLORING_PHRASES: tuple[str, ...] = (
    "Hmm, intriguing…",
    "Noted — keep going…",
    "That's a clue…",
    "A pattern is forming…",
    "Let's dig a little deeper…",
    "Interesting choice…",
    "Filing that away…",
    "Worth a closer look…",
    "Curious…",
    "Tell me more about that…",
)

NARROWING_PHRASES: tuple[str, ...] = (
    "I'm narrowing things down…",
    "A few candidates come to mind…",
    "The picture is sharpening…",
    "Starting to see a shape…",
    "A theme is emerging…",
    "We're getting somewhere…",
    "I have a hunch forming…",
    "Down to a short list…",
    "Closer than you think…",
    "Halfway to a verdict…",
)

CLOSING_PHRASES: tuple[str, ...] = (
    "I'm closing in…",
    "Almost there…",
    "One or two left…",
    "I have a strong guess…",
    "Just a couple more…",
    "Nearly ready to reveal…",
    "On the verge of an answer…",
    "Final stretch…",
    "Sharpening the verdict…",
    "Nearly decided…",
)

IMMINENT_PHRASES: tuple[str, ...] = (
    "Ah! I think I know…",
    "I've got it…",
    "Final answer brewing…",
    "Last check…",
    "Wrapping it up…",
    "Calling it now…",
    "Pretty sure already…",
    "Result on the way…",
    "Confidence high…",
    "Verdict imminent…",
)

# Convenience: a flat ordered tuple of every "narrowing" phrase, used when the
# LLM prompt wants to see the full pool at once.
ALL_NARROWING_PHRASES: tuple[str, ...] = (
    EXPLORING_PHRASES + NARROWING_PHRASES + CLOSING_PHRASES + IMMINENT_PHRASES
)


def band_for(confidence: float, *, answered: int, max_total: int) -> ConfidenceBand:
    """Map (confidence, progress) → a phrase band.

    The agent's confidence is the primary signal; the answered/max ratio is a
    secondary nudge so very long quizzes still progress through the bands
    even when confidence stays flat.
    """
    try:
        c = float(confidence)
    except (TypeError, ValueError):
        c = 0.0
    # Clamp into [0, 1] to defend against malformed LLM responses.
    c = max(0.0, min(1.0, c))

    answered_ratio = 0.0
    if max_total and max_total > 0:
        answered_ratio = max(0.0, min(1.0, answered / float(max_total)))

    # Take the higher of the two so we never appear to "go backwards".
    score = max(c, answered_ratio * 0.85)

    if score >= 0.90:
        return "imminent"
    if score >= 0.70:
        return "closing"
    if score >= 0.45:
        return "narrowing"
    if score >= 0.20:
        return "exploring"
    return "baseline"


def pool_for_band(band: ConfidenceBand) -> tuple[str, ...]:
    if band == "baseline":
        return BASELINE_PHRASES
    if band == "exploring":
        return EXPLORING_PHRASES
    if band == "narrowing":
        return NARROWING_PHRASES
    if band == "closing":
        return CLOSING_PHRASES
    return IMMINENT_PHRASES


def baseline_phrase_for_index(idx: int) -> str:
    """Deterministic phrase for baseline question at zero-based index `idx`."""
    if idx < 0:
        idx = 0
    return BASELINE_PHRASES[idx % len(BASELINE_PHRASES)]


def sanitize_phrase(
    phrase: str | None,
    *,
    forbidden_terms: Iterable[str] = (),
) -> str | None:
    """Trim, length-cap, and reject obviously broken / leaky phrases.

    Returns None when the input is unusable; callers should then fall back to
    a deterministic pool entry. Keeping this strict is important because the
    phrase appears prominently in the UI and any leak of the winning
    character's name would spoil the result.
    """
    if not isinstance(phrase, str):
        return None
    cleaned = " ".join(phrase.split())
    if not cleaned:
        return None
    # Strip surrounding quotes if the LLM wrapped the value.
    if len(cleaned) >= 2 and cleaned[0] in {'"', "'"} and cleaned[-1] == cleaned[0]:
        cleaned = cleaned[1:-1].strip()
    if not cleaned:
        return None
    if len(cleaned) > MAX_PHRASE_LEN:
        cleaned = cleaned[: MAX_PHRASE_LEN - 1].rstrip() + "…"
    lowered = cleaned.lower()
    for term in forbidden_terms:
        if term and isinstance(term, str) and term.strip():
            if term.strip().lower() in lowered:
                return None
    return cleaned


def pick_progress_phrase(
    *,
    confidence: float,
    answered: int,
    max_total: int,
    rng: random.Random | None = None,
) -> str:
    """Deterministic-ish fallback phrase when the LLM didn't supply one."""
    band = band_for(confidence, answered=answered, max_total=max_total)
    pool = pool_for_band(band)
    r = rng or random.Random(answered * 1009 + int(confidence * 1000))
    return r.choice(pool)
