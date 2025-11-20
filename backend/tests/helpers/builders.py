# tests/helpers/builders.py
from typing import List, Dict, Any
from app.agent.schemas import (
    QuestionList, 
    QuestionOut, 
    QuestionOption
)

def make_question_list_with_dupes() -> QuestionList:
    """
    Returns a QuestionList where:
    - Q1 has duplicate options ("B" vs "b") where one has an image.
    - Q2 and Q3 are standard.
    """
    q1 = QuestionOut(
        question_text="Pick one",
        options=[
            QuestionOption(text="A"),
            QuestionOption(text="B"),
            QuestionOption(text="b", image_url="http://x/img.png"),
            QuestionOption(text="C"),
        ],
    )
    q2 = QuestionOut(
        question_text="Second",
        options=[QuestionOption(text="Yes"), QuestionOption(text="No")],
    )
    q3 = QuestionOut(
        question_text="Third",
        options=[QuestionOption(text="Left"), QuestionOption(text="Right")],
    )
    return QuestionList(questions=[q1, q2, q3])

def make_sample_character(name: str = "The Analyst", category: str = "General") -> Dict[str, Any]:
    """Returns a raw dict representing a character, useful for tool inputs."""
    return {
        "name": name,
        "short_description": f"{name} short description",
        "profile_text": f"{name} profile text paragraph one. Paragraph two.",
        "image_url": None
    }

def make_sample_synopsis(title: str = "Quiz: Cats", summary: str = "About cats.") -> Dict[str, Any]:
    """Returns a raw dict representing a synopsis."""
    return {"title": title, "summary": summary}