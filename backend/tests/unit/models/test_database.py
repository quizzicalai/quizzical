# backend/tests/unit/models/test_database.py

import uuid
import pytest

# Prefer the services path (as in the provided code). If your project places it
# under app.models.database, this try/except keeps the tests flexible.
try:
    from app.services.database import (
        CharacterRepository,
        SessionRepository,
        ResultService,
        normalize_final_result,
        HYBRID_SEARCH_FOR_SESSIONS_SQL,
    )
except ImportError:  # pragma: no cover
    from app.models.database import (  # type: ignore
        CharacterRepository,
        SessionRepository,
        ResultService,
        normalize_final_result,
        HYBRID_SEARCH_FOR_SESSIONS_SQL,
    )

from app.models.api import FinalResult, FeedbackRatingEnum
from sqlalchemy.sql.elements import TextClause


# ---------------------------------------------------------------------------
# CharacterRepository (BYPASS)
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_character_repository_bypass_getters_and_create(monkeypatch):
    # Monkeypatch the Character class used inside the module to avoid relying
    # on the real DB model's constructor requirements.
    created = {}

    class FakeCharacter:
        def __init__(self, id, name, **kwargs):
            self.id = id
            self.name = name
            self.kwargs = kwargs
            created.update({"id": id, "name": name, "kwargs": kwargs})

    monkeypatch.setattr("app.services.database.Character", FakeCharacter, raising=True)

    repo = CharacterRepository(session=None)

    # get_by_id -> None (bypass)
    obj = await repo.get_by_id(uuid.uuid4())
    assert obj is None

    # get_many_by_ids -> [] (bypass)
    many = await repo.get_many_by_ids([uuid.uuid4(), uuid.uuid4()])
    assert many == []

    # create -> returns a stubbed Character (no persistence)
    stub = await repo.create("Alice", profile_text="hi", image_url="http://x")
    assert isinstance(stub, FakeCharacter)
    assert stub.name == "Alice"
    assert isinstance(stub.id, uuid.UUID)
    # ensure kwargs flowed through
    assert stub.kwargs.get("profile_text") == "hi"
    assert stub.kwargs.get("image_url") == "http://x"


# ---------------------------------------------------------------------------
# SessionRepository (BYPASS)
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_session_repository_bypass_methods():
    repo = SessionRepository(session=None)

    # find_relevant_sessions_for_rag -> [] (bypass)
    rows = await repo.find_relevant_sessions_for_rag("cats", [0.1, 0.2, 0.3], k=3)
    assert rows == []

    # save_feedback -> None (bypass)
    out = await repo.save_feedback(uuid.uuid4(), FeedbackRatingEnum.UP, "great")
    assert out is None

    # create_from_agent_state -> None (bypass)
    state = {"quiz_id": str(uuid.uuid4()), "final_result": {"title": "X"}}
    created = await repo.create_from_agent_state(state)
    assert created is None


# ---------------------------------------------------------------------------
# ResultService (BYPASS)
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_result_service_bypass_get_result_by_id():
    svc = ResultService(session=None)
    got = await svc.get_result_by_id(uuid.uuid4())
    assert got is None


# ---------------------------------------------------------------------------
# normalize_final_result helper
# ---------------------------------------------------------------------------

def test_normalize_final_result_none_and_string():
    assert normalize_final_result(None) is None

    d = normalize_final_result("You are brave.")
    assert d == {
        "title": "Quiz Result",
        "description": "You are brave.",
        "image_url": "",
    }


def test_normalize_final_result_dict_shapes():
    # Canonical keys
    d1 = normalize_final_result(
        {"title": "A", "description": "B", "image_url": "C"}
    )
    assert d1["title"] == "A"
    assert d1["description"] == "B"
    assert d1["image_url"] == "C"

    # Alt keys mapped to canonical
    d2 = normalize_final_result(
        {"profileTitle": "PT", "summary": "S", "imageUrl": "U"}
    )
    assert d2["title"] == "PT"
    assert d2["description"] == "S"
    assert d2["image_url"] == "U"


def test_normalize_final_result_pydantic_object():
    fr = FinalResult(title="T", description="D", image_url="U")
    d = normalize_final_result(fr)
    assert d["title"] == "T"
    assert d["description"] == "D"
    assert d["image_url"] == "U"


def test_normalize_final_result_unknown_type_logs_and_returns_none(caplog):
    class Weird: ...
    with caplog.at_level("WARNING"):
        out = normalize_final_result(Weird())
    assert out is None
    # Optional: sanity check that a warning was indeed emitted
    assert any("Could not normalize final_result" in r.message for r in caplog.records)


# ---------------------------------------------------------------------------
# SQL text object presence (kept for future re-enable)
# ---------------------------------------------------------------------------

def test_hybrid_search_sql_constant_is_text_clause():
    assert isinstance(HYBRID_SEARCH_FOR_SESSIONS_SQL, TextClause)
