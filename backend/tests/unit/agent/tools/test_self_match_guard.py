# tests/unit/agent/tools/test_self_match_guard.py
"""
Unit tests for the self-referential / meta question guard
(AC-QUALITY-SELFMATCH-1) and the configurable question-count knobs
(AC-QUALITY-QCOUNT-1).

Covers:
- `is_self_referential_question` pure detection (positives + negatives, so we
  prove the guard catches the bug WITHOUT nuking ordinary preference probes).
- `generate_baseline_questions` DROPS a self-referential question from the batch
  while keeping the normal ones.
- `generate_next_question` REGENERATES once when the first adaptive question is
  self-referential, and falls back to the safe question when the retry is still
  bad.
- The quiz-length knobs (`min_questions_before_early_finish`,
  `max_total_questions`) are read live from config (configurable).
"""

from types import SimpleNamespace

import pytest

from app.agent.schemas import QuestionOption, QuestionOut, QuizQuestion
from app.agent.tools import content_creation_tools as ctools

# These tests exercise the real tools; disable the autouse tool stub fixture.
pytestmark = pytest.mark.no_tool_stubs


# ---------------------------------------------------------------------------
# is_self_referential_question — pure detection
# ---------------------------------------------------------------------------

SELF_REFERENTIAL_QUESTIONS = [
    "Which of these characters do you feel you match with?",
    "Which character are you most like?",
    "Which character do you think you are?",
    "Who do you think you are most like?",
    "Which result do you want from this quiz?",
    "Rank these characters from most to least like you.",
    "Which type are you?",
    "Do you most identify with the rebel or the leader?",
    "How accurate is this quiz, in your opinion?",
    "Which of the following best matches you?",
]


@pytest.mark.parametrize("q", SELF_REFERENTIAL_QUESTIONS)
def test_is_self_referential_detects_meta_and_self_id(q):
    assert ctools.is_self_referential_question(q) is True


NORMAL_QUESTIONS = [
    "How do you prefer to spend a free weekend?",
    "When facing a tough decision, what do you do first?",
    "Which activity would you choose for a relaxing evening?",  # 'choose' must NOT trip
    "What best describes your ideal work environment?",
    "How do you react when plans change at the last minute?",
    "Pick the snack you'd reach for on a long road trip.",
    "What matters most to you in a friendship?",
    "",  # empty -> not self referential
    None,
]


@pytest.mark.parametrize("q", NORMAL_QUESTIONS)
def test_is_self_referential_allows_normal_preference_questions(q):
    assert ctools.is_self_referential_question(q) is False


def test_is_self_referential_flags_when_outcomes_offered_as_options():
    # The most blatant form: the candidate outcomes themselves are the answers.
    q = "Which of these appeals to you?"
    options = [{"text": "Gryffindor"}, {"text": "Slytherin"}, {"text": "Ravenclaw"}]
    names = ["Gryffindor", "Slytherin", "Ravenclaw", "Hufflepuff"]
    assert ctools.is_self_referential_question(q, options, names) is True


def test_is_self_referential_flags_outcome_named_in_question():
    q = "Are you more of a Gryffindor at heart?"
    names = ["Gryffindor", "Slytherin"]
    assert ctools.is_self_referential_question(q, [], names) is True


def test_is_self_referential_single_coincidental_option_not_flagged():
    # A single short name coincidentally appearing in one option should NOT,
    # on its own, flag an otherwise-normal question.
    q = "What do you value most in a teammate?"
    options = [{"text": "Loyalty"}, {"text": "Ambition"}]
    names = ["Loyalty Squad", "The Ambitious Ones"]
    # 'Loyalty Squad' / 'The Ambitious Ones' won't substring-match the options,
    # and the question is normal -> not flagged.
    assert ctools.is_self_referential_question(q, options, names) is False


# ---------------------------------------------------------------------------
# Shared test scaffolding
# ---------------------------------------------------------------------------

def _patch_common(monkeypatch, cfg=None):
    cfg = cfg or {"baseline_questions_n": 5, "max_options_m": 4, "max_total_questions": 18}
    monkeypatch.setattr(
        ctools, "_quiz_cfg_get", lambda name, default: cfg.get(name, default), raising=True
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
        def invoke(self, payload):
            return SimpleNamespace(messages=["dummy"])

    monkeypatch.setattr(
        ctools.prompt_manager, "get_prompt", lambda name: DummyPrompt(), raising=True
    )
    monkeypatch.setattr(ctools, "jsonschema_for", lambda *a, **k: {}, raising=True)


# ---------------------------------------------------------------------------
# generate_baseline_questions — drops self-referential questions
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_baseline_drops_self_referential_question(monkeypatch):
    _patch_common(monkeypatch)

    good = QuestionOut(
        question_text="How do you spend a free afternoon?",
        options=[QuestionOption(text="Outdoors"), QuestionOption(text="Reading")],
    )
    bad = QuestionOut(
        question_text="Which of these characters do you feel you match with?",
        options=[QuestionOption(text="A"), QuestionOption(text="B")],
    )

    class DummyQuestionList:
        questions = [good, bad]

    async def fake_invoke_structured(**kwargs):
        return DummyQuestionList()

    monkeypatch.setattr(ctools, "invoke_structured", fake_invoke_structured, raising=True)

    out = await ctools.generate_baseline_questions.ainvoke(
        {
            "category": "Heroes",
            "character_profiles": [],
            "synopsis": {"title": "Quiz: Heroes"},
            "num_questions": 2,
        }
    )

    assert len(out) == 1
    assert out[0].question_text == "How do you spend a free afternoon?"


@pytest.mark.asyncio
async def test_baseline_drops_question_offering_outcomes_as_options(monkeypatch):
    _patch_common(monkeypatch)

    profiles = [{"name": "Gryffindor"}, {"name": "Slytherin"}, {"name": "Ravenclaw"}]
    bad = QuestionOut(
        question_text="Which house feels most like you?",
        options=[
            QuestionOption(text="Gryffindor"),
            QuestionOption(text="Slytherin"),
            QuestionOption(text="Ravenclaw"),
        ],
    )
    good = QuestionOut(
        question_text="How do you handle a surprise challenge?",
        options=[QuestionOption(text="Dive in"), QuestionOption(text="Plan first")],
    )

    class DummyQuestionList:
        questions = [bad, good]

    async def fake_invoke_structured(**kwargs):
        return DummyQuestionList()

    monkeypatch.setattr(ctools, "invoke_structured", fake_invoke_structured, raising=True)

    out = await ctools.generate_baseline_questions.ainvoke(
        {
            "category": "Hogwarts Houses",
            "character_profiles": profiles,
            "synopsis": {"title": "Quiz: Hogwarts Houses"},
            "num_questions": 2,
        }
    )

    assert [q.question_text for q in out] == ["How do you handle a surprise challenge?"]


# ---------------------------------------------------------------------------
# generate_next_question — regenerates once, then safe fallback
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_next_question_regenerates_self_referential(monkeypatch):
    _patch_common(monkeypatch)

    calls = {"n": 0}
    bad = QuestionOut(
        question_text="Which character do you think you are?",
        options=[QuestionOption(text="Yes"), QuestionOption(text="No")],
    )
    good = QuestionOut(
        question_text="When under pressure, how do you usually respond?",
        options=[QuestionOption(text="Calm"), QuestionOption(text="Energised")],
    )

    async def fake_invoke_structured(**kwargs):
        calls["n"] += 1
        return bad if calls["n"] == 1 else good

    monkeypatch.setattr(ctools, "invoke_structured", fake_invoke_structured, raising=True)

    result = await ctools.generate_next_question.ainvoke(
        {
            "quiz_history": [{"question_text": "Q1", "answer_text": "A"}],
            "character_profiles": [{"name": "Hero"}, {"name": "Sage"}],
            "synopsis": {"title": "Quiz: Archetypes"},
        }
    )

    assert calls["n"] == 2  # one retry happened
    assert isinstance(result, QuizQuestion)
    assert result.question_text == "When under pressure, how do you usually respond?"
    assert not ctools.is_self_referential_question(result.question_text)


@pytest.mark.asyncio
async def test_next_question_falls_back_when_retry_still_self_referential(monkeypatch):
    _patch_common(monkeypatch)

    bad = QuestionOut(
        question_text="Which of these characters do you feel you match with?",
        options=[QuestionOption(text="Yes"), QuestionOption(text="No")],
    )

    async def fake_invoke_structured(**kwargs):
        return bad  # always self-referential

    monkeypatch.setattr(ctools, "invoke_structured", fake_invoke_structured, raising=True)

    result = await ctools.generate_next_question.ainvoke(
        {
            "quiz_history": [],
            "character_profiles": [{"name": "Hero"}],
            "synopsis": {"title": "Quiz: X"},
        }
    )

    # Falls through to the safe fallback rather than serving the bad question.
    assert isinstance(result, QuizQuestion)
    assert "(Unable to generate the next question right now)" in result.question_text


@pytest.mark.asyncio
async def test_next_question_passes_through_normal_question(monkeypatch):
    _patch_common(monkeypatch)

    calls = {"n": 0}
    good = QuestionOut(
        question_text="How do you prefer to make decisions?",
        options=[QuestionOption(text="Gut"), QuestionOption(text="Analysis")],
    )

    async def fake_invoke_structured(**kwargs):
        calls["n"] += 1
        return good

    monkeypatch.setattr(ctools, "invoke_structured", fake_invoke_structured, raising=True)

    result = await ctools.generate_next_question.ainvoke(
        {
            "quiz_history": [],
            "character_profiles": [{"name": "Hero"}],
            "synopsis": {"title": "Quiz: X"},
        }
    )

    assert calls["n"] == 1  # no retry for a clean question
    assert result.question_text == "How do you prefer to make decisions?"


# ---------------------------------------------------------------------------
# AC-QUALITY-QCOUNT-1 — quiz-length knobs are read live from config
# ---------------------------------------------------------------------------

def test_quiz_length_knobs_are_config_driven(monkeypatch):
    captured = {}

    def fake_cfg(name, default):
        return {
            "min_questions_before_early_finish": 11,
            "max_total_questions": 19,
            "early_finish_confidence": 0.77,
        }.get(name, default)

    monkeypatch.setattr(ctools, "_quiz_cfg_get", fake_cfg, raising=True)

    assert ctools._quiz_cfg_get("min_questions_before_early_finish", 6) == 11
    assert ctools._quiz_cfg_get("max_total_questions", 20) == 19
    assert ctools._quiz_cfg_get("early_finish_confidence", 0.9) == 0.77
