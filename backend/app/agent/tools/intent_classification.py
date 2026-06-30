# backend/app/agent/tools/intent_classification.py
"""
Intent & topic analysis aligned to the v2 strategy.

Configuration is now loaded primarily from the *application settings*,
consistent with other modules (e.g., `from app.core.config import settings`).

Precedence:
  1) App settings object: settings.topic_keywords (if present)
  2) Azure App Configuration (DISABLED stub returns None)
  3) Local appconfig YAML (backend/appconfig.local.yaml or APP_CONFIG_LOCAL_PATH)
  4) Embedded local defaults in this file

The local YAML path is hot-reloaded on mtime changes.
"""

from __future__ import annotations

import os
import re
import unicodedata
from pathlib import Path
from typing import Any

import yaml  # PyYAML

# === Load global app settings (consistent with other files) ===================
try:
    from app.core.config import settings as _base_settings  # type: ignore
except Exception:  # pragma: no cover - settings import may fail in isolated tests
    _base_settings = None  # type: ignore

from app.agent._settings_proxy import SettingsProxy as _SettingsProxy

settings = _SettingsProxy(_base_settings)

# ---------------------------------------------------------------------
# Config loading (App settings → Azure → appconfig.local.yaml → defaults)
# ---------------------------------------------------------------------

_APP_CONFIG_ENV = "APP_CONFIG_LOCAL_PATH"
_APPCONFIG_KEY_ROOT = ("quizzical", "topic_keywords")


class _ConfigCache:
    path: Path | None
    mtime: float
    data: dict[str, Any]

    def __init__(self):
        self.path = None
        self.mtime = -1.0
        self.data = {}


_CACHE = _ConfigCache()


def _default_appconfig_path() -> Path:
    """
    Default local config path: backend/appconfig.local.yaml
    (same as backend/app/core/config.py)
    """
    here = Path(__file__).resolve()
    backend_dir = here.parents[3]  # .../backend   (tools -> agent -> app -> backend)
    return backend_dir / "appconfig.local.yaml"


def _get_appconfig_path() -> Path:
    env = os.getenv(_APP_CONFIG_ENV)
    return Path(env).expanduser() if env else _default_appconfig_path()


def _safe_yaml_load(path: Path) -> dict[str, Any]:
    try:
        if not path.exists():
            return {}
        with path.open("r", encoding="utf-8") as f:
            return yaml.safe_load(f) or {}
    except Exception:
        return {}


def _load_from_azure_app_config() -> dict[str, Any] | None:
    """
    Placeholder for Azure App Config ingestion of the *topic keywords* subtree.
    Return a nested dict whose root contains 'quizzical' → 'topic_keywords'.
    Disabled during local/dev bring-up per product plan.
    """
    # To enable later, wire to the same Azure client logic used in core/config.py.
    return None


def _load_from_app_settings() -> dict[str, Any] | None:
    """
    Prefer app-global settings if an attribute `topic_keywords` exists.
    This mirrors how other modules fetch configuration from the shared Settings.
    """
    try:
        tk = getattr(settings, "topic_keywords", None)
        if isinstance(tk, dict) and tk:
            return {"quizzical": {"topic_keywords": tk}}
    except Exception:
        pass
    return None


def _load_from_appconfig_yaml() -> dict[str, Any] | None:
    """Read topic keywords from local appconfig YAML."""
    raw = _safe_yaml_load(_get_appconfig_path())
    cur: Any = raw
    for k in _APPCONFIG_KEY_ROOT:
        if isinstance(cur, dict):
            cur = cur.get(k)
        else:
            cur = None
            break
    return cur if isinstance(cur, dict) else None


def _embedded_defaults() -> dict[str, Any]:
    # Minimal defaults (kept from the previous file). The full dataset should
    # live in appconfig under quizzical.topic_keywords.
    return {
        "version": 2,
        "intents": {
            "sorting": ["sort", "house", "faction", "guild", "bucket"],
            "alignment": ["alignment", "lawful", "chaotic", "good", "evil", "neutral"],
            "compatibility": ["compatib", "match", "ship", "pair", "fit"],
            "team_role": ["role", "team", "position"],
            "vibe": ["vibe", "aesthetic", "core", "style"],
            "power_tier": ["tier", "power", "ranking", "rank"],
            "timeline_era": ["era", "timeline", "generation", "gen "],
            "career": ["career", "job", "profession", "specialty"],
        },
        "shapes": {
            "celestial": ["galaxy", "galaxies", "nebula", "star", "planet", "constellation"],
            "place_or_institution": ["school", "university", "college", "district", "city", "state", "province", "region", "county", "campus", "high school"],
            "object": ["lamp", "shade", "chair", "sofa", "device", "tool", "gadget", "instrument", "vehicle", "garment"],
            "person_or_character": ["character", "hero", "villain", "protagonist", "antagonist", "cast", "crew", "npc", "class", "house"],
        },
        "domains": {
            "media_characters": ["film", "movie", "series", "anime", "manga", "franchise", "character"],
            "sports_leagues_teams": ["nba", "nfl", "premier league", "mlb", "nhl", "club", "team", "league"],
            "sports_positions_disciplines": ["position", "striker", "goalkeeper", "setter", "sprinter", "breaststroke"],
            "music_artists_acts": ["k-pop", "grunge", "jazz", "motown", "band", "artist", "orchestra", "choir"],
            "frameworks_types_systems": ["mbti", "enneagram", "disc", "zodiac", "tarot", "hogwarts", "alignment"],
            "serious_professions_profiles": ["doctor", "physician", "lawyer", "attorney", "engineer", "nurse", "resume", "cv"],
            "animals_species_breeds": ["cat", "dog", "horse", "bird", "shark", "bear", "butterfly", "breed", "species"],
            "plants_gardening": ["succulent", "orchid", "rose", "tree", "houseplant", "garden"],
            "objects_devices_products": ["instrument", "camera", "laptop", "browser", "language", "watch", "smartphone", "console"],
            "food_drink_styles": ["cheese", "bread", "pizza", "bbq", "wine", "beer", "cocktail", "coffee", "tea", "spice"],
            "places_regions_cities": ["city", "cities", "country", "countries", "capital", "island", "river", "region", "park"],
            "mythology_folklore_figures": ["mythology", "legend", "folklore", "gods", "deities", "archetype"],
            "weather_nature_geoscience": ["biome", "climate", "volcano", "mineral", "gemstone", "cloud", "landform"],
            "vehicles_transport_modes": ["car", "motorcycle", "bicycle", "train", "airline", "ship", "ev"],
            "art_design_styles": ["graphic design", "typography", "color theory", "photography", "cinematography", "fashion design"],
            "architecture_interior_styles": ["architectural style", "house style", "roof", "tile", "kitchen layout", "lighting fixture"],
        },
        "media_hints": [
            "season", "episode", "saga", "trilogy", "universe", "series", "show", "sitcom", "drama",
            "film", "movie", "novel", "book", "manga", "anime", "cartoon", "comic", "graphic novel",
            "musical", "play", "opera", "broadway", "videogame", "video game", "franchise", "character", "cast"
        ],
        "serious_hints": [
            "disc", "myers", "mbti", "enneagram", "big five", "ocean", "hexaco", "strengthsfinder", "attachment style",
            "aptitude", "assessment", "clinical", "medical", "doctor", "physician", "lawyer", "attorney", "engineer",
            "accountant", "scientist", "resume", "cv", "career", "specialty", "role", "rank"
        ],
        "type_synonyms": [
            "type", "types", "kind", "kinds", "style", "styles", "variety", "varieties", "flavor", "flavors",
            "breed", "breeds", "class", "category", "archetype", "persona", "identity", "profile", "subtype", "variant", "path", "lineage"
        ],
    }


def _dedupe_list(items: list[Any]) -> list[Any]:
    seen = set()
    out: list[Any] = []
    for x in items or []:
        k = str(x).strip().casefold()
        if k and k not in seen:
            seen.add(k)
            out.append(x)
    return out


def _merge_with_defaults(user: dict[str, Any]) -> dict[str, Any]:
    base = _embedded_defaults()

    def merge_list(key: str):
        base[key] = _dedupe_list((base.get(key, []) or []) + (user.get(key, []) or []))

    def merge_map_of_lists(key: str):
        merged = dict(base.get(key, {}) or {})
        for k, v in (user.get(key, {}) or {}).items():
            merged[k] = _dedupe_list((merged.get(k, []) or []) + (v or []))
        base[key] = merged

    merge_map_of_lists("intents")
    merge_map_of_lists("shapes")
    merge_map_of_lists("domains")
    merge_list("media_hints")
    merge_list("serious_hints")
    merge_list("type_synonyms")
    base["version"] = user.get("version", base.get("version", 2))
    return base


def _maybe_reload() -> dict[str, Any]:
    """
    Load in priority order:
      1) App settings (settings.topic_keywords)
      2) Azure App Config (disabled → returns None)
      3) Local appconfig YAML (hot-reload on mtime)
      4) Embedded defaults
    """
    # 1) App settings (preferred)
    settings_blob = _load_from_app_settings()
    if isinstance(settings_blob, dict):
        tk = (settings_blob.get("quizzical") or {}).get("topic_keywords") or {}
        if isinstance(tk, dict) and tk:
            return _merge_with_defaults(tk)

    # 2) Azure (disabled now)
    azure_blob = _load_from_azure_app_config()
    if isinstance(azure_blob, dict):
        q = azure_blob.get("quizzical") or {}
        tk = q.get("topic_keywords") or {}
        if isinstance(tk, dict) and tk:
            return _merge_with_defaults(tk)

    # 3) Local appconfig YAML (hot-reload)
    path = _get_appconfig_path()
    try:
        mtime = path.stat().st_mtime
    except Exception:
        # If nothing cached, bootstrap with defaults
        if not _CACHE.data:
            _CACHE.path = None
            _CACHE.mtime = -1.0
            _CACHE.data = _embedded_defaults()
        return _CACHE.data

    if _CACHE.path != path or _CACHE.mtime != mtime:
        data = _load_from_appconfig_yaml() or {}
        _CACHE.path = path
        _CACHE.mtime = mtime
        try:
            _CACHE.data = _merge_with_defaults(data)
        except Exception:
            # Preserve prior cache, else fall back
            _CACHE.data = _CACHE.data or _embedded_defaults()

    return _CACHE.data


# ---------------------------------------------------------------------
# Helpers (unchanged)
# ---------------------------------------------------------------------

def _norm(s: str) -> str:
    s = s or ""
    s = unicodedata.normalize("NFKC", s)
    return s.casefold().strip()


def _text_corpus(category: str, synopsis: dict | None) -> str:
    parts = [category or ""]
    if isinstance(synopsis, dict):
        for k in ("summary", "synopsis", "synopsis_text", "title"):
            v = synopsis.get(k)
            if isinstance(v, str) and v.strip():
                parts.append(v)
    return _norm(" ".join(parts))


def _is_regex_token(tok: str) -> bool:
    return len(tok) >= 2 and tok.startswith("/") and tok.endswith("/")


def _token_score(text: str, token: str) -> float:
    """Heuristic scoring: regex > whole word > substring."""
    if not token:
        return 0.0
    if _is_regex_token(token):
        try:
            pat = re.compile(token[1:-1], re.IGNORECASE)
            return 2.0 if pat.search(text) else 0.0
        except re.error:
            return 0.0
    t = token.casefold()
    if not t:
        return 0.0
    if f" {t} " in f" {text} ":
        return 1.25
    return 1.0 if t in text else 0.0


def _simple_singularize(s: str) -> str:
    """Cheap singularization with a few irregulars; avoids heavyweight deps."""
    s = (s or "").strip()
    if not s:
        return s
    irregular = {
        "people": "person", "children": "child", "men": "man", "women": "woman",
        "geese": "goose", "mice": "mouse", "teeth": "tooth", "feet": "foot"
    }
    low = s.lower()
    if low in irregular:
        return irregular[low]
    if low.endswith("ies") and len(s) > 3:
        return s[:-3] + "y"
    if low.endswith("ses") and len(s) > 3:
        return s[:-2]
    if low.endswith("xes") and len(s) > 3:
        return s[:-2]
    if low.endswith("s") and not low.endswith("ss"):
        return s[:-1]
    return s


def _looks_like_media_title(raw: str, media_hints: list[str]) -> bool:
    """
    Treat as media if:
    - explicit media hints present OR
    - ends with 'characters' OR
    - Title-ish multi-word string with stopwords like 'of/and/the' (e.g., 'Lord of the Rings').
    """
    t = (raw or "").strip()
    if not t:
        return False
    lc = t.casefold()
    if lc.endswith(" characters"):
        return True
    if any(h in lc for h in media_hints or []):
        return True
    words = t.split()
    if len(words) >= 2:
        capitals = sum(1 for w in words if w[:1].isupper())
        if capitals >= max(2, len(words) // 2):
            if any(w.lower() in {"of", "and", "the", "a", "to", "in"} for w in words):
                return True
    return False


def _score_map(text: str, tokens: list[Any]) -> float:
    total = 0.0
    for tok in tokens or []:
        if isinstance(tok, dict):
            t = tok.get("token", "")
            w = float(tok.get("weight", 1.0))
        else:
            t, w = str(tok), 1.0
        total += _token_score(text, t) * w
    return total


def _ensure_types_of_prefix(label: str) -> str:
    """Ensures the label starts with 'Types of' if not already present."""
    s = (label or "").strip()
    # If already like "type of x" or "types of x", keep it (normalize capital T)
    if re.match(r"(?i)^\s*types?\s+of\s+", s):
        return s[0].upper() + s[1:] if s else s
    return f"Types of {s}"


# ---------------------------------------------------------------------
# Intent classification (Refactored to reduce complexity)
# ---------------------------------------------------------------------

_DOMAIN_INTENT_FALLBACKS = {
    "sports_positions_disciplines": "team_role",
    "serious_professions_profiles": "career",
    "frameworks_types_systems": "identify",
    "media_characters": "identify",
    "animals_species_breeds": "identify",
    "objects_devices_products": "identify",
    "food_drink_styles": "identify",
    "places_regions_cities": "identify",
    "music_artists_acts": "identify",
    "mythology_folklore_figures": "identify",
    "weather_nature_geoscience": "identify",
    "vehicles_transport_modes": "identify",
    "art_design_styles": "vibe",
    "architecture_interior_styles": "vibe",
    "plants_gardening": "identify",
    "sports_leagues_teams": "identify",
}


def classify_intent(category: str, synopsis: dict | None = None) -> dict[str, Any]:
    """
    Soft, data-driven intent classification.
    Returns: {"primary": str, "scores": {intent: float}, "shape": str}
    """
    cfg = _maybe_reload()
    text = _text_corpus(category, synopsis)

    # Score intents
    scores: dict[str, float] = {}
    for intent, tokens in (cfg.get("intents") or {}).items():
        total = _score_map(text, tokens)
        if total > 0:
            scores[intent] = total

    # Advisory shape
    shape_scores: dict[str, float] = {}
    for shape, tokens in (cfg.get("shapes") or {}).items():
        stotal = _score_map(text, tokens)
        if stotal > 0:
            shape_scores[shape] = stotal
    shape = max(shape_scores.items(), key=lambda kv: kv[1])[0] if shape_scores else "unspecified"

    # Primary intent with domain-aware fallback
    if scores:
        primary = max(scores.items(), key=lambda kv: kv[1])[0]
    else:
        domain = _primary_domain(category, synopsis)
        primary = _DOMAIN_INTENT_FALLBACKS.get(domain, "identify")

    return {"primary": primary, "scores": scores, "shape": shape}


# ---------------------------------------------------------------------
# Topic analysis (Refactored to reduce complexity)
# ---------------------------------------------------------------------

_SERIOUS_MAPPING = {
    "doctor": "Doctor Specialties",
    "doctors": "Doctor Specialties",
    "physician": "Doctor Specialties",
    "physicians": "Doctor Specialties",
    "lawyer": "Legal Practice Areas",
    "lawyers": "Legal Practice Areas",
    "attorney": "Legal Practice Areas",
    "attorneys": "Legal Practice Areas",
    "engineer": "Engineering Disciplines",
    "engineers": "Engineering Disciplines",
    "nurse": "Nursing Specialties",
    "nurses": "Nursing Specialties",
}

_TYPE_FOCUSED_DOMAINS = {
    "animals_species_breeds",
    "plants_gardening",
    "objects_devices_products",
    "food_drink_styles",
    "places_regions_cities",
    "mythology_folklore_figures",
    "weather_nature_geoscience",
    "vehicles_transport_modes",
    "art_design_styles",
    "architecture_interior_styles",
    "frameworks_types_systems",
    "sports_positions_disciplines",
}


def _primary_domain(category: str, synopsis: dict | None) -> str:
    cfg = _maybe_reload()
    text = _text_corpus(category, synopsis)
    domains: dict[str, list[Any]] = cfg.get("domains") or {}

    scored: list[tuple[str, float]] = []
    for name, tokens in domains.items():
        scored.append((name, _score_map(text, tokens)))

    # Heuristic bump for media-looking titles
    if _looks_like_media_title(category, cfg.get("media_hints", []) or []):
        scored.append(("media_characters", 1.5))

    if not scored:
        return ""

    best_name, best_score = max(scored, key=lambda kv: kv[1])
    return best_name if best_score > 0.0 else ""


def _handle_serious_topic(raw: str) -> tuple[str, str, str, bool]:
    """Handles serious professions/profiles logic."""
    base = _simple_singularize(raw) or "Profession"
    normalized = _SERIOUS_MAPPING.get(base.lower(), _ensure_types_of_prefix(base))
    return normalized, "types", "factual", False


# Subgroup nouns inside fictional universes that name *non-character* outcomes
# (e.g., "Hunger Games District", "Hogwarts House", "Star Wars Faction",
# "Wheel of Time Ajah"). When a media topic ends in one of these — or matches
# the pattern "<subgroup-noun> from <source>" — we must NOT append " Characters".
_MEDIA_SUBGROUP_NOUNS = {
    "district", "districts",
    "house", "houses",
    "faction", "factions",
    "team", "teams",
    "club", "clubs",
    "guild", "guilds",
    "clan", "clans",
    "tribe", "tribes",
    "kingdom", "kingdoms",
    "realm", "realms",
    "region", "regions",
    "planet", "planets",
    "world", "worlds",
    "nation", "nations",
    "side", "sides",
    "family", "families",
    "school", "schools",
    "race", "races",
    "class", "classes",
    "species",
    "role", "roles",
    # Wheel of Time
    "ajah", "ajahs", "aja",
    # Avatar / common bending/element nouns
    "bender", "benders", "element", "elements",
    # Magic the Gathering / D&D etc.
    "color", "colors", "colour", "colours",
    "alignment", "alignments",
    "path", "paths",
    "order", "orders",
    "sect", "sects",
    "vision", "visions",   # Genshin
    "lane", "lanes",       # League of Legends
}


# Match patterns like "aja from wheel of time", "district from hunger games",
# "house in harry potter". The first group is the subgroup noun, the second
# is the source/franchise.
_FROM_PATTERN_RE = re.compile(
    r"^([A-Za-z][A-Za-z'\-]*)\s+(?:from|in)\s+(.+?)$",
    re.IGNORECASE,
)


# ---------------------------------------------------------------------
# Explicit outcome-DIMENSION detection (character-vs-dimension).
# ---------------------------------------------------------------------
# Owner rule (2026-06-30): an AMBIGUOUS fandom topic DEFAULTS to characters
# ("Lord of the Rings" -> Frodo/Gandalf/…), but when the topic NAMES an explicit
# outcome dimension as a trailing qualifier ("Lord of the Rings Race",
# "Harry Potter House", "Star Wars faction") the outcomes are members of THAT
# dimension, NOT characters. This is the closed vocabulary of dimension nouns we
# recognise as a trailing qualifier on a "<fandom> <dimension>" topic.
#
# NOTE: this is deliberately NARROWER than _MEDIA_SUBGROUP_NOUNS — it omits
# ambiguous tokens like "team"/"class"/"role"/"type" that routinely appear in
# NON-fandom topics ("personality type", "blood type") to keep the detector
# conservative and avoid misfiring on ordinary topics. The handler still uses
# _MEDIA_SUBGROUP_NOUNS for the broader "no Characters suffix" decision.
_DIMENSION_NOUNS: frozenset[str] = frozenset(
    {
        "race", "races",
        "house", "houses",
        "faction", "factions",
        "side", "sides",
        "alignment", "alignments",
        "class", "classes",
        "species",
        "breed", "breeds",
        "role", "roles",
        "kind", "kinds",
        "element", "elements",
        "region", "regions",
        "team", "teams",
        "clan", "clans",
        "tribe", "tribes",
        "nation", "nations",
        "kingdom", "kingdoms",
        "guild", "guilds",
        "order", "orders",
    }
)

# Simple, mostly-correct singular->plural for the small dimension vocabulary so a
# detected "<Fandom> Race" normalizes to "<Fandom> Races" (a closed-list outcome
# title that the canonical lookup / planner reads as "members of this dimension").
_DIMENSION_PLURALS: dict[str, str] = {
    "race": "Races",
    "house": "Houses",
    "faction": "Factions",
    "side": "Sides",
    "alignment": "Alignments",
    "class": "Classes",
    "species": "Species",
    "breed": "Breeds",
    "role": "Roles",
    "kind": "Kinds",
    "element": "Elements",
    "region": "Regions",
    "team": "Teams",
    "clan": "Clans",
    "tribe": "Tribes",
    "nation": "Nations",
    "kingdom": "Kingdoms",
    "guild": "Guilds",
    "order": "Orders",
}


def _detect_outcome_dimension(raw: str) -> tuple[str, str] | None:
    """Detect a trailing ``<fandom> <dimension>`` outcome qualifier (SHALLOW).

    Returns ``(fandom, dimension_singular_lc)`` when the LAST token of ``raw`` is
    a recognised dimension noun AND there is a non-empty fandom prefix before it
    (so a bare "race"/"houses" with no fandom is NOT treated as a dimension
    topic). Returns ``None`` otherwise.

    IMPORTANT: this only detects the *shape* "<prefix> <dimension-noun>". It does
    NOT decide whether the prefix is confidently a fandom — that confidence gate
    lives in ``_resolve_dimension_route`` and is what actually authorises the
    dimension outcome. A shape match alone must NEVER route to dimension (it would
    fire on "master class", "chemical element", "Religious Order").
    """
    s = (raw or "").strip()
    if not s:
        return None
    tokens = s.split()
    if len(tokens) < 2:
        return None
    last_lc = tokens[-1].casefold()
    if last_lc not in _DIMENSION_NOUNS:
        return None
    fandom = " ".join(tokens[:-1]).strip()
    if not fandom:
        return None
    return fandom, last_lc


def _known_fandoms() -> frozenset[str]:
    """Return the curated known-fandom allowlist (casefolded), App-Config aware.

    Unions the built-in ``canonical_catalog.KNOWN_FANDOMS`` with an optional
    ``quizzical.known_fandoms`` list from app settings / the appconfig YAML so the
    owner can grow it without a code change. Robust to a missing catalog symbol
    or config source (falls back to whatever is available).
    """
    base: frozenset[str] = frozenset()
    try:
        from app.agent.canonical_catalog import (
            KNOWN_FANDOMS,  # local import avoids cycle
        )

        base = KNOWN_FANDOMS
    except Exception:
        base = frozenset()

    extra: list[str] = []
    # 1) app settings object
    try:
        kf = getattr(settings, "known_fandoms", None)
        if isinstance(kf, (list, tuple, set, frozenset)):
            extra.extend(str(x) for x in kf)
    except Exception:
        pass
    # 2) appconfig YAML (quizzical.known_fandoms)
    try:
        raw = _safe_yaml_load(_get_appconfig_path())
        q = (raw or {}).get("quizzical") if isinstance(raw, dict) else None
        kf2 = (q or {}).get("known_fandoms") if isinstance(q, dict) else None
        if isinstance(kf2, (list, tuple, set)):
            extra.extend(str(x) for x in kf2)
    except Exception:
        pass

    if not extra:
        return base
    merged = set(base)
    for x in extra:
        s = str(x).strip().casefold()
        if s:
            merged.add(s)
    return frozenset(merged)


def _fandom_prefix_is_known(prefix: str, media_hints: list[str]) -> bool:
    """Confidence gate: is ``prefix`` CONFIDENTLY a fictional universe?

    True when ANY of:
      (a) the prefix resolves to a canonical set (a known canonical fandom), OR
      (b) the prefix (noise-stripped, casefolded) is in the curated known-fandom
          allowlist, OR
      (c) the prefix is a genuine multi-word media-title shape
          (``_looks_like_media_title``).

    Deliberately does NOT consult the over-broad ``domain=='media_characters'``
    substring classifier (which false-positives on "master"/"order"/etc.).
    """
    p = (prefix or "").strip()
    if not p:
        return False

    # (a) canonical-known prefix.
    try:
        from app.agent.canonical_sets import canonical_for  # local import avoids cycle

        if canonical_for(p):
            return True
    except Exception:
        pass

    # (b) curated allowlist (case-insensitive). Compare both the raw lowercased
    # prefix and a light accent-stripped form so "Pokémon" matches "pokemon".
    p_cf = p.casefold()
    allow = _known_fandoms()
    if p_cf in allow:
        return True
    try:
        if unicodedata.normalize("NFKD", p_cf).encode("ascii", "ignore").decode() in allow:
            return True
    except Exception:
        pass

    # (c) genuine multi-word media-title shape.
    return bool(_looks_like_media_title(p, media_hints or []))


def _resolve_dimension_route(
    raw: str, media_hints: list[str]
) -> tuple[str, str] | None:
    """SINGLE confidence-gated entry point for the character-vs-dimension route.

    Returns the ``(fandom, dimension_lc)`` tuple ONLY when:
      * a real trailing dimension noun is present (``_detect_outcome_dimension``),
        AND
      * the fandom PREFIX is confidently a fictional universe
        (``_fandom_prefix_is_known`` — canonical prefix / allowlist / real media
        title shape).

    Otherwise returns ``None`` and the caller DEFAULTS to characters / normal
    routing (owner's rule). This is the only gate; both ``analyze_topic`` and
    ``_handle_media_topic`` call it so there is exactly one source of truth.
    """
    dim = _detect_outcome_dimension(raw)
    if dim is None:
        return None
    fandom_prefix = dim[0]
    if _fandom_prefix_is_known(fandom_prefix, media_hints):
        return dim
    return None


def _normalize_dimension_topic(fandom: str, dimension_lc: str) -> str:
    """Build the normalized "<Fandom> <Dimension-Plural>" outcome title.

    Pluralizes the dimension noun and Title-Cases it (e.g. "Star Wars Class" ->
    "Star Wars Classes", "Lord of the Rings Race" -> "Lord of the Rings Races").
    The fandom prefix is left as the user typed it (already a proper noun).
    """
    # Normalize the dimension to its singular lowercase form first, then look up
    # the curated plural (correct casing/spelling for every dimension noun).
    if dimension_lc == "species":
        singular = "species"
    elif dimension_lc.endswith("ies") and len(dimension_lc) > 3:
        singular = dimension_lc[:-3] + "y"
    elif dimension_lc.endswith("es") and dimension_lc[:-2] in _DIMENSION_PLURALS:
        singular = dimension_lc[:-2]
    elif dimension_lc.endswith("s") and dimension_lc[:-1] in _DIMENSION_PLURALS:
        singular = dimension_lc[:-1]
    else:
        singular = dimension_lc
    plural = _DIMENSION_PLURALS.get(singular)
    if plural is None:
        # Fallback: crude pluralization + Title Case for an unseen dimension noun.
        base = dimension_lc
        if not base.endswith("s"):
            base = base + ("es" if base.endswith(("ch", "sh", "x", "z", "s")) else "s")
        plural = base.title()
    return f"{fandom.strip()} {plural}".strip()


def _handle_dimension_topic(raw: str, dim: tuple[str, str]) -> tuple[str, str, str, bool]:
    """Handle a confidence-gated "<fandom> <dimension>" topic (NOT characters).

    Callers MUST have already authorised this via ``_resolve_dimension_route``
    (the prefix is confidently a fandom). Prefers the canonical set's own lookup
    key when the catalog knows the topic (so e.g. "Lord of the Rings Race" stays
    resolvable to the LOTR Races set), otherwise normalizes to
    "<Fandom> <Dimension-Plural>". Returns outcome_kind='dimension' and
    names_only=False so generation produces members of the named dimension.
    """
    stripped = (raw or "").strip()
    try:
        from app.agent.canonical_sets import canonical_for  # local import avoids cycle

        if canonical_for(stripped):
            return stripped, "dimension", "balanced", False
    except Exception:
        pass

    fandom, dimension_lc = dim
    normalized = _normalize_dimension_topic(fandom, dimension_lc)
    return normalized, "dimension", "balanced", False


# Question-chrome stripping: users often paste the full sentence into the
# topic field ("which aja from wheel of time am I?"). Strip the leading
# interrogative pronoun and trailing personality-fit phrases before any
# analysis so the downstream pipeline sees the bare noun phrase the user
# actually intends as the quiz category.
# Trailing personality-fit phrasing ("... am I?", "... are you?", "... fits me").
# Its PRESENCE is the strongest signal that the whole string is a quiz question
# and the leading interrogative is genuine chrome to remove.
_QUESTION_SUFFIX_RE = re.compile(
    r"\s+(am\s*i|are\s+you|fits\s+(?:me|my\s+personality)|matches\s+(?:me|my\s+personality)|best\s+fits\s+(?:me|my\s+personality))\s*$",
    re.IGNORECASE,
)

# Leading interrogative + bare subject pronoun chrome ("which X am I" style):
# "<which|what|...> [the] " optionally followed by a subject pronoun. NO copula
# and NO possessive here — those are handled by the possessive-frame below. This
# only fires once we've confirmed the string is a question (see below).
_QFRAME_SIMPLE_RE = re.compile(
    r"^(?:which|what|who|where|when|how)\s+(?:the\s+)?",
    re.IGNORECASE,
)

# Leading possessive-question frame: "what is my", "what's your", "which are
# our", etc. The copula + possessive together is itself a reliable question
# signal, so this may strip even WITHOUT a trailing fit phrase
# ("What is my DISC type" -> "DISC type"). Crucially it requires the possessive,
# so a declarative title like "When They See Us" is never matched.
_QFRAME_POSSESSIVE_RE = re.compile(
    r"^(?:which|what|whats|who|whos|where|when|how)\s*'?s?\s+"
    r"(?:(?:is|are|am|was|were|do|does|did|should|would|could|can|will)\s+)?"
    r"(?:(?:i|you|we|they|someone|one)\s+)?"
    r"(?:my|your|our|their|his|her|its)\s+",
    re.IGNORECASE,
)


def _strip_question_chrome(raw: str) -> str:
    """Remove ``which ... am i?`` / ``what is my ...`` framing from raw input.

    Per the canonical-matching contract this output is a LOOKUP KEY only; callers
    must not substitute it for the user's topic when it does not change anything.
    The function is conservative: it strips the leading interrogative ONLY when
    the string is clearly a question — either a trailing fit phrase ("... am I")
    is present, or the leading frame itself contains a copula+possessive
    ("what is my ..."). A bare declarative title ("When They See Us") is returned
    unchanged.

    Always returns a stripped string (never None).
    """
    s = (raw or "").strip()
    # Drop trailing punctuation a few times in case the user added "???"
    s = re.sub(r"[?!.\s]+$", "", s).strip()
    if not s:
        return ""

    original = s

    # 1) Possessive question frame ("what is my DISC type") — strong signal.
    new = _QFRAME_POSSESSIVE_RE.sub("", s).strip()
    if new and new != s:
        s = new
        s = re.sub(r"[?!.\s]+$", "", s).strip()
        return s or original

    # 2) Otherwise, only strip a leading interrogative if a trailing fit phrase
    #    confirms this is a quiz question.
    suffix_stripped = _QUESTION_SUFFIX_RE.sub("", s).strip()
    if suffix_stripped != s:
        s = suffix_stripped
        prefix_stripped = _QFRAME_SIMPLE_RE.sub("", s).strip()
        if prefix_stripped:
            s = prefix_stripped
        s = re.sub(r"[?!.\s]+$", "", s).strip()
        return s or original

    # 3) No question signal -> leave the (declarative) string untouched.
    return s


def _handle_media_topic(raw: str) -> tuple[str, str, str, bool]:
    """Handles media character logic.

    The default outcome for a media topic is its characters, but some media
    topics name an explicit *dimension*/subgroup (race/house/faction/district/
    Ajah/etc.) instead of the characters themselves. We also defer to a canonical
    catalog match if one exists for the raw input so we don't mangle e.g.
    "Hunger Games District" into "Hunger Games District Characters".

    Character-vs-dimension (owner rule, 2026-06-30):
      * AMBIGUOUS fandom ("Lord of the Rings") -> DEFAULT to characters.
      * EXPLICIT dimension qualifier ("Lord of the Rings Race", "Harry Potter
        House") -> members of THAT dimension, NOT characters. outcome_kind is
        "dimension" and names_only is False (so the planner does not re-bias to
        proper character names).
    """
    stripped = (raw or "").strip()
    media_hints = _maybe_reload().get("media_hints", []) or []

    # CONFIDENCE-GATED "<fandom> <dimension>" qualifier (trailing race/house/
    # faction/…). Uses the SAME single gate as analyze_topic
    # (_resolve_dimension_route): fires ONLY when the prefix is confidently a
    # fandom (canonical prefix / curated allowlist / real media-title shape),
    # NEVER on a bare domain-substring hit. The outcomes are members of that
    # dimension. Checked before the canonical-verbatim block so a known-fandom
    # dimension preserves its lookup key while a non-fandom token (e.g. "master
    # class") falls through to the default Characters path.
    dim = _resolve_dimension_route(stripped, media_hints)
    if dim is not None:
        return _handle_dimension_topic(stripped, dim)

    # If the catalog already knows this topic verbatim (e.g. "Hunger Games
    # District" → "Hunger Games Districts" set) — but it is NOT a confident
    # dimension topic — preserve the input as-is and do NOT force a Characters
    # suffix; the canonical lookup downstream supplies the right outcome list.
    try:
        from app.agent.canonical_sets import canonical_for  # local import avoids cycle
        if canonical_for(stripped):
            return stripped, "characters", "balanced", True
    except Exception:
        pass

    # "<subgroup-noun> from <source>" / "<noun> in <source>" / "<noun> of <source>"
    # — user is explicitly asking for the subgroup, not characters. Normalize to
    # "<Source> <Plural-Subgroup>" and skip the Characters suffix.
    m = _FROM_PATTERN_RE.match(stripped)
    if m:
        noun_raw = m.group(1).strip()
        source = m.group(2).strip()
        noun_lc = noun_raw.casefold()
        if noun_lc in _MEDIA_SUBGROUP_NOUNS:
            # Title-case the source words; pluralize subgroup if not already.
            plural = noun_raw
            if not noun_lc.endswith("s") and not noun_lc.endswith("es"):
                # Crude pluralization good enough for our subgroup vocabulary.
                plural = noun_raw + ("es" if noun_lc.endswith(("ch", "sh", "x", "z")) else "s")
            normalized = f"{source.strip().title()} {plural.title()}"
            # "<dimension> from <source>" is itself an explicit dimension request.
            kind = "dimension" if noun_lc in _DIMENSION_NOUNS else "characters"
            return normalized, kind, "balanced", kind != "dimension"
        # First token is NOT a known subgroup noun (e.g. a character name like
        # "Snape from Harry Potter") -> fall through to the default Characters path.

    # If the trailing token is a known non-character subgroup noun, treat it as
    # a faction-style outcome and skip the Characters suffix.
    last_token = stripped.split()[-1].casefold() if stripped else ""
    if last_token in _MEDIA_SUBGROUP_NOUNS:
        return stripped, "characters", "balanced", True

    base = stripped.removesuffix(" Characters").removesuffix(" characters").strip()
    normalized = f"{base} Characters" if base else "Characters"
    return normalized, "characters", "balanced", True


def _handle_music_topic(raw: str, lc: str) -> tuple[str, str, str, bool]:
    """Handles music artist/band logic."""
    label = "Artists & Groups"
    normalized = raw if label.casefold() in lc else f"{raw.strip()} {label}"
    return normalized, "characters", "balanced", True


def _handle_sports_topic(raw: str, lc: str) -> tuple[str, str, str, bool]:
    """Handles sports league/team logic."""
    suffix = " Teams"
    if any(w in lc for w in ["premier league", "uefa", "liga", "league", "mls", "club"]):
        suffix = " Clubs"

    has_team_keyword = any(tok in lc for tok in ["team", "club", "clubs", "teams"])
    normalized = raw if has_team_keyword else f"{raw.strip()}{suffix}"
    return normalized, "characters", "balanced", True


def _handle_general_topic(
    raw: str, lc: str, domain: str, type_synonyms: list[str]
) -> tuple[str, str, str, bool]:
    """Handles generic fallbacks and type-focused domains."""
    tokens = raw.split()

    # Case A: Short, purely alpha strings -> assume whimsical types
    if len(tokens) <= 2 and raw.replace(" ", "").isalpha():
        normalized = _ensure_types_of_prefix(_simple_singularize(raw))
        return normalized, "types", "whimsical", False

    # Case B: Explicit type synonyms
    if any(k in lc for k in type_synonyms):
        return raw, "types", "balanced", False

    # Case C: Specific type-focused domains
    if domain in _TYPE_FOCUSED_DOMAINS:
        return (raw or "General"), "types", "balanced", False

    # Default fallback: Archetypes
    return (raw or "General"), "archetypes", "balanced", False


def analyze_topic(category: str, synopsis: dict | None = None) -> dict[str, Any]:
    """
    Domain-driven topic analysis.
    Returns dict with:
      - normalized_category (str)
      - outcome_kind (str: "types" | "characters" | "profiles" | "archetypes")
      - creativity_mode (str: "balanced" | "factual" | "whimsical")
      - is_media (bool)
      - intent (str)
      - topic_shape (str)
      - domain (str)
      - names_only (bool)
    """
    cfg = _maybe_reload()
    raw = _strip_question_chrome(category or "")
    lc = raw.casefold()

    media_hints = cfg.get("media_hints", []) or []
    serious_hints = cfg.get("serious_hints", []) or []
    type_synonyms = cfg.get("type_synonyms", []) or []

    # Domain detection + flags
    domain = _primary_domain(category, synopsis)
    is_media = (domain == "media_characters") or _looks_like_media_title(raw, media_hints)

    # Strong signal: "<subgroup-noun> from <Source>" implies a fictional
    # universe, even if we don't have an explicit media_hint for the source.
    _from_match = _FROM_PATTERN_RE.match(raw)
    if _from_match and _from_match.group(1).casefold() in _MEDIA_SUBGROUP_NOUNS:
        is_media = True

    # EXPLICIT outcome-dimension qualifier ("<fandom> <race|house|faction|…>").
    # Strongest signal that the outcomes are members of a NAMED dimension, not
    # characters. Checked BEFORE media/serious/general routing via the SINGLE
    # confidence gate (_resolve_dimension_route): fires ONLY when a real dimension
    # noun is present AND the fandom PREFIX is confidently a fictional universe
    # (canonical prefix / curated allowlist / genuine media-title shape) — NEVER
    # on a bare domain-substring hit. Otherwise we default to characters / normal
    # routing (owner's rule), so "master class"/"chemical element"/"Religious
    # Order" are NOT misclassified as dimensions.
    _dimension = _resolve_dimension_route(raw, media_hints)

    is_serious = (domain == "serious_professions_profiles") or any(h in lc for h in serious_hints)

    # Intent & shape (advisory)
    intent_info = classify_intent(category, synopsis)
    intent = intent_info["primary"]
    topic_shape = intent_info["shape"]

    # Routing logic via distinct helpers to lower complexity
    if _dimension is not None:
        is_media = True
        normalized, outcome_kind, creativity_mode, names_only = _handle_dimension_topic(
            raw, _dimension
        )
    elif is_serious:
        normalized, outcome_kind, creativity_mode, names_only = _handle_serious_topic(raw)
    elif is_media:
        normalized, outcome_kind, creativity_mode, names_only = _handle_media_topic(raw)
    elif domain == "music_artists_acts":
        normalized, outcome_kind, creativity_mode, names_only = _handle_music_topic(raw, lc)
    elif domain == "sports_leagues_teams":
        normalized, outcome_kind, creativity_mode, names_only = _handle_sports_topic(raw, lc)
    else:
        normalized, outcome_kind, creativity_mode, names_only = _handle_general_topic(
            raw, lc, domain, type_synonyms
        )

    return {
        "normalized_category": normalized,
        "outcome_kind": outcome_kind,
        "creativity_mode": creativity_mode,
        "is_media": bool(is_media),
        "intent": intent,
        "topic_shape": topic_shape,
        "domain": domain,
        "names_only": names_only,
    }
