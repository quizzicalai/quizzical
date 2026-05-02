from __future__ import annotations

import pytest

from scripts.generate_ranked_pack_candidates import (
    FALLBACK_RANKED_TOPICS,
    RankedTopicCandidate,
    _generate_topic_entry_with_retries,
    evaluate_topic_entry,
    select_generation_queue,
)


def test_select_generation_queue_uses_fallback_order_when_prod_backlog_empty() -> None:
    queue = select_generation_queue(prod_topics=[], fallback_topics=FALLBACK_RANKED_TOPICS, limit=3)

    assert [c.slug for c in queue] == [
        FALLBACK_RANKED_TOPICS[0].slug,
        FALLBACK_RANKED_TOPICS[1].slug,
        FALLBACK_RANKED_TOPICS[2].slug,
    ]
    assert all(c.source == "fallback" for c in queue)


def test_select_generation_queue_prefers_unpacked_prod_topics_before_fallback() -> None:
    prod_topics = [
        {
            "slug": "already-packed-topic",
            "display_name": "Already Packed Topic",
            "popularity_rank": 10,
            "has_pack": True,
        },
        {
            "slug": "live-backlog-2",
            "display_name": "Live Backlog 2",
            "popularity_rank": 7,
            "has_pack": False,
        },
        {
            "slug": "live-backlog-1",
            "display_name": "Live Backlog 1",
            "popularity_rank": 2,
            "has_pack": False,
        },
    ]

    queue = select_generation_queue(prod_topics=prod_topics, fallback_topics=FALLBACK_RANKED_TOPICS, limit=4)

    assert [c.slug for c in queue[:2]] == ["live-backlog-1", "live-backlog-2"]
    assert queue[0].source == "production-popularity"
    assert queue[1].source == "production-popularity"
    assert queue[2].source == "fallback"


def test_evaluate_topic_entry_accepts_well_formed_v3_topic() -> None:
    topic = {
        "slug": "avatar-nations",
        "display_name": "Avatar Nations",
        "aliases": ["avatar nation", "four nations"],
        "synopsis": {"title": "Which Avatar Nation are you?", "summary": "Four nations, four temperaments."},
        "characters": [
            {"name": "Air Nomads", "short_description": "Free-spirited.", "profile_text": "You value freedom and perspective."},
            {"name": "Water Tribes", "short_description": "Adaptable.", "profile_text": "You move with change and protect your people."},
            {"name": "Earth Kingdom", "short_description": "Steady.", "profile_text": "You are grounded, stubborn, and enduring."},
            {"name": "Fire Nation", "short_description": "Driven.", "profile_text": "You are ambitious, intense, and transformative."},
        ],
        "baseline_questions": [
            {
                "question_text": f"Question {idx}",
                "options": [
                    {"text": "A"},
                    {"text": "B"},
                    {"text": "C"},
                    {"text": "D"},
                ],
            }
            for idx in range(1, 6)
        ],
    }

    out = evaluate_topic_entry(topic)

    assert out["ready"] is True
    assert out["errors"] == []
    assert out["score"] == 100


def test_evaluate_topic_entry_flags_duplicate_characters_and_bad_option_counts() -> None:
    topic = {
        "slug": "broken-topic",
        "display_name": "Broken Topic",
        "aliases": [],
        "synopsis": {"title": "", "summary": ""},
        "characters": [
            {"name": "Same", "short_description": "x", "profile_text": "x"},
            {"name": "Same", "short_description": "y", "profile_text": "y"},
        ],
        "baseline_questions": [
            {
                "question_text": "Repeated",
                "options": [{"text": "Only one"}],
            },
            {
                "question_text": "Repeated",
                "options": [{"text": "Only one"}],
            },
        ],
    }

    out = evaluate_topic_entry(topic)

    assert out["ready"] is False
    assert out["score"] < 100
    assert any("synopsis.title" in err for err in out["errors"])
    assert any("synopsis.summary" in err for err in out["errors"])
    assert any("character count" in err for err in out["errors"])
    assert any("duplicate character names" in err for err in out["errors"])
    assert any("baseline question count" in err for err in out["errors"])
    assert any("duplicate baseline question text" in err for err in out["errors"])
    assert any("option count" in err for err in out["errors"])


@pytest.mark.asyncio
async def test_generate_topic_entry_with_retries_stops_on_first_ready_topic() -> None:
    candidate = RankedTopicCandidate(slug="avatar-nations", display_name="Avatar Nations")
    attempts = 0

    invalid_topic = {
        "slug": candidate.slug,
        "display_name": candidate.display_name,
        "aliases": ["avatar nation", "four nations"],
        "synopsis": {"title": "Which Avatar Nation are you?", "summary": "Four nations, four temperaments."},
        "characters": [],
        "baseline_questions": [
            {
                "question_text": f"Question {idx}",
                "options": [
                    {"text": "A"},
                    {"text": "B"},
                    {"text": "C"},
                    {"text": "D"},
                ],
            }
            for idx in range(1, 6)
        ],
    }
    valid_topic = {
        **invalid_topic,
        "characters": [
            {"name": "Air Nomads", "short_description": "Free-spirited.", "profile_text": "You value freedom and perspective."},
            {"name": "Water Tribes", "short_description": "Adaptable.", "profile_text": "You move with change and protect your people."},
            {"name": "Earth Kingdom", "short_description": "Steady.", "profile_text": "You are grounded, stubborn, and enduring."},
            {"name": "Fire Nation", "short_description": "Driven.", "profile_text": "You are ambitious, intense, and transformative."},
        ],
    }

    async def fake_generate_topic_entry(_candidate: RankedTopicCandidate) -> dict[str, object]:
        nonlocal attempts
        attempts += 1
        return invalid_topic if attempts == 1 else valid_topic

    topic, evaluation = await _generate_topic_entry_with_retries(
        candidate,
        generate_topic_entry=fake_generate_topic_entry,
        max_attempts=3,
    )

    assert attempts == 2
    assert topic == valid_topic
    assert evaluation["ready"] is True
    assert evaluation["score"] == 100
