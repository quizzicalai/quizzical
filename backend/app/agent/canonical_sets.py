# backend/app/agent/canonical_sets.py
"""
Canonical sets loader (config-driven, noise-tolerant key matching)

This module exposes helpers to fetch canonical outcome-name lists
(e.g., MBTI types, Hogwarts Houses) from application configuration.

It aggressively *normalizes* messy user phrases before matching,
so inputs like:

- "Which Hogwarts house am I?"
- "please sort me into a Hogwarts House"
- "Test — Myers-Briggs Personality Types (official)"
- "Types of zodiac signs?"
- "Quiz: Alignment [2025]"

all resolve to the same canonical category keys.

YAML shape (under quizzical.canonical_sets):

quizzical:
  canonical_sets:
    # Optional global alias mapping (case/space/punct-insensitive)
    aliases:
      "myers-briggs personality types": ["mbti", "myers briggs", "myers-briggs", "mbti types"]
      "hogwarts houses": ["hogwarts", "harry potter houses", "hp houses"]

    # The sets themselves; each value can be either:
    # - a plain string[] OR
    # - an object { names: string[], count_hint?: int }
    sets:
      "Myers-Briggs Personality Types":
        names: ["ISTJ","ISFJ","INFJ","INTJ","ISTP","ISFP","INFP","INTP",
                "ESTP","ESFP","ENFP","ENTP","ESTJ","ESFJ","ENFJ","ENTJ"]
        count_hint: 16

      "Hogwarts Houses":
        - "Gryffindor"
        - "Slytherin"
        - "Ravenclaw"
        - "Hufflepuff"
"""

from __future__ import annotations

from functools import lru_cache
from pathlib import Path
from typing import Dict, List, Optional, Tuple, Any, Iterable
import json
import os
import re
import unicodedata

import yaml  # PyYAML

# Primary config object (if available)
try:
    from app.core.config import settings  # type: ignore
except Exception:
    settings = None  # type: ignore


# =============================================================================
# Config loading / fallback
# =============================================================================

def _default_appconfig_path() -> Path:
    """
    Mirrors backend/app/core/config.py default: backend/appconfig.local.yaml
    (Env override: APP_CONFIG_LOCAL_PATH)
    """
    env = os.getenv("APP_CONFIG_LOCAL_PATH")
    if env:
        return Path(env).expanduser().resolve()
    # backend/app/agent/canonical_sets.py → parents[2] == backend/
    backend_dir = Path(__file__).resolve().parents[2]
    return backend_dir / "appconfig.local.yaml"


def _safe_yaml_load(path: Path) -> Dict[str, Any]:
    try:
        if not path.exists():
            return {}
        with path.open("r", encoding="utf-8") as f:
            return yaml.safe_load(f) or {}
    except Exception:
        return {}


def _from_settings_object() -> Dict[str, Any]:
    """
    Best effort pull from app Settings if the app added canonical_sets to it.
    Falls back to {} if not present to avoid tight coupling with core config.
    """
    try:
        # Settings may not define this field; getattr(default) prevents AttributeError.
        return getattr(settings, "canonical_sets", {}) or {}
    except Exception:
        return {}


def _from_yaml_blob() -> Dict[str, Any]:
    """
    Read from the same YAML file used by the app (non-secret side), if present.
    Only extracts the 'quizzical.canonical_sets' subtree.
    """
    data = _safe_yaml_load(_default_appconfig_path())
    q = (data or {}).get("quizzical") or {}
    return (q.get("canonical_sets") or {}) if isinstance(q, dict) else {}


def _merge_config(a: Dict[str, Any], b: Dict[str, Any]) -> Dict[str, Any]:
    """Shallow merge: b overrides a."""
    out = dict(a or {})
    for k, v in (b or {}).items():
        out[k] = v
    return out


# =============================================================================
# Normalization (noise stripping & robust key building)
# =============================================================================

# Alphanumeric tokenizer (after accent strip)
_WORD_RE = re.compile(r"[A-Za-z0-9]+")

# Remove tiny bracketed trailers like "(official)", "[2025]", "{beta}" at the end
_TRAILING_BRACKETED_TAIL_RE = re.compile(r"[\(\[\{]\s*[^)\]\}]{0,40}\s*[\)\]\}]\s*$")

# Question punctuation at the end
_QUESTION_PUNCT_TAIL_RE = re.compile(r"[?？。？！]+$")

# "Quiz: ", "Test - ", "Assessment — " at the start
_LEADING_LABELED_PREFIX_RE = re.compile(r"^(?:quiz|test|assessment|exam)\s*[:\-–—]\s*", re.IGNORECASE)

# Politeness / request scaffolding
_LEADING_POLITE_RE = re.compile(r"^(?:please|kindly)\s+", re.IGNORECASE)
_LEADING_CAN_YOU_RE = re.compile(r"^(?:can|could|would|will|should|may|might)\s+you\s+(?:please\s+)?", re.IGNORECASE)
_LEADING_I_WANT_RE = re.compile(r"^i\s+(?:want|would\s+like|wanna|need)\s+to\s+", re.IGNORECASE)

# “What/Which/How … am I/are you …” style question frames
_LEADING_QFRAME_RE = re.compile(
    r"^(?:what|which|who|where|when|how)\s+"
    r"(?:(?:is|are|am|do|does|should|would|could|can)\s+)?"
    r"(?:(?:i|you|we|they|someone|one)\s+)?"
    r"(?:be\s+)?",
    re.IGNORECASE,
)

# Imperatives like "list/show/give/find (me) ..."
_LEADING_IMPERATIVE_RE = re.compile(r"^(?:list|name|show|give|find)\s+(?:me\s+)?(?:the\s+)?", re.IGNORECASE)

# “sort me into/assign me a/put me in(to) …”
_LEADING_SORT_ME_RE = re.compile(r"^(?:sort\s+me\s+into|assign\s+me\s+a|put\s+me\s+in(?:to)?)\s+", re.IGNORECASE)

# Trailing wrappers / tool names / fluff
_TRAILING_TOOL_RE = re.compile(
    r"\s*(?:quiz|test|assessment|exam|sorter|sorting|generator|checker|finder|picker|guide|tier\s*list)\s*$",
    re.IGNORECASE,
)

# Trailing marketing fluff
_TRAILING_MARKETING_RE = re.compile(r"\s*(?:official|ultimate|definitive|best|top)\s*$", re.IGNORECASE)

# Leading articles
_LEADING_ARTICLE_RE = re.compile(r"^(?:the|a|an)\s+", re.IGNORECASE)

# "types/kinds/categories of ..." at the start
_LEADING_TYPES_OF_RE = re.compile(r"(?i)^(?:types?|kinds?|categor(?:y|ies)|class(?:es)?)\s+of\s+")

# Canonical suffix labels we want to drop when present (kept from original, expanded)
_CANON_SUFFIXES = (
    " characters",
    " character",
    " types",
    " type",
    " profiles",
    " profile",
    " archetypes",
    " archetype",
)

def _strip_accents(text: str) -> str:
    """Remove diacritics so 'Pokémon' → 'Pokemon'."""
    if not text:
        return text
    try:
        nfkd = unicodedata.normalize("NFKD", text)
        return "".join(ch for ch in nfkd if not unicodedata.combining(ch))
    except Exception:
        return text

def _strip_noise(s: str) -> str:
    """Remove common leading/trailing scaffolding & qualifiers without touching core terms."""
    s = (s or "").strip()
    if not s:
        return s

    # Remove bracketed trailers & question punctuation at the tail
    s = _TRAILING_BRACKETED_TAIL_RE.sub("", s).strip()
    s = _QUESTION_PUNCT_TAIL_RE.sub("", s).strip()

    # Leading labeled prefixes and polite scaffolding
    s = _LEADING_LABELED_PREFIX_RE.sub("", s).strip()
    s = _LEADING_POLITE_RE.sub("", s).strip()
    s = _LEADING_CAN_YOU_RE.sub("", s).strip()
    s = _LEADING_I_WANT_RE.sub("", s).strip()
    s = _LEADING_QFRAME_RE.sub("", s).strip()
    s = _LEADING_IMPERATIVE_RE.sub("", s).strip()
    s = _LEADING_SORT_ME_RE.sub("", s).strip()

    # Leading articles & "types/kinds/categories of ..."
    s = _LEADING_ARTICLE_RE.sub("", s).strip()
    s = _LEADING_TYPES_OF_RE.sub("", s).strip()

    # Remove canonical suffix labels (once)
    low = s.lower()
    for suf in _CANON_SUFFIXES:
        if low.endswith(suf):
            s = s[: -len(suf)].strip()
            break

    # Trailing wrappers/fluff
    s = _TRAILING_TOOL_RE.sub("", s).strip()
    s = _TRAILING_MARKETING_RE.sub("", s).strip()

    return s

def _tokenize_for_key(s: str) -> List[str]:
    """Accent-strip, lower-case, and keep only alphanumeric tokens."""
    s = _strip_accents(s)
    toks = _WORD_RE.findall(s.lower())
    return toks

def _last_token_variants(tokens: List[str]) -> Iterable[str]:
    """
    Generate robust key variants by toggling singular/plural on the LAST token.
    This helps 'hogwarts house' match a config titled 'Hogwarts Houses' and vice versa.
    Heuristic & conservative to avoid exploding the index.
    """
    if not tokens:
        return []
    base = " ".join(tokens)
    yield base

    last = tokens[-1]
    # Skip obvious acronyms / short tokens
    if len(last) < 3 or last.isupper():
        return

    # Singularize simple plurals
    def singular(t: str) -> Optional[str]:
        low = t.lower()
        if low.endswith("ies") and len(t) > 3:
            return t[:-3] + "y"
        if low.endswith("ses") and len(t) > 3:
            return t[:-2]
        if low.endswith("xes") and len(t) > 3:
            return t[:-2]
        if low.endswith("s") and not low.endswith("ss"):
            return t[:-1]
        return None

    # Pluralize simple singulars
    def plural(t: str) -> Optional[str]:
        low = t.lower()
        if low.endswith(("s", "ss")):
            return None
        if low.endswith("y") and len(t) > 1 and t[-2].lower() not in "aeiou":
            return t[:-1] + "ies"
        if low.endswith(("s", "x", "z", "ch", "sh")):
            return t + "es"
        return t + "s"

    sing = singular(last)
    if sing and sing != last:
        yield " ".join([*tokens[:-1], sing])

    pl = plural(last)
    if pl and pl != last:
        yield " ".join([*tokens[:-1], pl])

@lru_cache(maxsize=4096)
def _norm_key(raw: str) -> str:
    """
    Case/space/punct-insensitive key with aggressive noise stripping.

    Steps:
      - trim & strip question/test scaffolding (please/can you..., what/which..., quiz/test labels)
      - strip tiny bracketed qualifiers at the tail (e.g., "(official)", "[2025]")
      - remove generic suffix labels (characters/types/profiles/archetypes)
      - strip leading articles and "types/kinds/categories of ..."
      - accent-strip, lowercase, alnum-tokenize
      - join tokens with single spaces
    """
    if not isinstance(raw, str):
        return ""
    s = raw.strip()
    # Preserve legacy handling of "Quiz:" but generalized via _strip_noise()
    s = _strip_noise(s)
    # One more suffix sweep in case noise removal revealed more text
    s = _strip_noise(s)
    tokens = _tokenize_for_key(s)
    return " ".join(tokens)


# =============================================================================
# Config compilation (aliases + sets → normalized index)
# =============================================================================

def _extract_names(entry: Any) -> Tuple[List[str], Optional[int]]:
    """
    Accepts either:
      - list[str]
      - {names: list[str], count_hint?: int}
    Returns (names, count_hint)
    """
    if isinstance(entry, dict):
        names = [str(x).strip() for x in (entry.get("names") or []) if str(x).strip()]
        ch = entry.get("count_hint")
        try:
            ch = int(ch) if ch is not None else None
        except Exception:
            ch = None
        return names, ch
    elif isinstance(entry, list):
        names = [str(x).strip() for x in entry if str(x).strip()]
        return names, None
    return [], None


def _add_index_key(index: Dict[str, str], key: str, title: str) -> None:
    """Add a key to the index if non-empty and not already present."""
    k = key.strip()
    if k and k not in index:
        index[k] = title


@lru_cache(maxsize=1)
def _compiled_config() -> Dict[str, Any]:
    """
    Loads and compiles:
      - aliases: dict[str, list[str]]
      - sets: dict[canonical_title(str)] -> {names: list[str], count_hint?: int}
      - index: dict[norm_key] -> canonical_title
        (includes robust singular/plural variants on the last token)
    """
    # Order of precedence: settings object overrides YAML
    raw = _merge_config(_from_yaml_blob(), _from_settings_object())

    aliases_raw: Dict[str, List[str]] = {}
    sets_raw: Dict[str, Any] = {}

    if isinstance(raw.get("aliases"), dict):
        aliases_raw = {
            str(k): [str(x) for x in (v or [])]
            for k, v in raw["aliases"].items()
        }

    if isinstance(raw.get("sets"), dict):
        sets_raw = dict(raw["sets"])

    # Normalize sets → canonical map
    sets: Dict[str, Dict[str, Any]] = {}
    for title, entry in (sets_raw or {}).items():
        names, hint = _extract_names(entry)
        if names:
            sets[str(title)] = {"names": names, "count_hint": hint}

    # Build alias index (normalized), with singular/plural last-token variants
    index: Dict[str, str] = {}

    # 1) Direct titles → keys & variants
    for title in sets.keys():
        nk = _norm_key(title)
        if nk:
            _add_index_key(index, nk, title)
            for var in _last_token_variants(nk.split()):
                _add_index_key(index, var, title)

    # 2) Explicit aliases (match alias "owner" to a set title by normalized key)
    # Owner may be the title itself or any phrase that normalizes to it.
    title_by_norm = {}
    for t in sets.keys():
        nk = _norm_key(t)
        if nk:
            title_by_norm[nk] = t
            # also map variants for owner lookups
            for var in _last_token_variants(nk.split()):
                title_by_norm.setdefault(var, t)

    for alias_owner, alias_list in (aliases_raw or {}).items():
        owner_nk = _norm_key(alias_owner)
        ct = title_by_norm.get(owner_nk)
        if not ct:
            # Try a variant in case of pluralization mismatch
            for var in _last_token_variants(owner_nk.split()):
                ct = title_by_norm.get(var)
                if ct:
                    break
        if not ct:
            continue
        for alias in alias_list or []:
            nk = _norm_key(alias)
            if not nk:
                continue
            _add_index_key(index, nk, ct)
            for var in _last_token_variants(nk.split()):
                _add_index_key(index, var, ct)

    return {
        "aliases": aliases_raw,
        "sets": sets,
        "index": index,
    }


# =============================================================================
# Public API
# =============================================================================


def canonical_for(category: Optional[str]) -> Optional[List[str]]:
    """
    Returns the canonical list of names for a category, if configured.

    Matching is tolerant to punctuation, case, politeness frames, question
    phrasing, test wrappers, tiny bracketed qualifiers, leading "types of ...",
    and generic suffix labels. Also tolerant to *simple singular/plural*
    mismatch on the final token (e.g., "hogwarts house" ↔ "Hogwarts Houses").
    """
    if not category:
        return None
    cfg = _compiled_config()
    key = _norm_key(category)
    title = cfg["index"].get(key)
    if not title:
        # Try last-token variants of the query key
        for var in _last_token_variants(key.split()):
            title = cfg["index"].get(var)
            if title:
                break
    if not title:
        return None
    names = list(cfg["sets"][title]["names"])
    # De-dup while preserving order
    seen = set()
    out: List[str] = []
    for n in names:
        k = n.strip().casefold()
        if k and k not in seen:
            seen.add(k)
            out.append(n.strip())
    return out or None


def count_hint_for(category: Optional[str]) -> Optional[int]:
    """
    Returns explicit count_hint if provided in YAML; otherwise len(canonical_for(..))
    if a set exists; otherwise None.
    """
    cfg = _compiled_config()
    key = _norm_key(category or "")
    title = cfg["index"].get(key)
    if not title:
        # Attempt with variants
        for var in _last_token_variants(key.split()):
            title = cfg["index"].get(var)
            if title:
                break
    if not title:
        return None
    hint = cfg["sets"].get(title, {}).get("count_hint")
    if isinstance(hint, int) and hint > 0:
        return hint
    names = cfg["sets"].get(title, {}).get("names") or []
    return len(names) if names else None
