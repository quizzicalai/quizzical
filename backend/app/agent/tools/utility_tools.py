"""
Agent Tools: Persistence
"""
from typing import List, Optional

import structlog
from langchain_core.runnables import RunnableConfig
from langchain_core.tools import tool
from pydantic import BaseModel, Field
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.future import select

from app.agent.state import GraphState
from app.models.db import Character, SessionHistory
from app.services.llm_service import llm_service

logger = structlog.get_logger(__name__)


# --- Pydantic Models for Tool Inputs ---

class PersistStateInput(BaseModel):
    """Input schema for the database persistence tool."""
    state: GraphState = Field(description="The complete final state of the agent graph.")


# --- Tool Definitions ---

@tool
async def persist_session_to_database(
    tool_input: PersistStateInput, config: RunnableConfig, trace_id: Optional[str] = None, session_id: Optional[str] = None
) -> str:
    """
    Saves the complete quiz session to the database. This should be the final
    tool called in a successful workflow.
    """
    state = tool_input.state
    session_uuid = state.get("session_id")
    logger.info("Persisting final session history to database", session_id=str(session_uuid))

    # FIX: Extract the database session from the RunnableConfig.
    db_session: Optional[AsyncSession] = config["configurable"].get("db_session")
    if not db_session:
        return "Error: Database session not available."

    try:
        async with db_session as db:
            synopsis_obj = state.get("category_synopsis")
            if not synopsis_obj:
                raise ValueError("Cannot save session without a category synopsis.")
            synopsis_text = f"{synopsis_obj.title}: {synopsis_obj.summary}"

            embedding_response = await llm_service.get_embedding(input=[synopsis_text])
            synopsis_embedding = embedding_response[0]

            final_characters: List[Character] = []
            for char_profile in state.get("generated_characters", []):
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

            final_result_obj = state.get("final_result")
            if not final_result_obj:
                raise ValueError("Cannot save session without a final result.")

            session_record = SessionHistory(
                session_id=session_uuid,
                category=state.get("category"),
                category_synopsis=synopsis_obj.model_dump(),
                synopsis_embedding=synopsis_embedding,
                session_transcript=[m.dict() for m in state.get("messages", [])],
                final_result=final_result_obj.model_dump(),
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
