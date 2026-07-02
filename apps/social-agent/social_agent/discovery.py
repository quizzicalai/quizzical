"""Dual-direction reply-target discovery (owner requirement).

Every reply cycle thinks in BOTH directions and merges the results:

1. TREND-LED: what's happening today (AI web-search probe; X trends later
   when keys/tier allow) -> playful personality angles -> search recent posts
   about those trends -> reply with the silly quafel angle ("Which FIFA team
   am I?" during a FIFA day).
2. TOPIC-LED: pick a silly/fun personality topic FIRST (sampled from the
   banked witty-topic pool in social_posts + freshly invented ones) -> search
   recent posts where that riff would land naturally.

Candidates from both directions merge into ONE ranked pool (dedup by tweet
id, direction tags merged) BEFORE the unchanged gate gauntlet: recency
window, visibility heuristic, sensitivity prefilter, uniqueness gate,
strong-judge. Which direction sourced each candidate is logged and stored in
judge_verdicts metadata.

Stdlib-only on purpose: planning/parsing/ranking/merging are pure and the
search step takes a duck-typed provider, so unit tests mock both directions
with no network.
"""
from __future__ import annotations

import json
import logging
import math
import re
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any

from .visibility import TweetCandidate

log = logging.getLogger("social_agent.discovery")

TREND = "trend"
TOPIC = "topic"

# Per-direction directive caps: keep the cycle to a handful of searches.
MAX_TREND_DIRECTIVES = 2
MAX_TOPIC_DIRECTIVES = 3
MAX_TERMS_PER_DIRECTIVE = 6

# Built-in silly topics used when the DB pool is empty AND the planner LLM
# fails — the topic-led direction must never be silently absent.
FALLBACK_TOPICS = ("ducks", "sandwiches", "houseplants", "ballroom gowns")


@dataclass
class SearchDirective:
    direction: str            # TREND | TOPIC
    label: str                # short slug, e.g. "fifa-final" or "ducks"
    angle: str                # the playful quafel angle to riff on
    terms: list[str] = field(default_factory=list)
    raw_query: str = ""       # overrides terms when set (base personality query)

    def query(self, lang: str = "en") -> str:
        if self.raw_query:
            return self.raw_query
        return build_x_query(self.terms, lang=lang)


@dataclass
class DiscoveredCandidate:
    tweet: TweetCandidate
    directions: list[str]
    labels: list[str]
    angles: list[str]
    score: float = 0.0

    @property
    def primary_direction(self) -> str:
        return self.directions[0] if self.directions else ""

    def meta(self) -> dict[str, Any]:
        """The observability payload stored in judge_verdicts."""
        return {
            "discovery": {
                "directions": self.directions,
                "labels": self.labels,
                "angles": self.angles,
                "rank_score": round(self.score, 3),
            }
        }


# ---------------------------------------------------------------------------
# Planning: parse the LLM's directive plan (defensive, refuse-junk)
# ---------------------------------------------------------------------------

_JSON_BLOCK = re.compile(r"\{.*\}", re.DOTALL)


def _clean_terms(raw: Any) -> list[str]:
    if not isinstance(raw, list):
        return []
    out: list[str] = []
    for t in raw[:MAX_TERMS_PER_DIRECTIVE]:
        s = " ".join(str(t).split()).strip()
        if s and len(s) <= 60:
            out.append(s)
    return out


def _parse_directive_items(items: Any, direction: str, cap: int) -> list[SearchDirective]:
    out: list[SearchDirective] = []
    if not isinstance(items, list):
        return out
    for item in items:
        if len(out) >= cap:
            break
        if not isinstance(item, dict):
            continue
        terms = _clean_terms(item.get("terms"))
        label = " ".join(str(item.get("label", "")).split())[:60]
        angle = " ".join(str(item.get("angle", "")).split())[:300]
        if not terms or not label:
            continue
        out.append(SearchDirective(direction=direction, label=label, angle=angle, terms=terms))
    return out


def parse_direction_plan(raw: str) -> list[SearchDirective]:
    """Parse {"trend_directives":[...], "topic_directives":[...]} defensively.

    Anything malformed is dropped (never fatal): a broken plan just means
    that direction contributes nothing this cycle.
    """
    if not raw or not raw.strip():
        return []
    text = raw.strip()
    if text.startswith("```"):
        text = re.sub(r"^```[a-zA-Z]*\n?", "", text)
        text = re.sub(r"\n?```$", "", text.strip())
    m = _JSON_BLOCK.search(text)
    if not m:
        return []
    try:
        data = json.loads(m.group(0))
    except (json.JSONDecodeError, ValueError):
        return []
    if not isinstance(data, dict):
        return []
    plan: list[SearchDirective] = []
    plan += _parse_directive_items(data.get("trend_directives"), TREND, MAX_TREND_DIRECTIVES)
    plan += _parse_directive_items(data.get("topic_directives"), TOPIC, MAX_TOPIC_DIRECTIVES)
    return plan


def fallback_topic_directives(banked_topics: list[str], k: int = 2) -> list[SearchDirective]:
    """Deterministic topic-led directives straight from banked topics — used
    when the planner LLM fails so the topic direction still runs."""
    stop = {"personalities", "personality", "quiz", "types", "type", "the", "of", "and"}
    out: list[SearchDirective] = []
    for topic in list(banked_topics) + list(FALLBACK_TOPICS):
        if len(out) >= k:
            break
        words = [w for w in re.sub(r"[^a-zA-Z0-9 ]", " ", str(topic)).lower().split() if w not in stop]
        if not words:
            continue
        label = "-".join(words)[:40]
        out.append(
            SearchDirective(
                direction=TOPIC,
                label=label,
                angle=f"silly quafel riff about {' '.join(words)}",
                terms=[" ".join(words)] if len(words) > 1 else words,
            )
        )
    return out


# ---------------------------------------------------------------------------
# Query building
# ---------------------------------------------------------------------------

def build_x_query(terms: list[str], lang: str = "en") -> str:
    """OR-join terms (quoting phrases) + the standard exclusion filters."""
    cleaned: list[str] = []
    for t in terms[:MAX_TERMS_PER_DIRECTIVE]:
        t = " ".join(str(t).split())
        if not t:
            continue
        cleaned.append(f'"{t}"' if " " in t else t)
    if not cleaned:
        return ""
    core = " OR ".join(cleaned)
    if len(cleaned) > 1:
        core = f"({core})"
    return f"{core} -is:retweet -is:reply lang:{lang}"


# ---------------------------------------------------------------------------
# Ranking + merging
# ---------------------------------------------------------------------------

def rank_score(t: TweetCandidate) -> float:
    """Engagement-vs-burial rank for the merged pool.

    More likes and a reasonably-followed author rank higher; every existing
    reply pushes the score down (our reply gets buried). This is only an
    ORDERING within already-filter-eligible candidates — hard limits live in
    the visibility policy, not here.
    """
    likes = math.log1p(max(0, t.like_count))
    followers = math.log1p(max(0, t.author_followers))
    burial = math.log1p(max(0, t.reply_count))
    return likes + 0.5 * followers - 0.75 * burial


def merge_and_rank(
    found: list[tuple[TweetCandidate, SearchDirective]],
) -> list[DiscoveredCandidate]:
    """Merge per-direction results into ONE ranked pool.

    Dedup by tweet_id; a tweet surfaced by several directives keeps ALL of
    its direction/label/angle tags (observability). Sorted by rank_score
    descending; ties keep first-seen order (stable sort).
    """
    by_id: dict[str, DiscoveredCandidate] = {}
    order: list[str] = []
    for tweet, directive in found:
        existing = by_id.get(tweet.tweet_id)
        if existing is None:
            by_id[tweet.tweet_id] = DiscoveredCandidate(
                tweet=tweet,
                directions=[directive.direction],
                labels=[directive.label],
                angles=[a for a in [directive.angle] if a],
                score=rank_score(tweet),
            )
            order.append(tweet.tweet_id)
        else:
            if directive.direction not in existing.directions:
                existing.directions.append(directive.direction)
            if directive.label not in existing.labels:
                existing.labels.append(directive.label)
            if directive.angle and directive.angle not in existing.angles:
                existing.angles.append(directive.angle)
    pool = [by_id[tid] for tid in order]
    pool.sort(key=lambda c: c.score, reverse=True)
    return pool


# ---------------------------------------------------------------------------
# Search execution (duck-typed provider — mockable, no network in tests)
# ---------------------------------------------------------------------------

async def run_directives(
    provider,
    directives: list[SearchDirective],
    start_time: datetime,
) -> tuple[list[DiscoveredCandidate], dict[str, Any]]:
    """Execute every directive's search and merge into one ranked pool.

    Returns (pool, stats). stats counts raw finds per direction plus the
    merged pool size — surfaced in logs and the cycle result.
    """
    found: list[tuple[TweetCandidate, SearchDirective]] = []
    per_direction = {TREND: 0, TOPIC: 0}
    per_directive: list[dict[str, Any]] = []
    for d in directives:
        q = d.query()
        if not q:
            continue
        try:
            results = await provider.search(q, start_time)
        except Exception:  # noqa: BLE001 — one bad search must not kill the cycle
            log.exception("search failed for directive %s/%s", d.direction, d.label)
            results = []
        per_direction[d.direction] = per_direction.get(d.direction, 0) + len(results)
        per_directive.append({"direction": d.direction, "label": d.label, "found": len(results)})
        found.extend((t, d) for t in results)
    pool = merge_and_rank(found)
    stats = {
        "trend_found": per_direction.get(TREND, 0),
        "topic_found": per_direction.get(TOPIC, 0),
        "merged_pool": len(pool),
        "directives": per_directive,
    }
    log.info(
        "discovery: trend-led found %d, topic-led found %d, merged pool %d "
        "(directives: %s)",
        stats["trend_found"], stats["topic_found"], stats["merged_pool"],
        ", ".join(f"{p['direction']}/{p['label']}={p['found']}" for p in per_directive) or "none",
    )
    return pool, stats


# ---------------------------------------------------------------------------
# Posts-only fallback: should the 12h profile post carry the trend flavor?
# ---------------------------------------------------------------------------

def should_trend_flavor_post(
    *,
    force_event: bool,
    events_enabled: bool,
    provider_name: str,
    roll: float,
    posts_only_ratio: float = 0.34,
) -> bool:
    """In posts-only mode (no search tier) the trend-led direction can't
    express itself through replies — so roughly every third profile post
    rides a current event instead. Pure; `roll` is a [0,1) random sample."""
    if force_event or events_enabled:
        return True
    if provider_name == "no-search" and roll < posts_only_ratio:
        return True
    return False
