"""Visibility + suitability heuristics for reply targets. Stdlib-only.

Owner rule: skip posts where our reply would be hard to see — buried under
thousands of replies, or from zero-visibility accounts. Also a cheap
sensitivity pre-filter so obviously tender topics never even reach the LLM
judge (the judge remains the real gate; this just saves tokens and adds a
deterministic floor).
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field


@dataclass
class TweetCandidate:
    tweet_id: str
    text: str
    author_id: str = ""
    author_username: str = ""
    author_followers: int = 0
    reply_count: int = 0
    like_count: int = 0
    retweet_count: int = 0
    created_at: str = ""  # ISO-8601 as returned by the X API
    lang: str = "en"


@dataclass
class VisibilityPolicy:
    # Below this follower count a reply is effectively invisible.
    min_followers: int = 50
    # Above this many existing replies ours would be buried.
    max_reply_count: int = 150
    # Mega-viral posts: buried no matter what, and brand risk is higher.
    max_like_count: int = 50_000
    only_langs: tuple[str, ...] = ("en",)


@dataclass
class Verdict:
    engage: bool
    reason: str = ""


def visibility_check(t: TweetCandidate, policy: VisibilityPolicy | None = None) -> Verdict:
    p = policy or VisibilityPolicy()
    if p.only_langs and t.lang and t.lang not in p.only_langs:
        return Verdict(False, f"language '{t.lang}' not in {p.only_langs}")
    if t.author_followers < p.min_followers:
        return Verdict(False, f"author has {t.author_followers} followers (< {p.min_followers}): zero-visibility")
    if t.reply_count > p.max_reply_count:
        return Verdict(False, f"{t.reply_count} replies (> {p.max_reply_count}): ours would be buried")
    if t.like_count > p.max_like_count:
        return Verdict(False, f"{t.like_count} likes (> {p.max_like_count}): mega-viral, buried")
    return Verdict(True, "visible")


# --- sensitivity pre-filter ---------------------------------------------------
# Deterministic floor UNDER the LLM judge: if the target post plausibly touches
# grief, illness, crisis, or charged identity/politics territory we never joke
# at it, full stop. Case-insensitive word-boundary matching.
_SENSITIVE_TERMS: tuple[str, ...] = (
    "died", "dying", "death", "passed away", "funeral", "grief", "grieving",
    "suicide", "suicidal", "self harm", "self-harm", "depression", "depressed",
    "anxiety attack", "panic attack", "ptsd", "trauma", "abuse", "abusive",
    "cancer", "diagnosis", "diagnosed", "terminal", "hospice", "hospital",
    "chronic illness", "disorder", "bipolar", "schizophren", "adhd meltdown",
    "divorce", "breakup", "broke up", "cheated on", "miscarriage", "infertility",
    "laid off", "layoff", "fired", "eviction", "homeless", "bankrupt",
    "shooting", "war", "bomb", "terror", "hostage", "genocide",
    "election", "vote for", "republican", "democrat", "maga", "president",
    "racist", "racism", "sexist", "homophob", "transphob", "islamophob",
    "antisemit", "immigration raid", "deport",
    "rest in peace", "rip ", " rip", "condolence", "memorial", "obituary",
    "relapse", "addiction", "overdose", "sober",
    "eating disorder", "anorexi", "bulimi",
)

_norm_ws = re.compile(r"\s+")


@dataclass
class SensitivityResult:
    sensitive: bool
    matched: list[str] = field(default_factory=list)


def sensitivity_prefilter(text: str) -> SensitivityResult:
    hay = " " + _norm_ws.sub(" ", text.lower()) + " "
    hits = [term for term in _SENSITIVE_TERMS if term in hay]
    return SensitivityResult(sensitive=bool(hits), matched=hits)
