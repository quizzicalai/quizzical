from app.agent.schemas import QuestionList, QuestionOut, QuestionOption

def make_question_list_with_dupes() -> QuestionList:
    # Q1 has duplicate B/b with an image on the lowercase one
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
