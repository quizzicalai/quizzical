"""Unit tests for the canonical growth queue (popular non-canonical topics)."""

from __future__ import annotations

from scripts.canonical_growth_queue import build_growth_queue


def test_excludes_canonical_topics() -> None:
    cats = ["DISC", "DISC", "Hogwarts Houses", "mbti"]
    queue = build_growth_queue(cats, top=10)
    assert queue == []  # all canonical → nothing to grow


def test_ranks_non_canonical_by_frequency() -> None:
    # Taylor Swift eras / Pixar movies / K-pop groups are genuinely non-canonical
    # (NOT in the merged code+App-Config catalog even after PR #48's growth batch,
    # which promoted "Greek gods"→Twelve Olympians and "Generations"→canonical);
    # DISC is canonical → excluded.
    cats = (
        ["Taylor Swift eras"] * 5
        + ["Pixar movies"] * 3
        + ["K-pop groups"] * 1
        + ["DISC"] * 4  # canonical → excluded
    )
    queue = build_growth_queue(cats, top=10)
    labels = [(e.sample_label, e.count) for e in queue]
    assert labels[0][1] == 5  # Taylor Swift eras first
    assert ("DISC", 4) not in labels
    counts = {e.sample_label.lower(): e.count for e in queue}
    assert counts.get("taylor swift eras") == 5
    assert counts.get("pixar movies") == 3


def test_groups_by_normalized_key() -> None:
    # "What are the X" and "X quiz" should fold into the same bucket. Use a
    # genuinely non-canonical base topic so it survives the canonical filter
    # (Taylor Swift eras is not in the catalog even after PR #48).
    cats = [
        "Taylor Swift eras",
        "what are the taylor swift eras",
        "taylor swift eras quiz",
    ]
    queue = build_growth_queue(cats, top=10)
    # All three fold to one entry with count 3.
    assert len(queue) == 1
    assert queue[0].count == 3


def test_top_limit_truncates() -> None:
    cats = [f"unique topic {i}" for i in range(20)]
    queue = build_growth_queue(cats, top=5)
    assert len(queue) == 5
