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
from collections.abc import Iterable
from functools import lru_cache
from pathlib import Path
from typing import Any

import yaml  # PyYAML

from app.agent.canonical_catalog import BUILTIN_CANONICAL_SETS

# Primary config object (if available). AC-QUALITY-R2-IMPORT-1: only ImportError
# is suppressed; other exceptions (e.g. malformed YAML, env var typos) MUST
# surface so misconfiguration is not silently masked at import time.
try:
    from app.core.config import settings  # type: ignore
except ImportError:
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


def _safe_yaml_load(path: Path) -> dict[str, Any]:
    try:
        if not path.exists():
            return {}
        with path.open("r", encoding="utf-8") as f:
            return yaml.safe_load(f) or {}
    except Exception:
        return {}


def _from_settings_object() -> dict[str, Any]:
    """Best effort pull from app Settings."""
    try:
        return getattr(settings, "canonical_sets", {}) or {}
    except Exception:
        return {}


def _from_yaml_blob() -> dict[str, Any]:
    """Read from the same YAML file used by the app (non-secret side)."""
    data = _safe_yaml_load(_default_appconfig_path())
    q = (data or {}).get("quizzical") or {}
    return (q.get("canonical_sets") or {}) if isinstance(q, dict) else {}


def _union_alias_lists(base: Any, overlay: Any) -> list[str]:
    """Union two alias lists, preserving order and de-duplicating case-folded.

    Code-defined aliases come first, then any new App-Config aliases. This makes
    the built-in catalog a *floor*: App-Config can ADD aliases for a title but
    cannot silently DROP the reviewed code-defined ones (which is exactly the
    drift that dropped "big 5"/"ffm" from Big Five before this change).
    """
    out: list[str] = []
    seen: set[str] = set()
    for src in (base, overlay):
        if not isinstance(src, list):
            continue
        for item in src:
            s = str(item).strip()
            key = s.casefold()
            if s and key not in seen:
                seen.add(key)
                out.append(s)
    return out


def _merge_config(a: dict[str, Any], b: dict[str, Any]) -> dict[str, Any]:
    """Shallow merge with section-aware overlay for set and alias maps.

    ``sets`` overlay (App-Config may correct a set's membership), but ``aliases``
    are UNIONED per-title so code-defined aliases survive App-Config drift.
    """
    out = dict(a or {})
    for k, v in (b or {}).items():
        if k in {"aliases", "sets"} and isinstance(out.get(k), dict) and isinstance(v, dict):
            merged_section = dict(out[k])
            if k == "aliases":
                for title, overlay_list in v.items():
                    merged_section[title] = _union_alias_lists(
                        merged_section.get(title), overlay_list
                    )
            else:
                merged_section.update(v)
            out[k] = merged_section
            continue
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
# Leading interrogative frame. The possessive group ("my"/"your"/...) is what
# turns "What is my DISC type" into "DISC type" (then the " type" canon suffix
# yields "DISC"); previously it mangled to "is my DISC type".
_LEADING_QFRAME_RE = re.compile(
    r"^(?:what|which|who|where|when|how)\s+"
    r"(?:(?:is|are|am|was|were|do|does|did|should|would|could|can|will)\s+)?"
    r"(?:(?:i|you|we|they|someone|one)\s+)?"
    r"(?:(?:my|your|our|their|his|her|its)\s+)?"
    r"(?:be\s+)?",
    re.IGNORECASE,
)
_LEADING_IMPERATIVE_RE = re.compile(r"^(?:list|name|show|give|find)\s+(?:me\s+)?(?:the\s+)?", re.IGNORECASE)
_LEADING_SORT_ME_RE = re.compile(r"^(?:sort\s+me\s+into|assign\s+me\s+a|put\s+me\s+in(?:to)?)\s+", re.IGNORECASE)
# Leading possessive ("my "/"your "/...) for bare phrasings like "my love language".
_LEADING_POSSESSIVE_RE = re.compile(r"^(?:my|your|our|their|his|her|its)\s+", re.IGNORECASE)
# Trailing "am i"/"are you" fit phrasing ("which hogwarts house am i").
_TRAILING_FIT_RE = re.compile(
    r"\s+(?:am\s*i|are\s+you|fits?\s+me|matches?\s+me)\s*$",
    re.IGNORECASE,
)
_TRAILING_TOOL_RE = re.compile(
    r"\s*(?:quiz|test|assessment|exam|sorter|sorting|generator|checker|finder|picker|guide|tier\s*list)\s*$",
    re.IGNORECASE,
)
# Trailing descriptors users append to a framework name. Includes a combined
# "personality type(s)" so "DISC personality type" collapses to "DISC" in one
# sweep. Ordered so the longer combined forms are tried before the bare ones.
_TRAILING_DESCRIPTOR_RE = re.compile(
    r"\s*(?:"
    r"personality\s+types?"
    r"|personalities"
    r"|personality"
    r"|styles?"
    r"|results?"
    r")\s*$",
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
    s = _LEADING_POSSESSIVE_RE.sub("", s).strip()
    s = _LEADING_ARTICLE_RE.sub("", s).strip()
    s = _LEADING_TYPES_OF_RE.sub("", s).strip()

    # Trailing fit phrasing ("... am i") and combined descriptors
    # ("... personality type") before the single-token canon suffixes so the
    # longer phrasings collapse fully (e.g. "DISC personality type" -> "DISC").
    s = _TRAILING_FIT_RE.sub("", s).strip()
    s = _TRAILING_DESCRIPTOR_RE.sub("", s).strip()

    low = s.lower()
    for suf in _CANON_SUFFIXES:
        if low.endswith(suf):
            s = s[: -len(suf)].strip()
            break

    s = _TRAILING_TOOL_RE.sub("", s).strip()
    s = _TRAILING_MARKETING_RE.sub("", s).strip()
    return s


def _tokenize_for_key(s: str) -> list[str]:
    s = _strip_accents(s)
    toks = _WORD_RE.findall(s.lower())
    return toks


def _singular(t: str) -> str | None:
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


def _plural(t: str) -> str | None:
    """Attempt simple pluralization of the last token."""
    low = t.lower()
    if low.endswith(("s", "ss")):
        return None
    if low.endswith("y") and len(t) > 1 and t[-2].lower() not in "aeiou":
        return t[:-1] + "ies"
    if low.endswith(("s", "x", "z", "ch", "sh")):
        return t + "es"
    return t + "s"


def _last_token_variants(tokens: list[str]) -> Iterable[str]:
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

def _extract_names(entry: Any) -> tuple[list[str], int | None]:
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


# Key provenance, used to resolve collisions when two different sources want to
# claim the same normalized key. Higher number == higher authority.
#   EXACT_TITLE    a set title indexed verbatim (e.g. "Oceans" -> "oceans").
#                  The strongest title-side claim.
#   ACRONYM_ALIAS  an alias that is the initialism of its set's member names
#                  (e.g. "OCEAN" = Openness/Conscientiousness/Extraversion/
#                  Agreeableness/Neuroticism). These win even over exact titles.
#   ALIAS          an explicit, author-declared alias phrase.
#   TITLE_VARIANT  a singular/plural variant *derived* from a title (e.g. the
#                  geographic title "Oceans" derives the variant "ocean"). These
#                  are the weakest: an explicit alias may override them.
#   ALIAS_VARIANT  a singular/plural variant derived from an alias.
_ORIGIN_TITLE_VARIANT = 0
_ORIGIN_ALIAS_VARIANT = 1
_ORIGIN_ALIAS = 2
_ORIGIN_EXACT_TITLE = 3
_ORIGIN_ACRONYM_ALIAS = 4


def _add_index_key(
    index: dict[str, str],
    key: str,
    title: str,
    *,
    origin: int = _ORIGIN_ALIAS,
    origins: dict[str, int] | None = None,
) -> None:
    """Insert ``key -> title`` honoring source precedence.

    First write wins among equal-authority sources (preserving the historical
    first-write-wins behavior), but a higher-authority source may overwrite a
    key previously claimed by a lower-authority one. This is what lets an
    explicit alias ("ocean" for Big Five) reclaim a key that a *derived* title
    variant (geographic "Oceans" -> "ocean") grabbed first, without disturbing
    the exact geographic title key "oceans".
    """
    k = key.strip()
    if not k:
        return
    if origins is None:
        # Back-compat path: behave as plain first-write-wins.
        if k not in index:
            index[k] = title
        return
    existing_origin = origins.get(k)
    if existing_origin is None or origin > existing_origin:
        index[k] = title
        origins[k] = origin


def _build_sets_map(sets_raw: dict[str, Any]) -> dict[str, dict[str, Any]]:
    """Parses raw set definitions into a clean dictionary."""
    sets: dict[str, dict[str, Any]] = {}
    for title, entry in (sets_raw or {}).items():
        names, hint = _extract_names(entry)
        if names:
            sets[str(title)] = {"names": names, "count_hint": hint}
    return sets


def _index_direct_titles(
    sets: dict[str, Any],
    index: dict[str, str],
    title_by_norm: dict[str, str],
    origins: dict[str, int],
) -> None:
    """Populate index with direct set titles and their singular/plural variants."""
    for title in sets.keys():
        nk = _norm_key(title)
        if nk:
            _add_index_key(index, nk, title, origin=_ORIGIN_EXACT_TITLE, origins=origins)
            title_by_norm[nk] = title
            for var in _last_token_variants(nk.split()):
                _add_index_key(index, var, title, origin=_ORIGIN_TITLE_VARIANT, origins=origins)
                title_by_norm.setdefault(var, title)


def _resolve_alias_owner(
    owner_nk: str, title_by_norm: dict[str, str]
) -> str | None:
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


def _is_acronym_alias(alias_nk: str, member_names: list[str]) -> bool:
    """Is ``alias_nk`` the initialism of its set's member names?

    True when the alias is a single short all-letter token whose letters match
    the leading letters of the set's members in order (e.g. "ocean" ->
    Openness/Conscientiousness/Extraversion/Agreeableness/Neuroticism). Such
    aliases are deliberate, high-signal labels and are allowed to win key
    collisions even against an exact (but coincidental) set title.
    """
    if not alias_nk or " " in alias_nk:
        return False
    if not alias_nk.isalpha() or not (2 <= len(alias_nk) <= 6):
        return False
    initials = "".join(n.strip()[0] for n in member_names if n.strip())
    return initials.casefold() == alias_nk.casefold()


def _process_aliases(
    aliases_raw: dict[str, list[str]],
    sets: dict[str, dict[str, Any]],
    index: dict[str, str],
    title_by_norm: dict[str, str],
    origins: dict[str, int],
) -> None:
    """Map user-defined aliases to their canonical titles in the index."""
    for alias_owner, alias_list in (aliases_raw or {}).items():
        owner_nk = _norm_key(alias_owner)

        ct = _resolve_alias_owner(owner_nk, title_by_norm)
        if not ct:
            continue

        member_names = (sets.get(ct) or {}).get("names") or []

        # Index the alias phrases pointing to that canonical title
        for alias in alias_list or []:
            nk = _norm_key(alias)
            if not nk:
                continue
            # An explicit alias outranks a *derived* title variant; an alias that
            # is the set's initialism (e.g. "OCEAN") outranks even an exact title.
            alias_origin = (
                _ORIGIN_ACRONYM_ALIAS if _is_acronym_alias(nk, member_names) else _ORIGIN_ALIAS
            )
            _add_index_key(index, nk, ct, origin=alias_origin, origins=origins)
            for var in _last_token_variants(nk.split()):
                _add_index_key(index, var, ct, origin=_ORIGIN_ALIAS_VARIANT, origins=origins)


def _build_search_index(
    sets: dict[str, dict[str, Any]], aliases_raw: dict[str, list[str]]
) -> dict[str, str]:
    """
    Constructs the normalized lookup index mapping keys -> canonical titles.
    Refactored to use sub-helpers for logic isolation.
    """
    index: dict[str, str] = {}
    title_by_norm: dict[str, str] = {}
    origins: dict[str, int] = {}

    _index_direct_titles(sets, index, title_by_norm, origins)
    _process_aliases(aliases_raw, sets, index, title_by_norm, origins)

    return index


@lru_cache(maxsize=1)
def _compiled_config() -> dict[str, Any]:
    """
    Loads and compiles config into optimized lookups.
    Refactored to use distinct build phases.
    """
    raw = _merge_config(
        _merge_config(BUILTIN_CANONICAL_SETS, _from_yaml_blob()),
        _from_settings_object(),
    )

    aliases_raw: dict[str, list[str]] = {}
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


def canonical_for(category: str | None) -> list[str] | None:
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
    out: list[str] = []
    for n in names:
        k = n.strip().casefold()
        if k and k not in seen:
            seen.add(k)
            out.append(n.strip())
    return out or None


def count_hint_for(category: str | None) -> int | None:
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
