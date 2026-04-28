# app/agent/tools/image_tools.py
"""Pure-function FAL prompt builders (§7.8.3).

Goals:
- IP-safe: when ``analysis.is_media is True`` we never pass the verbatim
  ``category`` or character ``name`` to FAL; we use only descriptive tokens
  drawn from ``short_description`` / ``profile_text``.
- Stylistic consistency: every prompt ends with ``style_suffix``.
- Zero LLM calls: this module is hot-path and must stay sub-millisecond.
"""
from __future__ import annotations

import hashlib
import re
from typing import Any

from app.models.api import CharacterProfile, FinalResult, Synopsis

# Conservative IP keywords we never echo verbatim.
_IP_GENERIC_TOKENS = {"hogwarts", "marvel", "disney", "pixar", "harry potter"}

_MAX_PROMPT_CHARS: int = 600  # FAL handles long prompts but shorter = faster

# AC-IMG-STYLE-1..3 — immutable cross-builder style anchor.
# Appended to EVERY prompt in addition to the configurable ``style_suffix``,
# guaranteeing a recognisable house style even when operators tweak the suffix.
# Keep this string short and free of subject nouns so it never fights with the
# per-image content tokens.
STYLE_ANCHOR: str = (
    "unified illustrated quiz art style, single consistent palette, "
    "matching brushwork across all images in the series"
)


def derive_seed(session_id: Any, subject: str) -> int:
    """AC-IMG-STYLE-4 — deterministic uint32 seed for FAL RNG.

    Pins the random seed so that re-rendering the same (session, subject)
    pair produces visually identical output, and so that all images for a
    single quiz draw from a related-but-distinct seed neighbourhood (helping
    visual cohesion). Pure function; no IO.
    """
    raw = f"{session_id}|{subject}".encode("utf-8")
    digest = hashlib.blake2b(raw, digest_size=4).digest()
    return int.from_bytes(digest, "big")


def _truncate(s: str, n: int) -> str:
    s = (s or "").strip()
    return s if len(s) <= n else (s[: n - 1].rstrip() + "…")


def _safe_descriptors(profile_text: str, short_description: str, max_chars: int = 240) -> str:
    """Pull short visual/personality clauses; drop sentences with proper-noun heavy content."""
    text = (short_description or "").strip()
    extra = (profile_text or "").strip()
    if extra:
        # Take the first sentence-ish chunk that doesn't look name-heavy.
        first = re.split(r"(?<=[.!?])\s+", extra)[0]
        if first and not _looks_name_heavy(first):
            text = f"{text}. {first}" if text else first
    return _truncate(text, max_chars)


def _looks_name_heavy(s: str) -> bool:
    """Crude heuristic: if more than 25% of tokens are Capitalized non-stopwords, treat as name-heavy."""
    tokens = re.findall(r"[A-Za-z']+", s)
    if len(tokens) < 4:
        return False
    caps = sum(1 for t in tokens[1:] if t and t[0].isupper())
    return (caps / max(1, len(tokens) - 1)) > 0.25


def _has_ip_token(text: str) -> bool:
    low = (text or "").lower()
    return any(tok in low for tok in _IP_GENERIC_TOKENS)


def _compose_with_anchor(head: str, style_suffix: str) -> str:
    """Compose ``head + style_suffix + STYLE_ANCHOR`` so the anchor is never
    truncated regardless of how long the head/suffix get (AC-IMG-STYLE-2)."""
    tail = f". {style_suffix}. {STYLE_ANCHOR}".rstrip()
    head = (head or "").strip().rstrip(".")
    budget_for_head = max(0, _MAX_PROMPT_CHARS - len(tail) - 1)
    head = _truncate(head, budget_for_head)
    return f"{head}{tail}"


# ---------------------------------------------------------------------------
# Builders
# ---------------------------------------------------------------------------

def build_character_image_prompt(
    profile: CharacterProfile,
    *,
    category: str,
    analysis: dict[str, Any] | None,
    style_suffix: str,
    negative_prompt: str,
) -> dict[str, str]:
    is_media = bool((analysis or {}).get("is_media", False))
    desc = _safe_descriptors(getattr(profile, "profile_text", ""),
                             getattr(profile, "short_description", ""))

    if is_media:
        # IP-safe: descriptive only; no name, no category.
        prefix = "Character portrait of a person:"
        body = desc
    else:
        prefix = f"Portrait illustration for the topic '{category}':"
        body = desc

    prompt = _compose_with_anchor(f"{prefix} {body}", style_suffix)
    return {"prompt": prompt,
            "negative_prompt": negative_prompt}


def build_synopsis_image_prompt(
    synopsis: Synopsis,
    *,
    category: str,
    analysis: dict[str, Any] | None,
    style_suffix: str,
    negative_prompt: str,
) -> dict[str, str]:
    is_media = bool((analysis or {}).get("is_media", False))
    summary = _truncate(getattr(synopsis, "summary", "") or "", 220)

    if is_media or _has_ip_token(category) or _has_ip_token(summary):
        # Abstract symbolic illustration; no IP names.
        body = "An evocative symbolic illustration representing a personality quiz theme; abstract motifs, no characters"
    else:
        body = f"An evocative illustration of {category}: {summary}"

    prompt = _compose_with_anchor(body, style_suffix)
    return {"prompt": prompt,
            "negative_prompt": negative_prompt}


def build_result_image_prompt(
    result: FinalResult,
    *,
    category: str,
    character_set: list[dict[str, Any]],
    style_suffix: str,
    negative_prompt: str,
    analysis: dict[str, Any] | None = None,
) -> dict[str, str]:
    is_media = bool((analysis or {}).get("is_media", False))
    title = (getattr(result, "title", "") or "").strip()
    description = (getattr(result, "description", "") or "").strip()

    matched: dict[str, Any] | None = None
    if title and character_set:
        # Title typically reads "You are <Name>" or contains the name.
        for c in character_set:
            name = (c.get("name") if isinstance(c, dict) else None) or ""
            if name and name.lower() in title.lower():
                matched = c
                break

    if matched:
        desc = _safe_descriptors(matched.get("profile_text", "") or "",
                                 matched.get("short_description", "") or "")
        prefix = "Character portrait of a person:"
        body = desc
    else:
        body = _truncate(description, 240)
        if is_media:
            prefix = "Character portrait of a person:"
        else:
            prefix = f"Illustration for the result of a '{category}' quiz:"

    prompt = _compose_with_anchor(f"{prefix} {body}", style_suffix)
    return {"prompt": prompt,
            "negative_prompt": negative_prompt}
