# backend/tests/unit/models/test_database.py

import uuid
import pytest
from typing import Optional

# Prefer the services path
try:
    from app.services.database import (
        CharacterRepository,
        SessionRepository,
        SessionQuestionsRepository,
        ResultService,
        normalize_final_result,
    )
except ImportError:
    # Fallback if structure is different
    from app.models.database import (  # type: ignore
        CharacterRepository,
        SessionRepository,
        SessionQuestionsRepository,
        ResultService,
        normalize_final_result,
    )

from app.models.api import FinalResult, FeedbackRatingEnum, ShareableResultResponse

pytestmark = pytest.mark.unit


# ---------------------------------------------------------------------------
# CharacterRepository (BYPASS)
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_character_repository_bypass_getters_and_create(monkeypatch):
    # Monkeypatch the Character class used inside the module to avoid relying
    # on the real DB model's constructor requirements.
    created = {}

    class FakeCharacter:
        def __init__(self, name=None, **kwargs):
            # Minimal init matching repo.create usage
            self.id = uuid.uuid4()
            self.name = name
            self.kwargs = kwargs
            created.update({"id": self.id, "name": name, "kwargs": kwargs})

    # Patch where Character is imported in app.services.database
    monkeypatch.setattr("app.services.database.Character", FakeCharacter, raising=False)

    # Stub the session
    class StubSession:
        def add(self, obj): pass
        async def flush(self): pass
        async def refresh(self, obj): pass
        async def get(self, cls, ident): return None
        async def execute(self, stmt): 
            # Return empty result
            class Res:
                def scalars(self): 
                    class Sc:
                        def all(self): return []
                    return Sc()
                def fetchone(self): return None
            return Res()

    repo = CharacterRepository(session=StubSession()) # type: ignore

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
    # ensure kwargs flowed through
    assert stub.kwargs.get("profile_text") == "hi"
    assert stub.kwargs.get("image_url") == "http://x"


# ---------------------------------------------------------------------------
# SessionRepository (BYPASS)
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_session_repository_bypass_methods(monkeypatch):
    # Stub session execute to return empty result for save_feedback
    class StubSession:
        async def execute(self, stmt):
            class Res:
                rowcount = 0
            return Res()
        async def get(self, cls, ident): return None

    repo = SessionRepository(session=StubSession()) # type: ignore

    # save_feedback -> None (bypass because rowcount=0)
    out = await repo.save_feedback(uuid.uuid4(), FeedbackRatingEnum.UP, "great")
    assert out is None


# ---------------------------------------------------------------------------
# ResultService (BYPASS)
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_result_service_bypass_get_result_by_id():
    class StubSession:
        async def get(self, cls, ident): return None

    svc = ResultService(session=StubSession()) # type: ignore
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
    assert any("unsupported type" in r.message for r in caplog.records)