# tests/test_graph.py

import asyncio
import uuid
import types
import pytest

from langchain_core.messages import HumanMessage

# The real graph module (we'll use some internal helpers directly)
import app.agent.graph as graph_mod

# Typed alias for state (dict-like)
from app.agent.state import GraphState
from app.models.api import FinalResult
from app.agent.schemas import QuestionOut, QuestionOption, QuestionList
# Handy fixtures & helpers already provided in the repo
from tests.fixtures.agent_graph_fixtures import (
    agent_graph_memory_saver,
    agent_thread_id,
    run_quiz_start,
    run_quiz_proceed,
    get_graph_state,
    assert_synopsis_and_characters,
)

pytestmark = pytest.mark.unit


# ---------------------------------------------------------------------------
# Helper utilities in graph.py (existing tests)
# ---------------------------------------------------------------------------


def test_validate_character_payload_roundtrip():
    """_validate_character_payload should accept dicts and return a CharacterProfile."""
    data = {"name": "The Optimist", "short_description": "", "profile_text": ""}
    out = graph_mod._validate_character_payload(data)
    assert out.name == "The Optimist"
    assert out.short_description == ""
    assert out.profile_text == ""


def test_coerce_question_to_state_variants():
    """_coerce_question_to_state should normalize loose shapes into state shape."""
    # FE-shaped payload with 'text' and a camelCase imageUrl
    obj = {
        "text": "Pick one",
        "options": [{"label": "A"}, {"text": "B", "imageUrl": "http://x/img.png"}, "C"],
    }
    q = graph_mod._coerce_question_to_state(obj)
    assert q.question_text == "Pick one"
    texts = [o.get("text") for o in q.options]
    assert texts == ["A", "B", "C"]
    # Image should be normalized to snake_case key
    b = next(o for o in q.options if o["text"] == "B")
    assert b.get("image_url") == "http://x/img.png"


def test_phase_router_paths():
    """Verify router decisions across key states."""
    # Gate off -> end
    s: GraphState = {
        "ready_for_questions": False,
        "quiz_history": [],
        "baseline_count": 0,
    }
    assert graph_mod._phase_router(s) == "end"

    # Gate on, no baseline yet -> baseline
    s = {"ready_for_questions": True, "baseline_ready": False}
    assert graph_mod._phase_router(s) == "baseline"

    # Baseline ready, not all baseline answered -> end
    s = {"ready_for_questions": True, "baseline_ready": True, "baseline_count": 3, "quiz_history": [{"i": 0}]}
    assert graph_mod._phase_router(s) == "end"

    # Baseline ready, all baseline answered -> adaptive
    s = {"ready_for_questions": True, "baseline_ready": True, "baseline_count": 2, "quiz_history": [{"i": 0}, {"i": 1}]}
    assert graph_mod._phase_router(s) == "adaptive"


# ---------------------------------------------------------------------------
# End-to-end graph behavior using the real compiled graph with MemorySaver
# (llm I/O is patched by autouse fixtures in tests/fixtures/llm_fixtures.py)
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_start_phase_produces_synopsis_and_characters(agent_graph_memory_saver, agent_thread_id):
    state = await run_quiz_start(
        agent_graph_memory_saver,
        session_id=agent_thread_id,
        category="Cats",
        trace_id="t-start",
    )
    assert_synopsis_and_characters(state, min_chars=1)
    # Idempotency: running "start" again should not wipe or duplicate characters/synopsis
    state2 = await run_quiz_start(
        agent_graph_memory_saver,
        session_id=agent_thread_id,
        category="Cats",
        trace_id="t-start-2",
    )
    assert state2.get("category_synopsis") == state.get("category_synopsis")
    assert len(state2.get("generated_characters") or []) == len(state.get("generated_characters") or [])


@pytest.mark.asyncio
async def test_proceed_generates_baseline_questions_once(agent_graph_memory_saver, agent_thread_id):
    # Phase 1
    await run_quiz_start(agent_graph_memory_saver, session_id=agent_thread_id, category="Cats")

    # First proceed -> baseline questions generated
    s1 = await run_quiz_proceed(agent_graph_memory_saver, session_id=agent_thread_id, expect_baseline=True)
    qs1 = s1.get("generated_questions") or []
    assert s1.get("baseline_ready") is True
    assert s1.get("baseline_count") == len(qs1)
    # CONTRACT: state-shaped dicts
    for q in qs1:
        assert isinstance(q, dict)
        assert isinstance(q.get("question_text"), str) and q["question_text"].strip()
        assert isinstance(q.get("options"), list) and len(q["options"]) >= 2
        for o in q["options"]:
            assert set(o.keys()) <= {"text", "image_url"}              # no extra fields
            assert isinstance(o["text"], str) and o["text"].strip()
            assert "image_url" not in o or isinstance(o["image_url"], str)  # never None/null

    # Second proceed should be a no-op for baseline generation (do not re-create)
    s2 = await run_quiz_proceed(agent_graph_memory_saver, session_id=agent_thread_id, expect_baseline=False)
    qs2 = s2.get("generated_questions") or []
    assert s2.get("baseline_ready") is True
    assert len(qs2) == len(qs1)
    assert qs2 == qs1


def test__coerce_questions_list_normalizes_state_shape():
    raw = [
        # FE-ish shape with camelCase + nulls
        {
            "text": "Pick one",
            "options": [{"label": "A"}, {"text": "B", "imageUrl": "http://x/img.png"}, "C", {"text": "C"}],
        },
        # Already state-shaped but with null image_url (should be dropped)
        {"question_text": "Q2", "options": [{"text": "Yes", "image_url": None}, {"text": "No"}]},
    ]
    out = graph_mod._coerce_questions_list(raw)
    assert isinstance(out, list) and len(out) == 2

    q1 = out[0]
    assert q1["question_text"] == "Pick one"
    assert [o["text"] for o in q1["options"]] == ["A", "B", "C"]  # dedup on text
    b = next(o for o in q1["options"] if o["text"] == "B")
    assert b["image_url"] == "http://x/img.png"

    q2 = out[1]
    assert q2["question_text"] == "Q2"
    # no nulls persisted
    assert all("image_url" not in o or isinstance(o["image_url"], str) for o in q2["options"])


def test__ensure_min_options_pads_and_filters():
    # 1 -> 2 with deterministic filler
    out = graph_mod._ensure_min_options([{"text": "A"}], minimum=2)
    assert [o["text"] for o in out] == ["A", "Yes"]
    # already >= minimum stays unchanged
    out2 = graph_mod._ensure_min_options([{"text": "A"}, {"text": "B"}], minimum=2)
    assert [o["text"] for o in out2] == ["A", "B"]
    # drops blanks and null image_url (should not persist None)
    out3 = graph_mod._ensure_min_options([{"text": "  "}, {"text": "X", "image_url": None}], minimum=2)
    assert [o["text"] for o in out3][:1] == ["X"]
    assert "image_url" not in out3[0]
    assert len(out3) == 2  # padded to 2


@pytest.mark.asyncio
async def test_adaptive_question_appended_when_enough_answers(agent_graph_memory_saver, agent_thread_id):
    # Start + proceed to build baseline
    s = await run_quiz_start(agent_graph_memory_saver, session_id=agent_thread_id, category="Cats")
    s = await run_quiz_proceed(agent_graph_memory_saver, session_id=agent_thread_id, expect_baseline=True)

    baseline_n = int(s.get("baseline_count") or 0)
    assert baseline_n > 0

    # Provide answers for all baseline questions to unlock adaptive flow
    # (content can be simple; the decider will ask one more under our patched llm)
    hist = [
        {"question_index": i, "question_text": f"Q{i}", "answer_text": "A", "option_index": 0}
        for i in range(baseline_n)
    ]

    # Kick the graph again with the new history; router -> decide_or_finish -> ask -> generate_adaptive_question
    delta = {
        "quiz_history": hist,
        "ready_for_questions": True,
        "trace_id": "t-adapt",
    }
    cfg = {"configurable": {"thread_id": str(agent_thread_id)}}
    await agent_graph_memory_saver.ainvoke(delta, config=cfg)
    s2 = await get_graph_state(agent_graph_memory_saver, agent_thread_id)

    qs_old = s.get("generated_questions") or []
    qs_new = s2.get("generated_questions") or []
    assert len(qs_new) == len(qs_old) + 1
    assert isinstance(qs_new[-1].get("question_text"), str)
    assert isinstance(qs_new[-1].get("options"), list) and len(qs_new[-1]["options"]) >= 2


@pytest.mark.asyncio
async def test_finish_path_when_max_questions_reached(agent_graph_memory_saver, agent_thread_id):
    # Start + proceed to seed synopsis/characters and mark baseline ready
    await run_quiz_start(agent_graph_memory_saver, session_id=agent_thread_id, category="Cats")
    await run_quiz_proceed(agent_graph_memory_saver, session_id=agent_thread_id, expect_baseline=True)

    # Force a long history (>= default max_total_questions=20) to trigger FINISH_NOW branch
    long_history = [
        {"question_index": i, "question_text": f"Q{i}", "answer_text": "A", "option_index": 0}
        for i in range(21)
    ]
    delta = {
        "quiz_history": long_history,
        "ready_for_questions": True,
        "trace_id": "t-finish",
    }
    cfg = {"configurable": {"thread_id": str(agent_thread_id)}}
    await agent_graph_memory_saver.ainvoke(delta, config=cfg)
    s2 = await get_graph_state(agent_graph_memory_saver, agent_thread_id)

    assert s2.get("should_finalize") is True
    final = s2.get("final_result")
    assert isinstance(final, FinalResult)
    assert isinstance(final.title, str) and final.title


@pytest.mark.asyncio
async def test_create_agent_graph_attaches_memory_saver(agent_graph_memory_saver):
    # The fixture already compiled the graph preferring MemorySaver (no Redis).
    # Sanity: the compiled runnable exposes the checkpointer handle; Redis CM should be None.
    cp = getattr(agent_graph_memory_saver, "_async_checkpointer", None)
    cm = getattr(agent_graph_memory_saver, "_redis_cm", None)
    assert cp is not None
    # When using MemorySaver path, there is no redis context manager
    assert cm is None or hasattr(cm, "__aexit__")


# ---------------------------------------------------------------------------
# ADDITIONAL TESTS FOR MORE COVERAGE
# ---------------------------------------------------------------------------

# === Helpers ================================================================

def test_validate_synopsis_payload_rejects_legacy_keys():
    with pytest.raises(ValueError):
        graph_mod._validate_synopsis_payload({"title": "X", "synopsis_text": "legacy"})


def test_to_plain_and_safe_getattr_behaviors():
    model = graph_mod.CharacterProfile(name="N", short_description="s", profile_text="p")
    dumped = graph_mod._to_plain(model)
    assert isinstance(dumped, dict) and dumped["name"] == "N"

    # _safe_getattr works on both models and dicts
    assert graph_mod._safe_getattr(model, "name") == "N"
    assert graph_mod._safe_getattr({"name": "D"}, "name") == "D"
    assert graph_mod._safe_getattr(object(), "missing", default=123) == 123


def test_safe_len_handles_non_sized():
    class NoLen:
        pass

    assert graph_mod._safe_len([1, 2, 3]) == 3
    assert graph_mod._safe_len(NoLen()) is None


def test_dedupe_options_by_text_merges_and_upgrades_image_url():
    opts = [
        {"text": " A "},
        {"text": "a", "image_url": "http://img"},
        {"text": "B"},
        {"text": ""},
        {"text": "b"},  # lowercased dup to check order/merge
    ]
    out = graph_mod._dedupe_options_by_text(opts)
    # order preserved by first appearance; 'A' is one item and gets image_url from later dup
    assert [o["text"] for o in out] == ["A", "B"]
    assert out[0].get("image_url") == "http://img"


def test_coerce_question_to_state_with_bare_string_and_questionout():
    q = graph_mod._coerce_question_to_state("Just text")
    assert q.question_text == "Just text"
    assert q.options == []

    qo = QuestionOut(
        question_text="Q1",
        options=[QuestionOption(text="Yes"), QuestionOption(text="No", image_url=None)],
    )
    q2 = graph_mod._coerce_question_to_state(qo)
    assert q2.question_text == "Q1"
    assert [o["text"] for o in q2.options] == ["Yes", "No"]
    # None image_url should be omitted by the normalizer
    assert all("image_url" not in o or isinstance(o["image_url"], str) for o in q2.options)


def test_coerce_questions_list_with_questionlist_and_padding():
    qlist = QuestionList(
        questions=[
            QuestionOut(question_text="Q1", options=[QuestionOption(text="OnlyOne")]),
            QuestionOut(
                question_text="Q2",
                options=[QuestionOption(text="A"), QuestionOption(text="A", image_url="http://i")],
            ),
        ]
    )
    out = graph_mod._coerce_questions_list(qlist)
    # Q1 padded to 2 options
    assert out[0]["question_text"] == "Q1"
    assert len(out[0]["options"]) >= 2
    # Q2 deduped to single 'A' but keeps image_url from the richer duplicate; then padded back to >=2
    q2opts = out[1]["options"]
    assert q2opts[0]["text"] == "A"
    assert q2opts[0].get("image_url") == "http://i"
    assert len(q2opts) >= 2


def test_phase_router_baseline_ready_missing_count_defaults_to_adaptive():
    s = {"ready_for_questions": True, "baseline_ready": True, "quiz_history": []}
    # answered=0, baseline_count defaults to 0 -> answered >= baseline_count -> adaptive
    assert graph_mod._phase_router(s) == "adaptive"


def test_env_name_uses_settings_and_defaults(monkeypatch):
    # When settings.app.environment is set
    fake_app = types.SimpleNamespace(environment="PROD")
    monkeypatch.setattr(graph_mod.settings, "app", fake_app, raising=False)
    assert graph_mod._env_name() == "prod"

    # When settings.app is missing or raises, default to "local"
    class Boom:
        @property
        def environment(self):
            raise RuntimeError("boom")

    monkeypatch.setattr(graph_mod.settings, "app", Boom(), raising=False)
    assert graph_mod._env_name() == "local"


def test_schema_for_registry_lookup():
    assert graph_mod.schema_for("decision_maker") is graph_mod.NextStepDecision
    assert graph_mod.schema_for("unknown") is None

@pytest.mark.asyncio
async def test_assemble_and_finish_summarizes_counts():
    s: GraphState = {
        "session_id": uuid.uuid4(),
        "trace_id": "t",
        "generated_characters": [
            graph_mod.CharacterProfile(name="A", short_description="", profile_text="")
        ],
        "generated_questions": [
            graph_mod.QuizQuestion(question_text="Q", options=[{"text": "X"}, {"text": "Y"}])
        ],
        "category_synopsis": graph_mod.Synopsis(title="T", summary="S"),
    }
    out = await graph_mod._assemble_and_finish(s)
    [msg] = out["messages"]
    assert "characters: 1" in msg.content and "questions: 1" in msg.content


# === Nodes / router edge cases ==============================================

@pytest.mark.asyncio
async def test_generate_baseline_questions_backfills_flag_without_tool(monkeypatch):
    # If questions exist but baseline flag is missing/false, node must not call the tool.
    called = {"tool": False}

    class Stub:
        async def ainvoke(self, *_args, **_kwargs):
            called["tool"] = True
            return [{"question_text": "ShouldNotCall", "options": [{"text": "X"}, {"text": "Y"}]}]

    monkeypatch.setattr(graph_mod, "tool_generate_baseline_questions", Stub(), raising=True)

    state: GraphState = {
        "session_id": uuid.uuid4(),
        "trace_id": "t",
        "generated_questions": [{"question_text": "Q", "options": [{"text": "A"}, {"text": "B"}]}],
        "baseline_ready": False,
        "ready_for_questions": True,
    }
    out = await graph_mod._generate_baseline_questions_node(state)
    assert out["baseline_ready"] is True
    assert out["baseline_count"] == 1
    # Ensure we never invoked the tool on this backfill path
    assert called["tool"] is False


@pytest.mark.asyncio
async def test_generate_adaptive_question_dedup_and_padding(monkeypatch):
    # Stub next-question tool to return duplicates and blanks
    class StubNext:
        async def ainvoke(self, *_args, **_kwargs):
            return {
                "question_text": "Choose",
                "options": [{"text": "A"}, {"text": "a", "image_url": "http://i"}, {"text": "   "}],
            }

    monkeypatch.setattr(graph_mod, "tool_generate_next_question", StubNext(), raising=True)

    state: GraphState = {
        "session_id": uuid.uuid4(),
        "trace_id": "t",
        "category_synopsis": graph_mod.Synopsis(title="T", summary="S"),
        "generated_characters": [
            graph_mod.CharacterProfile(name="C", short_description="", profile_text="")
        ],
        "generated_questions": [{"question_text": "Q0", "options": [{"text": "X"}, {"text": "Y"}]}],
        "quiz_history": [{"question_index": 0, "question_text": "Q0", "answer_text": "X"}],
    }
    out = await graph_mod._generate_adaptive_question_node(state)
    new_list = out["generated_questions"]
    assert len(new_list) == 2
    # Options should dedupe to 'A' (with image_url upgraded) then padded to >=2
    last_opts = new_list[-1]["options"]
    assert last_opts[0]["text"] == "A"
    assert last_opts[0].get("image_url") == "http://i"
    assert len(last_opts) >= 2


@pytest.mark.asyncio
async def test_generate_characters_node_no_archetypes_and_name_lock(monkeypatch):
    # --- No archetypes: returns a message and no 'generated_characters' key
    out = await graph_mod._generate_characters_node({
        "session_id": uuid.uuid4(),
        "trace_id": "t",
        "category": "Cats",
        "ideal_archetypes": [],
    })
    assert "generated_characters" not in out

    # --- Name lock: tool returns mismatched name; node should lock to requested label
    class StubDraft:
        async def ainvoke(self, payload):
            # Return a valid CharacterProfile but with the wrong name
            return graph_mod.CharacterProfile(name="Wrong", short_description="s", profile_text="p")

    monkeypatch.setattr(graph_mod, "tool_draft_character_profile", StubDraft(), raising=True)

    out2 = await graph_mod._generate_characters_node({
        "session_id": uuid.uuid4(),
        "trace_id": "t",
        "category": "Cats",
        "ideal_archetypes": ["Correct"],
    })
    chars = out2.get("generated_characters") or []
    assert len(chars) == 1 and chars[0].name == "Correct"


# New: explicit tool failure path for baseline generation
@pytest.mark.asyncio
async def test_generate_baseline_questions_tool_failure_sets_ready(monkeypatch):
    class Boom:
        async def ainvoke(self, *_a, **_k):
            raise RuntimeError("fail")
    monkeypatch.setattr(graph_mod, "tool_generate_baseline_questions", Boom(), raising=True)

    s: GraphState = {"session_id": uuid.uuid4(), "trace_id": "t", "ready_for_questions": True}
    out = await graph_mod._generate_baseline_questions_node(s)
    assert out["baseline_ready"] is True
    assert out["baseline_count"] == 0
    assert out["generated_questions"] == []


# New: enforce baseline_questions_n cap
@pytest.mark.asyncio
async def test_generate_baseline_questions_respects_baseline_questions_n(monkeypatch):
    q = types.SimpleNamespace(baseline_questions_n=2)
    monkeypatch.setattr(graph_mod.settings, "quiz", q, raising=False)

    class Stub:
        async def ainvoke(self, *_a, **_k):
            return [{"question_text": f"Q{i}", "options":[{"text":"A"},{"text":"B"}]} for i in range(5)]
    monkeypatch.setattr(graph_mod, "tool_generate_baseline_questions", Stub(), raising=True)

    s: GraphState = {"session_id": uuid.uuid4(), "trace_id": "t", "ready_for_questions": True}
    out = await graph_mod._generate_baseline_questions_node(s)
    assert out["baseline_ready"] is True
    assert out["baseline_count"] == 2
    assert len(out["generated_questions"]) == 2


@pytest.mark.asyncio
async def test_decide_or_finish_early_finish_threshold(monkeypatch):
    # Configure quiz knobs explicitly for determinism
    q = types.SimpleNamespace(
        max_total_questions=20,
        min_questions_before_early_finish=2,
        early_finish_confidence=0.9,
    )
    monkeypatch.setattr(graph_mod.settings, "quiz", q, raising=False)

    # Stub decision tool: asks to finish but with LOW confidence -> should NOT finalize due to threshold
    class StubDecisionLow:
        def __init__(self):
            self.action = "FINISH_NOW"
            self.confidence = 0.5
            self.winning_character_name = ""

    class StubDecider:
        async def ainvoke(self, *_args, **_kwargs):
            return StubDecisionLow()

    monkeypatch.setattr(graph_mod, "tool_decide_next_step", StubDecider(), raising=True)

    state: GraphState = {
        "session_id": uuid.uuid4(),
        "trace_id": "t",
        "category_synopsis": graph_mod.Synopsis(title="T", summary="S"),
        "generated_characters": [graph_mod.CharacterProfile(name="A", short_description="", profile_text="")],
        "quiz_history": [{"question_index": 0, "question_text": "Q", "answer_text": "A"}],
        "baseline_count": 1,  # baseline answered
    }
    out = await graph_mod._decide_or_finish_node(state)
    assert out["should_finalize"] is False
    assert 0.0 <= out.get("current_confidence", 0.0) <= 1.0


# New: confidence percent normalization + min-gate enforcement
@pytest.mark.asyncio
async def test_decide_or_finish_confidence_percent_and_min_gate(monkeypatch):
    q = types.SimpleNamespace(max_total_questions=20, min_questions_before_early_finish=3, early_finish_confidence=0.9)
    monkeypatch.setattr(graph_mod.settings, "quiz", q, raising=False)

    class StubDecision:
        def __init__(self):
            self.action = "FINISH_NOW"
            self.confidence = 95  # percent
            self.winning_character_name = "A"
    class StubDecider:
        async def ainvoke(self, *_a, **_k): return StubDecision()
    monkeypatch.setattr(graph_mod, "tool_decide_next_step", StubDecider(), raising=True)

    s: GraphState = {
        "session_id": uuid.uuid4(), "trace_id": "t",
        "category_synopsis": graph_mod.Synopsis(title="T", summary="S"),
        "generated_characters":[graph_mod.CharacterProfile(name="A", short_description="", profile_text="")],
        "quiz_history":[{"question_index":0,"question_text":"Q","answer_text":"X"}],  # answered=1 < min=3
        "baseline_count":1,
    }
    out = await graph_mod._decide_or_finish_node(s)
    assert out["should_finalize"] is False
    assert 0.9 <= out["current_confidence"] <= 1.0  # normalized to fraction


# New: final-writer error path -> placeholder FinalResult
@pytest.mark.asyncio
async def test_decide_or_finish_final_writer_error_returns_placeholder(monkeypatch):
    q = types.SimpleNamespace(max_total_questions=20, min_questions_before_early_finish=0, early_finish_confidence=0.0)
    monkeypatch.setattr(graph_mod.settings, "quiz", q, raising=False)

    class StubDec:
        async def ainvoke(self, *_a, **_k):
            class D: action="FINISH_NOW"; confidence=1.0; winning_character_name=None
            return D()
    class BoomWriter:
        async def ainvoke(self, *_a, **_k): raise RuntimeError("fail")
    monkeypatch.setattr(graph_mod, "tool_decide_next_step", StubDec(), raising=True)
    monkeypatch.setattr(graph_mod, "tool_write_final_user_profile", BoomWriter(), raising=True)

    s: GraphState = {
        "session_id": uuid.uuid4(),"trace_id":"t",
        "category_synopsis": graph_mod.Synopsis(title="T", summary="S"),
        "generated_characters":[graph_mod.CharacterProfile(name="A", short_description="", profile_text="")],
        "quiz_history":[{"question_index":0,"question_text":"Q","answer_text":"X"}],
        "baseline_count":1,
    }
    out = await graph_mod._decide_or_finish_node(s)
    assert out["should_finalize"] is True
    assert isinstance(out["final_result"], FinalResult)
    assert out["final_result"].title == "Result Error"


@pytest.mark.asyncio
async def test_decide_or_finish_winner_selection_and_fallback(monkeypatch):
    # Set knobs permissive so FINISH_NOW is allowed
    q = types.SimpleNamespace(
        max_total_questions=20,
        min_questions_before_early_finish=0,
        early_finish_confidence=0.0,
    )
    monkeypatch.setattr(graph_mod.settings, "quiz", q, raising=False)

    # Recorder for what 'winning_character' was sent to final profile writer
    recorded = {"winner": None}

    class StubWriter:
        async def ainvoke(self, payload):
            recorded["winner"] = payload.get("winning_character")
            return FinalResult(title="Done", description="ok", image_url=None)

    monkeypatch.setattr(graph_mod, "tool_write_final_user_profile", StubWriter(), raising=True)

    class StubDecisionHigh:
        def __init__(self, name):
            self.action = "FINISH_NOW"
            self.confidence = 1.0
            self.winning_character_name = name

    class StubDecider:
        def __init__(self, name):
            self.name = name

        async def ainvoke(self, *_args, **_kwargs):
            return StubDecisionHigh(self.name)

    # Case 1: winner name matches existing character -> that exact character is passed
    monkeypatch.setattr(graph_mod, "tool_decide_next_step", StubDecider("Bob"), raising=True)
    state: GraphState = {
        "session_id": uuid.uuid4(),
        "trace_id": "t",
        "category_synopsis": graph_mod.Synopsis(title="T", summary="S"),
        "generated_characters": [
            graph_mod.CharacterProfile(name="Alice", short_description="", profile_text=""),
            graph_mod.CharacterProfile(name="Bob", short_description="", profile_text=""),
        ],
        "quiz_history": [{"question_index": 0, "question_text": "Q", "answer_text": "A"}],
        "baseline_count": 1,
    }
    out = await graph_mod._decide_or_finish_node(state)
    assert out["should_finalize"] is True
    assert isinstance(out["final_result"], FinalResult)
    assert recorded["winner"]["name"] == "Bob"

    # Case 2: winner name not found -> falls back to first character
    monkeypatch.setattr(graph_mod, "tool_decide_next_step", StubDecider("Charlie"), raising=True)
    recorded["winner"] = None
    out2 = await graph_mod._decide_or_finish_node(state)
    assert out2["should_finalize"] is True
    assert recorded["winner"]["name"] == "Alice"


# === Graph factory / checkpointer ===========================================

@pytest.mark.asyncio
async def test_create_agent_graph_fallbacks_when_redis_unavailable(monkeypatch):
    """
    Force the Redis path (USE_MEMORY_SAVER=0, non-local env) but make it fail so
    create_agent_graph() falls back to MemorySaver. This should work without Redis.
    """
    monkeypatch.setenv("USE_MEMORY_SAVER", "0")
    # Pretend we're not in local/dev so Redis is preferred
    fake_app = types.SimpleNamespace(environment="production")
    monkeypatch.setattr(graph_mod.settings, "app", fake_app, raising=False)

    # Point REDIS_URL to something that will fail
    monkeypatch.setattr(graph_mod.settings, "REDIS_URL", "redis://invalid:6379/0", raising=False)

    g = await graph_mod.create_agent_graph()
    try:
        cp = getattr(g, "_async_checkpointer", None)
        cm = getattr(g, "_redis_cm", None)
        # We should still have a checkpointer (MemorySaver) and cm should be None (since Redis failed)
        assert cp is not None
        assert cm is None
    finally:
        # Be nice and close any contexts
        await graph_mod.aclose_agent_graph(g)


# New: explicit MemorySaver positive path in local env
@pytest.mark.asyncio
async def test_create_agent_graph_uses_memory_saver_in_local(monkeypatch):
    monkeypatch.setenv("USE_MEMORY_SAVER", "1")
    fake_app = types.SimpleNamespace(environment="local")
    monkeypatch.setattr(graph_mod.settings, "app", fake_app, raising=False)
    g = await graph_mod.create_agent_graph()
    try:
        assert getattr(g, "_redis_cm", None) is None
        assert getattr(g, "_async_checkpointer", None) is not None
    finally:
        await graph_mod.aclose_agent_graph(g)


# New: graceful close semantics when both handles exist
@pytest.mark.asyncio
async def test_aclose_agent_graph_calls_handles(monkeypatch):
    flags = {"cp": False, "cm": False}

    class CP:
        async def aclose(self):
            flags["cp"] = True

    class CM:
        async def __aexit__(self, *_):
            flags["cm"] = True

    agent_graph = types.SimpleNamespace(_async_checkpointer=CP(), _redis_cm=CM())
    await graph_mod.aclose_agent_graph(agent_graph)
    assert flags["cp"] is True and flags["cm"] is True


# === Extra guard rails around helpers =======================================

def test_validate_character_payload_accepts_model_instance():
    inst = graph_mod.CharacterProfile(name="N", short_description="", profile_text="")
    out = graph_mod._validate_character_payload(inst)
    assert out is inst


def test_coerce_question_to_state_normalizes_image_aliases_and_drops_none():
    obj = {
        "question_text": "Q",
        "options": [
            {"label": "A"},
            {"text": "B", "imageUrl": "http://img"},
            {"text": "C", "image_url": None},
        ],
    }
    q = graph_mod._coerce_question_to_state(obj)
    assert [o["text"] for o in q.options] == ["A", "B", "C"]
    b = next(o for o in q.options if o["text"] == "B")
    assert b.get("image_url") == "http://img"
    c = next(o for o in q.options if o["text"] == "C")
    assert "image_url" not in c


# New: None/garbage coercions for questions/options
def test_coerce_questions_list_handles_none_and_garbage():
    out = graph_mod._coerce_questions_list(None)
    assert out == []

    raw = [
        {"text": "Q", "options": ["A", {"text": ""}, 123, {"text": "B"}]},
    ]
    out2 = graph_mod._coerce_questions_list(raw)
    assert out2 and out2[0]["question_text"] == "Q"
    # "A" becomes {"text":"A"}, blank/invalid are dropped, then padded to >= 2
    texts = [o["text"] for o in out2[0]["options"]]
    assert "A" in texts and "B" in texts
    assert len(out2[0]["options"]) >= 2


def test_ensure_min_options_with_non_dicts_and_padding():
    out = graph_mod._ensure_min_options(["A", {"text": ""}], minimum=2)
    # non-dicts and blanks are dropped; padded deterministically
    assert [o["text"] for o in out] == ["Yes", "No"]


# New: character generation timeouts & retries stay fast
@pytest.mark.asyncio
async def test_generate_characters_timeouts_and_retries_fast(monkeypatch):
    # Tiny timeout to force wait_for to cancel the tool
    llm = types.SimpleNamespace(per_call_timeout_s=0.01)
    monkeypatch.setattr(graph_mod.settings, "llm", llm, raising=False)

    async def slow_tool(*_a, **_k):
        await asyncio.sleep(0.02)

    class StubDraft:
        async def ainvoke(self, *_a, **_k):
            return await slow_tool()

    # Patch the tool and make backoff sleeps instant
    monkeypatch.setattr(graph_mod, "tool_draft_character_profile", StubDraft(), raising=True)
    monkeypatch.setattr(graph_mod.asyncio, "sleep", lambda *_a, **_k: asyncio.sleep(0), raising=True)

    out = await graph_mod._generate_characters_node({
        "session_id": uuid.uuid4(),
        "trace_id": "t",
        "category": "Cats",
        "ideal_archetypes": ["A", "B"],
    })
    # All attempts time out -> no characters emitted, but message returned
    assert "generated_characters" not in out
    assert out["messages"] and out["is_error"] is False


# ---------------------------------------------------------------------------
# (End of file)
# ---------------------------------------------------------------------------
