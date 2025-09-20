# backend/app/agent/tools/utility_tools.py
"""
Agent Tools: Persistence

- persist_session_to_database: saves a completed session to DB
  * Tolerant: saves best-effort if some fields are missing
  * Embeddings are optional; failure does not block persistence
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


# -------------------------
# Pydantic Inputs
# -------------------------

class PersistStateInput(BaseModel):
    """Input schema for database persistence tool."""
    state: GraphState = Field(description="The complete final state of the agent graph.")

# -------------------------
# Helpers
# -------------------------

def _to_dict(msg: Any) -> Any:
    """Best-effort message serialization for transcripts."""
    if hasattr(msg, "model_dump"):
        try:
            return msg.model_dump()
        except Exception:
            pass
    if hasattr(msg, "dict"):
        try:
            return msg.dict()
        except Exception:
            pass
    if isinstance(msg, dict):
        return msg
    return {"value": str(msg)}

# -------------------------
# Tool
# -------------------------

@tool
async def persist_session_to_database(
    tool_input: PersistStateInput,
    config: RunnableConfig,
    trace_id: Optional[str] = None,
    session_id: Optional[str] = None,
) -> str:
    """
    Saves the complete quiz session to the database.

    Non-blocking guarantees:
    - If embeddings fail, we save without an embedding.
    - If some fields are missing, we save best-effort and log a warning.
    """
    state = tool_input.state
    session_uuid = state.get("session_id")
    logger.info("tool.persist_session_to_database.start", session_id=str(session_uuid))

    db_session: Optional[AsyncSession] = config.get("configurable", {}).get("db_session")  # type: ignore
    if not db_session:
        logger.error("tool.persist_session_to_database.nodb")
        return "Error: Database session not available."

    # Build synopsis text (best-effort)
    synopsis_obj = state.get("category_synopsis")
    synopsis_text = None
    try:
        if synopsis_obj:
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
            embs = await llm_service.get_embedding(input=[synopsis_text])
            if embs and isinstance(embs[0], list):
                synopsis_embedding = embs[0]
        except Exception as e:
            logger.warning("tool.persist_session_to_database.embed_fail", error=str(e))

    # Upsert characters (tolerant)
    final_characters: List[Character] = []
    try:
        generated_chars = state.get("generated_characters", []) or []
        async with db_session as db:
            for char_profile in generated_chars:
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
                    new_char = Character(name=name, short_description=short_desc, profile_text=profile_text)
                    db.add(new_char)
                    final_characters.append(new_char)
            await db.flush()
    except Exception as e:
        logger.warning("tool.persist_session_to_database.char_upsert_warn", error=str(e))

    # Transcript (best-effort)
    messages = state.get("messages", []) or []
    try:
        transcript = [_to_dict(m) for m in messages]
    except Exception:
        transcript = []

    # Final result may be missing; treat as optional
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
        logger.warning("tool.persist_session_to_database.no_final_result", session_id=str(session_uuid))

    # Save session
    try:
        async with db_session as db:
            record = SessionHistory(
                session_id=session_uuid,
                category=state.get("category"),
                category_synopsis=(synopsis_obj.model_dump() if hasattr(synopsis_obj, "model_dump") else synopsis_obj),
                synopsis_embedding=synopsis_embedding,  # may be None
                session_transcript=transcript,
                final_result=final_result,             # may be None
                characters=final_characters,           # list[Character]
            )
            db.add(record)
            await db.commit()
        logger.info("tool.persist_session_to_database.ok", session_id=str(session_uuid))
        return f"Session {session_uuid} was successfully saved."
    except Exception as e:
        logger.error("tool.persist_session_to_database.fail", session_id=str(session_uuid), error=str(e), exc_info=True)
        return f"Error: Could not save session. Reason: {e}"
