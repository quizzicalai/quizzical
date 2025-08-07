"""
Agent Tools: Data & Persistence

This module contains tools for the agent to interact with the database.
It includes tools for creating, reading, and updating quiz sessions, characters,
and user feedback.

All tools are designed with simple, LLM-friendly arguments. Complex objects
like SQLAlchemy models are constructed *inside* the tool, not passed as arguments.
"""
import json
import uuid
from typing import List, Optional

import structlog
from langchain_core.tools import tool
from pydantic import BaseModel, Field

from app.db.models import Feedback, Quiz, QuizCharacter
from app.db.session import get_db_session

logger = structlog.get_logger(__name__)


# --- Database Writing Tools ---


@tool
def store_quiz(
    topic: str, synopsis: str, characters: List[dict], questions: List[dict]
) -> str:
    """
    Saves the complete quiz data, including the topic, synopsis, characters,
    and questions, to the database. This should be one of the final steps.
    """
    logger.info("Attempting to store full quiz data", topic=topic)
    try:
        with get_db_session() as db:
            # Create Quiz record
            quiz_record = Quiz(
                topic=topic,
                synopsis=synopsis,
                questions=json.dumps(questions),  # Serialize list of dicts
            )
            db.add(quiz_record)
            db.flush()  # Flush to get the quiz_record.id

            # Create Character records and associate them
            for char_data in characters:
                character_record = QuizCharacter(
                    quiz_id=quiz_record.id,
                    name=char_data.get("name"),
                    short_description=char_data.get("short_description"),
                    profile_text=char_data.get("profile_text"),
                )
                db.add(character_record)

            db.commit()
            logger.info("Successfully stored quiz", quiz_id=quiz_record.id)
            return f"Successfully stored quiz with ID {quiz_record.id} and its associated characters."
    except Exception as e:
        logger.error("Failed to store quiz data", error=str(e))
        return f"Error: Failed to store quiz data in the database. Reason: {e}"


@tool
def store_user_feedback(
    session_id: str,
    rating: bool,
    written_feedback: Optional[str] = None,
) -> str:
    """
    Saves the user's feedback (thumbs up/down and optional text)
    for a specific quiz session.
    """
    logger.info(
        "Storing user feedback",
        session_id=session_id,
        rating=rating,
    )
    try:
        with get_db_session() as db:
            feedback_record = Feedback(
                session_id=uuid.UUID(session_id),
                rating=rating,
                written_feedback=written_feedback,
            )
            db.add(feedback_record)
            db.commit()
            return "Feedback successfully recorded. Thank you!"
    except Exception as e:
        logger.error("Failed to store feedback", error=str(e))
        return f"Error: Could not save user feedback. Reason: {e}"


# --- Database Reading Tools ---


@tool
def get_similar_characters(topic: str) -> List[dict]:
    """
    Searches the database for existing characters from past quizzes on a
    similar topic to potentially reuse.
    """
    logger.info("Searching for similar characters", topic=topic)
    try:
        with get_db_session() as db:
            # This is a simplified search. A real implementation would use
            # vector search on embeddings of the character descriptions.
            similar_quizzes = (
                db.query(Quiz).filter(Quiz.topic.ilike(f"%{topic}%")).limit(5).all()
            )
            if not similar_quizzes:
                return []

            character_list = []
            for quiz in similar_quizzes:
                for char in quiz.characters:
                    character_list.append(
                        {
                            "id": str(char.id),
                            "name": char.name,
                            "short_description": char.short_description,
                            "profile_text": char.profile_text,
                        }
                    )
            return character_list
    except Exception as e:
        logger.error("Failed to search for characters", error=str(e))
        return []