import time
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

# Module under test
from app.agent.tools import intent_classification as ic

# ---------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------

@pytest.fixture(autouse=True)
def reset_cache():
    """Reset the global config cache before and after every test."""
    ic._CACHE.path = None
    ic._CACHE.mtime = -1.0
    ic._CACHE.data = {}
    yield
    ic._CACHE.path = None
    ic._CACHE.mtime = -1.0
    ic._CACHE.data = {}

@pytest.fixture
def mock_defaults(monkeypatch):
    """Return specific predictable defaults for testing logic."""
    defaults = {
        "intents": {
            "sorting": ["sort", "house"],
            "vibe": ["vibe", "aesthetic"],
        },
        "domains": {
            "media_characters": ["movie", "film"],
            "serious_professions_profiles": ["doctor", "lawyer"],
            "sports_leagues_teams": ["nba", "team"],
        },
        "shapes": {
            "person": ["character", "hero"],
        },
        "media_hints": ["season", "episode"],
        "serious_hints": ["cv", "resume"],
        "type_synonyms": ["type", "kind"],
        "version": 99
    }
    monkeypatch.setattr(ic, "_embedded_defaults", lambda: defaults)
    return defaults

# ---------------------------------------------------------------------
# Helpers Tests
# ---------------------------------------------------------------------

def test_norm():
    assert ic._norm("  HeLLo  ") == "hello"
    assert ic._norm(None) == ""
    # NFKC normalization check (e.g., compatibility chars)
    assert ic._norm("ℍello") == "hello" 

def test_text_corpus():
    assert ic._text_corpus("Cat", None) == "cat"
    synopsis = {"title": " Quiz ", "summary": "Details"}
    corpus = ic._text_corpus("Topic", synopsis)
    assert "topic" in corpus
    assert "quiz" in corpus
    assert "details" in corpus

def test_simple_singularize():
    assert ic._simple_singularize("cats") == "cat"
    # "buses" ends in "ses", handled by s[:-2] logic
    assert ic._simple_singularize("buses") == "bus" 
    assert ic._simple_singularize("people") == "person" # irregular
    assert ic._simple_singularize("mice") == "mouse"
    assert ic._simple_singularize("families") == "family"
    assert ic._simple_singularize("foxes") == "fox"
    assert ic._simple_singularize("grass") == "grass" # ends in ss, preserved
    
    # Implementation converts None -> "" via (s or "").strip()
    assert ic._simple_singularize(None) == ""

def test_token_score():
    text = "The quick brown fox"
    
    # Exact substring match (1.0)
    assert ic._token_score(text, "quick") >= 1.0
    
    # Padded match (1.25) - 'brown' is surrounded by spaces effectively in logic
    assert ic._token_score(text, "brown") >= 1.25
    
    # No match
    assert ic._token_score(text, "missing") == 0.0
    
    # Regex match (2.0) - starts/ends with /
    assert ic._token_score(text, "/b.own/") == 2.0
    assert ic._token_score(text, "/^The/") == 2.0
    assert ic._token_score(text, "/missing/") == 0.0
    
    # Bad regex
    assert ic._token_score(text, "/[unclosed/") == 0.0

def test_looks_like_media_title():
    hints = ["season", "movie"]
    
    # Hint match
    assert ic._looks_like_media_title("Game Season 1", hints) is True
    
    # Suffix match
    assert ic._looks_like_media_title("Mario Characters", hints) is True
    
    # Structure match (Title Case with stopwords)
    assert ic._looks_like_media_title("Lord of the Rings", hints) is True
    assert ic._looks_like_media_title("A Tale of Two Cities", hints) is True
    
    # Failures
    assert ic._looks_like_media_title("Apple Pie", hints) is False
    assert ic._looks_like_media_title("", hints) is False

def test_ensure_types_of_prefix():
    assert ic._ensure_types_of_prefix("Cheese") == "Types of Cheese"
    assert ic._ensure_types_of_prefix("Types of Cheese") == "Types of Cheese"
    assert ic._ensure_types_of_prefix("type of Cheese") == "Type of Cheese"
    assert ic._ensure_types_of_prefix("") == "Types of "

# ---------------------------------------------------------------------
# Config Loading & Hierarchy Tests
# ---------------------------------------------------------------------

def test_dedupe_list():
    raw = ["a", "B", "a ", "c"]
    assert ic._dedupe_list(raw) == ["a", "B", "c"]

def test_merge_with_defaults(mock_defaults):
    user_config = {
        "intents": {"sorting": ["custom_sort"]},
        "version": 100
    }
    merged = ic._merge_with_defaults(user_config)
    
    # Should contain default + new
    assert "sort" in merged["intents"]["sorting"]
    assert "custom_sort" in merged["intents"]["sorting"]
    
    # Should override version
    assert merged["version"] == 100
    
    # Should keep other defaults
    assert "vibe" in merged["intents"]

def test_config_hierarchy_defaults_only(monkeypatch, mock_defaults):
    """Ensure embedded defaults are returned when no other sources exist."""
    monkeypatch.setattr(ic, "_load_from_app_settings", lambda: None)
    monkeypatch.setattr(ic, "_get_appconfig_path", lambda: Path("/non/existent"))
    
    cfg = ic._maybe_reload()
    assert cfg["version"] == 99

def test_config_hierarchy_app_settings_priority(monkeypatch, mock_defaults):
    """Ensure settings.topic_keywords overrides everything."""
    # 1. Define App Settings override
    app_settings_data = {
        "quizzical": {
            "topic_keywords": {
                "version": 500,
                "intents": {"new_intent": ["token"]}
            }
        }
    }
    monkeypatch.setattr(ic, "_load_from_app_settings", lambda: app_settings_data)
    
    # 2. Define YAML data (should be ignored)
    mock_yaml_load = MagicMock(return_value={"quizzical": {"topic_keywords": {"version": 200}}})
    monkeypatch.setattr(ic, "_load_from_appconfig_yaml", mock_yaml_load)

    cfg = ic._maybe_reload()
    
    assert cfg["version"] == 500
    assert "new_intent" in cfg["intents"]

def test_config_hierarchy_yaml_reload(monkeypatch, tmp_path, mock_defaults):
    """Test loading from YAML and hot-reloading logic."""
    monkeypatch.setattr(ic, "_load_from_app_settings", lambda: None)
    
    # Create a real temp yaml file
    yaml_file = tmp_path / "appconfig.local.yaml"
    yaml_content = """
quizzical:
  topic_keywords:
    version: 101
    intents:
      yaml_intent: [ "y" ]
"""
    yaml_file.write_text(yaml_content, encoding="utf-8")
    
    monkeypatch.setattr(ic, "_get_appconfig_path", lambda: yaml_file)
    
    # 1st Load
    cfg = ic._maybe_reload()
    assert cfg["version"] == 101
    assert "yaml_intent" in cfg["intents"]
    
    # Modify file
    time.sleep(0.01) # Ensure mtime differs
    yaml_file.write_text(yaml_content.replace("101", "102"), encoding="utf-8")
    
    # 2nd Load (Should pick up change)
    cfg2 = ic._maybe_reload()
    assert cfg2["version"] == 102

# ---------------------------------------------------------------------
# Logic Tests: Intent & Topic Analysis
# ---------------------------------------------------------------------

def test_classify_intent_explicit_match(monkeypatch, mock_defaults):
    """Test scoring logic picking a primary intent."""
    # Mock defaults puts 'sort' in 'sorting' intent
    # _text_corpus will combine category and synopsis
    
    res = ic.classify_intent("Sort these items")
    assert res["primary"] == "sorting"
    assert res["scores"]["sorting"] > 0

def test_classify_intent_fallback(monkeypatch, mock_defaults):
    """Test fallback to 'identify' or domain-based fallback."""
    # "NBA" matches 'sports_leagues_teams' domain in mock_defaults
    # _DOMAIN_INTENT_FALLBACKS maps 'sports_leagues_teams' -> 'identify' in the actual code
    
    res = ic.classify_intent("NBA")
    
    # It shouldn't match explicit sorting/vibe keywords
    assert not res["scores"]
    assert res["primary"] == "identify"

def test_primary_domain(monkeypatch, mock_defaults):
    assert ic._primary_domain("Movie Stars", None) == "media_characters"
    assert ic._primary_domain("NBA Stats", None) == "sports_leagues_teams"
    assert ic._primary_domain("Unknown Stuff", None) == ""

def test_analyze_topic_serious(monkeypatch, mock_defaults):
    """Test _handle_serious_topic routing."""
    # 'doctor' is in mock_defaults['serious_professions_profiles']
    
    res = ic.analyze_topic("Doctors")
    assert res["domain"] == "serious_professions_profiles"
    assert res["normalized_category"] == "Doctor Specialties"
    assert res["outcome_kind"] == "types"
    assert res["creativity_mode"] == "factual"

def test_analyze_topic_media(monkeypatch, mock_defaults):
    """Test _handle_media_topic routing."""
    # 'movie' is in mock_defaults['media_hints']
    
    res = ic.analyze_topic("Batman Movie")
    assert res["is_media"] is True
    assert res["normalized_category"] == "Batman Movie Characters"
    assert res["outcome_kind"] == "characters"

def test_analyze_topic_sports(monkeypatch, mock_defaults):
    """Test _handle_sports_topic."""
    # 'nba' is in mock_defaults['sports_leagues_teams']
    
    res = ic.analyze_topic("NBA")
    # _handle_sports_topic logic: adds " Teams" if not present
    assert res["normalized_category"] == "NBA Teams"
    assert res["outcome_kind"] == "characters"

    res2 = ic.analyze_topic("Manchester Club") # 'Club' keyword present
    assert res2["normalized_category"] == "Manchester Club"

def test_analyze_topic_music(monkeypatch, mock_defaults):
    """Test _handle_music_topic routing via monkeypatching domain response."""
    
    # Force domain to be music by hijacking the domain detection
    monkeypatch.setattr(ic, "_primary_domain", lambda c, s: "music_artists_acts")
    
    res = ic.analyze_topic("Nirvana")
    assert res["normalized_category"] == "Nirvana Artists & Groups"
    assert res["outcome_kind"] == "characters"

def test_analyze_topic_general_whimsical(monkeypatch, mock_defaults):
    """Test short alpha string -> whimsical types."""
    # Force unknown domain
    monkeypatch.setattr(ic, "_primary_domain", lambda c, s: "")
    
    res = ic.analyze_topic("Cheese")
    assert res["normalized_category"] == "Types of Cheese"
    assert res["outcome_kind"] == "types"
    assert res["creativity_mode"] == "whimsical"

def test_analyze_topic_general_synonym(monkeypatch, mock_defaults):
    """Test explicit type synonym -> balanced types."""
    # "type" is in mock_defaults['type_synonyms']
    monkeypatch.setattr(ic, "_primary_domain", lambda c, s: "")
    
    # Use lowercase "cars" to ensure it doesn't trigger the "media title" heuristic
    # (which looks for capitalized words like "Types of Cars")
    res = ic.analyze_topic("Types of cars")
    assert res["normalized_category"] == "Types of cars"
    assert res["outcome_kind"] == "types"
    assert res["creativity_mode"] == "balanced"

def test_analyze_topic_general_fallback(monkeypatch, mock_defaults):
    """Test fallback to archetypes."""
    monkeypatch.setattr(ic, "_primary_domain", lambda c, s: "")
    
    # Long string, no keywords
    res = ic.analyze_topic("Complex geopolitical landscape 2024")
    assert res["normalized_category"] == "Complex geopolitical landscape 2024"
    assert res["outcome_kind"] == "archetypes"
    assert res["creativity_mode"] == "balanced"