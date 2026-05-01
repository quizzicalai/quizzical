"""§21 Phase 4 — Redis pack cache + hot-character pinning.

The hot path of `/quiz/start` (when a published pack exists for the
incoming topic) is dominated by the JOIN that hydrates synopsis +
character_set + baseline_question_set + media_assets. Phase 4 absorbs
that cost in two layers:

1. **Per-pack cache** — `tk:pack:{topic_id}` holds the JSON-serialised
   resolved pack for 1 h. A SETNX-based fill lock prevents thundering
   herds when many users land on the same topic simultaneously
   (`AC-PRECOMP-PERF-2`).
2. **Hot-character pinning** — characters referenced by ≥ N
   `character_session_map` rows get their preferred media-asset
   storage_uri pinned at `media:hot:{asset_id}` so renderers never miss
   the asset on first paint (`AC-PRECOMP-PERF-6`).

All Redis interactions tolerate transient outages by returning
`None` / treating the cache as a MISS — the caller falls back to the
DB JOIN. We never raise from this module on a Redis fault, matching
the project-wide fail-open posture for cache layers.

Keys & TTLs (single source of truth):

| Key pattern                  | Purpose                          | TTL |
|------------------------------|----------------------------------|-----|
| `tk:pack:{topic_id}`         | resolved pack JSON               | 1h  |
| `tk:pack:lock:{topic_id}`    | SETNX fill lock                  | 30s |
| `media:hot:{asset_id}`       | pinned `storage_uri` for hot ref | 24h |
"""

from __future__ import annotations

import asyncio
import json
import logging
from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field
from typing import Any
from uuid import UUID

logger = logging.getLogger("app.services.precompute.cache")

PACK_KEY_FMT = "tk:pack:{topic_id}"
PACK_LOCK_KEY_FMT = "tk:pack:lock:{topic_id}"
HOT_CHAR_KEY_FMT = "media:hot:{asset_id}"

PACK_TTL_S = 3600  # 1 hour
LOCK_TTL_S = 30    # 30 seconds — long enough for one DB JOIN, short
                   # enough that a crashed filler unblocks quickly.
HOT_CHAR_TTL_S = 86_400  # 24 hours
HOT_CHAR_REF_THRESHOLD = 200  # `AC-PRECOMP-PERF-6`


@dataclass(frozen=True)
class ResolvedPack:
    """Lightweight serialisable view of a resolved topic pack.

    Only the fields the hot-path response builder needs — keep the JSON
    blob small enough that a 1 h TTL across thousands of topics fits
    comfortably in the prod Redis instance.
    """

    topic_id: str
    pack_id: str
    version: int
    synopsis_id: str
    character_set_id: str
    baseline_question_set_id: str
    storage_uris: tuple[str, ...] = field(default_factory=tuple)
    """Distinct media-asset URIs used by this pack (for 103 Early Hints
    `Link: rel=preload` headers; `AC-PRECOMP-PERF-3`)."""

    def to_json(self) -> str:
        return json.dumps(
            {
                "topic_id": self.topic_id,
                "pack_id": self.pack_id,
                "version": self.version,
                "synopsis_id": self.synopsis_id,
                "character_set_id": self.character_set_id,
                "baseline_question_set_id": self.baseline_question_set_id,
                "storage_uris": list(self.storage_uris),
            },
            separators=(",", ":"),
            ensure_ascii=False,
        )

    @classmethod
    def from_json(cls, raw: str | bytes) -> "ResolvedPack | None":
        try:
            data = json.loads(raw)
        except (TypeError, ValueError, json.JSONDecodeError):
            return None
        try:
            return cls(
                topic_id=str(data["topic_id"]),
                pack_id=str(data["pack_id"]),
                version=int(data.get("version") or 0),
                synopsis_id=str(data["synopsis_id"]),
                character_set_id=str(data["character_set_id"]),
                baseline_question_set_id=str(data["baseline_question_set_id"]),
                storage_uris=tuple(data.get("storage_uris") or ()),
            )
        except (KeyError, TypeError, ValueError):
            return None


# ---------------------------------------------------------------------------
# Cache helpers
# ---------------------------------------------------------------------------


def _to_str(uid: UUID | str) -> str:
    return str(uid)


async def get_pack(redis, topic_id: UUID | str) -> ResolvedPack | None:
    """Return the cached `ResolvedPack` or `None` on MISS / Redis error.

    Never raises — Redis outages must not break `/quiz/start`."""
    if redis is None:
        return None
    key = PACK_KEY_FMT.format(topic_id=_to_str(topic_id))
    try:
        raw = await redis.get(key)
    except Exception:  # noqa: BLE001 — fail-open by design
        logger.debug("precompute.cache.get_failed key=%s", key, exc_info=True)
        return None
    if raw is None:
        return None
    return ResolvedPack.from_json(raw)


async def set_pack(
    redis,
    pack: ResolvedPack,
    *,
    ttl_s: int = PACK_TTL_S,
) -> bool:
    """Write `pack` to Redis with the configured TTL. Returns True on
    success, False on Redis error (caller can ignore — best effort)."""
    if redis is None:
        return False
    key = PACK_KEY_FMT.format(topic_id=pack.topic_id)
    try:
        await redis.set(key, pack.to_json(), ex=ttl_s)
        return True
    except Exception:  # noqa: BLE001
        logger.debug("precompute.cache.set_failed key=%s", key, exc_info=True)
        return False


async def invalidate_pack(redis, topic_id: UUID | str) -> bool:
    """Remove the cached pack for `topic_id`. Idempotent.

    Called transactionally from `publish()` and from the quarantine
    cascade (Phase 6) so stale packs never resurface after a swap."""
    if redis is None:
        return False
    key = PACK_KEY_FMT.format(topic_id=_to_str(topic_id))
    try:
        await redis.delete(key)
        return True
    except Exception:  # noqa: BLE001
        logger.debug("precompute.cache.invalidate_failed key=%s", key, exc_info=True)
        return False


# ---------------------------------------------------------------------------
# Fill lock (`AC-PRECOMP-PERF-2`)
# ---------------------------------------------------------------------------


FillFn = Callable[[], Awaitable[ResolvedPack | None]]


async def get_or_fill(
    redis,
    topic_id: UUID | str,
    fill_fn: FillFn,
    *,
    pack_ttl_s: int = PACK_TTL_S,
    lock_ttl_s: int = LOCK_TTL_S,
    poll_interval_s: float = 0.025,
    max_wait_s: float = 1.5,
) -> ResolvedPack | None:
    """Single-flight cache fill.

    Algorithm (`AC-PRECOMP-PERF-2`):

    1. Try cache GET — return on HIT.
    2. SETNX a fill lock. The single winner runs `fill_fn`, writes the
       result to cache, then deletes the lock.
    3. Losers poll the cache (cheap GET) until either a value appears or
       `max_wait_s` elapses, then fall back to a direct `fill_fn` call
       (better than blocking `/quiz/start` indefinitely if the holder
       crashed).
    """
    cached = await get_pack(redis, topic_id)
    if cached is not None:
        return cached

    if redis is None:
        # No redis → degenerate path: just compute it.
        return await fill_fn()

    lock_key = PACK_LOCK_KEY_FMT.format(topic_id=_to_str(topic_id))
    try:
        acquired = await redis.set(lock_key, "1", ex=lock_ttl_s, nx=True)
    except Exception:  # noqa: BLE001
        acquired = False

    if acquired:
        try:
            pack = await fill_fn()
            if pack is not None:
                await set_pack(redis, pack, ttl_s=pack_ttl_s)
            return pack
        finally:
            try:
                await redis.delete(lock_key)
            except Exception:  # noqa: BLE001
                pass

    # Loser path — poll for the cached fill, bounded.
    waited = 0.0
    while waited < max_wait_s:
        await asyncio.sleep(poll_interval_s)
        waited += poll_interval_s
        cached = await get_pack(redis, topic_id)
        if cached is not None:
            return cached
    # Lock holder died or fill_fn was slow — fall through to a direct
    # compute so the request still completes.
    return await fill_fn()


# ---------------------------------------------------------------------------
# Hot-character pinning (`AC-PRECOMP-PERF-6`)
# ---------------------------------------------------------------------------


async def maybe_pin_hot_character(
    redis,
    *,
    asset_id: UUID | str,
    storage_uri: str,
    ref_count: int,
    threshold: int = HOT_CHAR_REF_THRESHOLD,
    ttl_s: int = HOT_CHAR_TTL_S,
) -> bool:
    """Pin `storage_uri` for an asset when its reference count crosses
    `threshold`. Returns True when a pin was written.

    Pinning is best-effort; transient Redis errors return False without
    raising."""
    if redis is None:
        return False
    if ref_count < threshold:
        return False
    key = HOT_CHAR_KEY_FMT.format(asset_id=_to_str(asset_id))
    try:
        await redis.set(key, storage_uri, ex=ttl_s)
        return True
    except Exception:  # noqa: BLE001
        logger.debug("precompute.cache.hot_pin_failed key=%s", key, exc_info=True)
        return False


# ---------------------------------------------------------------------------
# Link-header helper for 103 Early Hints (`AC-PRECOMP-PERF-3`)
# ---------------------------------------------------------------------------


def build_link_header(uris: tuple[str, ...] | list[str], *, max_links: int = 8) -> str:
    """Build an RFC 8288 `Link` header value preloading `uris`.

    Starlette / FastAPI does not yet support emitting an actual `103
    Early Hints` informational response from a sync handler; we instead
    attach the same `Link` header to the final 201 response. The browser
    treats it identically for `<link rel=preload>` purposes — the only
    behavioural difference is that the preload is initiated after the
    final response headers arrive rather than during synopsis
    generation. When Starlette gains 103-EH support we will replace this
    helper at the call site only.
    """
    cleaned = [u for u in (uris or ()) if isinstance(u, str) and u]
    if not cleaned:
        return ""
    parts = [f'<{u}>; rel=preload; as=image' for u in cleaned[:max_links]]
    return ", ".join(parts)


def collect_storage_uris(pack: Any) -> tuple[str, ...]:
    """Best-effort collection of distinct image URIs from any object
    exposing a `storage_uris` attribute / key. Returns an empty tuple
    when the pack carries no media (e.g. text-only topic)."""
    if pack is None:
        return ()
    uris: Any = None
    if hasattr(pack, "storage_uris"):
        uris = pack.storage_uris
    elif isinstance(pack, dict):
        uris = pack.get("storage_uris")
    if not uris:
        return ()
    seen: dict[str, None] = {}
    for u in uris:
        if isinstance(u, str) and u and u not in seen:
            seen[u] = None
    return tuple(seen.keys())
