# app/services/image_pipeline.py
"""Background-task orchestration for FAL image generation (§7.8.5).

All public functions are intended to be scheduled via FastAPI ``BackgroundTasks``
**after** the primary persistence call has returned. They never block the
user-visible response and never raise to the caller.

Persistence model:
- ``characters.image_url`` is updated unconditionally so the row tracks the
  freshest known-good asset (the precompute pipeline keeps the canonical
  URL via the same code path, so this is safe).
- A short HEAD probe in :func:`generate_character_images` short-circuits
  FAL regeneration when the existing DB URL is still reachable, which is
  what makes precomputed packs render instantly on a cold start.
- ``session_history.character_set`` is a JSONB snapshot; we refresh the
  ``image_url`` of every element whose ``name`` matches.
- ``session_history.category_synopsis`` and ``session_history.final_result``
  are JSONB; we set ``image_url`` via ``jsonb_set``.
"""
from __future__ import annotations

import asyncio
from contextlib import asynccontextmanager
from typing import Any
from uuid import UUID

import structlog
from sqlalchemy import bindparam, text

from app.agent.tools import image_tools
from app.api import dependencies as deps
from app.core.config import settings
from app.models.api import CharacterProfile, FinalResult, Synopsis
from app.services.image_service import _client_singleton as _client

logger = structlog.get_logger(__name__)

# P1 — bounded retry when ``_client.generate`` returns None (FAL retries
# exhausted, NSFW redaction, or every branded rung came back empty). This is
# distinct from the transient-error retry inside ``FalImageClient.generate``
# (§16.2): that one recovers from *exceptions*; a clean None return means FAL
# answered but produced no usable image, which previously got persisted as a
# permanent null. We re-issue the same prompt a small number of times before
# giving up. Semantics stay fail-open — after the budget is spent we still map
# to None and never raise.
#
# Total generate() calls for one image == 1 + _null_retry_attempts(). The value
# is read from ``image_gen.retry.max_attempts`` (already a tuned, overridable
# config knob) and clamped to a small bound so a misconfiguration can't turn a
# fail-open background task into a credit sink. Fallback constant applies when
# config is unavailable.
_DEFAULT_NULL_RETRY_ATTEMPTS = 2
_MAX_NULL_RETRY_ATTEMPTS = 3


def _null_retry_attempts() -> int:
    """Number of *extra* re-issues of a prompt after a None result.

    0 disables the behaviour (single attempt, legacy semantics). Derived from
    the image-gen retry config and clamped to ``_MAX_NULL_RETRY_ATTEMPTS``.
    """
    cfg = _img_cfg()
    retry = getattr(cfg, "retry", None) if cfg else None
    # max_attempts counts the first try; extra re-issues == max_attempts - 1.
    raw = getattr(retry, "max_attempts", None)
    if raw is None:
        extra = _DEFAULT_NULL_RETRY_ATTEMPTS
    else:
        try:
            extra = max(0, int(raw) - 1)
        except (TypeError, ValueError):
            extra = _DEFAULT_NULL_RETRY_ATTEMPTS
    return min(extra, _MAX_NULL_RETRY_ATTEMPTS)


async def _generate_with_null_retry(prompt: str, **kwargs: Any) -> tuple[str | None, int]:
    """Call ``_client.generate`` and, on a None result, re-issue the same
    prompt up to ``_null_retry_attempts()`` more times.

    Returns ``(url_or_None, n_generate_calls)``. The second element is the COUNT
    of actual ``_client.generate`` invocations made — FAL bills per completed
    call, so the daily cents breaker (Hitlist #2/#5) must meter the real call
    count, not "1 per character". A success on the first try returns
    ``(url, 1)``; two re-issues before success returns ``(url, 3)``; all attempts
    None returns ``(None, 1 + _null_retry_attempts())``.

    A None return from ``_client.generate`` means FAL responded but yielded no
    usable URL (exhausted internal retries / NSFW redaction / empty result).
    Re-issuing the identical prompt is worthwhile because the underlying causes
    are frequently nondeterministic (transient upstream load, sampler-dependent
    safety trips). Never raises — any exception from ``generate`` propagates the
    same way it did before this wrapper existed; callers already guard the
    pipeline against that.
    """
    calls = 1
    url = await _client.generate(prompt, **kwargs)
    if url:
        return url, calls
    attempts = _null_retry_attempts()
    for i in range(attempts):
        logger.info(
            "image.null_retry",
            attempt=i + 1, of=attempts, prompt_prefix=(prompt or "")[:48],
        )
        calls += 1
        url = await _client.generate(prompt, **kwargs)
        if url:
            return url, calls
    return None, calls


# ---------------------------------------------------------------------------
# Config helpers
# ---------------------------------------------------------------------------

def _img_cfg() -> Any:
    return getattr(settings, "image_gen", None)


def _style_suffix() -> str:
    cfg = _img_cfg()
    return getattr(cfg, "style_suffix", "") if cfg else ""


def _negative_prompt() -> str:
    cfg = _img_cfg()
    return getattr(cfg, "negative_prompt", "") if cfg else ""


def _img_model() -> str:
    """The global (small-image) FAL model id — schnell by default."""
    cfg = _img_cfg()
    return str(getattr(cfg, "model", "fal-ai/flux/schnell")) if cfg else "fal-ai/flux/schnell"


def _img_size() -> dict[str, int]:
    """The global default render size (cast thumbnails) — 256×256 by default."""
    cfg = _img_cfg()
    sz = getattr(cfg, "image_size", None) if cfg else None
    if isinstance(sz, dict) and "width" in sz and "height" in sz:
        return {"width": int(sz["width"]), "height": int(sz["height"])}
    return {"width": 256, "height": 256}


def _hero_model() -> str | None:
    """FLUX dev (or whatever ``image_gen.hero_model`` configures) for the two
    LARGE hero images. ``None`` => fall back to the global ``model`` (schnell).
    The cheap small-image paths (cast thumbs, answer tiles) never read this."""
    cfg = _img_cfg()
    m = getattr(cfg, "hero_model", None) if cfg else None
    return str(m) if m else None


def _hero_steps() -> int | None:
    """Higher inference-step count for the hero model (FLUX dev's quality
    default ~28). ``None`` => the client falls back to ``num_inference_steps``."""
    cfg = _img_cfg()
    s = getattr(cfg, "hero_num_inference_steps", None) if cfg else None
    try:
        return int(s) if s is not None else None
    except (TypeError, ValueError):
        return None


def _get_concurrency() -> int:
    cfg = _img_cfg()
    return max(1, int(getattr(cfg, "concurrency", 4))) if cfg else 4


def _max_character_images() -> int:
    """Hitlist #5 — hard cap on PAID character-image FAL calls per quiz. 0 (or a
    misconfigured non-positive value) disables the cap. Read from
    ``quiz.max_character_images``."""
    cfg = getattr(settings, "quiz", None)
    try:
        return int(getattr(cfg, "max_character_images", 0) or 0) if cfg else 0
    except Exception:
        return 0


def _enabled() -> bool:
    cfg = _img_cfg()
    if not cfg or not bool(getattr(cfg, "enabled", False)):
        return False
    # Fail-safe: require an API key in env. Prevents accidental network
    # traffic in tests/dev when no provider is configured.
    import os as _os
    return bool(
        _os.getenv("FAL_KEY")
        or _os.getenv("FAL_AI_KEY")
        or _os.getenv("FAL_AI_API_KEY")
    )


# ---------------------------------------------------------------------------
# DB context — uses the app's async_session_factory created at startup
# ---------------------------------------------------------------------------

@asynccontextmanager
async def _db_session_ctx():
    factory = deps.async_session_factory
    if factory is None:
        # Background tasks may run after shutdown; degrade gracefully.
        yield None
        return
    async with factory() as session:
        yield session


# ---------------------------------------------------------------------------
# Persistence
# ---------------------------------------------------------------------------

async def _persist_character_url(*, name: str, url: str) -> None:
    if not url:
        return
    async with _db_session_ctx() as session:
        if session is None:
            return
        try:
            # Unconditional overwrite: any code path that calls this has
            # just produced a fresh URL (either FAL regen or a precompute
            # archive re-seed). Keeping the row stale -- the previous
            # ``WHERE image_url IS NULL`` guard -- caused starter packs to
            # be re-imported but never visually refreshed.
            #
            # Scoped by the unique ``name`` column on purpose. ``canonical_key``
            # is a NON-unique, name-derived (case/accent/whitespace-folded) key:
            # distinct names ("Héctor" / "HECTOR") fold to one key yet coexist as
            # separate rows, so an UPDATE scoped by canonical_key would rewrite
            # image_url on every collided row and clobber a different (possibly
            # curated/branded) character's art. True per-topic image isolation
            # would require a composite (topic + name) key and is deferred.
            await session.execute(
                text(
                    "UPDATE characters SET image_url = :url, last_updated_at = now() "
                    "WHERE name = :name"
                ),
                {"url": url, "name": name},
            )
            await session.commit()
        except Exception as e:
            # AC-IMG-TX-1 — explicit rollback so the AsyncSession is returned
            # to the pool in a clean state and partial writes are not silently
            # committed by ``__aexit__``.
            try:
                await session.rollback()
            except Exception:
                pass
            logger.info("image.persist.character.fail", name=name, error=str(e))


async def _refresh_character_set_image(
    *, session_id: UUID, name: str, url: str
) -> None:
    """Update the ``image_url`` of any element in ``character_set`` whose name matches."""
    if not url:
        return
    async with _db_session_ctx() as session:
        if session is None:
            return
        try:
            await session.execute(
                text(
                    """
                    UPDATE session_history
                    SET character_set = (
                        SELECT COALESCE(jsonb_agg(
                            CASE WHEN elem->>'name' = :name
                                 THEN jsonb_set(elem, '{image_url}', to_jsonb(CAST(:url AS text)))
                                 ELSE elem
                            END
                        ), '[]'::jsonb)
                        FROM jsonb_array_elements(character_set) elem
                    ),
                    last_updated_at = now()
                    WHERE session_id = :sid
                    """
                ),
                {"sid": str(session_id), "name": name, "url": url},
            )
            await session.commit()
        except Exception as e:
            try:
                await session.rollback()
            except Exception:
                pass
            logger.info("image.persist.character_set.fail",
                        session_id=str(session_id), name=name, error=str(e))


async def _persist_character_urls_batch(items: list[tuple[str, str]]) -> None:
    """Hitlist #14 — persist many ``(name, url)`` rows in a SINGLE DB session,
    replacing the per-character ``_persist_character_url`` fan-out (one pooled
    connection per character). The same unconditional UPDATE scoped by the unique
    ``name`` column, just reusing one connection.

    Per-row durability (review fix): each row's UPDATE runs inside its OWN
    SAVEPOINT (``begin_nested``) so a single garbage/failing row rolls back only
    itself and the rest still commit — preserving the old per-row semantics
    (each character used to commit in its own session, so one failure never
    discarded the whole cast's URL cache and forced needless FAL regeneration).
    Skips empty urls. Best-effort: log-and-continue per row, swallow on outer
    commit failure."""
    items = [(n, u) for (n, u) in items if u]
    if not items:
        return
    async with _db_session_ctx() as session:
        if session is None:
            return
        for name, url in items:
            try:
                async with session.begin_nested():
                    await session.execute(
                        text(
                            "UPDATE characters SET image_url = :url, last_updated_at = now() "
                            "WHERE name = :name"
                        ),
                        {"url": url, "name": name},
                    )
            except Exception as e:
                # SAVEPOINT auto-rolled-back this row only; others persist.
                logger.info("image.persist.character.row_fail", name=name, error=str(e))
        try:
            await session.commit()
        except Exception as e:
            try:
                await session.rollback()
            except Exception:
                pass
            logger.info("image.persist.character.batch_fail", error=str(e))


async def _refresh_character_set_images_batch(
    *, session_id: UUID, items: list[tuple[str, str]]
) -> None:
    """Hitlist #14 — refresh the ``character_set`` JSONB ``image_url`` for many
    names in a SINGLE DB session, replacing the per-character
    ``_refresh_character_set_image`` fan-out. Each statement is the SAME
    name-scoped ``jsonb_set`` UPDATE the single-row helper runs, so the persisted
    JSONB ends up identical; only the connection is shared.

    Per-row durability (review fix): each row's UPDATE runs inside its OWN
    SAVEPOINT (``begin_nested``) so one failing row rolls back only itself and
    the rest still commit — preserving the old per-row semantics. Skips empty
    urls. Best-effort: log-and-continue per row, swallow on outer commit
    failure."""
    items = [(n, u) for (n, u) in items if u]
    if not items:
        return
    async with _db_session_ctx() as session:
        if session is None:
            return
        for name, url in items:
            try:
                async with session.begin_nested():
                    await session.execute(
                        text(
                            """
                            UPDATE session_history
                            SET character_set = (
                                SELECT COALESCE(jsonb_agg(
                                    CASE WHEN elem->>'name' = :name
                                         THEN jsonb_set(elem, '{image_url}', to_jsonb(CAST(:url AS text)))
                                         ELSE elem
                                    END
                                ), '[]'::jsonb)
                                FROM jsonb_array_elements(character_set) elem
                            ),
                            last_updated_at = now()
                            WHERE session_id = :sid
                            """
                        ),
                        {"sid": str(session_id), "name": name, "url": url},
                    )
            except Exception as e:
                # SAVEPOINT auto-rolled-back this row only; others persist.
                logger.info(
                    "image.persist.character_set.row_fail",
                    session_id=str(session_id),
                    name=name,
                    error=str(e),
                )
        try:
            await session.commit()
        except Exception as e:
            try:
                await session.rollback()
            except Exception:
                pass
            logger.info(
                "image.persist.character_set.batch_fail",
                session_id=str(session_id),
                error=str(e),
            )


async def _persist_synopsis_image(*, session_id: UUID, url: str) -> None:
    if not url:
        return
    async with _db_session_ctx() as session:
        if session is None:
            return
        try:
            await session.execute(
                text(
                    """
                    UPDATE session_history
                    SET category_synopsis =
                        jsonb_set(COALESCE(category_synopsis, '{}'::jsonb),
                                  '{image_url}', to_jsonb(CAST(:url AS text)), true),
                        last_updated_at = now()
                    WHERE session_id = :sid
                    """
                ),
                {"sid": str(session_id), "url": url},
            )
            await session.commit()
        except Exception as e:
            try:
                await session.rollback()
            except Exception:
                pass
            logger.info("image.persist.synopsis.fail",
                        session_id=str(session_id), error=str(e))


async def _persist_result_image(*, session_id: UUID, url: str) -> None:
    if not url:
        return
    async with _db_session_ctx() as session:
        if session is None:
            return
        try:
            await session.execute(
                text(
                    """
                    UPDATE session_history
                    SET final_result = CASE
                        WHEN final_result IS NULL THEN final_result
                        ELSE jsonb_set(final_result, '{image_url}', to_jsonb(CAST(:url AS text)), true)
                    END,
                    last_updated_at = now()
                    WHERE session_id = :sid
                    """
                ),
                {"sid": str(session_id), "url": url},
            )
            await session.commit()
        except Exception as e:
            try:
                await session.rollback()
            except Exception:
                pass
            logger.info("image.persist.result.fail",
                        session_id=str(session_id), error=str(e))


# ---------------------------------------------------------------------------
# Public orchestration
# ---------------------------------------------------------------------------

async def _get_character_url(name: str) -> str | None:
    """Return the current ``characters.image_url`` for ``name``, or None.

    Scoped by the unique ``name`` column. We deliberately do NOT scope by
    ``canonical_key``: it is a non-unique, name-derived key under which distinct
    names collide, so a canonical_key lookup (no ORDER BY) would return an
    arbitrary collided row's URL. Per-topic image isolation is a separate design
    item (needs a composite topic+name key); see ``_persist_character_url``.
    """
    async with _db_session_ctx() as session:
        if session is None:
            return None
        try:
            row = await session.execute(
                text("SELECT image_url FROM characters WHERE name = :name LIMIT 1"),
                {"name": name},
            )
            r = row.first()
            if r is None:
                return None
            val = r[0]
            return str(val) if val else None
        except Exception as e:
            logger.info("image.lookup.character.fail", name=name, error=str(e))
            return None


async def _get_character_urls(names: list[str]) -> dict[str, str | None]:
    """Hitlist #14 (2026-06-30) — BATCHED lookup of ``characters.image_url`` for
    a whole cast in a SINGLE DB session/query, replacing the per-character
    ``_get_character_url`` fan-out (one ``SELECT ... WHERE name=:name`` and one
    pooled connection PER character — the N+1 that, under a pool with no
    ``pool_timeout``, could back up the worker).

    Returns ``{name: url_or_None}`` for every requested name (names with no row,
    or a NULL ``image_url``, map to ``None``). Same column scoping rationale as
    :func:`_get_character_url`. Fail-soft: any error yields all-None so the
    caller behaves exactly as a clean set of cache misses (regenerates), which
    is the same outcome the per-character path produced on error.
    """
    out: dict[str, str | None] = dict.fromkeys(names)
    if not names:
        return out
    async with _db_session_ctx() as session:
        if session is None:
            return out
        try:
            # Expanding bindparam renders a fully-parameterised ``IN (...)`` with
            # no string interpolation of the names (injection-safe; no f-string
            # SQL). The unique ``name`` column means at most one row per name.
            stmt = text(
                "SELECT name, image_url FROM characters WHERE name IN :names"
            ).bindparams(bindparam("names", expanding=True))
            rows = await session.execute(stmt, {"names": list(names)})
            for name_val, url_val in rows.all():
                if name_val in out:
                    out[name_val] = str(url_val) if url_val else None
            return out
        except Exception as e:
            logger.info("image.lookup.character.batch_fail", error=str(e))
            return dict.fromkeys(names)


async def _url_alive(url: str, *, timeout_s: float = 3.0) -> bool:
    """Cheap HEAD probe. Returns True iff the URL responds 2xx/3xx.

    Used to gate FAL regeneration when the DB already has an image URL --
    avoids spending FAL credits regenerating an asset that is still served
    by the upstream CDN.
    """
    if not url:
        return False
    try:
        # SEC1 (defence-in-depth): validate the URL uses an allowed scheme and
        # resolves to a public IP before probing (raises on SSRF), and never
        # follow redirects — a 3xx to a rebound internal target must not be
        # chased. We only need to know the CDN URL is reachable.
        from app.services.precompute.outbound import assert_url_safe
        assert_url_safe(url)
        import httpx  # local import keeps cold-start light
        async with httpx.AsyncClient(timeout=timeout_s, follow_redirects=False) as client:
            resp = await client.head(url)
            return 200 <= resp.status_code < 400
    except Exception as e:
        logger.info("image.head_probe.fail", url=url, error=str(e))
        return False


async def generate_character_images(  # noqa: C901 — linear two-phase fan-out: dedup -> resolve cache (all chars) -> cap misses -> generate -> meter (Hitlist #5 review item B)
    *,
    session_id: UUID,
    characters: list[CharacterProfile],
    category: str,
    analysis: dict[str, Any] | None = None,
) -> dict[str, str | None]:
    """Fan out FAL calls (bounded by semaphore). Returns ``{name: Optional[url]}``.

    Cache semantics: when ``characters.image_url`` is already populated AND a
    HEAD probe to that URL succeeds, the FAL call is skipped entirely and the
    existing URL is reused. This is what makes precomputed packs serve their
    canonical art on cold start without paying for regeneration.
    """
    if not _enabled() or not characters:
        return {}

    # Dedup by name preserving order.
    seen: set[str] = set()
    unique: list[CharacterProfile] = []
    for c in characters:
        n = getattr(c, "name", None)
        if n and n not in seen:
            seen.add(n)
            unique.append(c)

    sem = asyncio.Semaphore(_get_concurrency())
    style = _style_suffix()
    neg = _negative_prompt()

    # Branded topics (TV/film/book/game IP) route through the multi-rung
    # fallback ladder so the character actually looks like themselves. For
    # non-branded archetype topics ("Greek God", "Pokémon Type") the source
    # name adds no information and the legacy descriptive prompt is fine.
    is_branded = bool((analysis or {}).get("is_media", False))
    source_name = (category or "").strip()

    # ---------------------------------------------------------------------
    # Phase 1 — resolve the cache for EVERY character. Hitlist #5 fix (review
    # item B): the cap must NOT drop already-CACHED (free) thumbnails. A cache
    # hit reuses the existing CDN URL with NO paid FAL call, so every cache hit
    # is always returned — precomputed packs render the FULL cast uncapped.
    #
    # Hitlist #14 (2026-06-30): resolve the cache in ONE batched DB query for the
    # whole cast (``_get_character_urls``) instead of one SELECT + one connection
    # per character. The HEAD liveness probes stay concurrent (network I/O, not
    # DB), and the cache-hit ``character_set`` refreshes are flushed in a SINGLE
    # batched session afterward — eliminating the per-character N+1 connection
    # fan-out. The set of cache hits and the URLs reused are unchanged.
    # ---------------------------------------------------------------------
    existing_urls = await _get_character_urls([c.name for c in unique])

    async def _probe(profile: CharacterProfile) -> tuple[str, str | None]:
        existing = existing_urls.get(profile.name)
        if existing and await _url_alive(existing):
            return profile.name, existing
        return profile.name, None

    probe_results = await asyncio.gather(*[_probe(c) for c in unique])
    cached_urls: dict[str, str] = {
        name: url for name, url in probe_results if url is not None
    }
    # Single batched session: refresh every cache-hit's character_set snapshot.
    await _refresh_character_set_images_batch(
        session_id=session_id, items=list(cached_urls.items())
    )
    for name in cached_urls:
        logger.info(
            "image.character.cache_hit", name=name, session_id=str(session_id)
        )
    misses = [c for c in unique if cached_urls.get(c.name) is None]

    # ---------------------------------------------------------------------
    # Phase 2 — cap only the NEW (uncached / paid) generations. Hitlist #5:
    # canonical archetype sets run 16–26 characters; uncapped that is ~$0.18–
    # 0.30 of FAL spend for 56px cast thumbnails. The miss list is sliced to
    # ``max_character_images`` (the cast is ordered primary-first). The synopsis-
    # hero + winning-result hero images are SEPARATE pipeline calls and are never
    # affected by this cap. Cached thumbnails are NOT counted toward the cap.
    # ---------------------------------------------------------------------
    cap = _max_character_images()
    if cap > 0 and len(misses) > cap:
        logger.info(
            "image.character.fanout_capped",
            session_id=str(session_id),
            requested=len(unique),
            cache_hits=len(cached_urls),
            new_generations=len(misses),
            cap=cap,
        )
        misses = misses[:cap]

    async def _generate_one(profile: CharacterProfile) -> tuple[str, str | None, int]:
        """Generate a fresh image. Returns ``(name, url, n_fal_calls)`` where
        ``n_fal_calls`` is the ACTUAL number of ``_client.generate`` invocations
        (FAL bills per completed call, including null-retries and brand-ladder
        rungs), so the daily cents breaker meters real FAL spend (review item A).
        """
        seed = image_tools.derive_seed(session_id, profile.name)
        async with sem:
            if is_branded:
                url, n_calls = await _generate_character_with_brand_fallback(
                    name=profile.name,
                    source=source_name,
                    style_suffix=style,
                    negative_prompt=neg,
                    seed=seed,
                )
            else:
                try:
                    spec = image_tools.build_character_image_prompt(
                        profile, category=category, analysis=analysis or {},
                        style_suffix=style, negative_prompt=neg,
                    )
                except Exception as e:
                    logger.info("image.character.prompt_build.fail",
                                name=profile.name, error=str(e))
                    return profile.name, None, 0
                # P1 — re-issue on a clean None (FAL gave no usable image) before
                # persisting a permanent null. Fail-open: still None after budget.
                url, n_calls = await _generate_with_null_retry(
                    spec["prompt"], negative_prompt=spec.get("negative_prompt"),
                    seed=seed,
                )
        # Hitlist #14 — persistence is deferred to a single batched session after
        # the fan-out (see below) instead of 2 connections per generated
        # character. What is persisted is unchanged.
        return profile.name, url, n_calls

    gen_results = await asyncio.gather(
        *[_generate_one(c) for c in misses], return_exceptions=False
    )

    # Hitlist #14 — flush all newly-generated character images in two batched
    # sessions (characters table, then session_history JSONB) rather than per
    # character. Identical rows are written; only connection use is reduced.
    generated_items = [(name, url) for name, url, _ in gen_results if url]
    await _persist_character_urls_batch(generated_items)
    await _refresh_character_set_images_batch(
        session_id=session_id, items=generated_items
    )

    # Hitlist #2/#5 — record FAL image spend into the daily cents breaker by the
    # ACTUAL FAL call count (review item A), not character count. Cache hits made
    # no FAL call. Best-effort / fail-open: a metering fault must never affect
    # the image pipeline.
    total_fal_calls = sum(n for _, _, n in gen_results)
    # Blackbox #3 — cast thumbnails are the CHEAP schnell small-image path; meter
    # them with the schnell rate at the cast-thumb size (NOT the hero rate).
    await _record_image_spend(
        total_fal_calls, session_id, model=_img_model(), image_size=_img_size()
    )

    # Build the full result: every cached thumbnail (uncapped) + the generated
    # ones. Characters dropped by the cap are absent (no thumbnail), exactly as
    # if generation had failed — the FE already tolerates a missing image_url.
    out: dict[str, str | None] = dict(cached_urls)
    for name, url, _ in gen_results:
        out[name] = url
    return out


async def _generate_character_with_brand_fallback(
    *,
    name: str,
    source: str,
    style_suffix: str,
    negative_prompt: str,
    seed: int,
    image_size: dict[str, int] | None = None,
) -> tuple[str | None, int]:
    """Three-rung FAL ladder for branded characters.

    1. Literal: ``"<name> from <source>"`` (let FAL handle licensing).
    2. LLM-described physical prompt (no branded/licensed items).
    3. LLM-described stricter prompt (no proper nouns at all).

    Returns ``(first_successful_url_or_None, total_generate_calls)``. The call
    count accumulates ACROSS rungs and across each rung's null-retries — a
    branded character that exhausts rung 1 (1 + null-retries calls) before
    succeeding on rung 2 is billed for every FAL ``generate`` it actually made,
    so the daily cents breaker (Hitlist #2/#5) meters real FAL spend. The
    LLM-description calls (``describe_character_physically``) are NOT counted
    here — they are text-only ``llm_service`` calls metered separately by the
    LLM cost meter. Never raises.
    """
    # Lazy import keeps this hot-path module testable without litellm at
    # import time and avoids a circular import via app.services.llm_service.
    from app.services import character_describer  # local

    total_calls = 0

    # 2026-07-02 owner fix — classify the outcome ONCE (deterministic, no
    # LLM): an object outcome ("Frappuccino" from a branded drinks topic)
    # must be depicted as the item itself on every rung, and the describer
    # must describe the item, never a person.
    subject_kind = image_tools.infer_subject_kind(name=name, category=source)

    # Rung 1 — literal name + source.
    spec1 = image_tools.build_branded_attempt_prompt(
        name=name, source=source,
        style_suffix=style_suffix, negative_prompt=negative_prompt,
        subject_kind=subject_kind,
    )
    kwargs: dict[str, Any] = {
        "negative_prompt": spec1.get("negative_prompt"),
        "seed": seed,
    }
    if image_size:
        kwargs["image_size"] = image_size
    # P1 — each rung re-issues on a clean None before stepping down the ladder,
    # so a transient empty result doesn't prematurely abandon a higher-fidelity
    # rung. Still fail-open: the ladder ends at None when every rung is empty.
    url, n = await _generate_with_null_retry(spec1["prompt"], **kwargs)
    total_calls += n
    if url:
        return url, total_calls
    logger.info("image.brand.rung1.empty", name=name, source=source)

    # Rung 2 — LLM physical description (no branded items).
    desc = await character_describer.describe_character_physically(
        name=name, source=source, strict_level=0, subject_kind=subject_kind,
    )
    if desc:
        spec2 = image_tools.build_descriptive_attempt_prompt(
            description=desc,
            style_suffix=style_suffix, negative_prompt=negative_prompt,
            subject_kind=subject_kind,
        )
        url, n = await _generate_with_null_retry(spec2["prompt"], **kwargs)
        total_calls += n
        if url:
            return url, total_calls
        logger.info("image.brand.rung2.empty", name=name, source=source)

    # Rung 3 — stricter LLM description (no proper nouns at all).
    desc2 = await character_describer.describe_character_physically(
        name=name, source=source, strict_level=1, subject_kind=subject_kind,
    )
    if desc2:
        spec3 = image_tools.build_descriptive_attempt_prompt(
            description=desc2,
            style_suffix=style_suffix, negative_prompt=negative_prompt,
            subject_kind=subject_kind,
        )
        url, n = await _generate_with_null_retry(spec3["prompt"], **kwargs)
        total_calls += n
        if url:
            return url, total_calls
        logger.info("image.brand.rung3.empty", name=name, source=source)

    return None, total_calls


async def generate_synopsis_image(
    *,
    session_id: UUID,
    synopsis: Synopsis,
    category: str,
    analysis: dict[str, Any] | None = None,
) -> str | None:
    if not _enabled():
        return None
    try:
        spec = image_tools.build_synopsis_image_prompt(
            synopsis, category=category, analysis=analysis or {},
            style_suffix=_style_suffix(), negative_prompt=_negative_prompt(),
        )
    except Exception as e:
        logger.info("image.synopsis.prompt_build.fail", error=str(e))
        return None
    # Landscape hero card (frontend renders w-full h-64 object-cover); square
    # source would crop top/bottom. 16:9 matches the container aspect.
    syn_size = {"width": 1024, "height": 576}
    # Blackbox fix #1 — route the LARGE synopsis hero through FLUX dev (owner
    # choice) at the higher step count so it isn't soft. Falls back to the
    # global model when ``hero_model`` is unset.
    hero_model = _hero_model()
    url = await _client.generate(
        spec["prompt"], negative_prompt=spec.get("negative_prompt"),
        seed=image_tools.derive_seed(session_id, "__synopsis__"),
        image_size=syn_size,
        model=hero_model,
        num_inference_steps=_hero_steps() if hero_model else None,
    )
    # Hitlist #2 / blackbox #3 — the synopsis hero is a paid FAL call; record it
    # in the daily cents breaker with model+size-aware cost (best-effort /
    # fail-open).
    await _record_image_spend(
        1, session_id, model=hero_model or _img_model(), image_size=syn_size
    )
    if url:
        await _persist_synopsis_image(session_id=session_id, url=url)
    return url


async def generate_result_image(
    *,
    session_id: UUID,
    result: FinalResult,
    category: str,
    character_set: list[dict[str, Any]],
    analysis: dict[str, Any] | None = None,
) -> str | None:
    if not _enabled():
        return None
    try:
        spec = image_tools.build_result_image_prompt(
            result, category=category, character_set=character_set,
            style_suffix=_style_suffix(), negative_prompt=_negative_prompt(),
            analysis=analysis or {},
        )
    except Exception as e:
        logger.info("image.result.prompt_build.fail", error=str(e))
        return None
    # Square hero on the results page — the result card frames a single
    # subject (the matched character/outcome) and reads better as a
    # portrait. The FE renders this with `aspect-square` so source and
    # display containers agree and there is no cropping.
    res_size = {"width": 1024, "height": 1024}
    # Blackbox fix #1 — the matched-character result portrait is the second
    # LARGE hero; route it through FLUX dev at the higher step count too.
    hero_model = _hero_model()
    url = await _client.generate(
        spec["prompt"], negative_prompt=spec.get("negative_prompt"),
        seed=image_tools.derive_seed(session_id, "__result__"),
        image_size=res_size,
        model=hero_model,
        num_inference_steps=_hero_steps() if hero_model else None,
    )
    # Hitlist #2 / blackbox #3 — the winning-result hero is a paid FAL call;
    # record it with model+size-aware cost.
    await _record_image_spend(
        1, session_id, model=hero_model or _img_model(), image_size=res_size
    )
    # Owner finding #3 (2026-07-02, "bridge troll") — LIVE quality gate: judge
    # the rendered pixels; below-bar => ONE strengthened-prompt retry; persist
    # the best-of. Strictly best-effort: any fault inside the gate accepts the
    # first render unchanged (fail-open), and we are already on the background
    # path so the 202 flow is never blocked.
    if url:
        try:
            url = await _judge_and_maybe_retry_result_image(
                session_id=session_id,
                first_url=url,
                spec=spec,
                subject=(getattr(result, "title", "") or "").strip() or category,
                topic=category,
                hero_model=hero_model,
                res_size=res_size,
            )
        except Exception as e:  # noqa: BLE001 — gate is best-effort by contract
            logger.info(
                "image.result.judge.gate_error",
                session_id=str(session_id),
                error=str(e),
            )
    if url:
        await _persist_result_image(session_id=session_id, url=url)
    return url


# ---------------------------------------------------------------------------
# LIVE result-image quality gate (owner finding #3, 2026-07-02)
# ---------------------------------------------------------------------------
# The final-result hero is the single image the user shares — the one the
# owner called out ("the final profile image for bridge troll sucked"). It is
# generated LIVE per session (never precomputed), so the offline pack judges
# (`scripts/generate_images_for_packs.py`, `scripts/eval_image_quality.py`)
# never see it before the user does. This gate closes that hole: judge the
# actual rendered pixels right after FAL returns, retry ONCE with the judge's
# failure reason folded into the prompt when below bar, and persist whichever
# render scored best. Config: `images.result_judge_enabled` (ON by default),
# `images.result_judge_min_score`, `images.result_judge_model`,
# `images.result_judge_timeout_s`.


def _images_cfg() -> Any:
    return getattr(settings, "images", None)


def _result_judge_enabled() -> bool:
    cfg = _images_cfg()
    return bool(getattr(cfg, "result_judge_enabled", False)) if cfg else False


def _result_judge_min_score() -> int:
    cfg = _images_cfg()
    try:
        v = int(getattr(cfg, "result_judge_min_score", 7)) if cfg else 7
    except (TypeError, ValueError):
        v = 7
    return max(1, min(10, v))


def _result_judge_model() -> str:
    cfg = _images_cfg()
    m = getattr(cfg, "result_judge_model", None) if cfg else None
    return str(m) if m else "gemini/gemini-flash-latest"


def _result_judge_timeout_s() -> float:
    cfg = _images_cfg()
    try:
        v = float(getattr(cfg, "result_judge_timeout_s", 45.0)) if cfg else 45.0
    except (TypeError, ValueError):
        v = 45.0
    return v if v > 0 else 45.0


def _make_result_judge_client() -> Any:
    """Factory seam: the real LiteLLM vision client (monkeypatched in tests)."""
    from app.services.vision_judge import LiteLLMVisionClient  # noqa: PLC0415

    return LiteLLMVisionClient()


async def _judge_result_image(url: str, *, subject: str, topic: str) -> Any:
    """Fetch the rendered image and score it with the shared vision judge.

    Returns a ``VisionScore``, or ``None`` when the judge is unavailable for
    ANY reason (fetch failure, no key, timeout, provider error) — the caller
    treats ``None`` as "fail-open, accept what we have". Never raises.
    """
    try:
        from app.services.vision_judge import to_data_url  # noqa: PLC0415

        timeout_s = _result_judge_timeout_s()
        data_url = await to_data_url(image_url=url, timeout_s=int(timeout_s))
        if data_url is None:
            logger.info("image.result.judge.fetch_fail", url=url)
            return None
        client = _make_result_judge_client()
        return await asyncio.wait_for(
            client.score(
                model=_result_judge_model(),
                subject=subject,
                topic=topic,
                expected_description=None,
                image_data_url=data_url,
                timeout_s=int(timeout_s),
            ),
            timeout=timeout_s + 5,
        )
    except Exception as e:  # noqa: BLE001 — judge faults must never break the pipeline
        logger.info("image.result.judge.error", error=str(e))
        return None


def _result_judge_passes(score: Any, min_score: int) -> bool:
    """LIVE pass rule: min(fidelity, relevance) >= bar AND no hard blockers.

    Deliberately EXCLUDES the offline harness's ``style_ok`` axis — style is
    too subjective to spend a paid FAL retry on; the hard blockers
    (deformed_face / off_topic / placeholder_or_blank / text_garbage /
    ip_violation) are what made "bridge troll" suck.
    """
    return (
        min(int(score.fidelity), int(score.relevance)) >= int(min_score)
        and not score.blocking_reasons
    )


def _score_rank(score: Any) -> tuple[int, int, int]:
    """Best-of ordering for two judged renders: blocker-free first, then the
    weaker axis, then the sum (ties keep the FIRST render — stable UX)."""
    return (
        0 if score.blocking_reasons else 1,
        min(int(score.fidelity), int(score.relevance)),
        int(score.fidelity) + int(score.relevance),
    )


def _strengthen_result_prompt(base_prompt: str, *, subject: str, score: Any) -> str:
    """Fold the judge's failure reason into the retry prompt.

    The retry is the ONE extra shot we pay for, so it must not re-roll the
    same dice: the corrective clause names what failed (the judge's blocking
    reasons and/or notes) and re-asserts the subject explicitly.
    """
    reasons = ", ".join(str(b) for b in (score.blocking_reasons or []))
    notes = (getattr(score, "notes", "") or "").strip()
    why = "; ".join(p for p in (reasons, notes) if p) or "low fidelity/relevance"
    return (
        f"{base_prompt}. IMPORTANT correction — a previous render of this was "
        f"rejected by quality review ({why}). Render {subject} clearly and "
        "unmistakably: one coherent, well-formed subject, correct anatomy, "
        "clean composition, no text, no watermark, no extra limbs or faces"
    )


async def _judge_and_maybe_retry_result_image(
    *,
    session_id: UUID,
    first_url: str,
    spec: dict[str, str],
    subject: str,
    topic: str,
    hero_model: str | None,
    res_size: dict[str, int],
) -> str:
    """Judge the first render; below-bar => ONE strengthened retry; return the
    best-of. Always returns a usable URL (fail-open to ``first_url``)."""
    if not _result_judge_enabled():
        return first_url
    min_score = _result_judge_min_score()

    score1 = await _judge_result_image(first_url, subject=subject, topic=topic)
    if score1 is None:
        # Judge unavailable — fail-open: accept the first render, spend nothing.
        logger.info(
            "image.result.judge.unavailable_fail_open", session_id=str(session_id)
        )
        return first_url
    passed1 = _result_judge_passes(score1, min_score)
    logger.info(
        "image.result.judge.verdict",
        session_id=str(session_id),
        attempt=1,
        fidelity=score1.fidelity,
        relevance=score1.relevance,
        blocking=list(score1.blocking_reasons),
        notes=score1.notes,
        passed=passed1,
        min_score=min_score,
    )
    if passed1:
        return first_url

    # ONE retry with the strengthened prompt (a paid FAL call, metered like
    # the first). A different seed on purpose — the first seed produced the
    # below-bar render.
    retry_prompt = _strengthen_result_prompt(spec["prompt"], subject=subject, score=score1)
    try:
        retry_url = await _client.generate(
            retry_prompt,
            negative_prompt=spec.get("negative_prompt"),
            seed=image_tools.derive_seed(session_id, "__result_retry__"),
            image_size=res_size,
            model=hero_model,
            num_inference_steps=_hero_steps() if hero_model else None,
        )
    except Exception as e:  # noqa: BLE001 — retry is best-effort
        logger.info(
            "image.result.judge.retry_generate_error",
            session_id=str(session_id),
            error=str(e),
        )
        return first_url
    await _record_image_spend(
        1, session_id, model=hero_model or _img_model(), image_size=res_size
    )
    if not retry_url:
        logger.info("image.result.judge.retry_empty", session_id=str(session_id))
        return first_url

    score2 = await _judge_result_image(retry_url, subject=subject, topic=topic)
    if score2 is None:
        # First render is KNOWN below-bar; the retry was generated against the
        # judge's specific objections. With no second verdict available, the
        # corrected render is the better bet.
        logger.info(
            "image.result.judge.retry_unjudged_accepted",
            session_id=str(session_id),
        )
        return retry_url
    passed2 = _result_judge_passes(score2, min_score)
    best_is_retry = _score_rank(score2) > _score_rank(score1)
    logger.info(
        "image.result.judge.verdict",
        session_id=str(session_id),
        attempt=2,
        fidelity=score2.fidelity,
        relevance=score2.relevance,
        blocking=list(score2.blocking_reasons),
        notes=score2.notes,
        passed=passed2,
        min_score=min_score,
        accepted="retry" if best_is_retry else "first",
    )
    return retry_url if best_is_retry else first_url


async def _record_image_spend(
    n_images: int,
    session_id: UUID,
    *,
    model: str | None = None,
    image_size: dict[str, int] | None = None,
) -> None:
    """Record ``n_images`` FAL image calls into the daily cents breaker. Best-
    effort / fail-open — a metering fault must never affect the image pipeline.

    Blackbox #3 — the per-image cost is now model+size-aware: a 256px schnell
    thumb (~$0.0002) and a 1024px FLUX-dev hero (~$0.025) draw the breaker down
    by their TRUE spend rather than a flat $0.011 constant."""
    if n_images <= 0:
        return
    try:
        from app.services import cost_meter
        await cost_meter.record_fal_image_cost(
            n_images, model=model, image_size=image_size
        )
    except Exception:
        logger.debug("image.cost_record.fail", session_id=str(session_id))
