"""
Unit Tests for Services and Agent Tools

This module contains unit tests for the core business logic within the /services
and /agent/tools directories.

These tests focus on individual functions in isolation, using mocks to replace
external dependencies like database sessions or LLM API calls. This ensures
that the tests are fast, deterministic, and precisely target the logic
being tested.
"""

import uuid
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from pydantic import BaseModel, ValidationError
from sqlalchemy.exc import IntegrityError

from app.agent.tools.content_creation_tools import generate_category_synopsis
from app.agent.tools.data_tools import search_for_contextual_sessions
from app.models.db import Character
from app.services.database import CharacterRepository, SessionRepository
from app.services.llm_service import LLMService, StructuredOutputError, RETRYABLE_EXCEPTIONS

# Mark all tests in this module as asynchronous
pytestmark = pytest.mark.asyncio


class TestLLMService:
    """Unit tests for the LLMService."""

    @pytest.fixture
    def mock_litellm(self, mocker) -> AsyncMock:
        """Provides a mock for the litellm.acompletion function."""
        return mocker.patch("litellm.acompletion", new_callable=AsyncMock)

    async def test_get_structured_response_success(self, mock_litellm):
        """
        Tests the success path for get_structured_response, ensuring it returns
        a validated Pydantic object.
        """
        class TestModel(BaseModel):
            name: str
            value: int

        # litellm returns the parsed model directly when using response_model
        mock_litellm.return_value = TestModel(name="test", value=123)
        
        llm_service = LLMService()
        result = await llm_service.get_structured_response(
            tool_name="planner",
            messages=[{"role": "user", "content": "test"}],
            response_model=TestModel,
        )
        assert isinstance(result, TestModel)
        assert result.name == "test"

    async def test_llm_service_retries_on_transient_errors(self, mock_litellm):
        """
        Verifies that the tenacity @retry decorator correctly retries on
        specified transient exceptions.
        """
        # Simulate a transient error on the first call, then a success.
        mock_litellm.side_effect = [
            RETRYABLE_EXCEPTIONS[0]("API is temporarily unavailable"),
            MagicMock(choices=[MagicMock(message=MagicMock(content="Success"))]),
        ]
        
        llm_service = LLMService()
        result = await llm_service.get_text_response(tool_name="default", messages=[])
        
        assert result == "Success"
        assert mock_litellm.call_count == 2 # Assert that it was called twice (1 failure + 1 success)

    async def test_get_structured_response_raises_custom_error(self, mock_litellm):
        """
        Ensures that a Pydantic ValidationError from litellm is wrapped
        in our custom StructuredOutputError.
        """
        mock_litellm.side_effect = ValidationError.from_exception_data("Test", [])
        llm_service = LLMService()
        
        with pytest.raises(StructuredOutputError):
            await llm_service.get_structured_response(
                tool_name="planner", messages=[], response_model=BaseModel
            )


class TestDatabaseRepositories:
    """Unit tests for the database repositories."""

    @pytest.fixture
    def mock_session(self) -> AsyncMock:
        """Provides a mock SQLAlchemy AsyncSession."""
        session = AsyncMock()
        session.__aenter__.return_value = session
        session.__aexit__.return_value = None
        return session

    async def test_session_repo_find_relevant_sessions(self, mock_session):
        """
        Tests that the RAG search method calls session.execute with the
        correct SQL and parameters.
        """
        repo = SessionRepository(mock_session)
        query_text = "test query"
        query_vector = [0.1] * 384
        
        await repo.find_relevant_sessions_for_rag(query_text, query_vector)
        
        mock_session.execute.assert_called_once()
        call_args = mock_session.execute.call_args[0]
        # Check that the raw SQL text object was passed
        assert "WITH semantic_search AS" in str(call_args[0].text)
        # Check that the parameters were passed correctly
        assert call_args[1]["query_text"] == query_text
        assert call_args[1]["query_vector"] == str(query_vector)


class TestAgentTools:
    """Unit tests for individual agent tools."""

    @patch("app.agent.tools.content_creation_tools.llm_service", spec=LLMService)
    async def test_generate_category_synopsis_tool(self, mock_llm_service: MagicMock):
        """
        Tests the `generate_category_synopsis` tool, ensuring it calls the
        correct LLM service method.
        """
        mock_llm_service.get_text_response.return_value = "A detailed synopsis."
        
        from app.agent.tools.content_creation_tools import SynopsisInput
        tool_input = SynopsisInput(category="Classic Video Games")
        result = await generate_category_synopsis(tool_input)

        assert result == "A detailed synopsis."
        mock_llm_service.get_text_response.assert_called_once()
        call_args = mock_llm_service.get_text_response.call_args.kwargs
        assert call_args["tool_name"] == "synopsis_writer"

    @patch("app.agent.tools.data_tools.SessionRepository")
    async def test_search_for_contextual_sessions_tool(self, MockSessionRepository: MagicMock):
        """
        Tests the `search_for_contextual_sessions` tool, ensuring it correctly
        instantiates and calls the SessionRepository.
        """
        # Mock the instance of the repository that the tool will create
        mock_repo_instance = MockSessionRepository.return_value
        mock_repo_instance.find_relevant_sessions_for_rag = AsyncMock(return_value=[{"session_id": "123"}])

        from app.agent.tools.data_tools import SearchContextInput
        tool_input = SearchContextInput(category_synopsis="A synopsis about space.")
        mock_db_session = AsyncMock() # The tool needs a session to be passed in
        
        result = await search_for_contextual_sessions(tool_input, db_session=mock_db_session)

        assert result == [{"session_id": "123"}]
        # Verify that the repository was initialized with the session
        MockSessionRepository.assert_called_once_with(mock_db_session)
        # Verify the correct method was called on the instance
        mock_repo_instance.find_relevant_sessions_for_rag.assert_called_once()
