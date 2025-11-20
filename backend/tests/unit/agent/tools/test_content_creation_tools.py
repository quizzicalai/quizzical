# tests/unit/agent/tools/test_content_creation_tools.py

"""
Unit tests for app.agent.tools.content_creation_tools

Notes:
- These tests exercise the *real* tool implementations, not the global stubs.
- We rely on:
  - tests/fixtures/llm_fixtures.py to fake the LLM / embeddings / web.
  - tests/fixtures/tool_fixtures.py but with tool stubs DISABLED via the
    `no_tool_stubs` marker (see top-level pytestmark below).

Make sure stub_all_tools in tests/fixtures/tool_fixtures.py checks:
    if request.node.get_closest_marker("no_tool_stubs"):
        return
so these tests can hit the real content tools.
"""

import dataclasses
from types import SimpleNamespace
from typing import Any, Dict, List

import pytest
from pydantic import ValidationError

from app.agent.tools import content_creation_tools as ctools
from app.agent.schemas import (
    CharacterProfile,
    QuestionList,
    QuestionOut,
    QuestionOption,
    QuizQuestion,
    NextStepDecision,
)
from app.models.api import FinalResult

# Ensure the autouse tool stub fixture is a no-op for this module
pytestmark = pytest.mark.no_tool_stubs


# ---------------------------------------------------------------------------
# _deep_get
# ---------------------------------------------------------------------------


def test_deep_get_with_dict_and_attrs():
    obj = {"quiz": {"nested": {"value": 42}}}
    ns = SimpleNamespace(quiz=SimpleNamespace(nested=SimpleNamespace(value=99)))

    assert ctools._deep_get(obj, ["quiz", "nested", "value"]) == 42
    assert ctools._deep_get(ns, ["quiz", "nested", "value"]) == 99


def test_deep_get_missing_and_default():
    obj = {"quiz": {"nested": {}}}

    assert ctools._deep_get(obj, ["quiz", "nested", "missing"], default="x") == "x"

    # Stops early on None
    obj2 = {"quiz": None}
    assert ctools._deep_get(obj2, ["quiz", "nested", "value"], default="y") == "y"


# ---------------------------------------------------------------------------
# _quiz_cfg_get
# ---------------------------------------------------------------------------


def test_quiz_cfg_get_prefers_settings_quiz(monkeypatch):
    stub_settings = SimpleNamespace(
        quiz=SimpleNamespace(baseline_questions_n=7),
        quizzical=SimpleNamespace(quiz=SimpleNamespace(baseline_questions_n=9)),
    )
    monkeypatch.setattr(ctools, "settings", stub_settings, raising=False)

    assert ctools._quiz_cfg_get("baseline_questions_n", 3) == 7


def test_quiz_cfg_get_falls_back_to_quizzical(monkeypatch):
    stub_settings = SimpleNamespace(
        quiz=None,
        quizzical=SimpleNamespace(quiz=SimpleNamespace(max_options_m=10)),
    )
    monkeypatch.setattr(ctools, "settings", stub_settings, raising=False)

    assert ctools._quiz_cfg_get("max_options_m", 4) == 10


def test_quiz_cfg_get_uses_default_when_missing(monkeypatch):
    stub_settings = SimpleNamespace(quiz=None, quizzical=None)
    monkeypatch.setattr(ctools, "settings", stub_settings, raising=False)

    assert ctools._quiz_cfg_get("nonexistent", "default") == "default"


# ---------------------------------------------------------------------------
# _analyze_topic_safe
# ---------------------------------------------------------------------------


def test_analyze_topic_safe_two_arg_signature(monkeypatch):
    calls: Dict[str, Any] = {}

    def stub(category: str, synopsis: Dict[str, Any]):
        calls["category"] = category
        calls["synopsis"] = synopsis
        return {
            "normalized_category": "Norm",
            "outcome_kind": "types",
            "creativity_mode": "balanced",
        }

    monkeypatch.setattr(ctools, "analyze_topic", stub, raising=True)

    out = ctools._analyze_topic_safe("Cats", {"title": "Quiz: Cats"})
    assert out["normalized_category"] == "Norm"
    assert calls["category"] == "Cats"
    assert calls["synopsis"] == {"title": "Quiz: Cats"}


def test_analyze_topic_safe_one_arg_signature(monkeypatch):
    # Simulate older signature analyze_topic(category) -> dict
    def stub(category: str):
        return {
            "normalized_category": f"Norm-{category}",
            "outcome_kind": "types",
            "creativity_mode": "balanced",
        }

    monkeypatch.setattr(ctools, "analyze_topic", stub, raising=True)

    out = ctools._analyze_topic_safe("Dogs", {"title": "Ignored"})
    assert out["normalized_category"] == "Norm-Dogs"


# ---------------------------------------------------------------------------
# _resolve_analysis
# ---------------------------------------------------------------------------


def test_resolve_analysis_uses_provided_when_valid(monkeypatch):
    analysis = {
        "normalized_category": "Provided",
        "outcome_kind": "types",
        "creativity_mode": "balanced",
    }

    # If _analyze_topic_safe gets called, we want to fail the test
    def boom(*_a, **_k):
        raise AssertionError("_analyze_topic_safe should not be called")

    monkeypatch.setattr(ctools, "_analyze_topic_safe", boom, raising=True)

    out = ctools._resolve_analysis("Cats", None, analysis)
    assert out is analysis


def test_resolve_analysis_falls_back_when_missing_normalized(monkeypatch):
    analysis = {"outcome_kind": "types"}

    def stub(category: str, synopsis: Dict[str, Any]):
        return {
            "normalized_category": category.upper(),
            "outcome_kind": "types",
            "creativity_mode": "balanced",
        }

    monkeypatch.setattr(ctools, "_analyze_topic_safe", stub, raising=True)
    out = ctools._resolve_analysis("Cats", {"title": "Quiz: Cats"}, analysis)
    assert out["normalized_category"] == "CATS"


# ---------------------------------------------------------------------------
# _option_to_dict
# ---------------------------------------------------------------------------


def test_option_to_dict_from_string():
    out = ctools._option_to_dict("  Yes  ")
    assert out == {"text": "Yes"}


def test_option_to_dict_from_dict_with_image_variants():
    out1 = ctools._option_to_dict({"text": "A", "image_url": " http://img "})
    assert out1 == {"text": "A", "image_url": "http://img"}

    out2 = ctools._option_to_dict({"label": "B", "imageUrl": "http://img2"})
    assert out2 == {"text": "B", "image_url": "http://img2"}

    out3 = ctools._option_to_dict({"option": "C", "image": " http://img3 "})
    assert out3 == {"text": "C", "image_url": "http://img3"}


def test_option_to_dict_from_model_dump_like():
    class Dummy:
        def model_dump(self):
            return {"text": "X", "imageUrl": "http://x"}

    out = ctools._option_to_dict(Dummy())
    assert out == {"text": "X", "image_url": "http://x"}


def test_option_to_dict_from_dataclass_and_object_attrs():
    @dataclasses.dataclass
    class DC:
        text: str
        image_url: str

    dc = DC("Y", "http://y")
    out_dc = ctools._option_to_dict(dc)
    assert out_dc == {"text": "Y", "image_url": "http://y"}

    class Obj:
        def __init__(self):
            self.text = "Z"
            self.image = "http://z"

    out_obj = ctools._option_to_dict(Obj())
    assert out_obj == {"text": "Z", "image_url": "http://z"}


def test_option_to_dict_fallback_str():
    class Weird:
        def __str__(self):
            return " W "

    out = ctools._option_to_dict(Weird())
    assert out == {"text": "W"}


# ---------------------------------------------------------------------------
# _norm_text_key / _normalize_options / _ensure_min_options
# ---------------------------------------------------------------------------


def test_norm_text_key_normalizes_whitespace_and_case():
    assert ctools._norm_text_key("  Foo   Bar ") == "foo bar"
    assert ctools._norm_text_key("") == ""
    # None should behave like empty string via (s or "")
    assert ctools._norm_text_key(None) == ""  # type: ignore[arg-type]


def test_normalize_options_dedupes_and_prefers_image():
    raw = [
        " Yes ",
        {"text": "yes", "image_url": "http://img"},
        {"text": "YES"},
        {"text": "No"},
        {"text": ""},  # ignored
    ]
    out = ctools._normalize_options(raw, max_options=None)
    # Should dedupe "Yes" into one entry, with the image_url preserved
    assert len(out) == 2
    yes = next(o for o in out if o["text"] == "Yes")
    no = next(o for o in out if o["text"] == "No")
    assert yes["image_url"] == "http://img"
    assert "image_url" not in no


def test_normalize_options_respects_max_options_and_strips_empty():
    raw = [
        {"text": "A"},
        {"text": "B"},
        {"text": "C"},
    ]
    out = ctools._normalize_options(raw, max_options=2)
    assert [o["text"] for o in out] == ["A", "B"]


def test_ensure_min_options_cleans_and_pads():
    raw = [
        {"text": "  A  ", "image_url": "  "},  # image_url stripped out
        {"text": "B", "image_url": None},
        {"not_text": "C"},  # ignored
        "not a dict",  # ignored
    ]
    out = ctools._ensure_min_options(raw, minimum=2)
    assert len(out) == 2
    assert out[0] == {"text": "A"}
    assert out[1] == {"text": "B"}


def test_ensure_min_options_uses_fillers_when_too_few():
    raw = [{"text": "Only"}]
    out = ctools._ensure_min_options(raw, minimum=3)
    assert [o["text"] for o in out] == ["Only", "Yes", "No"]


# ---------------------------------------------------------------------------
# draft_character_profiles
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_draft_character_profiles_noop_on_empty_list(monkeypatch):
    """
    For empty character_names, we should short-circuit and never call
    invoke_structured (we don't care if analysis is run).
    """
    called = {"value": False}

    async def boom(**_):
        called["value"] = True
        raise RuntimeError("should not be called")

    monkeypatch.setattr(ctools, "invoke_structured", boom, raising=True)

    result = await ctools.draft_character_profiles.ainvoke(
        {"character_names": [], "category": "Cats"}
    )
    assert result == []
    assert called["value"] is False


@pytest.mark.asyncio
async def test_draft_character_profiles_happy_path_with_name_lock(monkeypatch):
    monkeypatch.setattr(
        ctools,
        "_resolve_analysis",
        lambda category, synopsis, analysis=None: {
            "normalized_category": category,
            "outcome_kind": "types",
            "creativity_mode": "balanced",
            "intent": "identify",
        },
        raising=True,
    )

    prompt_used = {}

    class DummyPrompt:
        def invoke(self, payload):
            prompt_used["payload"] = payload
            return SimpleNamespace(messages=["dummy"])

    monkeypatch.setattr(
        ctools.prompt_manager, "get_prompt", lambda name: DummyPrompt(), raising=True
    )

    async def fake_invoke_structured(**kwargs):
        # First has wrong name, second correct
        return [
            CharacterProfile(
                name="WrongName",
                short_description="Hero SD",
                profile_text="Hero PF",
                image_url="hero.png",
            ),
            CharacterProfile(
                name="Sage",
                short_description="Sage SD",
                profile_text="Sage PF",
                image_url=None,
            ),
        ]

    monkeypatch.setattr(ctools, "invoke_structured", fake_invoke_structured, raising=True)

    names = ["Hero", "Sage"]
    result: List[CharacterProfile] = await ctools.draft_character_profiles.ainvoke(
        {"character_names": names, "category": "Cats"}
    )

    assert len(result) == 2

    hero = result[0]
    assert hero.name == "Hero"
    assert hero.short_description == "Hero SD"
    assert hero.profile_text == "Hero PF"
    assert hero.image_url == "hero.png"

    sage = result[1]
    assert sage.name == "Sage"
    assert sage.short_description == "Sage SD"
    assert sage.profile_text == "Sage PF"


@pytest.mark.asyncio
async def test_draft_character_profiles_short_result_list(monkeypatch):
    monkeypatch.setattr(
        ctools,
        "_resolve_analysis",
        lambda category, synopsis, analysis=None: {
            "normalized_category": category,
            "outcome_kind": "types",
            "creativity_mode": "balanced",
            "intent": "identify",
        },
        raising=True,
    )

    class DummyPrompt:
        def invoke(self, payload):
            return SimpleNamespace(messages=["dummy"])

    monkeypatch.setattr(
        ctools.prompt_manager, "get_prompt", lambda name: DummyPrompt(), raising=True
    )

    async def fake_invoke_structured(**kwargs):
        return [
            CharacterProfile(
                name="Hero",
                short_description="Hero SD",
                profile_text="Hero PF",
            )
        ]

    monkeypatch.setattr(ctools, "invoke_structured", fake_invoke_structured, raising=True)

    names = ["Hero", "Sage"]
    result = await ctools.draft_character_profiles.ainvoke(
        {"character_names": names, "category": "Cats"}
    )

    assert len(result) == 2
    assert result[0].name == "Hero"
    # Second is fallback blank profile for "Sage"
    assert result[1].name == "Sage"
    assert result[1].short_description == ""
    assert result[1].profile_text == ""


@pytest.mark.asyncio
async def test_draft_character_profiles_handles_invoke_failure(monkeypatch):
    monkeypatch.setattr(
        ctools,
        "_resolve_analysis",
        lambda category, synopsis, analysis=None: {
            "normalized_category": category,
            "outcome_kind": "types",
            "creativity_mode": "balanced",
            "intent": "identify",
        },
        raising=True,
    )

    class DummyPrompt:
        def invoke(self, payload):
            return SimpleNamespace(messages=["dummy"])

    monkeypatch.setattr(
        ctools.prompt_manager, "get_prompt", lambda name: DummyPrompt(), raising=True
    )

    async def boom(**_):
        raise RuntimeError("invoke failed")

    monkeypatch.setattr(ctools, "invoke_structured", boom, raising=True)

    result = await ctools.draft_character_profiles.ainvoke(
        {"character_names": ["Hero"], "category": "Cats"}
    )
    assert result == []


# ---------------------------------------------------------------------------
# draft_character_profile
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_draft_character_profile_happy_path(monkeypatch):
    monkeypatch.setattr(
        ctools,
        "_resolve_analysis",
        lambda category, synopsis, analysis=None: {
            "normalized_category": category,
            "outcome_kind": "types",
            "creativity_mode": "balanced",
            "intent": "identify",
        },
        raising=True,
    )

    class DummyPrompt:
        def invoke(self, payload):
            return SimpleNamespace(messages=["dummy"])

    monkeypatch.setattr(
        ctools.prompt_manager, "get_prompt", lambda name: DummyPrompt(), raising=True
    )

    async def fake_invoke_structured(**kwargs):
        return CharacterProfile(
            name="The Optimist",
            short_description="Bright outlook",
            profile_text="Always sees the good.",
        )

    monkeypatch.setattr(ctools, "invoke_structured", fake_invoke_structured, raising=True)

    out = await ctools.draft_character_profile.ainvoke(
        {"character_name": "The Optimist", "category": "Cats"}
    )
    assert isinstance(out, CharacterProfile)
    assert out.name == "The Optimist"


@pytest.mark.asyncio
async def test_draft_character_profile_fills_missing_name(monkeypatch):
    monkeypatch.setattr(
        ctools,
        "_resolve_analysis",
        lambda category, synopsis, analysis=None: {
            "normalized_category": category,
            "outcome_kind": "types",
            "creativity_mode": "balanced",
            "intent": "identify",
        },
        raising=True,
    )

    class DummyPrompt:
        def invoke(self, payload):
            return SimpleNamespace(messages=["dummy"])

    monkeypatch.setattr(
        ctools.prompt_manager, "get_prompt", lambda name: DummyPrompt(), raising=True
    )

    async def fake_invoke_structured(**kwargs):
        return CharacterProfile(name="", short_description="desc", profile_text="text")

    monkeypatch.setattr(ctools, "invoke_structured", fake_invoke_structured, raising=True)

    out = await ctools.draft_character_profile.ainvoke(
        {"character_name": "FallbackName", "category": "Cats"}
    )
    assert out.name == "FallbackName"


@pytest.mark.asyncio
async def test_draft_character_profile_validation_error(monkeypatch):
    monkeypatch.setattr(
        ctools,
        "_resolve_analysis",
        lambda category, synopsis, analysis=None: {
            "normalized_category": category,
            "outcome_kind": "types",
            "creativity_mode": "balanced",
            "intent": "identify",
        },
        raising=True,
    )

    class DummyPrompt:
        def invoke(self, payload):
            return SimpleNamespace(messages=["dummy"])

    monkeypatch.setattr(
        ctools.prompt_manager, "get_prompt", lambda name: DummyPrompt(), raising=True
    )

    def make_validation_error():
        return ValidationError(
            [{"loc": ("name",), "msg": "err", "type": "value_error"}],
            CharacterProfile,
        )

    async def fake_invoke_structured(**kwargs):
        raise make_validation_error()

    monkeypatch.setattr(ctools, "invoke_structured", fake_invoke_structured, raising=True)

    out = await ctools.draft_character_profile.ainvoke(
        {"character_name": "NameOnError", "category": "Cats"}
    )
    assert out.name == "NameOnError"
    assert out.short_description == ""
    assert out.profile_text == ""


@pytest.mark.asyncio
async def test_draft_character_profile_generic_exception(monkeypatch):
    monkeypatch.setattr(
        ctools,
        "_resolve_analysis",
        lambda category, synopsis, analysis=None: {
            "normalized_category": category,
            "outcome_kind": "types",
            "creativity_mode": "balanced",
            "intent": "identify",
        },
        raising=True,
    )

    class DummyPrompt:
        def invoke(self, payload):
            return SimpleNamespace(messages=["dummy"])

    monkeypatch.setattr(
        ctools.prompt_manager, "get_prompt", lambda name: DummyPrompt(), raising=True
    )

    async def boom(**_):
        raise RuntimeError("fail")

    monkeypatch.setattr(ctools, "invoke_structured", boom, raising=True)

    out = await ctools.draft_character_profile.ainvoke(
        {"character_name": "NameOnError", "category": "Cats"}
    )
    assert out.name == "NameOnError"
    assert out.short_description == ""
    assert out.profile_text == ""


# ---------------------------------------------------------------------------
# generate_baseline_questions
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_generate_baseline_questions_happy_path(monkeypatch):
    monkeypatch.setattr(
        ctools,
        "_quiz_cfg_get",
        lambda name, default: {"baseline_questions_n": 5, "max_options_m": 3}.get(
            name, default
        ),
        raising=True,
    )
    monkeypatch.setattr(
        ctools,
        "_resolve_analysis",
        lambda category, synopsis, analysis=None: {
            "normalized_category": category,
            "outcome_kind": "types",
            "creativity_mode": "balanced",
            "intent": "identify",
        },
        raising=True,
    )

    class DummyPrompt:
        def __init__(self):
            self.payload = None

        def invoke(self, payload):
            self.payload = payload
            return SimpleNamespace(messages=["dummy"])

    prompt = DummyPrompt()
    monkeypatch.setattr(
        ctools.prompt_manager, "get_prompt", lambda name: prompt, raising=True
    )

    q1 = QuestionOut(
        question_text="Q1",
        options=[
            QuestionOption(text="A"),
            QuestionOption(text="B", image_url="http://img"),
            QuestionOption(text="b"),  # duplicate text, no extra image
        ],
    )

    # q2 is a dict with an empty question_text to exercise the fallback
    q2 = {
        "question_text": "",
        "options": [QuestionOption(text="Only")],  # will be padded to >=2
    }

    # Dummy container that mimics the LLM response shape just enough
    class DummyQuestionList:
        def __init__(self, questions):
            self.questions = questions

    qlist = DummyQuestionList([q1, q2])

    async def fake_invoke_structured(**kwargs):
        return qlist

    monkeypatch.setattr(ctools, "invoke_structured", fake_invoke_structured, raising=True)
    monkeypatch.setattr(ctools, "jsonschema_for", lambda *a, **k: {}, raising=True)

    out: List[QuizQuestion] = await ctools.generate_baseline_questions.ainvoke(
        {
            "category": "Cats",
            "character_profiles": [],
            "synopsis": {"title": "Quiz: Cats"},
            "num_questions": 2,
        }
    )

    assert len(out) == 2

    q1_out = out[0]
    texts = [o["text"] for o in q1_out.options]
    assert "A" in texts and "B" in texts
    b_opt = [o for o in q1_out.options if o["text"] == "B"][0]
    assert b_opt.get("image_url") == "http://img"

    q2_out = out[1]
    assert q2_out.question_text == "Baseline question"
    assert len(q2_out.options) >= 2


@pytest.mark.asyncio
async def test_generate_baseline_questions_handles_invoke_failure(monkeypatch):
    monkeypatch.setattr(
        ctools,
        "_quiz_cfg_get",
        lambda name, default: {"baseline_questions_n": 5, "max_options_m": 4}.get(
            name, default
        ),
        raising=True,
    )
    monkeypatch.setattr(
        ctools,
        "_resolve_analysis",
        lambda category, synopsis, analysis=None: {
            "normalized_category": category,
            "outcome_kind": "types",
            "creativity_mode": "balanced",
            "intent": "identify",
        },
        raising=True,
    )

    class DummyPrompt:
        def invoke(self, payload):
            return SimpleNamespace(messages=["dummy"])

    monkeypatch.setattr(
        ctools.prompt_manager, "get_prompt", lambda name: DummyPrompt(), raising=True
    )

    async def boom(**_):
        raise RuntimeError("fail")

    monkeypatch.setattr(ctools, "invoke_structured", boom, raising=True)
    monkeypatch.setattr(ctools, "jsonschema_for", lambda *a, **k: {}, raising=True)

    out = await ctools.generate_baseline_questions.ainvoke(
        {
            "category": "Cats",
            "character_profiles": [],
            "synopsis": {"title": "Quiz: Cats"},
            "num_questions": 3,
        }
    )
    assert out == []


# ---------------------------------------------------------------------------
# generate_next_question
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_generate_next_question_happy_path(monkeypatch):
    monkeypatch.setattr(
        ctools,
        "_quiz_cfg_get",
        lambda name, default: {"max_options_m": 4}.get(name, default),
        raising=True,
    )

    captured_analysis = {}

    def resolve_analysis(category: str, synopsis: Dict[str, Any], analysis=None):
        captured_analysis["category"] = category
        captured_analysis["synopsis"] = synopsis
        return {
            "normalized_category": category or "General",
            "outcome_kind": "types",
            "creativity_mode": "balanced",
            "intent": "identify",
        }

    monkeypatch.setattr(ctools, "_resolve_analysis", resolve_analysis, raising=True)

    class DummyPrompt:
        def __init__(self):
            self.payload = None

        def invoke(self, payload):
            self.payload = payload
            return SimpleNamespace(messages=["dummy"])

    prompt = DummyPrompt()
    monkeypatch.setattr(
        ctools.prompt_manager, "get_prompt", lambda name: prompt, raising=True
    )

    q_out = QuestionOut(
        question_text=" Adaptive? ",
        options=[
            QuestionOption(text="Yes"),
            QuestionOption(text="yes", image_url="http://img"),
            QuestionOption(text="No"),
        ],
    )

    async def fake_invoke_structured(**kwargs):
        return q_out

    monkeypatch.setattr(ctools, "invoke_structured", fake_invoke_structured, raising=True)
    monkeypatch.setattr(ctools, "jsonschema_for", lambda *a, **k: {}, raising=True)

    synopsis = {"title": "Quiz: Dogs", "summary": "..."}
    result = await ctools.generate_next_question.ainvoke(
        {
            "quiz_history": [{"question_text": "Q1", "answer_text": "A"}],
            "character_profiles": [],
            "synopsis": synopsis,
        }
    )

    assert captured_analysis["category"] == "Dogs"
    assert captured_analysis["synopsis"] == synopsis

    assert isinstance(result, QuizQuestion)
    assert result.question_text == "Adaptive?"
    texts = [o["text"] for o in result.options]
    assert "Yes" in texts and "No" in texts
    yes = [o for o in result.options if o["text"] == "Yes"][0]
    assert yes.get("image_url") == "http://img"


@pytest.mark.asyncio
async def test_generate_next_question_failure_fallback(monkeypatch):
    monkeypatch.setattr(
        ctools,
        "_quiz_cfg_get",
        lambda name, default: {"max_options_m": 4}.get(name, default),
        raising=True,
    )
    monkeypatch.setattr(
        ctools,
        "_resolve_analysis",
        lambda category, synopsis, analysis=None: {
            "normalized_category": "General",
            "outcome_kind": "types",
            "creativity_mode": "balanced",
            "intent": "identify",
        },
        raising=True,
    )

    class DummyPrompt:
        def invoke(self, payload):
            return SimpleNamespace(messages=["dummy"])

    monkeypatch.setattr(
        ctools.prompt_manager, "get_prompt", lambda name: DummyPrompt(), raising=True
    )

    async def boom(**_):
        raise RuntimeError("fail")

    monkeypatch.setattr(ctools, "invoke_structured", boom, raising=True)
    monkeypatch.setattr(ctools, "jsonschema_for", lambda *a, **k: {}, raising=True)

    result = await ctools.generate_next_question.ainvoke(
        {
            "quiz_history": [],
            "character_profiles": [],
            "synopsis": {"title": "Quiz: X"},
        }
    )
    assert isinstance(result, QuizQuestion)
    assert "(Unable to generate the next question right now)" in result.question_text
    assert [o["text"] for o in result.options] == ["Continue", "Skip"]


# ---------------------------------------------------------------------------
# decide_next_step
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_decide_next_step_uses_structured_llm_and_to_dict(monkeypatch):
    monkeypatch.setattr(
        ctools,
        "_quiz_cfg_get",
        lambda name, default: {
            "min_questions_before_early_finish": 6,
            "early_finish_confidence": 0.9,
            "max_total_questions": 20,
        }.get(name, default),
        raising=True,
    )

    monkeypatch.setattr(
        ctools,
        "_resolve_analysis",
        lambda category, synopsis, analysis=None: {
            "normalized_category": category or "General",
            "outcome_kind": "types",
            "creativity_mode": "balanced",
            "intent": "identify",
        },
        raising=True,
    )

    class DummyPrompt:
        def __init__(self):
            self.payload = None

        def invoke(self, payload):
            self.payload = payload
            return SimpleNamespace(messages=["dummy"])

    prompt = DummyPrompt()
    monkeypatch.setattr(
        ctools.prompt_manager, "get_prompt", lambda name: prompt, raising=True
    )

    class HistoryItem:
        def model_dump(self):
            return {"question_text": "Q", "answer_text": "A"}

    class CharItem:
        def dict(self):
            return {"name": "Hero"}

    async def fake_invoke_structured(**kwargs):
        assert kwargs["tool_name"] == "decision_maker"
        return NextStepDecision(
            action="ASK_ONE_MORE_QUESTION",
            confidence=0.5,
            winning_character_name=None,
        )

    monkeypatch.setattr(ctools, "invoke_structured", fake_invoke_structured, raising=True)
    monkeypatch.setattr(ctools, "jsonschema_for", lambda *a, **k: {}, raising=True)

    out = await ctools.decide_next_step.ainvoke(
        {
            "quiz_history": [HistoryItem()],
            "character_profiles": [CharItem()],
            "synopsis": {"title": "Quiz: Cats"},
        }
    )
    assert isinstance(out, NextStepDecision)
    assert out.action == "ASK_ONE_MORE_QUESTION"
    assert out.confidence == 0.5


# ---------------------------------------------------------------------------
# write_final_user_profile
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_write_final_user_profile_happy_path_with_image_and_title(monkeypatch):
    class DummyPrompt:
        def __init__(self):
            self.payload = None

        def invoke(self, payload):
            self.payload = payload
            return SimpleNamespace(messages=["dummy"])

    prompt = DummyPrompt()
    monkeypatch.setattr(
        ctools.prompt_manager, "get_prompt", lambda name: prompt, raising=True
    )

    async def fake_invoke_structured(**kwargs):
        return FinalResult(
            title=" Custom Title ",
            description=" Desc ",
            image_url="http://llm-img",
        )

    monkeypatch.setattr(ctools, "invoke_structured", fake_invoke_structured, raising=True)
    monkeypatch.setattr(ctools, "jsonschema_for", lambda *a, **k: {}, raising=True)

    winning = {"name": "Hero", "image_url": "http://hero-img"}
    out = await ctools.write_final_user_profile.ainvoke(
        {
            "winning_character": winning,
            "quiz_history": [],
            "category": "Cats",
            "outcome_kind": "types",
            "creativity_mode": "balanced",
        }
    )

    assert isinstance(out, FinalResult)
    assert out.title == "Custom Title"
    assert out.description == "Desc"
    assert out.image_url == "http://llm-img"


@pytest.mark.asyncio
async def test_write_final_user_profile_inherits_image_and_fallback_title(monkeypatch):
    class DummyPrompt:
        def invoke(self, payload):
            return SimpleNamespace(messages=["dummy"])

    monkeypatch.setattr(
        ctools.prompt_manager, "get_prompt", lambda name: DummyPrompt(), raising=True
    )

    async def fake_invoke_structured(**kwargs):
        return FinalResult(title="   ", description="   ", image_url=None)

    monkeypatch.setattr(ctools, "invoke_structured", fake_invoke_structured, raising=True)
    monkeypatch.setattr(ctools, "jsonschema_for", lambda *a, **k: {}, raising=True)

    winning = {"name": "Hero", "image_url": "http://hero-img"}
    out = await ctools.write_final_user_profile.ainvoke(
        {"winning_character": winning, "quiz_history": []}
    )

    assert out.title == "You are Hero!"
    assert out.description == ""
    assert out.image_url == "http://hero-img"


@pytest.mark.asyncio
async def test_write_final_user_profile_exception_fallback(monkeypatch):
    class DummyPrompt:
        def invoke(self, payload):
            return SimpleNamespace(messages=["dummy"])

    monkeypatch.setattr(
        ctools.prompt_manager, "get_prompt", lambda name: DummyPrompt(), raising=True
    )

    async def boom(**_):
        raise RuntimeError("fail")

    monkeypatch.setattr(ctools, "invoke_structured", boom, raising=True)
    monkeypatch.setattr(ctools, "jsonschema_for", lambda *a, **k: {}, raising=True)

    winning = {"name": "Hero", "image_url": "http://hero-img"}
    out = await ctools.write_final_user_profile.ainvoke(
        {"winning_character": winning, "quiz_history": []}
    )

    assert isinstance(out, FinalResult)
    assert out.title == "You are Hero!"
    assert "consistently aligned" in out.description
    assert out.image_url == "http://hero-img"


@pytest.mark.asyncio
async def test_write_final_user_profile_exception_fallback_without_name(monkeypatch):
    class DummyPrompt:
        def invoke(self, payload):
            return SimpleNamespace(messages=["dummy"])

    monkeypatch.setattr(
        ctools.prompt_manager, "get_prompt", lambda name: DummyPrompt(), raising=True
    )

    async def boom(**_):
        raise RuntimeError("fail")

    monkeypatch.setattr(ctools, "invoke_structured", boom, raising=True)
    monkeypatch.setattr(ctools, "jsonschema_for", lambda *a, **k: {}, raising=True)

    winning = {}  # no name
    out = await ctools.write_final_user_profile.ainvoke(
        {"winning_character": winning, "quiz_history": []}
    )

    assert out.title == "You are Your Best Self!"
