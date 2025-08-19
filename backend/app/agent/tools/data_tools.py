# backend/app/agent/tools/data_tools.py
"""
Agent Tools: Data Retrieval (RAG)
"""
from typing import Dict, List, Optional

import structlog
from langchain_core.tools import tool
from app.models.db import SessionHistory
from app.api.dependencies import get_db_session
from app.services.llm_service import llm_service

logger = structlog.get_logger(__name__)

@tool
def search_for_contextual_sessions(
    category_synopsis: str, trace_id: Optional[str] = None, session_id: Optional[str] = None
) -> List[Dict]:
    """
    Performs a semantic vector search to find similar past quiz sessions
    and returns their data, including the associated character profiles.
    This is the core of the agent's RAG process.
    """
    logger.info("Searching for contextual sessions", synopsis_preview=category_synopsis[:80])
    try:
        # 1. Generate an embedding for the new synopsis
        embedding_response = llm_service.embedding(
            model="sentence-transformers/all-MiniLM-L6-v2", input=[category_synopsis]
        )
        query_vector = embedding_response.data[0]["embedding"]

        # 2. Perform the vector similarity search in the database
        with get_db_session() as db:
            # The l2_distance operator (<->) comes from pgvector
            similar_sessions = (
                db.query(SessionHistory)
                .order_by(SessionHistory.synopsis_embedding.l2_distance(query_vector))
                .limit(5)
                .all()
            )

            # 3. Format the results for the agent
            results = []
            for session in similar_sessions:
                results.append({
                    "sessionId": str(session.session_id),
                    "category": session.category,
                    "synopsis": session.category_synopsis,
                    "characters": [
                        {
                            "id": str(char.id),
                            "name": char.name,
                            "profile_text": char.profile_text,
                            "last_updated": str(char.last_updated_at),
                            "quality_score": char.judge_quality_score,
                            "feedback": char.judge_feedback,
                        }
                        for char in session.characters
                    ],
                })
            return results

    except Exception as e:
        logger.error("Failed to search for contextual sessions", error=str(e))
        return []