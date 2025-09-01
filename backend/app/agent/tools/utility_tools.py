"""
Agent Tools: Persistence
"""
from __future__ import annotations

from typing import Any, List, Optional

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
    tool_input: PersistStateInput,
    config: RunnableConfig,
    trace_id: Optional[str] = None,
    session_id: Optional[str] = None,
) -> str:
    """
    Saves the complete quiz session to the database. Non-blocking:
    - If embeddings fail, saves with NULL embedding.
    - If some fields are missing, saves best-effort and logs warnings.
    """
    state = tool_input.state
    session_uuid = state.get("session_id")
    logger.info("Persisting final session history to database", session_id=str(session_uuid))

    db_session: Optional[AsyncSession] = config.get("configurable", {}).get("db_session")  # type: ignore
    if not db_session:
        return "Error: Database session not available."

    # Build synopsis text (best-effort)
    synopsis_text = None
    synopsis_obj = state.get("category_synopsis")
    try:
        if synopsis_obj:
            # supports both pydantic object (has .title/.summary) or dict
            title = getattr(synopsis_obj, "title", None) or (synopsis_obj.get("title") if isinstance(synopsis_obj, dict) else None)
            summary = getattr(synopsis_obj, "summary", None) or (synopsis_obj.get("summary") if isinstance(synopsis_obj, dict) else None)
            if title or summary:
                synopsis_text = f"{title or ''}: {summary or ''}".strip(": ").strip()
    except Exception:
        synopsis_text = None

    # Generate embedding (tolerant)
    synopsis_embedding: Optional[List[float]] = None
    if synopsis_text:
        try:
            embedding_response = await llm_service.get_embedding(input=[synopsis_text])
            if isinstance(embedding_response, list) and embedding_response and isinstance(embedding_response[0], list):
                synopsis_embedding = embedding_response[0]
        except Exception as e:
            logger.warn("Embedding generation failed; saving without embedding", error=str(e))

    # Prepare character rows (dedupe by name; tolerant)
    final_characters: List[Character] = []
    try:
        generated_chars = state.get("generated_characters", []) or []
        async with db_session as db:
            for char_profile in generated_chars:
                # char_profile may be a pydantic model or dict
                name = getattr(char_profile, "name", None) or (char_profile.get("name") if isinstance(char_profile, dict) else None)
                short_desc = getattr(char_profile, "short_description", None) or (
                    char_profile.get("short_description") if isinstance(char_profile, dict) else None
                )
                profile_text = getattr(char_profile, "profile_text", None) or (
                    char_profile.get("profile_text") if isinstance(char_profile, dict) else None
                )
                if not name:
                    continue

                result = await db.execute(select(Character).filter_by(name=name))
                db_char = result.scalars().first()
                if db_char:
                    final_characters.append(db_char)
                else:
                    new_char = Character(
                        name=name,
                        short_description=short_desc,
                        profile_text=profile_text,
                    )
                    db.add(new_char)
                    final_characters.append(new_char)
            await db.flush()
    except Exception as e:
        logger.warn("Character upsert encountered issues; proceeding", error=str(e))

    # Build transcript safely
    def _to_dict(msg: Any) -> Any:
        # try common methods, otherwise return as-is
        if hasattr(msg, "model_dump"):
            return msg.model_dump()
        if hasattr(msg, "dict"):
            try:
                return msg.dict()
            except Exception:
                pass
        if isinstance(msg, dict):
            return msg
        return {"value": str(msg)}

    messages = state.get("messages", []) or []
    transcript = []
    try:
        transcript = [_to_dict(m) for m in messages]
    except Exception:
        transcript = []

    # Final result may be missing; don't fail
    final_result_obj = state.get("final_result")
    if hasattr(final_result_obj, "model_dump"):
        try:
            final_result = final_result_obj.model_dump()
        except Exception:
            final_result = None
    elif isinstance(final_result_obj, dict):
        final_result = final_result_obj
    else:
        final_result = None
        logger.warn("Final result missing or unparsable; saving without it", session_id=str(session_uuid))

    # Save session history (tolerant commit)
    try:
        async with db_session as db:
            record = SessionHistory(
                session_id=session_uuid,
                category=state.get("category"),
                category_synopsis=(synopsis_obj.model_dump() if hasattr(synopsis_obj, "model_dump") else synopsis_obj),
                synopsis_embedding=synopsis_embedding,  # can be None
                session_transcript=transcript,
                final_result=final_result,             # can be None
                characters=final_characters,           # may be []
                # judge_plan_feedback / user_feedback_text (if your model has them) can be set later
            )
            db.add(record)
            await db.commit()
        logger.info("Persisted session history", session_id=str(session_uuid))
        return f"Session {session_uuid} was successfully saved."
    except Exception as e:
        logger.error(
            "Failed to persist session history; non-blocking",
            session_id=str(session_uuid),
            error=str(e),
            exc_info=True,
        )
        return f"Error: Could not save session. Reason: {e}"
