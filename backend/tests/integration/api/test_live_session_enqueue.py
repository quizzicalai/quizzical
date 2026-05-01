"""§21 Phase 7 — live-session enqueue gate (`AC-PRECOMP-COST-8`).

Opportunistic post-quiz enqueue is allowed only when the topic has no
current pack AND its popularity rank is in the head (< 5000)."""

from __future__ import annotations

import uuid

from app.models.db import Topic
from app.services.precompute.enqueue_gate import (
    POPULARITY_RANK_CUTOFF,
    should_enqueue_after_session,
)


def _make(*, current_pack_id, popularity_rank):
    return Topic(
        id=uuid.uuid4(),
        slug="t",
        display_name="T",
        current_pack_id=current_pack_id,
        popularity_rank=popularity_rank,
    )


def test_skipped_when_pack_already_exists():
    t = _make(current_pack_id=uuid.uuid4(), popularity_rank=10)
    assert should_enqueue_after_session(t) is False


def test_skipped_for_popular_topic_when_outside_head():
    t = _make(current_pack_id=None, popularity_rank=POPULARITY_RANK_CUTOFF + 1)
    assert should_enqueue_after_session(t) is False


def test_enqueue_skipped_for_popular_topic():
    """`AC-PRECOMP-COST-8` named test — long-tail rank is skipped."""
    t = _make(current_pack_id=None, popularity_rank=9999)
    assert should_enqueue_after_session(t) is False


def test_skipped_when_rank_unknown():
    t = _make(current_pack_id=None, popularity_rank=None)
    assert should_enqueue_after_session(t) is False


def test_allowed_when_head_topic_no_pack():
    t = _make(current_pack_id=None, popularity_rank=42)
    assert should_enqueue_after_session(t) is True
