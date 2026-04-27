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

import re
from typing import Any, Dict, List, Optional

from app.models.api import CharacterProfile, FinalResult, Synopsis

# Conservative IP keywords we never echo verbatim.
_IP_GENERIC_TOKENS = {"hogwarts", "marvel", "disney", "pixar", "harry potter"}

_MAX_PROMPT_CHARS: int = 600  # FAL handles long prompts but shorter = faster


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


# ---------------------------------------------------------------------------
# Builders
# ---------------------------------------------------------------------------

def build_character_image_prompt(
    profile: CharacterProfile,
    *,
    category: str,
    analysis: Optional[Dict[str, Any]],
    style_suffix: str,
    negative_prompt: str,
) -> Dict[str, str]:
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

    prompt = f"{prefix} {body}. {style_suffix}"
    return {"prompt": _truncate(prompt, _MAX_PROMPT_CHARS),
            "negative_prompt": negative_prompt}


def build_synopsis_image_prompt(
    synopsis: Synopsis,
    *,
    category: str,
    analysis: Optional[Dict[str, Any]],
    style_suffix: str,
    negative_prompt: str,
) -> Dict[str, str]:
    is_media = bool((analysis or {}).get("is_media", False))
    summary = _truncate(getattr(synopsis, "summary", "") or "", 220)

    if is_media or _has_ip_token(category) or _has_ip_token(summary):
        # Abstract symbolic illustration; no IP names.
        body = "An evocative symbolic illustration representing a personality quiz theme; abstract motifs, no characters"
    else:
        body = f"An evocative illustration of {category}: {summary}"

    prompt = f"{body}. {style_suffix}"
    return {"prompt": _truncate(prompt, _MAX_PROMPT_CHARS),
            "negative_prompt": negative_prompt}


def build_result_image_prompt(
    result: FinalResult,
    *,
    category: str,
    character_set: List[Dict[str, Any]],
    style_suffix: str,
    negative_prompt: str,
    analysis: Optional[Dict[str, Any]] = None,
) -> Dict[str, str]:
    is_media = bool((analysis or {}).get("is_media", False))
    title = (getattr(result, "title", "") or "").strip()
    description = (getattr(result, "description", "") or "").strip()

    matched: Optional[Dict[str, Any]] = None
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

    prompt = f"{prefix} {body}. {style_suffix}"
    return {"prompt": _truncate(prompt, _MAX_PROMPT_CHARS),
            "negative_prompt": negative_prompt}
