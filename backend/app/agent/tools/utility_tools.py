"""
Agent Tools: Utility & Persistence

This module contains utility tools for final actions, such as saving
the completed session to the database.
"""
import json

import structlog
from langchain_core.tools import tool

from app.agent.state import GraphState
from app.db.session import get_db_session
from app.services.database import SessionRepository

logger = structlog.get_logger(__name__)


@tool
def persist_session_to_database(state: GraphState) -> str:
    """
    Saves the complete, successful quiz session to the long-term database.
    This should be one of the final actions in a successful workflow.
    The entire final agent state is passed to this tool.
    """
    session_id = state.get("session_id")
    logger.info("Persisting final session state to the database", session_id=session_id)

    try:
        # We extract all the generated content from the final state.
        final_data = {
            "session_id": str(session_id),
            "category": state.get("category"),
            "synopsis": state.get("category_synopsis"),
            # Pydantic models in the state need to be converted to dicts
            "characters": [char.model_dump() for char in state.get("generated_characters", [])],
            "questions": [q.model_dump() for q in state.get("generated_questions", [])],
            "final_result": state.get("final_result").model_dump() if state.get("final_result") else None,
        }

        # The full state is saved as a JSON blob for later analysis and debugging.
        full_state_json = json.dumps(final_data, indent=2)

        with get_db_session() as db:
            session_repo = SessionRepository(db)
            session_repo.save_completed_session(
                session_id=session_id,
                final_data=full_state_json
            )

        logger.info("Successfully persisted session", session_id=session_id)
        return f"Session {session_id} was successfully saved to the database."

    except Exception as e:
        logger.error(
            "Failed to persist session to database",
            session_id=session_id,
            error=str(e),
        )
        return f"Error: Could not save session {session_id}. Reason: {e}"