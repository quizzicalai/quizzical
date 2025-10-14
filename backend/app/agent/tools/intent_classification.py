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

Config shape expected at: quizzical.topic_keywords
  topic_keywords:
    version: <int>
    intents: { <intent>: [tokens...] }
    shapes:  { <shape>:  [tokens...] }
    domains: { <domain>: [tokens...] }
    media_hints: [..]
    serious_hints: [..]
    type_synonyms: [..]
"""

from __future__ import annotations

from typing import Any, Dict, List, Optional, Tuple
import os
import re
import unicodedata
from pathlib import Path

import yaml  # PyYAML

# === Load global app settings (consistent with other files) ===================
try:
    from app.core.config import settings as _base_settings  # type: ignore
except Exception:  # pragma: no cover - settings import may fail in isolated tests
    _base_settings = None  # type: ignore


class _SettingsProxy:
    """Proxy to allow dynamic overrides in tests via attribute setting."""
    def __init__(self, base):
        object.__setattr__(self, "_base", base)
        object.__setattr__(self, "_overrides", {})

    def __getattr__(self, name):
        ov = object.__getattribute__(self, "_overrides")
        if name in ov:
            return ov[name]
        base = object.__getattribute__(self, "_base")
        if base is None:
            raise AttributeError(name)
        return getattr(base, name)

    def __setattr__(self, name, value):
        object.__getattribute__(self, "_overrides")[name] = value


settings = _SettingsProxy(_base_settings)

# ---------------------------------------------------------------------
# Config loading (App settings → Azure → appconfig.local.yaml → defaults)
# ---------------------------------------------------------------------

_APP_CONFIG_ENV = "APP_CONFIG_LOCAL_PATH"
_APPCONFIG_KEY_ROOT = ("quizzical", "topic_keywords")


class _ConfigCache:
    path: Optional[Path]
    mtime: float
    data: Dict[str, Any]

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


def _safe_yaml_load(path: Path) -> Dict[str, Any]:
    try:
        if not path.exists():
            return {}
        with path.open("r", encoding="utf-8") as f:
            return yaml.safe_load(f) or {}
    except Exception:
        return {}


def _load_from_azure_app_config() -> Optional[Dict[str, Any]]:
    """
    Placeholder for Azure App Config ingestion of the *topic keywords* subtree.
    Return a nested dict whose root contains 'quizzical' → 'topic_keywords'.
    Disabled during local/dev bring-up per product plan.
    """
    # To enable later, wire to the same Azure client logic used in core/config.py.
    return None


def _load_from_app_settings() -> Optional[Dict[str, Any]]:
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


def _load_from_appconfig_yaml() -> Optional[Dict[str, Any]]:
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


def _embedded_defaults() -> Dict[str, Any]:
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


def _dedupe_list(items: List[Any]) -> List[Any]:
    seen = set()
    out: List[Any] = []
    for x in items or []:
        k = str(x).strip().casefold()
        if k and k not in seen:
            seen.add(k)
            out.append(x)
    return out


def _merge_with_defaults(user: Dict[str, Any]) -> Dict[str, Any]:
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


def _maybe_reload() -> Dict[str, Any]:
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


def _text_corpus(category: str, synopsis: Optional[Dict]) -> str:
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


def _looks_like_media_title(raw: str, media_hints: List[str]) -> bool:
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


def _score_map(text: str, tokens: List[Any]) -> float:
    total = 0.0
    for tok in tokens or []:
        if isinstance(tok, dict):
            t = tok.get("token", "")
            w = float(tok.get("weight", 1.0))
        else:
            t, w = str(tok), 1.0
        total += _token_score(text, t) * w
    return total


# ---------------------------------------------------------------------
# Intent classification (unchanged API)
# ---------------------------------------------------------------------

def classify_intent(category: str, synopsis: Optional[Dict] = None) -> Dict[str, Any]:
    """
    Soft, data-driven intent classification.
    Returns: {"primary": str, "scores": {intent: float}, "shape": str}
    """
    cfg = _maybe_reload()
    text = _text_corpus(category, synopsis)

    # Score intents
    scores: Dict[str, float] = {}
    for intent, tokens in (cfg.get("intents") or {}).items():
        total = _score_map(text, tokens)
        if total > 0:
            scores[intent] = total

    # Advisory shape
    shape_scores: Dict[str, float] = {}
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
        primary = {
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
        }.get(domain, "identify")

    return {"primary": primary, "scores": scores, "shape": shape}


# ---------------------------------------------------------------------
# Topic analysis (domain-first, unchanged API)
# ---------------------------------------------------------------------

def _primary_domain(category: str, synopsis: Optional[Dict]) -> str:
    cfg = _maybe_reload()
    text = _text_corpus(category, synopsis)
    domains: Dict[str, List[Any]] = cfg.get("domains") or {}

    scored: List[Tuple[str, float]] = []
    for name, tokens in domains.items():
        scored.append((name, _score_map(text, tokens)))

    # Heuristic bump for media-looking titles
    if _looks_like_media_title(category, cfg.get("media_hints", []) or []):
        scored.append(("media_characters", 1.5))

    if not scored:
        return ""

    best_name, best_score = max(scored, key=lambda kv: kv[1])
    return best_name if best_score > 0.0 else ""


def analyze_topic(category: str, synopsis: Optional[Dict] = None) -> Dict[str, Any]:
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
    raw = (category or "").strip()
    lc = raw.casefold()

    media_hints = cfg.get("media_hints", []) or []
    serious_hints = cfg.get("serious_hints", []) or []
    type_synonyms = cfg.get("type_synonyms", []) or []

    # Domain detection + flags
    domain = _primary_domain(category, synopsis)
    is_media = (domain == "media_characters") or _looks_like_media_title(raw, media_hints)
    is_serious = (domain == "serious_professions_profiles") or any(h in lc for h in serious_hints)

    # Intent & shape (advisory)
    intent_info = classify_intent(category, synopsis)
    intent = intent_info["primary"]
    topic_shape = intent_info["shape"]

    # Defaults
    outcome_kind = "types"
    creativity_mode = "balanced"
    normalized = raw or "General"
    names_only = False

    def _ensure_types_of_prefix(label: str) -> str:
        s = (label or "").strip()
        # If already like "type of x" or "types of x", keep it (normalize capital T)
        if re.match(r"(?i)^\s*types?\s+of\s+", s):
            return s[0].upper() + s[1:] if s else s
        return f"Types of {s}"  

    if is_serious:
        outcome_kind = "types"
        creativity_mode = "factual"
        base = _simple_singularize(raw) or "Profession"
        mapping = {
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
        normalized = mapping.get(base.lower(), _ensure_types_of_prefix(base))

    elif is_media:
        outcome_kind = "characters"
        creativity_mode = "balanced"
        base = raw.removesuffix(" Characters").removesuffix(" characters").strip()
        normalized = f"{base} Characters" if base else "Characters"
        names_only = True

    elif domain == "music_artists_acts":
        outcome_kind = "characters"
        creativity_mode = "balanced"
        label = "Artists & Groups"
        normalized = raw if label.casefold() in lc else f"{raw.strip()} {label}"
        names_only = True

    elif domain == "sports_leagues_teams":
        outcome_kind = "characters"
        creativity_mode = "balanced"
        suffix = " Teams"
        if any(w in lc for w in ["premier league", "uefa", "liga", "league", "mls", "club"]):
            suffix = " Clubs"
        normalized = raw if any(tok in lc for tok in ["team", "club", "clubs", "teams"]) else f"{raw.strip()}{suffix}"
        names_only = True

    else:
        tokens = raw.split()
        if len(tokens) <= 2 and raw.replace(" ", "").isalpha():
            normalized = _ensure_types_of_prefix(_simple_singularize(raw))
            outcome_kind = "types"
            creativity_mode = "whimsical"
        elif any(k in lc for k in type_synonyms):
            normalized = raw
            outcome_kind = "types"
            creativity_mode = "balanced"
        else:
            if domain in {
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
            }:
                outcome_kind = "types"
                creativity_mode = "balanced"
                normalized = raw or "General"
            else:
                outcome_kind = "archetypes"
                creativity_mode = "balanced"
                normalized = raw or "General"

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
