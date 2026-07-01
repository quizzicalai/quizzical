import uuid
from unittest.mock import AsyncMock, MagicMock

import pytest
from pydantic import ValidationError

# Module under test
from app.api.endpoints import feedback as feedback_mod
from app.models.api import FeedbackRatingEnum
from tests.fixtures.db_fixtures import override_db_dependency  # noqa: F401

# Fixtures
from tests.fixtures.turnstile_fixtures import turnstile_bypass  # noqa: F401


@pytest.fixture
def mock_session_repo(monkeypatch):
    """
    Mocks the SessionRepository class to prevent actual DB calls.
    Returns the mock instance that will be used by the endpoint.
    """
    mock_instance = MagicMock()
    # Async methods must be AsyncMock
    mock_instance.save_feedback = AsyncMock()

    # Patch the CLASS in the endpoint module, so instantiation returns our mock
    monkeypatch.setattr(feedback_mod, "SessionRepository", MagicMock(return_value=mock_instance))

    return mock_instance

@pytest.mark.anyio
@pytest.mark.usefixtures("turnstile_bypass", "override_db_dependency")
async def test_submit_feedback_happy_path(async_client, mock_session_repo):
    """
    Valid payload -> 204 No Content.
    """
    quiz_id = uuid.uuid4()
    payload = {
        "quiz_id": str(quiz_id),
        "rating": "up",
        "text": "Great quiz!",
        "cf-turnstile-response": "fake-token" # Should be stripped/ignored
    }

    # Setup mock: save_feedback returns a truthy object (indicating found session)
    mock_session_repo.save_feedback.return_value = {"id": quiz_id}

    response = await async_client.post("/api/v1/feedback", json=payload)

    assert response.status_code == 204

    # Verify mock called correctly
    mock_session_repo.save_feedback.assert_awaited_once()
    call_args = mock_session_repo.save_feedback.await_args[1]
    assert call_args["session_id"] == quiz_id
    assert call_args["rating"] == FeedbackRatingEnum.UP
    assert call_args["feedback_text"] == "Great quiz!"

@pytest.mark.anyio
@pytest.mark.usefixtures("turnstile_bypass", "override_db_dependency")
@pytest.mark.parametrize("bad_body", [[], "oops", 42])
async def test_submit_feedback_non_dict_body_400_not_500(
    async_client, mock_session_repo, bad_body
):
    """Hitlist #7 — a non-object JSON body (e.g. a top-level array) must yield a
    clean coded 400, NOT a 500. When Turnstile is disabled (bypass fixture) the
    verify dependency doesn't coerce the body, so a non-dict previously reached
    ``body.pop(...)`` and crashed with an AttributeError -> 500."""
    response = await async_client.post("/api/v1/feedback", json=bad_body)
    assert response.status_code == 400, response.text
    body = response.json()
    assert body["code"] == "QF-BAD-REQUEST"
    # Never reached the repository.
    mock_session_repo.save_feedback.assert_not_awaited()


@pytest.mark.anyio
@pytest.mark.usefixtures("turnstile_bypass", "override_db_dependency")
async def test_submit_feedback_session_not_found(async_client, mock_session_repo):
    """
    Repository returns None -> 404.
    """
    mock_session_repo.save_feedback.return_value = None

    payload = {"quiz_id": str(uuid.uuid4()), "rating": "down"}
    response = await async_client.post("/api/v1/feedback", json=payload)

    assert response.status_code == 404
    assert "not found" in response.json()["detail"].lower()

@pytest.mark.anyio
@pytest.mark.usefixtures("turnstile_bypass", "override_db_dependency")
async def test_submit_feedback_db_error(async_client, mock_session_repo):
    """
    Repository raises exception -> 500.
    """
    mock_session_repo.save_feedback.side_effect = Exception("DB Kaboom")

    payload = {"quiz_id": str(uuid.uuid4()), "rating": "up"}
    response = await async_client.post("/api/v1/feedback", json=payload)

    assert response.status_code == 500
    assert "could not save feedback" in response.json()["detail"].lower()

@pytest.mark.anyio
@pytest.mark.usefixtures("turnstile_bypass", "override_db_dependency")
async def test_submit_feedback_validation_error(async_client):
    """
    Invalid UUID / Missing Rating.

    The application performs manual Pydantic validation but does not catch
    ValidationError. In tests, the AsyncClient propagates this exception directly.
    We use pytest.raises to verify the app is indeed failing validation as expected.
    """
    # Missing rating
    with pytest.raises(ValidationError):
        await async_client.post("/api/v1/feedback", json={"quiz_id": str(uuid.uuid4())})

    # Invalid UUID
    with pytest.raises(ValidationError):
        await async_client.post("/api/v1/feedback", json={"quiz_id": "not-uuid", "rating": "up"})

@pytest.mark.anyio
@pytest.mark.usefixtures("turnstile_bypass", "override_db_dependency")
async def test_submit_feedback_empty_text_normalization(async_client, mock_session_repo):
    """
    Empty/Whitespace string -> converted to None.
    """
    mock_session_repo.save_feedback.return_value = {"id": "ok"}

    payload = {
        "quiz_id": str(uuid.uuid4()),
        "rating": "down",
        "text": "   " # Whitespace
    }

    await async_client.post("/api/v1/feedback", json=payload)

    # Verify text passed as None
    call_args = mock_session_repo.save_feedback.await_args[1]
    assert call_args["feedback_text"] is None
