# backend/tests/helpers/builders.py

from app.agent.schemas import QuestionOption, QuestionOut, QuestionList

def make_question_list_with_dupes():
    q1 = QuestionOut(
        question_text="Pick one",
        options=[
            QuestionOption(text="A"),
            QuestionOption(text="A"),
            QuestionOption(text="B", image_url="http://x/img.png"),
            QuestionOption(text="C"),
        ],
    )
    q2 = QuestionOut(
        question_text="Choose your vibe",
        options=[QuestionOption(text="Cozy"), QuestionOption(text="Cozy"), QuestionOption(text="Noir")],
    )
    q3 = QuestionOut(question_text="Third?", options=[QuestionOption(text="Yes"), QuestionOption(text="No")])
    return QuestionList(questions=[q1, q2, q3])
