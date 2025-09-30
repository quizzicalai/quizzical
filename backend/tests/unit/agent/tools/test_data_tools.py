import sys
from types import SimpleNamespace
import pytest

from app.agent.tools import data_tools as dtools

pytestmark = pytest.mark.unit


# ---------------------------------------------------------------------------
# search_for_contextual_sessions
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_search_for_contextual_sessions_returns_empty_when_no_db():
    out = await dtools.search_for_contextual_sessions.ainvoke(
        {"category_synopsis": "Cats cats cats"}
    )
    assert out == []


@pytest.mark.asyncio
async def test_search_for_contextual_sessions_embedding_error(monkeypatch):
    # Embed fails -> non-blocking empty list
    async def _boom(**_):
        raise RuntimeError("embedding down")
    monkeypatch.setattr(dtools.llm_service, "get_embedding", _boom, raising=True)

    from tests.helpers.fakes import FakeAsyncSession, FakeResult
    cfg = {"configurable": {"db_session": FakeAsyncSession(FakeResult(mappings_rows=[]))}}

    out = await dtools.search_for_contextual_sessions.ainvoke(
        {"category_synopsis": "A synopsis"}, config=cfg
    )
    assert out == []


@pytest.mark.asyncio
async def test_search_for_contextual_sessions_no_embedding_data(monkeypatch):
    # Invalid shape / empty embedding -> []
    async def _bad(**_):
        return [[]]  # first vector empty -> treated as no embedding
    monkeypatch.setattr(dtools.llm_service, "get_embedding", _bad, raising=True)

    from tests.helpers.fakes import FakeAsyncSession, FakeResult
    cfg = {"configurable": {"db_session": FakeAsyncSession(FakeResult(mappings_rows=[]))}}

    out = await dtools.search_for_contextual_sessions.ainvoke(
        {"category_synopsis": "A synopsis"}, config=cfg
    )
    assert out == []


@pytest.mark.asyncio
async def test_search_for_contextual_sessions_success_and_row_filtering(monkeypatch):
    async def _ok(**_):
        return [[0.1, 0.2, 0.3]]  # looks like a vector
    monkeypatch.setattr(dtools.llm_service, "get_embedding", _ok, raising=True)

    # Two good rows, one malformed row (distance not float-able) is skipped.
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
            "distance": "oops",  # malformed -> will be skipped
        },
        {
            "session_id": "33333333-3333-3333-3333-333333333333",
            "category": "Cats",
            "category_synopsis": {"title": "T3"},
            "final_result": {"title": "FR3"},
            "judge_plan_feedback": None,
            "user_feedback_text": None,
            "distance": "0.55",  # string that can be float-ed
        },
    ]

    from tests.helpers.fakes import FakeAsyncSession, FakeResult
    cfg = {"configurable": {"db_session": FakeAsyncSession(FakeResult(mappings_rows=rows))}}

    out = await dtools.search_for_contextual_sessions.ainvoke(
        {"category_synopsis": "Fuzzy felines everywhere"}, config=cfg
    )
    assert isinstance(out, list)
    # only 2 valid rows should pass
    assert len(out) == 2
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

    # Execute raises -> []
    from tests.helpers.fakes import FakeAsyncSession, FakeResult
    cfg = {"configurable": {"db_session": FakeAsyncSession(FakeResult(), raise_on_execute=True)}}

    out = await dtools.search_for_contextual_sessions.ainvoke(
        {"category_synopsis": "A synopsis"}, config=cfg
    )
    assert out == []


# ---------------------------------------------------------------------------
# fetch_character_details
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_fetch_character_details_no_db():
    out = await dtools.fetch_character_details.ainvoke({"character_id": "abc"})
    assert out is None


@pytest.mark.asyncio
async def test_fetch_character_details_not_found(monkeypatch):
    # scalars().first() -> None
    from tests.helpers.fakes import FakeAsyncSession, FakeResult
    cfg = {"configurable": {"db_session": FakeAsyncSession(FakeResult(scalar_obj=None))}}

    out = await dtools.fetch_character_details.ainvoke({"character_id": "not-there"}, config=cfg)
    assert out is None


@pytest.mark.asyncio
async def test_fetch_character_details_ok(monkeypatch):
    # scalars().first() -> minimal fake Character-like object
    class _Char:
        def __init__(self):
            self.id = "12345678-1234-1234-1234-123456789abc"
            self.name = "Lorelai Gilmore"
            self.profile_text = "Coffee, quick wit."
            self.short_description = "Stars Hollow icon."

    from tests.helpers.fakes import FakeAsyncSession, FakeResult
    cfg = {"configurable": {"db_session": FakeAsyncSession(FakeResult(scalar_obj=_Char()))}}

    out = await dtools.fetch_character_details.ainvoke({"character_id": "whatever"}, config=cfg)
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

    monkeypatch.setattr(dtools, "_wikipedia_search", _Stub(), raising=True)
    out = dtools.wikipedia_search.invoke({"query": "Cat"})
    assert out == "Summary for Cat"


def test_wikipedia_search_handles_error(monkeypatch):
    class _Stub:
        def run(self, q):
            raise RuntimeError("no internet")

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
    # Provide a minimal config
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

    # Simulate "from openai import AsyncOpenAI" ImportError by
    # injecting a module without that attribute.
    module = SimpleNamespace()  # no AsyncOpenAI attr
    monkeypatch.setitem(sys.modules, "openai", module)
    out = await dtools.web_search.ainvoke({"query": "cats"})
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

    class _Resp:
        # SDK convenience accessor
        output_text = "Top results for cats..."

    class _Responses:
        async def create(self, **kwargs):
            return _Resp()

    class _Client:
        def __init__(self):
            self.responses = _Responses()

    # Fake the OpenAI SDK import
    fake_mod = SimpleNamespace(AsyncOpenAI=_Client)
    monkeypatch.setitem(sys.modules, "openai", fake_mod)

    out = await dtools.web_search.ainvoke({"query": "cats"})
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
        def __init__(self):
            self.responses = _Responses()

    fake_mod = SimpleNamespace(AsyncOpenAI=_Client)
    monkeypatch.setitem(sys.modules, "openai", fake_mod)

    out = await dtools.web_search.ainvoke({"query": "cats"})
    assert out == "Parsed text!"
