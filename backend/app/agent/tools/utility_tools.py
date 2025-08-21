# backend/app/agent/tools/utility_tools.py
"""
Agent Tools: Persistence
"""
import json
from typing import List, Optional

import structlog
from langchain_core.tools import tool
from sqlalchemy.future import select

from app.agent.state import GraphState
from app.api.dependencies import async_session_factory
from app.models.db import Character, SessionHistory
from app.services.llm_service import llm_service

logger = structlog.get_logger(__name__)


@tool
async def persist_session_to_database(
    state: GraphState, trace_id: Optional[str] = None, session_id: Optional[str] = None
) -> str:
    """
    Saves the complete quiz session to the database. This should be the final
    tool called in a successful workflow.
    """
    session_uuid = state.get("session_id")
    logger.info("Persisting final session history to database", session_id=str(session_uuid))

    try:
        # FIX: Use the async session factory correctly.
        async with async_session_factory() as db:
            synopsis_obj = state.get("category_synopsis")
            if not synopsis_obj:
                raise ValueError("Cannot save session without a category synopsis.")
            synopsis_text = f"{synopsis_obj.title}: {synopsis_obj.summary}"

            embedding_response = await llm_service.get_embedding(input=[synopsis_text])
            synopsis_embedding = embedding_response[0]

            final_characters: List[Character] = []
            for char_profile in state.get("generated_characters", []):
                # Using select() for modern async SQLAlchemy
                result = await db.execute(select(Character).filter_by(name=char_profile.name))
                db_char = result.scalars().first()
                if db_char:
                    final_characters.append(db_char)
                else:
                    new_char = Character(
                        name=char_profile.name,
                        short_description=char_profile.short_description,
                        profile_text=char_profile.profile_text,
                    )
                    db.add(new_char)
                    final_characters.append(new_char)
            await db.flush()

            session_record = SessionHistory(
                session_id=session_uuid,
                category=state.get("category"),
                category_synopsis=synopsis_obj.model_dump(),
                synopsis_embedding=synopsis_embedding,
                session_transcript=[m.dict() for m in state.get("messages", [])],
                final_result=state.get("final_result").model_dump(),
                characters=final_characters,
            )

            db.add(session_record)
            await db.commit()

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
