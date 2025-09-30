# backend/tests/helpers/samples.py

def sample_character(name="The Optimist"):
    return {"name": name, "short_description": "", "profile_text": "", "image_url": None}

def sample_synopsis(title="Quiz: Cats", summary="syn"):
    return {"title": title, "summary": summary}
