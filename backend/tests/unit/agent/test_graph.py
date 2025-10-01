import uuid
import pytest

from langchain_core.messages import HumanMessage

# The real graph module (we'll use some internal helpers directly)
import app.agent.graph as graph_mod

# Typed alias for state (dict-like)
from app.agent.state import GraphState

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
# Helper utilities in graph.py
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
    s = await run_quiz_proceed(agent_graph_memory_saver, session_id=agent_thread_id, expect_baseline=True)

    # Force a long history (>= default max_total_questions=20) to trigger FINISH_NOW branch
    long_history = [
        {"question_index": i, "question_text": f"Q{i}", "answer_text": "A", "option_index": 0}
        for i in range(21)
    ]
    delta = {
        "quiz_history": long_history,
        "ready_for_questions": True,
        # keep whatever baseline_count already is; answered >= baseline_count anyway
        "trace_id": "t-finish",
    }
    cfg = {"configurable": {"thread_id": str(agent_thread_id)}}
    await agent_graph_memory_saver.ainvoke(delta, config=cfg)
    s2 = await get_graph_state(agent_graph_memory_saver, agent_thread_id)

    assert s2.get("should_finalize") is True
    final = s2.get("final_result")
    assert final and isinstance(final.get("title"), str) and final["title"]


@pytest.mark.asyncio
async def test_create_agent_graph_attaches_memory_saver(agent_graph_memory_saver):
    # The fixture already compiled the graph preferring MemorySaver (no Redis).
    # Sanity: the compiled runnable exposes the checkpointer handle; Redis CM should be None.
    cp = getattr(agent_graph_memory_saver, "_async_checkpointer", None)
    cm = getattr(agent_graph_memory_saver, "_redis_cm", None)
    assert cp is not None
    # When using MemorySaver path, there is no redis context manager
    assert cm is None or hasattr(cm, "__aexit__")

