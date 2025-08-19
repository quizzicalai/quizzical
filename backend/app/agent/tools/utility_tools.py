# backend/app/agent/tools/utility_tools.py
"""
Agent Tools: Persistence

This module contains the definitive tool for persisting a completed quiz
session to the long-term database, ensuring all data is correctly mapped
from the agent's final state to the ORM models.
"""
import json
from typing import List, Optional

import structlog
from langchain_core.tools import tool

from app.agent.state import GraphState
from app.models.db import Character, SessionHistory
from app.api.dependencies import get_db_session
from app.services.llm_service import llm_service  # For embeddings

logger = structlog.get_logger(__name__)


@tool
def persist_session_history(
    state: GraphState, trace_id: Optional[str] = None, session_id: Optional[str] = None
) -> str:
    """
    Saves the complete, successful quiz session to the long-term database.
    This tool correctly maps the final agent state to the SessionHistory and
    Character ORM models, creates embeddings, and links relationships.
    This should be the final tool called in a successful quiz workflow.
    """
    session_uuid = state.get("session_id")
    logger.info("Persisting final session history to database", session_id=str(session_uuid))

    try:
        with get_db_session() as db:
            # 1. Generate Embedding for the Synopsis
            synopsis = state.get("category_synopsis")
            if not synopsis:
                raise ValueError("Cannot save session without a category synopsis.")

            # Use the LLM service to create the vector embedding
            embedding_response = llm_service.embedding(
                model="sentence-transformers/all-MiniLM-L6-v2", input=[synopsis]
            )
            synopsis_embedding = embedding_response.data[0]["embedding"]

            # 2. Find or Create Canonical Character Records
            # This ensures characters are unique in the DB and can be reused.
            final_characters: List[Character] = []
            for char_profile in state.get("generated_characters", []):
                # Check if a character with this name already exists
                db_char = db.query(Character).filter_by(name=char_profile.name).first()
                if db_char:
                    # Update existing character if needed (optional)
                    final_characters.append(db_char)
                else:
                    # Create a new canonical character
                    new_char = Character(
                        name=char_profile.name,
                        short_description=char_profile.short_description,
                        profile_text=char_profile.profile_text,
                        # Image persistence would be handled by another tool
                    )
                    db.add(new_char)
                    final_characters.append(new_char)
            db.flush() # Ensure new characters get IDs

            # 3. Create the SessionHistory Record
            session_record = SessionHistory(
                session_id=session_uuid,
                category=state.get("category"),
                category_synopsis=synopsis,
                synopsis_embedding=synopsis_embedding,
                # Serialize complex objects into JSON for storage
                agent_plan=json.dumps({"plan": "Initial plan data would go here"}), # Placeholder
                session_transcript=json.dumps([m.dict() for m in state.get("messages", [])]),
                final_result=json.dumps(state.get("final_result").model_dump()),
                # Link the characters used in this session
                characters=final_characters,
            )

            db.add(session_record)
            db.commit()

        logger.info("Successfully persisted session history", session_id=str(session_uuid))
        return f"Session {session_uuid} was successfully saved."

    except Exception as e:
        logger.error(
            "Failed to persist session history",
            session_id=str(session_uuid),
            error=str(e),
            exc_info=True
        )
        return f"Error: Could not save session. Reason: {e}"