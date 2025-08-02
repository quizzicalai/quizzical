"""
Integration Tests for API Endpoints

This module contains tests that verify the behavior of the FastAPI endpoints.
It uses the `test_client` fixture from `conftest.py` to make live requests
to the application and asserts the responses.

External dependencies, such as the agent graph and repositories, are mocked to
ensure tests are fast, deterministic, and focused on the API layer's logic.
"""

import asyncio
import uuid
from unittest.mock import AsyncMock, MagicMock

import pytest
from httpx import AsyncClient

# Mark all tests in this module as asynchronous
pytestmark = pytest.mark.asyncio


class TestHealthCheck:
    """Tests for the health check endpoint."""

    async def test_health_check(self, test_client: AsyncClient):
        """Ensures the /health endpoint is operational."""
        response = await test_client.get("/health")
        assert response.status_code == 200
        assert response.json() == {"status": "ok"}


class TestQuizEndpoints:
    """Comprehensive tests for all /api/quiz/* endpoints."""

    @pytest.fixture
    def mock_cache_repo(self, mocker):
        """A fixture to provide a mocked CacheRepository instance."""
        mock = MagicMock()
        # Configure the async methods to be awaitable
        mock.get_quiz_state = AsyncMock()
        mock.save_quiz_state = AsyncMock()
        mocker.patch("app.api.endpoints.quiz.CacheRepository", return_value=mock)
        return mock

    @pytest.fixture
    def mock_agent_graph(self, mocker):
        """A fixture to provide a mocked agent_graph."""
        mock = MagicMock()
        mock.ainvoke = AsyncMock()
        mocker.patch("app.api.endpoints.quiz.agent_graph", new=mock)
        return mock

    async def test_start_quiz_success(self, test_client: AsyncClient, mock_agent_graph, mock_cache_repo):
        """Tests the successful creation of a new quiz session."""
        mock_agent_graph.ainvoke.return_value = {
            "generated_questions": [{"question_text": "Test Question?", "options": []}]
        }

        response = await test_client.post(
            "/api/quiz/start",
            json={"category": "Famous Dogs", "captchaToken": "test-token"},
        )

        assert response.status_code == 201
        data = response.json()
        assert "quizId" in data
        assert data["question"]["questionText"] == "Test Question?"
        mock_cache_repo.save_quiz_state.assert_called_once()

    async def test_next_question_success(self, test_client: AsyncClient, mock_cache_repo, mocker):
        """Tests that POST /api/quiz/next successfully queues a background task."""
        session_id = uuid.uuid4()
        mock_cache_repo.get_quiz_state.return_value = {"messages": [], "session_id": session_id}
        mock_add_task = mocker.patch("fastapi.BackgroundTasks.add_task")

        response = await test_client.post(
            "/api/quiz/next",
            json={"quizId": str(session_id), "answer": "Blue"},
        )

        assert response.status_code == 202
        mock_add_task.assert_called_once() # Verify the background task was queued

    async def test_get_status_new_question(self, test_client: AsyncClient, mock_cache_repo):
        """Tests that GET /api/quiz/status returns a new question when one is available."""
        session_id = uuid.uuid4()
        mock_state = {
            "generated_questions": [
                {"question_text": "Old Question", "options": []},
                {"question_text": "New Question", "options": []},
            ]
        }
        mock_cache_repo.get_quiz_state.return_value = mock_state

        response = await test_client.get(f"/api/quiz/status/{session_id}?known_questions_count=1")
        
        assert response.status_code == 200
        data = response.json()
        assert data["status"] == "active"
        assert data["data"]["questionText"] == "New Question"


class TestFeedbackEndpoints:
    """Tests for the /api/feedback endpoint."""

    @pytest.fixture
    def mock_session_repo(self, mocker):
        """A fixture to provide a mocked SessionRepository instance."""
        mock = MagicMock()
        mock.save_feedback = AsyncMock()
        mocker.patch("app.api.endpoints.feedback.SessionRepository", return_value=mock)
        return mock

    async def test_submit_feedback_success(self, test_client: AsyncClient, mock_session_repo):
        """Tests that a valid feedback submission returns a 204 No Content."""
        mock_session_repo.save_feedback.return_value = True # Simulate a successful save

        response = await test_client.post(
            "/api/feedback",
            json={"quizId": str(uuid.uuid4()), "rating": "up", "text": "Great quiz!"},
        )
        assert response.status_code == 204

    async def test_submit_feedback_not_found(self, test_client: AsyncClient, mock_session_repo):
        """Tests that feedback for a non-existent session returns a 404 Not Found."""
        mock_session_repo.save_feedback.return_value = None # Simulate session not found

        response = await test_client.post(
            "/api/feedback",
            json={"quizId": str(uuid.uuid4()), "rating": "down"},
        )
        assert response.status_code == 404


class TestAssetEndpoints:
    """Tests for the /api/character/{character_id}/image endpoint."""

    @pytest.fixture
    def mock_char_repo(self, mocker):
        """A fixture to provide a mocked CharacterRepository instance."""
        mock = MagicMock()
        mock.get_by_id = AsyncMock()
        mocker.patch("app.api.endpoints.assets.CharacterRepository", return_value=mock)
        return mock

    async def test_get_image_success(self, test_client: AsyncClient, mock_char_repo):
        """Tests that a request for an existing image returns the image data and cache headers."""
        mock_image_bytes = b"fake-image-data"
        mock_character = MagicMock(profile_picture=mock_image_bytes)
        mock_char_repo.get_by_id.return_value = mock_character

        response = await test_client.get(f"/api/character/{uuid.uuid4()}/image")

        assert response.status_code == 200
        assert response.content == mock_image_bytes
        assert response.headers["content-type"] == "image/png"
        assert "etag" in response.headers
        assert "cache-control" in response.headers

    async def test_get_image_not_found(self, test_client: AsyncClient, mock_char_repo):
        """Tests that a request for a non-existent image returns a 404 Not Found."""
        mock_char_repo.get_by_id.return_value = None # Simulate character not found

        response = await test_client.get(f"/api/character/{uuid.uuid4()}/image")
        assert response.status_code == 404
