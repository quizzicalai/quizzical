"""Content generation: pre-computed profile posts and personalized replies.

Generation model is cheap (gpt-4o-mini); the JUDGE is the strong model — see
judge.py. Nothing generated here is postable until it passes the judge AND
the uniqueness gate.
"""
from __future__ import annotations

import json
import logging
import random
from typing import Any

from .llm import LLMClient
from .textutils import LINK_PLACEHOLDER, fits_tweet
from .visibility import TweetCandidate

log = logging.getLogger("social_agent.generator")

_POST_SYSTEM = f"""\
You write X posts for quafel (ALWAYS lowercase), a playful AI site where a short quiz tells you
what you are — anything: a duck breed, a kitchen appliance, a mid-century ballroom gown,
homemade mac-n-cheese, a 1997 Honda Civic.

Each post is a RIDICULOUS fake personality result, first person, announced with total sincerity.
Voice: short, silly, warm, a little deadpan. Never mean, never political, never crude, no
hashtag spam (at most one playful hashtag, usually none), no emojis in more than half of them.

Rules:
- 60 to 200 characters. Short is funnier.
- End with the literal placeholder {LINK_PLACEHOLDER} where the share link goes.
- Vary the opening — do NOT start every post the same way. Mix formats: "I was today years old
  when...", "This morning, I'm...", "Just got my results:", "update:", plain declarations,
  fake alarm, fake pride, fake resignation.
- Every post must be about a DIFFERENT thing (different object/animal/food/era/vibe).
- "quafel" may appear, always lowercase, but the link can also just speak for itself.

For each post, also invent the matching quiz-result page: a title (the thing itself, title case
OK there), a 1-2 sentence delightfully specific description of that personality, and a short
quiz category it plausibly came from.

Respond ONLY with JSON: {{"posts": [{{"text": "...", "profile_title": "...",
"profile_description": "...", "category": "..."}}, ...]}}
"""

_EVENT_POST_SYSTEM = f"""\
You write X posts for quafel (ALWAYS lowercase), a playful AI personality-quiz site.

You will be given a CURRENT EVENT summary. Write ridiculous fake personality-result posts that
ride that moment (e.g. during a FIFA tournament: "took a quiz to find out which team I am.
I'm the one that loses on penalties. {LINK_PLACEHOLDER}"). Rules: first person, sincere-deadpan,
60-200 chars, end with the literal placeholder {LINK_PLACEHOLDER}, never mean, never political,
never about tragedies. Also invent the matching result page (title, 1-2 sentence description,
category).

Respond ONLY with JSON: {{"posts": [{{"text": "...", "profile_title": "...",
"profile_description": "...", "category": "...", "event_tag": "<short-event-slug>"}}, ...]}}
"""

_REPLY_SYSTEM = """\
You write X replies for quafel (ALWAYS lowercase), a playful AI personality-quiz site
(quafel.com). You reply to people talking about personality quizzes/types.

Voice: silly, warm, ridiculous, never mean, never salesy, never emotionally naive. You are the
friend who takes the joke one step further, not a marketer. Mention quafel.com naturally at most
once, as a joke, e.g. "Interesting. Perhaps you could use quafel.com to find out what type of
duck you are." Personalize: react to something SPECIFIC in their post. Under 220 characters.
No hashtags. No emojis unless the target post uses them.

Respond ONLY with JSON: {"replies": [{"index": <target index>, "text": "..."}, ...]}
"""


def _parse_json_or_empty(raw: str, key: str) -> list[dict[str, Any]]:
    try:
        data = json.loads(raw)
    except (json.JSONDecodeError, ValueError):
        log.warning("generator returned unparseable JSON; dropping batch")
        return []
    items = data.get(key) if isinstance(data, dict) else None
    return [i for i in items if isinstance(i, dict)] if isinstance(items, list) else []


def _valid_post_item(item: dict[str, Any]) -> bool:
    text = str(item.get("text", "")).strip()
    return (
        bool(text)
        and LINK_PLACEHOLDER in text
        and fits_tweet(text)
        and bool(str(item.get("profile_title", "")).strip())
        and bool(str(item.get("profile_description", "")).strip())
        and bool(str(item.get("category", "")).strip())
    )


async def generate_post_candidates(
    llm: LLMClient,
    model: str,
    count: int,
    avoid_samples: list[str],
    event_summary: str | None = None,
) -> list[dict[str, Any]]:
    """One generation call producing up to `count` candidate posts."""
    system = _EVENT_POST_SYSTEM if event_summary else _POST_SYSTEM
    user_parts = [f"Write {count} posts."]
    if event_summary:
        user_parts.append(f"CURRENT EVENT: {event_summary}")
    if avoid_samples:
        sample = random.sample(avoid_samples, min(30, len(avoid_samples)))
        user_parts.append(
            "For reference, posts ALREADY used (yours must be about different things, "
            "with different phrasing):\n- " + "\n- ".join(sample)
        )
    raw = await llm.chat_json(model, system, "\n\n".join(user_parts), temperature=1.1)
    items = _parse_json_or_empty(raw, "posts")
    valid = [i for i in items if _valid_post_item(i)]
    dropped = len(items) - len(valid)
    if dropped:
        log.info("dropped %d malformed/oversized candidates from batch", dropped)
    return valid


async def generate_replies(
    llm: LLMClient,
    model: str,
    targets: list[TweetCandidate],
) -> dict[int, str]:
    """Generate one personalized reply per target. Returns {target_index: text}."""
    if not targets:
        return {}
    lines = ["Write one reply for each target post below."]
    for i, t in enumerate(targets):
        author = t.author_username or "someone"
        lines.append(f"--- target {i} (by @{author}) ---\n{t.text}")
    raw = await llm.chat_json(model, _REPLY_SYSTEM, "\n".join(lines), temperature=1.0)
    out: dict[int, str] = {}
    for item in _parse_json_or_empty(raw, "replies"):
        try:
            idx = int(item.get("index", -1))
        except (TypeError, ValueError):
            continue
        text = str(item.get("text", "")).strip()
        if 0 <= idx < len(targets) and text and fits_tweet(text):
            out[idx] = text
    return out


async def fetch_event_summary(llm: LLMClient, model: str = "gpt-4o") -> str | None:
    """Optional current-events probe (OpenAI web search). None = no event."""
    try:
        text = (await llm.web_search_events(model)).strip()
    except Exception:  # noqa: BLE001 — events are strictly optional
        log.exception("events probe failed; continuing without an event")
        return None
    if not text or text.upper().startswith("NONE") or "NONE" == text.strip().upper():
        return None
    return text
