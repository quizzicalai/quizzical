import asyncio
import uuid
from types import SimpleNamespace
from typing import Any, Dict

import pytest
from langchain_core.messages import AIMessage

import app.agent.graph as graph_mod
from app.agent.state import CharacterProfile, Synopsis
from app.models.api import FinalResult


pytestmark = pytest.mark.unit


# ---------------------------------------------------------------------------
# _SettingsProxy / helpers
# ---------------------------------------------------------------------------


def _clear_settings_overrides():
    proxy = graph_mod.settings
    ov = object.__getattribute__(proxy, "_overrides")
    ov.clear()


def test_settings_proxy_override_and_fallback():
    """_SettingsProxy should prefer overrides but fall back to base settings."""
    proxy = graph_mod.settings
    _clear_settings_overrides()

    # Unknown attribute -> override only
    proxy.foo_bar = "baz"
    assert proxy.foo_bar == "baz"

    # Overriding an existing attr should be honored as well
    proxy.app = SimpleNamespace(environment="TESTING")
    assert proxy.app.environment == "TESTING"


def test_env_name_uses_app_environment(monkeypatch):
    """_env_name should use settings.app.environment and lowercase it."""
    proxy = graph_mod.settings
    _clear_settings_overrides()

    proxy.app = SimpleNamespace(environment="Prod")
    assert graph_mod._env_name() == "prod"


def test_env_name_falls_back_on_error():
    """If reading settings.app.environment fails, _env_name returns 'local'."""
    proxy = graph_mod.settings
    _clear_settings_overrides()

    class BadApp:
        @property
        def environment(self):
            raise RuntimeError("boom")

    proxy.app = BadApp()
    assert graph_mod._env_name() == "local"


def test_to_plain_model_dict_and_primitive():
    """_to_plain should return model_dump for models and pass through dicts/primitives."""

    class Dummy:
        def __init__(self):
            self.data = {"a": 1}

        def model_dump(self):
            return {"a": 1}

    d = Dummy()
    assert graph_mod._to_plain(d) == {"a": 1}
    assert graph_mod._to_plain({"b": 2}) == {"b": 2}
    assert graph_mod._to_plain(42) == 42


def test_safe_getattr_object_dict_and_default():
    """_safe_getattr should work for objects, dicts, and honor default."""

    class Obj:
        foo = "bar"

    o = Obj()
    assert graph_mod._safe_getattr(o, "foo") == "bar"
    assert graph_mod._safe_getattr({"foo": "x"}, "foo") == "x"
    assert graph_mod._safe_getattr(o, "missing", "default") == "default"
    assert graph_mod._safe_getattr({"foo": "x"}, "missing", 123) == 123


def test_validate_character_payload_accepts_model_and_dict():
    """_validate_character_payload handles CharacterProfile and dict payloads."""
    data = {"name": "The Optimist", "short_description": "desc", "profile_text": "text"}
    out_from_dict = graph_mod._validate_character_payload(data)
    assert isinstance(out_from_dict, CharacterProfile)
    assert out_from_dict.name == "The Optimist"

    model = CharacterProfile(**data)
    out_from_model = graph_mod._validate_character_payload(model)
    assert out_from_model is model


# ---------------------------------------------------------------------------
# _analyze_topic_safe
# ---------------------------------------------------------------------------


def test_analyze_topic_safe_happy_path(monkeypatch):
    """_analyze_topic_safe merges tool output with sensible defaults."""

    def fake_analyze_topic(category: str) -> Dict[str, Any]:
        return {
            "normalized_category": "Normalized",
            "creativity_mode": "wild",
            "names_only": True,
            "intent": "classify",
            "raw_extra": "keep_me",
        }

    monkeypatch.setattr(graph_mod, "analyze_topic", fake_analyze_topic, raising=True)

    out = graph_mod._analyze_topic_safe("Cats")
    # Tool-provided value
    assert out["normalized_category"] == "Normalized"
    assert out["creativity_mode"] == "wild"
    assert out["names_only"] is True
    assert out["intent"] == "classify"
    # Defaults
    assert out["outcome_kind"] == "types"
    assert out["domain"] == ""
    # Extra keys preserved
    assert out["raw_extra"] == "keep_me"


def test_analyze_topic_safe_tool_failure(monkeypatch):
    """If analyze_topic raises, we get a sensible default analysis."""

    def boom(_category: str):
        raise RuntimeError("LLM down")

    monkeypatch.setattr(graph_mod, "analyze_topic", boom, raising=True)
    out = graph_mod._analyze_topic_safe("Dogs")

    assert out["normalized_category"] == "Dogs"
    assert out["outcome_kind"] == "types"
    assert out["creativity_mode"] == "balanced"
    assert out["names_only"] is False
    assert out["intent"] == "identify"
    assert out["domain"] == ""


# ---------------------------------------------------------------------------
# _repair_archetypes_if_needed
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_repair_archetypes_no_repair_needed():
    """If list size is within [min,max] and names_only doesn't fail, the list is returned unchanged."""
    proxy = graph_mod.settings
    _clear_settings_overrides()
    proxy.quiz = SimpleNamespace(min_characters=1, max_characters=5)

    archetypes = ["The Optimist", "The Analyst"]
    result = await graph_mod._repair_archetypes_if_needed(
        archetypes,
        category="Cats",
        synopsis_text="synopsis",
        analysis={},
        names_only=False,
        trace_id="t",
        session_id="s",
    )
    assert result == archetypes


@pytest.mark.asyncio
async def test_repair_archetypes_triggers_tool_when_too_few(monkeypatch):
    """Too few archetypes should trigger the generator tool and return repaired list."""
    proxy = graph_mod.settings
    _clear_settings_overrides()
    proxy.quiz = SimpleNamespace(min_characters=3, max_characters=10)

    async def fake_gen(payload):
        assert payload["category"] == "Cats"
        return ["Hero", "Sage", "Rogue"]

    monkeypatch.setattr(
        graph_mod,
        "tool_generate_character_list",
        SimpleNamespace(ainvoke=fake_gen),
        raising=True,
    )

    archetypes = ["OnlyOne"]
    out = await graph_mod._repair_archetypes_if_needed(
        archetypes,
        category="Cats",
        synopsis_text="synopsis",
        analysis={},
        names_only=False,
        trace_id="t",
        session_id="s",
    )
    assert out == ["Hero", "Sage", "Rogue"]


@pytest.mark.asyncio
async def test_repair_archetypes_clamps_on_tool_failure(monkeypatch):
    """On tool failure, we still clamp to max_characters and strip whitespace."""
    proxy = graph_mod.settings
    _clear_settings_overrides()
    proxy.quiz = SimpleNamespace(min_characters=1, max_characters=3)

    async def boom(_payload):
        raise RuntimeError("tool fail")

    monkeypatch.setattr(
        graph_mod,
        "tool_generate_character_list",
        SimpleNamespace(ainvoke=boom),
        raising=True,
    )

    archetypes = [" A ", " B ", " C ", " D "]
    out = await graph_mod._repair_archetypes_if_needed(
        archetypes,
        category="Cats",
        synopsis_text="synopsis",
        analysis={},
        names_only=False,
        trace_id="t",
        session_id="s",
    )
    # Original list, trimmed and clamped to max=3
    assert out == ["A", "B", "C"]


# ---------------------------------------------------------------------------
# _try_batch_generation
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_try_batch_generation_returns_none_when_batch_tool_missing(monkeypatch):
    """If batch tool is None or not usable, we just get a map of name->None."""
    monkeypatch.setattr(
        graph_mod, "tool_draft_character_profiles", None, raising=True
    )
    names = ["Hero", "Sage"]
    out = await graph_mod._try_batch_generation(
        archetypes=names,
        category="Cats",
        analysis={},
        trace_id="t",
        session_id="s",
        timeout=5,
    )
    assert set(out.keys()) == set(names)
    assert all(v is None for v in out.values())


@pytest.mark.asyncio
async def test_try_batch_generation_populates_profiles_and_enforces_name_lock(monkeypatch):
    """Batch generation should return CharacterProfile instances with name-locked to the requested archetype."""

    class StubBatchTool:
        async def ainvoke(self, payload):
            # Intentionally mis-name to exercise 'name lock' rewriting
            res = []
            for n in payload["character_names"]:
                res.append(
                    CharacterProfile(
                        name="WrongName",
                        short_description=f"{n} short",
                        profile_text=f"{n} profile",
                    )
                )
            return res

    monkeypatch.setattr(
        graph_mod,
        "tool_draft_character_profiles",
        StubBatchTool(),
        raising=True,
    )

    names = ["Hero", "Sage"]
    out = await graph_mod._try_batch_generation(
        archetypes=names,
        category="Cats",
        analysis={},
        trace_id="t",
        session_id="s",
        timeout=5,
    )

    assert set(out.keys()) == set(names)
    for n in names:
        prof = out[n]
        assert isinstance(prof, CharacterProfile)
        assert prof.name == n  # name-lock enforcement


@pytest.mark.asyncio
async def test_try_batch_generation_handles_tool_failure(monkeypatch):
    """If the batch tool raises, we log and return all None."""

    class BadBatch:
        async def ainvoke(self, *_args, **_kwargs):
            raise RuntimeError("boom")

    monkeypatch.setattr(
        graph_mod,
        "tool_draft_character_profiles",
        BadBatch(),
        raising=True,
    )
    names = ["Hero", "Sage"]
    out = await graph_mod._try_batch_generation(
        archetypes=names,
        category="Cats",
        analysis={},
        trace_id="t",
        session_id="s",
        timeout=1,
    )
    assert set(out.keys()) == set(names)
    assert all(v is None for v in out.values())


# ---------------------------------------------------------------------------
# _fill_missing_with_concurrency
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_fill_missing_with_concurrency_fills_all(monkeypatch):
    """Missing entries should be filled via the per-item tool."""

    class StubTool:
        async def ainvoke(self, payload):
            name = payload["character_name"]
            return CharacterProfile(
                name=name,
                short_description=f"{name} short",
                profile_text=f"{name} profile",
            )

    monkeypatch.setattr(
        graph_mod,
        "tool_draft_character_profile",
        StubTool(),
        raising=True,
    )

    results_map = {"Hero": None, "Sage": None}
    await graph_mod._fill_missing_with_concurrency(
        results_map=results_map,
        category="Cats",
        analysis={},
        trace_id="t",
        session_id="s",
        concurrency=2,
        timeout=5,
        max_retries=1,
    )

    for v in results_map.values():
        assert isinstance(v, CharacterProfile)


@pytest.mark.asyncio
async def test_fill_missing_with_concurrency_gives_up_after_retries(monkeypatch):
    """If tool never returns a profile, entries remain None and we hit the 'gave_up' path."""

    class AlwaysNoneTool:
        async def ainvoke(self, payload):
            # _validate_character_payload will fail, causing a log + retry
            return None

    monkeypatch.setattr(
        graph_mod,
        "tool_draft_character_profile",
        AlwaysNoneTool(),
        raising=True,
    )

    results_map = {"Hero": None}
    await graph_mod._fill_missing_with_concurrency(
        results_map=results_map,
        category="Cats",
        analysis={},
        trace_id="t",
        session_id="s",
        concurrency=1,
        timeout=1,
        max_retries=0,  # single attempt then should 'gave_up'
    )
    assert results_map["Hero"] is None


# ---------------------------------------------------------------------------
# _generate_baseline_questions_node
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_generate_baseline_questions_node_flag_backfill():
    """If questions already exist but baseline_ready is False, we backfill the flag and count."""
    state = {
        "session_id": uuid.uuid4(),
        "generated_questions": [
            {"question_text": "Q1", "options": [{"text": "A"}, {"text": "B"}]},
            {"question_text": "Q2", "options": [{"text": "C"}, {"text": "D"}]},
        ],
        "baseline_ready": False,
    }
    out = await graph_mod._generate_baseline_questions_node(state)
    assert out["baseline_ready"] is True
    assert out["baseline_count"] == 2
    assert isinstance(out["messages"][0], AIMessage)


@pytest.mark.asyncio
async def test_generate_baseline_questions_node_noop_when_already_ready():
    """If baseline_ready is already True, node returns a no-op delta."""
    state = {"baseline_ready": True}
    out = await graph_mod._generate_baseline_questions_node(state)
    assert out == {}


@pytest.mark.asyncio
async def test_generate_baseline_questions_node_success_with_dedupe_and_at_least_one(monkeypatch):
    """
    Baseline tool output is processed, deduped by normalized text, and we
    guarantee that at least one valid baseline question is produced when
    the tool returns valid data.
    """
    proxy = graph_mod.settings
    _clear_settings_overrides()
    # Even if this is >1, the implementation currently only guarantees >=1.
    proxy.quiz = SimpleNamespace(baseline_questions_n=2)

    class DummyRaw:
        def __init__(self, qs):
            self.questions = qs

    def mk_obj(text, options):
        return SimpleNamespace(question_text=text, options=options)

    class Opt:
        def __init__(self, text, image_url=None):
            self._text = text
            self._image_url = image_url

        def model_dump(self):
            return {"text": self._text, "image_url": self._image_url}

    # Only one unique question ("Q1"), with a duplicate variant
    qs = [
        mk_obj("Q1", [Opt("A"), Opt("B")]),
        mk_obj("Q1  ", [Opt("C"), Opt("D")]),  # duplicate by normalized text
    ]
    raw = DummyRaw(qs)

    async def fake_baseline(payload):
        assert payload["category"] == "Cats"
        return raw

    monkeypatch.setattr(
        graph_mod,
        "tool_generate_baseline_questions",
        SimpleNamespace(ainvoke=fake_baseline),
        raising=True,
    )

    state = {
        "session_id": uuid.uuid4(),
        "category": "Cats",
        "generated_characters": [],
        "synopsis": Synopsis(title="Quiz: Cats", summary="..."),
        "topic_analysis": {},
        "baseline_ready": False,
        "generated_questions": [],
    }

    out = await graph_mod._generate_baseline_questions_node(state)
    assert out["baseline_ready"] is True

    qs_state = out["generated_questions"]
    assert isinstance(qs_state, list)
    # At least one question is required
    assert len(qs_state) >= 1
    # baseline_count should match the number of stored questions
    assert out["baseline_count"] == len(qs_state)

    # Dedupe: normalized texts should be unique
    normalized = {q["question_text"].strip().lower() for q in qs_state}
    assert len(normalized) == len(qs_state)
    # And we kept "Q1"
    assert "q1" in normalized


@pytest.mark.asyncio
async def test_generate_baseline_questions_node_handles_tool_exception(monkeypatch):
    """If baseline tool fails, we still mark baseline_ready but with zero questions."""

    async def boom(_payload):
        raise RuntimeError("fail")

    monkeypatch.setattr(
        graph_mod,
        "tool_generate_baseline_questions",
        SimpleNamespace(ainvoke=boom),
        raising=True,
    )

    state = {
        "session_id": uuid.uuid4(),
        "category": "Cats",
        "generated_characters": [],
        "synopsis": Synopsis(title="Quiz: Cats", summary="..."),
        "topic_analysis": {},
        "baseline_ready": False,
        "generated_questions": [],
    }

    out = await graph_mod._generate_baseline_questions_node(state)
    assert out["baseline_ready"] is True
    assert out["baseline_count"] == 0
    assert out["generated_questions"] == []


# ---------------------------------------------------------------------------
# _determine_decision_action / _resolve_winning_character
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_determine_decision_action_forces_finish_when_max_reached(monkeypatch):
    """If answered >= max_total_questions, we immediately FINISH_NOW."""
    proxy = graph_mod.settings
    _clear_settings_overrides()
    proxy.quiz = SimpleNamespace(
        max_total_questions=10,
        min_questions_before_early_finish=3,
        early_finish_confidence=0.9,
    )

    called = {"value": False}

    class SpyTool:
        async def ainvoke(self, *_args, **_kwargs):
            called["value"] = True
            return None

    monkeypatch.setattr(
        graph_mod, "tool_decide_next_step", SpyTool(), raising=True
    )

    action, conf, name = await graph_mod._determine_decision_action(
        history_payload=[{"x": i} for i in range(11)],
        characters_payload=[],
        synopsis_payload={},
        analysis={},
        trace_id="t",
        session_id="s",
        answered=11,
    )

    assert action == "FINISH_NOW"
    assert conf == 1.0
    assert name == ""
    assert called["value"] is False  # tool should not have been called


@pytest.mark.asyncio
async def test_determine_decision_action_respects_thresholds(monkeypatch):
    """We only accept FINISH_NOW when enough answers and confidence above threshold."""
    proxy = graph_mod.settings
    _clear_settings_overrides()
    proxy.quiz = SimpleNamespace(
        max_total_questions=20,
        min_questions_before_early_finish=5,
        early_finish_confidence=0.9,
    )

    class StubTool:
        async def ainvoke(self, *_args, **_kwargs):
            class Decision:
                action = "FINISH_NOW"
                confidence = 95.0  # >1.0 to exercise normalization
                winning_character_name = "Hero"

            return Decision()

    monkeypatch.setattr(
        graph_mod, "tool_decide_next_step", StubTool(), raising=True
    )

    # Enough answers, should finish
    action, conf, name = await graph_mod._determine_decision_action(
        history_payload=[{}] * 6,
        characters_payload=[],
        synopsis_payload={},
        analysis={},
        trace_id="t",
        session_id="s",
        answered=6,
    )
    assert action == "FINISH_NOW"
    # normalized from 95 -> 0.95
    assert pytest.approx(conf, rel=1e-3) == 0.95
    assert name == "Hero"


@pytest.mark.asyncio
async def test_determine_decision_action_asks_more_when_below_min(monkeypatch):
    """Even if tool suggests finish, we ask more when below min_questions_before_early_finish."""
    proxy = graph_mod.settings
    _clear_settings_overrides()
    proxy.quiz = SimpleNamespace(
        max_total_questions=20,
        min_questions_before_early_finish=5,
        early_finish_confidence=0.9,
    )

    class StubTool:
        async def ainvoke(self, *_args, **_kwargs):
            class Decision:
                action = "FINISH_NOW"
                confidence = 1.0
                winning_character_name = ""

            return Decision()

    monkeypatch.setattr(
        graph_mod, "tool_decide_next_step", StubTool(), raising=True
    )

    action, conf, _ = await graph_mod._determine_decision_action(
        history_payload=[{}] * 3,
        characters_payload=[],
        synopsis_payload={},
        analysis={},
        trace_id="t",
        session_id="s",
        answered=3,
    )
    assert action == "ASK_ONE_MORE_QUESTION"
    assert conf == 1.0


@pytest.mark.asyncio
async def test_determine_decision_action_tool_failure(monkeypatch):
    """On tool failure we fall back to ASK_ONE_MORE_QUESTION with zero confidence."""

    async def boom(*_a, **_k):
        raise RuntimeError("decide fail")

    monkeypatch.setattr(
        graph_mod,
        "tool_decide_next_step",
        SimpleNamespace(ainvoke=boom),
        raising=True,
    )

    proxy = graph_mod.settings
    _clear_settings_overrides()
    proxy.quiz = SimpleNamespace(
        max_total_questions=20,
        min_questions_before_early_finish=5,
        early_finish_confidence=0.9,
    )

    action, conf, name = await graph_mod._determine_decision_action(
        history_payload=[{}] * 10,
        characters_payload=[],
        synopsis_payload={},
        analysis={},
        trace_id="t",
        session_id="s",
        answered=10,
    )

    assert action in {"ASK_ONE_MORE_QUESTION", "FINISH_NOW"}
    # confidence should be zero from default path
    assert conf == 0.0
    assert name == ""


def test_resolve_winning_character_matches_case_insensitive():
    chars = [
        CharacterProfile(name="Alpha", short_description="", profile_text=""),
        CharacterProfile(name="Bravo", short_description="", profile_text=""),
    ]
    out = graph_mod._resolve_winning_character("bravo", chars)
    assert out is chars[1]


def test_resolve_winning_character_falls_back_to_first():
    chars = [
        CharacterProfile(name="Alpha", short_description="", profile_text=""),
        CharacterProfile(name="Bravo", short_description="", profile_text=""),
    ]
    out = graph_mod._resolve_winning_character("Nonexistent", chars)
    assert out is chars[0]


def test_resolve_winning_character_none_when_no_characters():
    assert graph_mod._resolve_winning_character("Anything", []) is None


# ---------------------------------------------------------------------------
# _decide_or_finish_node
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_decide_or_finish_node_waits_for_baseline(monkeypatch):
    """When answered < baseline_count we do not decide yet."""
    # Spy to ensure determine_decision_action is not called
    called = {"value": False}

    async def spy(*_a, **_k):
        called["value"] = True
        return "ASK_ONE_MORE_QUESTION", 0.5, ""

    monkeypatch.setattr(
        graph_mod, "_determine_decision_action", spy, raising=True
    )

    state = {
        "session_id": uuid.uuid4(),
        "trace_id": "t",
        "synopsis": Synopsis(title="Quiz: Cats", summary=""),
        "generated_characters": [],
        "quiz_history": [{}],  # 1 answer
        "baseline_count": 3,
        "topic_analysis": {},
    }
    out = await graph_mod._decide_or_finish_node(state)
    assert out["should_finalize"] is False
    assert "Awaiting baseline" in out["messages"][0].content
    assert called["value"] is False


@pytest.mark.asyncio
async def test_decide_or_finish_node_ask_more(monkeypatch):
    """If decision is ASK_ONE_MORE_QUESTION we set should_finalize=False and current_confidence."""

    async def stub_decision(*_a, **_k):
        return "ASK_ONE_MORE_QUESTION", 0.7, ""

    monkeypatch.setattr(
        graph_mod, "_determine_decision_action", stub_decision, raising=True
    )

    state = {
        "session_id": uuid.uuid4(),
        "trace_id": "t",
        "synopsis": Synopsis(title="Quiz: Cats", summary=""),
        "generated_characters": [],
        "quiz_history": [{}] * 3,
        "baseline_count": 3,
        "topic_analysis": {},
    }
    out = await graph_mod._decide_or_finish_node(state)
    assert out["should_finalize"] is False
    assert out["current_confidence"] == 0.7
    assert "final_result" not in out


@pytest.mark.asyncio
async def test_decide_or_finish_node_finish_but_no_winner(monkeypatch):
    """FINISH_NOW with no winning character yields 'ask one more' style response."""

    async def stub_decision(*_a, **_k):
        return "FINISH_NOW", 0.8, "MissingName"

    monkeypatch.setattr(
        graph_mod, "_determine_decision_action", stub_decision, raising=True
    )

    state = {
        "session_id": uuid.uuid4(),
        "trace_id": "t",
        "synopsis": Synopsis(title="Quiz: Cats", summary=""),
        "generated_characters": [],  # no characters to win
        "quiz_history": [{}] * 3,
        "baseline_count": 3,
        "topic_analysis": {},
    }
    out = await graph_mod._decide_or_finish_node(state)
    assert out["should_finalize"] is False
    assert "No winner" in out["messages"][0].content


@pytest.mark.asyncio
async def test_decide_or_finish_node_finish_success(monkeypatch):
    """FINISH_NOW with a winner calls tool_write_final_user_profile and returns FinalResult."""

    async def stub_decision(*_a, **_k):
        return "FINISH_NOW", 0.9, "Hero"

    async def stub_write(payload):
        # winning_character gets normalized via _to_plain
        return FinalResult(title="You are Hero", description="desc", image_url=None)

    monkeypatch.setattr(
        graph_mod, "_determine_decision_action", stub_decision, raising=True
    )
    monkeypatch.setattr(
        graph_mod,
        "tool_write_final_user_profile",
        SimpleNamespace(ainvoke=stub_write),
        raising=True,
    )

    hero = CharacterProfile(name="Hero", short_description="", profile_text="")
    state = {
        "session_id": uuid.uuid4(),
        "trace_id": "t",
        "synopsis": Synopsis(title="Quiz: Cats", summary=""),
        "generated_characters": [hero],
        "quiz_history": [{}] * 4,
        "baseline_count": 3,
        "topic_analysis": {},
        "category": "Cats",
        "outcome_kind": "types",
        "creativity_mode": "balanced",
    }
    out = await graph_mod._decide_or_finish_node(state)
    assert out["should_finalize"] is True
    assert out["current_confidence"] == 0.9
    final = out["final_result"]
    assert isinstance(final, FinalResult)
    assert final.title == "You are Hero"


@pytest.mark.asyncio
async def test_decide_or_finish_node_final_result_failure(monkeypatch):
    """If final result tool fails, we fall back to Result Error FinalResult."""

    async def stub_decision(*_a, **_k):
        return "FINISH_NOW", 0.5, "Hero"

    async def boom(_payload):
        raise RuntimeError("fail")

    monkeypatch.setattr(
        graph_mod, "_determine_decision_action", stub_decision, raising=True
    )
    monkeypatch.setattr(
        graph_mod,
        "tool_write_final_user_profile",
        SimpleNamespace(ainvoke=boom),
        raising=True,
    )

    hero = CharacterProfile(name="Hero", short_description="", profile_text="")
    state = {
        "session_id": uuid.uuid4(),
        "trace_id": "t",
        "synopsis": Synopsis(title="Quiz: Cats", summary=""),
        "generated_characters": [hero],
        "quiz_history": [{}] * 4,
        "baseline_count": 3,
        "topic_analysis": {},
    }
    out = await graph_mod._decide_or_finish_node(state)
    assert out["should_finalize"] is True
    final = out["final_result"]
    assert isinstance(final, FinalResult)
    assert final.title == "Result Error"


# ---------------------------------------------------------------------------
# _generate_adaptive_question_node
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_generate_adaptive_question_node_appends_question(monkeypatch):
    """Adaptive node should append a single normalized question dict to state."""

    class Opt:
        def __init__(self, text, image_url=None):
            self.text = text
            self.image_url = image_url

        def model_dump(self):
            return {"text": self.text, "image_url": self.image_url}

    class RawQuestion:
        def __init__(self):
            self.question_text = "Adaptive Q"
            self.options = [Opt("Yes"), Opt("No", image_url="http://img")]

    async def stub_next(_payload):
        return RawQuestion()

    monkeypatch.setattr(
        graph_mod,
        "tool_generate_next_question",
        SimpleNamespace(ainvoke=stub_next),
        raising=True,
    )

    state = {
        "session_id": uuid.uuid4(),
        "trace_id": "t",
        "synopsis": Synopsis(title="Quiz: Cats", summary=""),
        "generated_characters": [
            CharacterProfile(name="Hero", short_description="", profile_text="")
        ],
        "quiz_history": [{"question_text": "Q1", "answer_text": "A"}],
        "topic_analysis": {},
        "generated_questions": [
            {"question_text": "Existing", "options": [{"text": "X"}]}
        ],
    }

    out = await graph_mod._generate_adaptive_question_node(state)
    qs = out["generated_questions"]
    assert len(qs) == 2
    new_q = qs[-1]
    assert new_q["question_text"] == "Adaptive Q"
    assert new_q["options"][0]["text"] == "Yes"
    assert new_q["options"][1]["image_url"] == "http://img"


# ---------------------------------------------------------------------------
# _assemble_and_finish
# ---------------------------------------------------------------------------


def test_assemble_and_finish_summarizes_state():
    """Sink node should emit a summary AIMessage with counts."""
    state = {
        "session_id": uuid.uuid4(),
        "trace_id": "t",
        "synopsis": Synopsis(title="Quiz: Cats", summary=""),
        "generated_characters": [
            CharacterProfile(name="Hero", short_description="", profile_text="")
        ]
        * 2,
        "generated_questions": [{"question_text": "Q1", "options": []}] * 3,
    }
    out = asyncio.run(graph_mod._assemble_and_finish(state))
    msgs = out["messages"]
    assert len(msgs) == 1
    m = msgs[0]
    assert isinstance(m, AIMessage)
    assert "synopsis: True" in m.content
    assert "characters: 2" in m.content
    assert "questions: 3" in m.content


# ---------------------------------------------------------------------------
# _phase_router (basic sanity)
# ---------------------------------------------------------------------------


def test_phase_router_paths_basic():
    """Re-assert routing behavior in isolation."""
    # Gate off -> end
    s = {"ready_for_questions": False}
    assert graph_mod._phase_router(s) == "end"

    # Gate on, no baseline -> baseline
    s = {"ready_for_questions": True, "baseline_ready": False}
    assert graph_mod._phase_router(s) == "baseline"

    # Baseline ready but not fully answered -> end
    s = {
        "ready_for_questions": True,
        "baseline_ready": True,
        "baseline_count": 3,
        "quiz_history": [{}],
    }
    assert graph_mod._phase_router(s) == "end"

    # Baseline ready and fully answered -> adaptive
    s = {
        "ready_for_questions": True,
        "baseline_ready": True,
        "baseline_count": 2,
        "quiz_history": [{}, {}],
    }
    assert graph_mod._phase_router(s) == "adaptive"
