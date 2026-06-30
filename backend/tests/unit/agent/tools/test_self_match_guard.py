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


def test_is_self_referential_flags_single_distinctive_name_in_question():
    # #7: a single DISTINCTIVE outcome name (>=4 chars, not a common word) in
    # the question IS the bug — the model is asking which outcome the user is.
    q = "Are you more of a Gryffindor at heart?"
    names = ["Gryffindor", "Slytherin"]
    assert ctools.is_self_referential_question(q, [], names) is True


def test_is_self_referential_flags_two_outcomes_named_in_question():
    # Two distinct candidate names co-occurring ("are you more of an X or a Y?")
    # is just as blatant.
    q = "Are you more of a Gryffindor or a Slytherin at heart?"
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


def test_is_self_referential_short_name_substring_in_common_words_not_flagged():
    # Regression: candidate names must match as WHOLE WORDS, not substrings, so
    # short names (Ron/Sam/Cat/Tom) don't flag ordinary words
    # (wrong/same/scattered/uncategorized) and wrongly drop a legitimate
    # preference question.
    names = ["Ron", "Sam", "Cat", "Tom"]
    q = "Do you prefer the same steady routine, or something wrong-footed, scattered and uncategorized?"
    assert ctools.is_self_referential_question(q, [], names) is False
    # Superstrings appearing across multiple options must also not trip the
    # 2+-names-as-options rule.
    options = [{"text": "Wrong turns"}, {"text": "The same path"}, {"text": "A whole category"}]
    assert (
        ctools.is_self_referential_question("Which path feels right to you?", options, names)
        is False
    )


# ---------------------------------------------------------------------------
# #7 — common-word outcome names must not false-positive on ordinary questions
# ---------------------------------------------------------------------------

COMMON_WORD_NAME_FALSE_POSITIVES = [
    ("What do you hope to achieve this year?", ["Will", "Hope", "Grace"]),
    ("How do you find grace under pressure?", ["Will", "Hope", "Grace", "May"]),
    ("Where there is a will, what do you do next?", ["Will", "Hope"]),
    ("What artistic hobby appeals to you most?", ["Art", "Sky", "Joy"]),  # 'art' inside? no - whole word
    ("What do you hope and will to accomplish?", ["Will", "Hope"]),  # 2 common names, still ordinary
]


@pytest.mark.parametrize("q,names", COMMON_WORD_NAME_FALSE_POSITIVES)
def test_common_word_names_do_not_flag_ordinary_questions(q, names):
    # Regression for #7: ordinary-word outcomes (Will/Hope/Grace/May/Art/...)
    # appearing as whole words in a legitimate preference question must NOT be
    # treated as the user being asked to pick their own outcome.
    assert ctools.is_self_referential_question(q, [], names) is False


def test_genuine_which_character_are_you_still_flagged_with_common_word_names():
    # Even when the roster contains common-word names, a genuine self-ID
    # question (phrase layer) still flags.
    names = ["Will", "Hope", "Grace"]
    assert (
        ctools.is_self_referential_question("Which character are you most like?", [], names)
        is True
    )


def test_common_word_names_as_two_options_still_flagged():
    # #7 (low): an outcome name appearing as a discrete ANSWER OPTION is blatant
    # regardless of common-word status — the model is literally listing the
    # outcomes. Two common-word outcomes offered as options -> flagged, even
    # though those same words in the QUESTION text would not flag.
    q = "Which of these resonates with you?"  # ordinary-looking question stem
    options = [{"text": "Hope"}, {"text": "Will"}, {"text": "Something else"}]
    names = ["Hope", "Will", "Grace"]
    assert ctools.is_self_referential_question(q, options, names) is True


def test_single_common_word_name_as_one_option_not_flagged():
    # A single common-word name as one option is coincidence (the 2+ rule).
    q = "What gets you out of bed in the morning?"
    options = [{"text": "Hope"}, {"text": "A good breakfast"}]
    names = ["Hope", "Grace"]
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
# #6 — min-count backstop: regenerate the batch ONCE on self-ref underflow
# ---------------------------------------------------------------------------

def _ql(*qs):
    class _QL:
        questions = list(qs)
    return _QL()


@pytest.mark.asyncio
async def test_baseline_regenerates_once_on_self_referential_underflow(monkeypatch):
    # num_questions=4 -> min_keep = max(3, 4//2=2) = 3. First batch drops 3 of
    # 4 (1 survivor < 3) -> regenerate once; second batch is all-good and is
    # kept because it has MORE survivors.
    _patch_common(monkeypatch, cfg={"baseline_questions_n": 4, "max_options_m": 4})

    bad = lambda i: QuestionOut(  # noqa: E731
        question_text="Which character are you most like?",
        options=[QuestionOption(text=f"A{i}"), QuestionOption(text=f"B{i}")],
    )
    good = lambda i: QuestionOut(  # noqa: E731
        question_text=f"How do you spend free time, take {i}?",
        options=[QuestionOption(text="X"), QuestionOption(text="Y")],
    )

    first = _ql(good(0), bad(1), bad(2), bad(3))            # 1 survivor
    second = _ql(good(10), good(11), good(12), good(13))    # 4 survivors

    calls = {"n": 0}

    async def fake_invoke_structured(**kwargs):
        calls["n"] += 1
        return first if calls["n"] == 1 else second

    monkeypatch.setattr(ctools, "invoke_structured", fake_invoke_structured, raising=True)

    out = await ctools.generate_baseline_questions.ainvoke(
        {
            "category": "Heroes",
            "character_profiles": [],
            "synopsis": {"title": "Quiz: Heroes"},
            "num_questions": 4,
        }
    )

    assert calls["n"] == 2  # exactly one regeneration
    assert len(out) == 4
    assert all(not ctools.is_self_referential_question(q.question_text) for q in out)


@pytest.mark.asyncio
async def test_baseline_falls_back_to_survivors_when_retry_no_better(monkeypatch):
    # Underflow triggers a retry, but the retry is no better -> keep the
    # original survivors (never serve fewer), and only one retry is attempted.
    _patch_common(monkeypatch, cfg={"baseline_questions_n": 4, "max_options_m": 4})

    good = QuestionOut(
        question_text="What energises you on a slow day?",
        options=[QuestionOption(text="X"), QuestionOption(text="Y")],
    )
    bad = QuestionOut(
        question_text="Which character are you most like?",
        options=[QuestionOption(text="A"), QuestionOption(text="B")],
    )

    first = _ql(good, bad, bad, bad)   # 1 survivor (< min_keep=3)
    retry = _ql(bad, bad, bad, bad)    # 0 survivors (no better)

    calls = {"n": 0}

    async def fake_invoke_structured(**kwargs):
        calls["n"] += 1
        return first if calls["n"] == 1 else retry

    monkeypatch.setattr(ctools, "invoke_structured", fake_invoke_structured, raising=True)

    out = await ctools.generate_baseline_questions.ainvoke(
        {
            "category": "Heroes",
            "character_profiles": [],
            "synopsis": {"title": "Quiz: Heroes"},
            "num_questions": 4,
        }
    )

    assert calls["n"] == 2  # one retry, no more
    assert [q.question_text for q in out] == ["What energises you on a slow day?"]


@pytest.mark.asyncio
async def test_baseline_no_regeneration_when_above_floor(monkeypatch):
    # 1 dropped but 4 survivors (>= min_keep=3) -> NO regeneration.
    _patch_common(monkeypatch, cfg={"baseline_questions_n": 6, "max_options_m": 4})

    good = lambda i: QuestionOut(  # noqa: E731
        question_text=f"What do you value in a team, take {i}?",
        options=[QuestionOption(text="X"), QuestionOption(text="Y")],
    )
    bad = QuestionOut(
        question_text="Which character are you most like?",
        options=[QuestionOption(text="A"), QuestionOption(text="B")],
    )

    batch = _ql(good(0), good(1), good(2), good(3), bad)  # 4 survivors, 1 dropped

    calls = {"n": 0}

    async def fake_invoke_structured(**kwargs):
        calls["n"] += 1
        return batch

    monkeypatch.setattr(ctools, "invoke_structured", fake_invoke_structured, raising=True)

    out = await ctools.generate_baseline_questions.ainvoke(
        {
            "category": "Heroes",
            "character_profiles": [],
            "synopsis": {"title": "Quiz: Heroes"},
            "num_questions": 5,
        }
    )

    assert calls["n"] == 1  # no regeneration
    assert len(out) == 4


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
