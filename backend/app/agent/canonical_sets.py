# backend/app/agent/canonical_sets.py
"""
Canonical sets loader (config-driven, noise-tolerant key matching)

This module exposes helpers to fetch canonical outcome-name lists
(e.g., MBTI types, Hogwarts Houses) from application configuration.
"""

from __future__ import annotations

import os
import re
import unicodedata
from functools import lru_cache
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional, Tuple

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
    """Mirrors backend/app/core/config.py default: backend/appconfig.local.yaml"""
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
    """Best effort pull from app Settings."""
    try:
        return getattr(settings, "canonical_sets", {}) or {}
    except Exception:
        return {}


def _from_yaml_blob() -> Dict[str, Any]:
    """Read from the same YAML file used by the app (non-secret side)."""
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

_WORD_RE = re.compile(r"[A-Za-z0-9]+")
_TRAILING_BRACKETED_TAIL_RE = re.compile(r"[\(\[\{]\s*[^)\]\}]{0,40}\s*[\)\]\}]\s*$")
_QUESTION_PUNCT_TAIL_RE = re.compile(r"[?？。？！]+$")
_LEADING_LABELED_PREFIX_RE = re.compile(r"^(?:quiz|test|assessment|exam)\s*[:\-–—]\s*", re.IGNORECASE)
_LEADING_POLITE_RE = re.compile(r"^(?:please|kindly)\s+", re.IGNORECASE)
_LEADING_CAN_YOU_RE = re.compile(r"^(?:can|could|would|will|should|may|might)\s+you\s+(?:please\s+)?", re.IGNORECASE)
_LEADING_I_WANT_RE = re.compile(r"^i\s+(?:want|would\s+like|wanna|need)\s+to\s+", re.IGNORECASE)
_LEADING_QFRAME_RE = re.compile(
    r"^(?:what|which|who|where|when|how)\s+"
    r"(?:(?:is|are|am|do|does|should|would|could|can)\s+)?"
    r"(?:(?:i|you|we|they|someone|one)\s+)?"
    r"(?:be\s+)?",
    re.IGNORECASE,
)
_LEADING_IMPERATIVE_RE = re.compile(r"^(?:list|name|show|give|find)\s+(?:me\s+)?(?:the\s+)?", re.IGNORECASE)
_LEADING_SORT_ME_RE = re.compile(r"^(?:sort\s+me\s+into|assign\s+me\s+a|put\s+me\s+in(?:to)?)\s+", re.IGNORECASE)
_TRAILING_TOOL_RE = re.compile(
    r"\s*(?:quiz|test|assessment|exam|sorter|sorting|generator|checker|finder|picker|guide|tier\s*list)\s*$",
    re.IGNORECASE,
)
_TRAILING_MARKETING_RE = re.compile(r"\s*(?:official|ultimate|definitive|best|top)\s*$", re.IGNORECASE)
_LEADING_ARTICLE_RE = re.compile(r"^(?:the|a|an)\s+", re.IGNORECASE)
_LEADING_TYPES_OF_RE = re.compile(r"(?i)^(?:types?|kinds?|categor(?:y|ies)|class(?:es)?)\s+of\s+")

_CANON_SUFFIXES = (
    " characters", " character", " types", " type",
    " profiles", " profile", " archetypes", " archetype",
)


def _strip_accents(text: str) -> str:
    if not text:
        return text
    try:
        nfkd = unicodedata.normalize("NFKD", text)
        return "".join(ch for ch in nfkd if not unicodedata.combining(ch))
    except Exception:
        return text


def _strip_noise(s: str) -> str:
    s = (s or "").strip()
    if not s:
        return s

    s = _TRAILING_BRACKETED_TAIL_RE.sub("", s).strip()
    s = _QUESTION_PUNCT_TAIL_RE.sub("", s).strip()
    s = _LEADING_LABELED_PREFIX_RE.sub("", s).strip()
    s = _LEADING_POLITE_RE.sub("", s).strip()
    s = _LEADING_CAN_YOU_RE.sub("", s).strip()
    s = _LEADING_I_WANT_RE.sub("", s).strip()
    s = _LEADING_QFRAME_RE.sub("", s).strip()
    s = _LEADING_IMPERATIVE_RE.sub("", s).strip()
    s = _LEADING_SORT_ME_RE.sub("", s).strip()
    s = _LEADING_ARTICLE_RE.sub("", s).strip()
    s = _LEADING_TYPES_OF_RE.sub("", s).strip()

    low = s.lower()
    for suf in _CANON_SUFFIXES:
        if low.endswith(suf):
            s = s[: -len(suf)].strip()
            break

    s = _TRAILING_TOOL_RE.sub("", s).strip()
    s = _TRAILING_MARKETING_RE.sub("", s).strip()
    return s


def _tokenize_for_key(s: str) -> List[str]:
    s = _strip_accents(s)
    toks = _WORD_RE.findall(s.lower())
    return toks


def _singular(t: str) -> Optional[str]:
    """Attempt simple singularization of the last token."""
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


def _plural(t: str) -> Optional[str]:
    """Attempt simple pluralization of the last token."""
    low = t.lower()
    if low.endswith(("s", "ss")):
        return None
    if low.endswith("y") and len(t) > 1 and t[-2].lower() not in "aeiou":
        return t[:-1] + "ies"
    if low.endswith(("s", "x", "z", "ch", "sh")):
        return t + "es"
    return t + "s"


def _last_token_variants(tokens: List[str]) -> Iterable[str]:
    """
    Generate robust key variants by toggling singular/plural on the LAST token.
    Refactored to reduce complexity by delegating transforms.
    """
    if not tokens:
        return

    base = " ".join(tokens)
    yield base

    last = tokens[-1]
    # Skip obvious acronyms / short tokens
    if len(last) < 3 or last.isupper():
        return

    sing = _singular(last)
    if sing and sing != last:
        yield " ".join([*tokens[:-1], sing])

    pl = _plural(last)
    if pl and pl != last:
        yield " ".join([*tokens[:-1], pl])


@lru_cache(maxsize=4096)
def _norm_key(raw: str) -> str:
    if not isinstance(raw, str):
        return ""
    s = raw.strip()
    s = _strip_noise(s)
    s = _strip_noise(s)  # One more sweep
    tokens = _tokenize_for_key(s)
    return " ".join(tokens)


# =============================================================================
# Config compilation (aliases + sets → normalized index)
# =============================================================================

def _extract_names(entry: Any) -> Tuple[List[str], Optional[int]]:
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
    k = key.strip()
    if k and k not in index:
        index[k] = title


def _build_sets_map(sets_raw: Dict[str, Any]) -> Dict[str, Dict[str, Any]]:
    """Parses raw set definitions into a clean dictionary."""
    sets: Dict[str, Dict[str, Any]] = {}
    for title, entry in (sets_raw or {}).items():
        names, hint = _extract_names(entry)
        if names:
            sets[str(title)] = {"names": names, "count_hint": hint}
    return sets


def _index_direct_titles(
    sets: Dict[str, Any], index: Dict[str, str], title_by_norm: Dict[str, str]
) -> None:
    """Populate index with direct set titles and their singular/plural variants."""
    for title in sets.keys():
        nk = _norm_key(title)
        if nk:
            _add_index_key(index, nk, title)
            title_by_norm[nk] = title
            for var in _last_token_variants(nk.split()):
                _add_index_key(index, var, title)
                title_by_norm.setdefault(var, title)


def _resolve_alias_owner(
    owner_nk: str, title_by_norm: Dict[str, str]
) -> Optional[str]:
    """Find canonical title for an alias owner key (trying exact match then variants)."""
    # Direct lookup
    ct = title_by_norm.get(owner_nk)
    if ct:
        return ct

    # Try variants (e.g. owner="HP Houses" but sets="HP House")
    for var in _last_token_variants(owner_nk.split()):
        ct = title_by_norm.get(var)
        if ct:
            return ct

    return None


def _process_aliases(
    aliases_raw: Dict[str, List[str]],
    index: Dict[str, str],
    title_by_norm: Dict[str, str]
) -> None:
    """Map user-defined aliases to their canonical titles in the index."""
    for alias_owner, alias_list in (aliases_raw or {}).items():
        owner_nk = _norm_key(alias_owner)

        ct = _resolve_alias_owner(owner_nk, title_by_norm)
        if not ct:
            continue

        # Index the alias phrases pointing to that canonical title
        for alias in alias_list or []:
            nk = _norm_key(alias)
            if nk:
                _add_index_key(index, nk, ct)
                for var in _last_token_variants(nk.split()):
                    _add_index_key(index, var, ct)


def _build_search_index(
    sets: Dict[str, Dict[str, Any]], aliases_raw: Dict[str, List[str]]
) -> Dict[str, str]:
    """
    Constructs the normalized lookup index mapping keys -> canonical titles.
    Refactored to use sub-helpers for logic isolation.
    """
    index: Dict[str, str] = {}
    title_by_norm: Dict[str, str] = {}

    _index_direct_titles(sets, index, title_by_norm)
    _process_aliases(aliases_raw, index, title_by_norm)

    return index


@lru_cache(maxsize=1)
def _compiled_config() -> Dict[str, Any]:
    """
    Loads and compiles config into optimized lookups.
    Refactored to use distinct build phases.
    """
    raw = _merge_config(_from_yaml_blob(), _from_settings_object())

    aliases_raw: Dict[str, List[str]] = {}
    if isinstance(raw.get("aliases"), dict):
        aliases_raw = {
            str(k): [str(x) for x in (v or [])]
            for k, v in raw["aliases"].items()
        }

    sets_raw = dict(raw.get("sets") or {})
    sets = _build_sets_map(sets_raw)
    index = _build_search_index(sets, aliases_raw)

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
    """
    if not category:
        return None
    cfg = _compiled_config()
    key = _norm_key(category)
    title = cfg["index"].get(key)

    if not title:
        for var in _last_token_variants(key.split()):
            title = cfg["index"].get(var)
            if title:
                break

    if not title:
        return None

    names = list(cfg["sets"][title]["names"])
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
