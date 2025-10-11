# tests/unit/agent/tools/test_data_tools.py

from types import SimpleNamespace
import sys
import uuid
import pytest

from app.agent.tools import data_tools as dtools
from app.agent.tools.data_tools import (
    search_for_contextual_sessions as _real_search_for_contextual_sessions,
    fetch_character_details as _real_fetch_character_details,
    wikipedia_search as _real_wikipedia_search,
    web_search as _real_web_search,
)

# Reuse existing helpers/fixtures instead of defining local copies
from tests.fixtures.agent_graph_fixtures import build_graph_config

pytestmark = pytest.mark.unit


# Ensure autouse tool stubs are bypassed for this module: we want real implementations.
@pytest.fixture(autouse=True)
def _restore_real_data_tools(monkeypatch):
    monkeypatch.setattr(dtools, "search_for_contextual_sessions", _real_search_for_contextual_sessions, raising=False)
    monkeypatch.setattr(dtools, "fetch_character_details", _real_fetch_character_details, raising=False)
    monkeypatch.setattr(dtools, "wikipedia_search", _real_wikipedia_search, raising=False)
    monkeypatch.setattr(dtools, "web_search", _real_web_search, raising=False)


# Reset the per-run retrieval budget so tests don't leak into each other.
@pytest.fixture(autouse=True)
def _reset_retrieval_budget(monkeypatch):
    monkeypatch.setattr(dtools, "_RETRIEVAL_BUDGET", {}, raising=False)


# ---------------------------------------------------------------------------
# search_for_contextual_sessions
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_search_for_contextual_sessions_returns_empty_when_no_db():
    out = await dtools.search_for_contextual_sessions.ainvoke(
        {"tool_input": {"category_synopsis": "Cats cats cats"}},
        config={},  # no db_session present
    )
    assert out == []


@pytest.mark.asyncio
async def test_search_for_contextual_sessions_embedding_error(monkeypatch):
    # get_embedding fails -> tolerant empty list
    async def _boom(**_):
        raise RuntimeError("embedding down")
    monkeypatch.setattr(dtools.llm_service, "get_embedding", _boom, raising=True)

    # Use existing fakes from your fixtures
    from tests.fixtures.db_fixtures import FakeAsyncSession, FakeResult
    cfg = build_graph_config(uuid.uuid4(), db_session=FakeAsyncSession(FakeResult(mappings_rows=[])))

    out = await dtools.search_for_contextual_sessions.ainvoke(
        {"tool_input": {"category_synopsis": "A synopsis"}},
        config=cfg,
    )
    assert out == []


@pytest.mark.asyncio
async def test_search_for_contextual_sessions_no_embedding_data(monkeypatch):
    # Invalid shape / empty embedding -> []
    async def _bad(**_):
        return [[]]  # first vector empty -> treated as no embedding
    monkeypatch.setattr(dtools.llm_service, "get_embedding", _bad, raising=True)

    from tests.fixtures.db_fixtures import FakeAsyncSession, FakeResult
    cfg = build_graph_config(uuid.uuid4(), db_session=FakeAsyncSession(FakeResult(mappings_rows=[])))

    out = await dtools.search_for_contextual_sessions.ainvoke(
        {"tool_input": {"category_synopsis": "A synopsis"}},
        config=cfg,
    )
    assert out == []


@pytest.mark.asyncio
async def test_search_for_contextual_sessions_success_and_row_filtering(monkeypatch):
    async def _ok(**_):
        return [[0.1, 0.2, 0.3]]  # looks like an embedding vector
    monkeypatch.setattr(dtools.llm_service, "get_embedding", _ok, raising=True)

    rows = [
        {
            "session_id": "11111111-1111-1111-1111-111111111111",
            "category": "Cats",
            "category_synopsis": {"title": "T1"},
            "final_result": {"title": "FR1"},
            "judge_plan_feedback": "gp1",
            "user_feedback_text": "uf1",
            "distance": 0.12,
        },
        {
            "session_id": "22222222-2222-2222-2222-222222222222",
            "category": "Gilmore Girls",
            "category_synopsis": {"title": "T2"},
            "final_result": {"title": "FR2"},
            "judge_plan_feedback": "gp2",
            "user_feedback_text": "uf2",
            "distance": "oops",  # malformed -> row skipped
        },
        {
            "session_id": "33333333-3333-3333-3333-333333333333",
            "category": "Cats",
            "category_synopsis": {"title": "T3"},
            "final_result": {"title": "FR3"},
            "judge_plan_feedback": None,
            "user_feedback_text": None,
            "distance": "0.55",  # string parse OK
        },
    ]

    from tests.fixtures.db_fixtures import FakeAsyncSession, FakeResult
    cfg = build_graph_config(uuid.uuid4(), db_session=FakeAsyncSession(FakeResult(mappings_rows=rows)))

    out = await dtools.search_for_contextual_sessions.ainvoke(
        {"tool_input": {"category_synopsis": "Fuzzy felines everywhere"}},
        config=cfg,
    )
    assert isinstance(out, list)
    assert len(out) == 2  # one row skipped due to bad distance
    for hit in out:
        assert set(hit.keys()) == {
            "session_id",
            "category",
            "category_synopsis",
            "final_result",
            "judge_feedback",
            "user_feedback",
            "distance",
        }
        assert isinstance(hit["session_id"], str)


@pytest.mark.asyncio
async def test_search_for_contextual_sessions_query_error(monkeypatch):
    async def _ok(**_):
        return [[1.0, 0.0, 0.0]]
    monkeypatch.setattr(dtools.llm_service, "get_embedding", _ok, raising=True)

    # Make execute() raise using existing FakeAsyncSession by monkeypatching the instance
    from tests.fixtures.db_fixtures import FakeAsyncSession, FakeResult
    session = FakeAsyncSession(FakeResult(mappings_rows=[]))

    async def _boom_execute(*_a, **_k):
        raise RuntimeError("execute boom")
    # patch the bound method on this instance only
    monkeypatch.setattr(session, "execute", _boom_execute, raising=False)

    cfg = build_graph_config(uuid.uuid4(), db_session=session)

    out = await dtools.search_for_contextual_sessions.ainvoke(
        {"tool_input": {"category_synopsis": "A synopsis"}},
        config=cfg,
    )
    assert out == []


# ---------------------------------------------------------------------------
# fetch_character_details
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_fetch_character_details_no_db():
    out = await dtools.fetch_character_details.ainvoke(
        {"tool_input": {"character_id": "abc"}},
        config={},  # no db_session
    )
    assert out is None


@pytest.mark.asyncio
async def test_fetch_character_details_not_found():
    from tests.fixtures.db_fixtures import FakeAsyncSession, FakeResult
    cfg = build_graph_config(uuid.uuid4(), db_session=FakeAsyncSession(FakeResult(scalar_obj=None)))

    out = await dtools.fetch_character_details.ainvoke(
        {"tool_input": {"character_id": "not-there"}},
        config=cfg,
    )
    assert out is None


@pytest.mark.asyncio
async def test_fetch_character_details_ok():
    class _Char:
        def __init__(self):
            self.id = "12345678-1234-1234-1234-123456789abc"
            self.name = "Lorelai Gilmore"
            self.profile_text = "Coffee, quick wit."
            self.short_description = "Stars Hollow icon."

    from tests.fixtures.db_fixtures import FakeAsyncSession, FakeResult
    cfg = build_graph_config(uuid.uuid4(), db_session=FakeAsyncSession(FakeResult(scalar_obj=_Char())))

    out = await dtools.fetch_character_details.ainvoke(
        {"tool_input": {"character_id": "whatever"}},
        config=cfg,
    )
    assert out == {
        "id": "12345678-1234-1234-1234-123456789abc",
        "name": "Lorelai Gilmore",
        "profile_text": "Coffee, quick wit.",
        "short_description": "Stars Hollow icon.",
    }


# ---------------------------------------------------------------------------
# wikipedia_search (sync tool)
# ---------------------------------------------------------------------------

def test_wikipedia_search_ok(monkeypatch):
    class _Stub:
        def run(self, q):
            return f"Summary for {q}"

    # Make sure policy allows it and budget is non-zero
    monkeypatch.setattr(
        dtools.settings,
        "retrieval",
        SimpleNamespace(policy="all", allow_wikipedia=True, allow_web=False, max_calls_per_run=1),
        raising=False,
    )

    # Replace the wrapper instance used by the tool
    monkeypatch.setattr(dtools, "_wikipedia_search", _Stub(), raising=True)
    out = dtools.wikipedia_search.invoke({"query": "Cat"})
    assert out == "Summary for Cat"


def test_wikipedia_search_handles_error(monkeypatch):
    class _Stub:
        def run(self, q):
            raise RuntimeError("no internet")

    monkeypatch.setattr(
        dtools.settings,
        "retrieval",
        SimpleNamespace(policy="all", allow_wikipedia=True, allow_web=False, max_calls_per_run=1),
        raising=False,
    )

    monkeypatch.setattr(dtools, "_wikipedia_search", _Stub(), raising=True)
    out = dtools.wikipedia_search.invoke({"query": "Cat"})
    assert out == ""


# ---------------------------------------------------------------------------
# web_search (async)
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_web_search_returns_empty_without_config(monkeypatch):
    monkeypatch.setattr(dtools.settings, "llm_tools", {}, raising=False)
    out = await dtools.web_search.ainvoke({"query": "cats"})
    assert out == ""


@pytest.mark.asyncio
async def test_web_search_handles_missing_sdk(monkeypatch):
    # Provide minimal config
    monkeypatch.setattr(
        dtools.settings,
        "llm_tools",
        {
            "web_search": SimpleNamespace(
                model="gpt-4.1-mini",
                allowed_domains=["https://example.com/"],
                user_location=None,
                include_sources=True,
                effort=None,
                tool_choice="auto",
                timeout_s=5,
            )
        },
        raising=False,
    )

    # Allow web + ample budget
    monkeypatch.setattr(
        dtools.settings,
        "retrieval",
        SimpleNamespace(policy="all", allow_wikipedia=False, allow_web=True, max_calls_per_run=10),
        raising=False,
    )

    # Simulate import without AsyncOpenAI symbol
    module = SimpleNamespace()  # lacks AsyncOpenAI
    monkeypatch.setitem(sys.modules, "openai", module)

    out = await dtools.web_search.ainvoke({"query": "cats", "trace_id": str(uuid.uuid4())})
    assert out == ""


@pytest.mark.asyncio
async def test_web_search_happy_path_uses_output_text(monkeypatch):
    monkeypatch.setattr(
        dtools.settings,
        "llm_tools",
        {
            "web_search": SimpleNamespace(
                model="gpt-4.1-mini",
                allowed_domains=[],
                user_location=None,
                include_sources=True,
                effort=None,
                tool_choice="auto",
                timeout_s=5,
            )
        },
        raising=False,
    )

    # Allow web + ample budget
    monkeypatch.setattr(
        dtools.settings,
        "retrieval",
        SimpleNamespace(policy="all", allow_wikipedia=False, allow_web=True, max_calls_per_run=10),
        raising=False,
    )

    class _Resp:
        output_text = "Top results for cats..."

    class _Responses:
        async def create(self, **kwargs):
            return _Resp()

    class _Client:
        def __init__(self, *args, **kwargs):
            self.responses = _Responses()
        async def __aenter__(self):
            return self
        async def __aexit__(self, exc_type, exc, tb):
            return False

    fake_mod = SimpleNamespace(AsyncOpenAI=_Client)
    monkeypatch.setitem(sys.modules, "openai", fake_mod)

    out = await dtools.web_search.ainvoke({"query": "cats", "trace_id": str(uuid.uuid4())})
    assert out == "Top results for cats..."


@pytest.mark.asyncio
async def test_web_search_parse_fallback_when_no_output_text(monkeypatch):
    monkeypatch.setattr(
        dtools.settings,
        "llm_tools",
        {
            "web_search": SimpleNamespace(
                model="gpt-4.1-mini",
                allowed_domains=[],
                user_location=None,
                include_sources=True,
                effort=None,
                tool_choice={"type": "web_search"},
                timeout_s=5,
            )
        },
        raising=False,
    )

    # Allow web + ample budget
    monkeypatch.setattr(
        dtools.settings,
        "retrieval",
        SimpleNamespace(policy="all", allow_wikipedia=False, allow_web=True, max_calls_per_run=10),
        raising=False,
    )

    class _Content:
        def __init__(self, text):
            self.type = "output_text"
            self.text_out = text

    class _Message:
        def __init__(self, text):
            self.type = "message"
            self.content = [_Content(text)]

    class _Resp:
        output_text = ""   # force fallback path
        output = [_Message("Parsed text!")]

    class _Responses:
        async def create(self, **kwargs):
            return _Resp()

    class _Client:
        def __init__(self, *args, **kwargs):
            self.responses = _Responses()
        async def __aenter__(self):
            return self
        async def __aexit__(self, exc_type, exc, tb):
            return False

    fake_mod = SimpleNamespace(AsyncOpenAI=_Client)
    monkeypatch.setitem(sys.modules, "openai", fake_mod)

    out = await dtools.web_search.ainvoke({"query": "cats", "trace_id": str(uuid.uuid4())})
    assert out == "Parsed text!"
