import json
import uuid
import pytest

from app.main import API_PREFIX


def _assert_uuid4(value: str) -> str:
    u = uuid.UUID(str(value))
    assert u.version == 4, f"expected v4 UUID, got v{u.version}"
    return str(u)


def _redis_key_for(quiz_id: str) -> str:
    return f"quiz_session:{quiz_id}"


def _load_state(fake_cache_store, quiz_id: str) -> dict:
    key = _redis_key_for(quiz_id)
    blob = fake_cache_store.get(key)
    assert blob, f"expected state in cache under {key}"
    if isinstance(blob, bytes):
        blob = blob.decode("utf-8")
    return json.loads(blob)


def _save_state(fake_cache_store, quiz_id: str, state: dict) -> None:
    key = _redis_key_for(quiz_id)
    fake_cache_store[key] = json.dumps(state)


def _make_baseline_questions() -> list[dict]:
    return [
        {
            "question_text": "Which morning routine sounds most like you?",
            "options": [
                {"text": "Coffee before talk"},
                {"text": "Jog + podcast"},
                {"text": "Make a to-do list"},
                {"text": "Sleep in, then cram"},
            ],
        },
        {
            "question_text": "Pick a Friday night plan.",
            "options": [
                {"text": "Movie marathon"},
                {"text": "Town diner hangout"},
                {"text": "Study group"},
                {"text": "Local event"},
            ],
        },
    ]


@pytest.mark.smoke
@pytest.mark.anyio
@pytest.mark.usefixtures("use_fake_agent_graph")  # opt-in to the fake graph for this test
async def test_happy_path_start_to_finish(client, fake_cache_store):
    api = API_PREFIX.rstrip("/")

    # 1) Start a new quiz
    start_payload = {
        "category": "Gilmore Girls",
        # request model uses alias; populate_by_name=True means either works
        "cf-turnstile-response": "test-ok",
    }
    r = await client.post(f"{api}/quiz/start?_a=dev&_k=dev", json=start_payload)
    assert r.status_code == 201, r.text
    body = r.json()

    # Basic shape checks
    quiz_id = _assert_uuid4(body["quizId"])
    assert body["initialPayload"], "initialPayload missing"
    assert body["initialPayload"]["type"] in {"synopsis", "question"}
    if body["initialPayload"]["type"] == "synopsis":
        syn = body["initialPayload"]["data"]
        assert isinstance(syn.get("title"), str) and syn["title"], "synopsis.title should be present"

    # 2) Proceed (opens questions gate and schedules background work)
    r = await client.post(f"{api}/quiz/proceed", json={"quiz_id": quiz_id})
    assert r.status_code == 202, r.text
    assert r.json()["status"] == "processing"

    # 3) Seed baseline questions into the cached state (fake agent won't produce them)
    state = _load_state(fake_cache_store, quiz_id)
    state["ready_for_questions"] = True
    state["generated_questions"] = _make_baseline_questions()
    state["baseline_count"] = len(state["generated_questions"])
    state["baseline_ready"] = True
    state["quiz_history"] = []
    _save_state(fake_cache_store, quiz_id, state)

    # 4) Poll status: should return the first unseen question (index 0)
    r = await client.get(
        f"{api}/quiz/status/{quiz_id}",
        params={"known_questions_count": 0},
    )
    assert r.status_code == 200, r.text
    js = r.json()
    assert js["status"] == "active"
    assert js["type"] == "question"
    q1 = js["data"]
    assert isinstance(q1.get("text"), str) and q1["text"]
    assert isinstance(q1.get("options"), list) and len(q1["options"]) >= 2

    # 5) Submit answer for question 0
    answer0 = {
        "quiz_id": quiz_id,
        "question_index": 0,
        "option_index": 0,
    }
    r = await client.post(f"{api}/quiz/next", json=answer0)
    assert r.status_code == 202, r.text
    assert r.json()["status"] == "processing"

    # 6) Poll status for next unseen question (index 1)
    r = await client.get(
        f"{api}/quiz/status/{quiz_id}",
        params={"known_questions_count": 1},
    )
    assert r.status_code == 200, r.text
    js = r.json()
    assert js["status"] == "active"
    assert js["type"] == "question"
    q2 = js["data"]
    assert isinstance(q2.get("text"), str) and q2["text"]
    assert isinstance(q2.get("options"), list) and len(q2["options"]) >= 2

    # 7) Submit answer for question 1 (now answers == baseline_count)
    answer1 = {
        "quiz_id": quiz_id,
        "question_index": 1,
        "option_index": 0,
    }
    r = await client.post(f"{api}/quiz/next", json=answer1)
    assert r.status_code == 202, r.text
    assert r.json()["status"] == "processing"

    # Simulate agent finishing by writing a final result into the cache
    state = _load_state(fake_cache_store, quiz_id)
    state["final_result"] = {
        "title": "You’re Lorelai Gilmore",
        "description": "Witty, warm, and fueled by coffee.",
        "image_url": "https://example.com/lorelai.png",
    }
    _save_state(fake_cache_store, quiz_id, state)

    # 8) Final status → finished + result
    r = await client.get(f"{api}/quiz/status/{quiz_id}")
    assert r.status_code == 200, r.text
    js = r.json()
    assert js["status"] == "finished"
    assert js["type"] == "result"
    result = js["data"]
    assert isinstance(result.get("title"), str) and result["title"]
    assert isinstance(result.get("description"), str) and result["description"]
