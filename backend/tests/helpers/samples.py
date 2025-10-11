def sample_character(name: str = "The Analyst", category: str | None = None) -> dict:
    data = {
        "name": name,
        "short_description": f"{name} short description",
        "profile_text": f"{name} profile text paragraph one. Paragraph two.",
    }
    if category:
        data["category"] = category
    return data

def sample_synopsis(title: str = "Quiz: Cats", summary: str = "About cats.") -> dict:
    return {"title": title, "summary": summary}
