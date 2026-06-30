"""Unit tests for the canonical growth queue (popular non-canonical topics)."""

from __future__ import annotations

from scripts.canonical_growth_queue import build_growth_queue


def test_excludes_canonical_topics() -> None:
    cats = ["DISC", "DISC", "Hogwarts Houses", "mbti"]
    queue = build_growth_queue(cats, top=10)
    assert queue == []  # all canonical → nothing to grow


def test_ranks_non_canonical_by_frequency() -> None:
    # Greek gods / Generations / Taylor Swift eras are genuinely non-canonical
    # (NOT in the merged code+App-Config catalog); DISC is canonical → excluded.
    cats = (
        ["Greek gods"] * 5
        + ["Generations"] * 3
        + ["Taylor Swift eras"] * 1
        + ["DISC"] * 4  # canonical → excluded
    )
    queue = build_growth_queue(cats, top=10)
    labels = [(e.sample_label, e.count) for e in queue]
    assert labels[0][1] == 5  # Greek gods first
    assert ("DISC", 4) not in labels
    counts = {e.sample_label.lower(): e.count for e in queue}
    assert counts.get("greek gods") == 5
    assert counts.get("generations") == 3


def test_groups_by_normalized_key() -> None:
    # "What are the X" and "X quiz" should fold into the same bucket. Use a
    # genuinely non-canonical base topic so it survives the canonical filter.
    cats = ["Greek gods", "what are the greek gods", "greek gods quiz"]
    queue = build_growth_queue(cats, top=10)
    # All three fold to one entry with count 3.
    assert len(queue) == 1
    assert queue[0].count == 3


def test_top_limit_truncates() -> None:
    cats = [f"unique topic {i}" for i in range(20)]
    queue = build_growth_queue(cats, top=5)
    assert len(queue) == 5
