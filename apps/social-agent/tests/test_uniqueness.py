"""Uniqueness gate: exact + semantic dedup (owner rule: never repeat)."""
from social_agent.textutils import normalize_for_dedup
from social_agent.uniqueness import (
    SEMANTIC_DUP_THRESHOLD,
    UniquenessGate,
    cosine,
    hash_embedding,
)


def test_cosine_basics():
    assert cosine([1.0, 0.0], [1.0, 0.0]) == 1.0
    assert cosine([1.0, 0.0], [0.0, 1.0]) == 0.0
    assert cosine([1.0, 0.0], [-1.0, 0.0]) == -1.0
    assert cosine([0.0, 0.0], [1.0, 0.0]) == 0.0  # zero vector -> 0, not NaN


def test_hash_embedding_deterministic_and_normalized():
    a = hash_embedding("i am homemade mac-n-cheese")
    b = hash_embedding("i am homemade mac-n-cheese")
    assert a == b
    assert len(a) == 384
    assert abs(sum(x * x for x in a) - 1.0) < 1e-9


def test_hash_embedding_similar_texts_score_higher():
    base = hash_embedding("this morning I am a mid-century ballroom gown")
    near = hash_embedding("this morning I am a mid-century ballroom dress")
    far = hash_embedding("breaking: local duck acquires small business loan")
    assert cosine(base, near) > cosine(base, far)


def test_exact_duplicate_rejected_even_with_case_punct_link_changes():
    gate = UniquenessGate()
    t1 = "I was today years old when I learned I'm soup. {link}"
    gate.admit(normalize_for_dedup(t1), hash_embedding(t1), t1)
    t2 = "i was TODAY years old when i learned im soup!!! https://quafel.com/result/abc"
    res = gate.check(normalize_for_dedup(t2), hash_embedding(t2))
    assert not res.unique
    assert "exact duplicate" in res.reason


def test_semantic_duplicate_rejected_above_threshold():
    gate = UniquenessGate()
    emb = [1.0] + [0.0] * 383
    gate.admit("some old post", emb, "some old post")
    near = [1.0] + [0.01] * 383  # cosine ~0.98 vs emb
    res = gate.check("a different exact text", near)
    assert not res.unique
    assert "semantic duplicate" in res.reason
    assert res.max_similarity > SEMANTIC_DUP_THRESHOLD


def test_dissimilar_text_passes_and_reports_similarity():
    gate = UniquenessGate()
    gate.admit("old", [1.0, 0.0, 0.0], "old")
    res = gate.check("new", [0.0, 1.0, 0.0])
    assert res.unique
    assert res.max_similarity < SEMANTIC_DUP_THRESHOLD


def test_empty_text_never_unique():
    gate = UniquenessGate()
    assert not gate.check("", None).unique


def test_dimension_mismatch_rows_are_skipped_not_fatal():
    gate = UniquenessGate()
    gate.admit("old", [1.0, 0.0], "old")  # legacy 2-dim row
    res = gate.check("new", [1.0, 0.0, 0.0])
    assert res.unique  # mismatched row ignored rather than crashing
