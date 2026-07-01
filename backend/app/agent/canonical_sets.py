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
# claim the same normalized (case-blind) index key. Higher number == higher
# authority. (Uppercase initialisms like "OCEAN" are handled separately in a
# case-sensitive acronym map, NOT via this case-blind index — see
# ``_acronym_key_for``.)
#   EXACT_TITLE    a set title indexed verbatim (e.g. "Oceans" -> "oceans").
#   ALIAS          an explicit, author-declared alias phrase. May override a
#                  *derived* title variant but never an exact title.
#   TITLE_VARIANT  a singular/plural variant *derived* from a title (e.g. the
#                  geographic title "Oceans" derives the variant "ocean").
#   ALIAS_VARIANT  a singular/plural variant *derived* from an alias. This is the
#                  WEAKEST source: it must NOT outrank another set's title-derived
#                  variant (otherwise e.g. the "Musical Modes (7)" alias variant
#                  "church mode" would steal the "Church Modes" title variant).
#
# Precedence (low -> high): ALIAS_VARIANT < TITLE_VARIANT < ALIAS < EXACT_TITLE.
_ORIGIN_ALIAS_VARIANT = 0
_ORIGIN_TITLE_VARIANT = 1
_ORIGIN_ALIAS = 2
_ORIGIN_EXACT_TITLE = 3


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
    """Populate index with direct set titles and their singular/plural variants.

    Titles are indexed under their fully noise-stripped key (matching the prior
    topology); the full-original-first lookup at query time additionally tries
    the un-stripped light key, but the index itself is not widened so existing
    key ownership/precedence is preserved.
    """
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


def _acronym_key_for(alias_nk: str, member_names: list[str]) -> str | None:
    """Return the UPPERCASE acronym key if ``alias_nk`` is the set's initialism.

    Detection is by content: a single short all-letter token whose letters are
    the leading letters of the set's members in order (e.g. "ocean" ->
    Openness/Conscientiousness/Extraversion/Agreeableness/Neuroticism). Returns
    the uppercase key (e.g. "OCEAN") used by the *case-sensitive* query-time
    acronym map, or None when it is not an acronym of the set.

    Crucially this does NOT touch the case-blind ``index``: the acronym only wins
    when the user actually TYPES it uppercase (handled in ``canonical_for``), so
    lowercase "ocean"/"Ocean" still resolves to the geographic set.
    """
    if not alias_nk or " " in alias_nk:
        return None
    if not alias_nk.isalpha() or not (2 <= len(alias_nk) <= 6):
        return None
    initials = "".join(n.strip()[0] for n in member_names if n.strip())
    if initials and initials.casefold() == alias_nk.casefold():
        return alias_nk.upper()
    return None


def _process_aliases(
    aliases_raw: dict[str, list[str]],
    sets: dict[str, dict[str, Any]],
    index: dict[str, str],
    title_by_norm: dict[str, str],
    origins: dict[str, int],
    acronyms: dict[str, str],
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
            # If the alias is the set's initialism (e.g. "ocean" -> OCEAN), always
            # record it in the CASE-SENSITIVE acronym map (consulted only for
            # uppercase raw queries).
            ak = _acronym_key_for(nk, member_names)
            if ak is not None:
                acronyms.setdefault(ak, ct)
                # Only DIVERT it out of the case-blind index when the key is
                # already owned by a DIFFERENT set (a real collision, e.g.
                # geographic "Oceans" already owns "ocean"). For a non-colliding
                # acronym (RIASEC, VARK) keep indexing it normally so the common
                # lowercase form still resolves.
                existing = index.get(nk)
                if existing is not None and existing != ct:
                    continue
            _add_index_key(index, nk, ct, origin=_ORIGIN_ALIAS, origins=origins)
            for var in _last_token_variants(nk.split()):
                _add_index_key(index, var, ct, origin=_ORIGIN_ALIAS_VARIANT, origins=origins)


def _build_search_index(
    sets: dict[str, dict[str, Any]], aliases_raw: dict[str, list[str]]
) -> tuple[dict[str, str], dict[str, str]]:
    """
    Constructs the normalized lookup index mapping keys -> canonical titles, plus
    a case-sensitive acronym map (UPPERCASE initialism -> title).
    """
    index: dict[str, str] = {}
    title_by_norm: dict[str, str] = {}
    origins: dict[str, int] = {}
    acronyms: dict[str, str] = {}

    _index_direct_titles(sets, index, title_by_norm, origins)
    _process_aliases(aliases_raw, sets, index, title_by_norm, origins, acronyms)

    return index, acronyms


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
    index, acronyms = _build_search_index(sets, aliases_raw)

    return {
        "aliases": aliases_raw,
        "sets": sets,
        "index": index,
        "acronyms": acronyms,
    }


# =============================================================================
# Title resolution (full-original-first, strip-on-miss)
# =============================================================================

# Minimum length for a normalized lookup key. Below this we treat the strip as
# having eaten the topic and fall back to the un-stripped form (LOW fix): a
# trailing-descriptor/possessive strip must never reduce a real topic to ""/"a".
_MIN_KEY_LEN = 2


@lru_cache(maxsize=4096)
def _norm_key_light(raw: str) -> str:
    """Normalize for matching WITHOUT the aggressive noise stripping.

    Accent-strip + tokenize only. This is the key used for the "try the full
    original string first" pass so that a real title/alias whose tail looks like
    a descriptor (e.g. "Attachment Styles", "DISC Styles") matches verbatim
    before any descriptor stripping is attempted.
    """
    if not isinstance(raw, str):
        return ""
    return " ".join(_tokenize_for_key(raw.strip()))


def _lookup_in_index(index: dict[str, str], key: str) -> str | None:
    """Exact then singular/plural-variant lookup for a single normalized key."""
    if not key or len(key) < _MIN_KEY_LEN:
        return None
    title = index.get(key)
    if title:
        return title
    for var in _last_token_variants(key.split()):
        title = index.get(var)
        if title:
            return title
    return None


def _resolve_title(category: str | None) -> str | None:
    """Resolve a raw category string to a canonical set title.

    Order (per the canonical-matching contract):
      1. Case-sensitive acronym map — only when the user TYPED an uppercase
         acronym (e.g. "OCEAN"); lowercase "ocean" skips this and resolves to
         the geographic set below.
      2. The FULL original string (light normalization, no noise strip).
      3. On a miss, the noise-stripped key (descriptors/possessives removed),
         with an empty/too-short guard that falls back to the light key.

    The returned value is the title only; callers map it to names/counts. The
    raw ``category`` string itself is never mutated for downstream consumers.
    """
    if not category:
        return None
    cfg = _compiled_config()

    # 1) Case-sensitive acronym (uppercase only).
    stripped_raw = category.strip()
    if stripped_raw and stripped_raw == stripped_raw.upper():
        ak = _norm_key_light(stripped_raw).upper()
        title = cfg["acronyms"].get(ak)
        if title:
            return title

    index = cfg["index"]

    # 2) Full original first (light normalization).
    light_key = _norm_key_light(category)
    title = _lookup_in_index(index, light_key)
    if title:
        return title

    # 3) Strip-on-miss (noise-stripped key), guarding against empty/short keys.
    full_key = _norm_key(category)
    if full_key and len(full_key) >= _MIN_KEY_LEN and full_key != light_key:
        title = _lookup_in_index(index, full_key)
        if title:
            return title

    return None


# =============================================================================
# Public API
# =============================================================================


def canonical_for(category: str | None) -> list[str] | None:
    """
    Returns the canonical list of names for a category, if configured.
    """
    title = _resolve_title(category)
    if not title:
        return None

    cfg = _compiled_config()
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
    title = _resolve_title(category)
    if not title:
        return None

    cfg = _compiled_config()
    hint = cfg["sets"].get(title, {}).get("count_hint")
    if isinstance(hint, int) and hint > 0:
        return hint

    names = cfg["sets"].get(title, {}).get("names") or []
    return len(names) if names else None
