"""§21 Phase 7 — live-session opportunistic enqueue gate
(`AC-PRECOMP-COST-8`).

After a successful live quiz, only enqueue a precompute build job if
both:
  - the topic has no current pack (`current_pack_id IS NULL`), AND
  - the topic is in the popularity head (`popularity_rank < 5000`).

Topics with `popularity_rank IS NULL` are conservatively treated as
long-tail and skipped — operators can still enqueue them manually via
`/admin/precompute/jobs`."""

from __future__ import annotations

from app.models.db import Topic

POPULARITY_RANK_CUTOFF: int = 5000


def should_enqueue_after_session(topic: Topic) -> bool:
    if topic.current_pack_id is not None:
        return False
    rank = topic.popularity_rank
    if rank is None:
        return False
    return int(rank) < POPULARITY_RANK_CUTOFF
