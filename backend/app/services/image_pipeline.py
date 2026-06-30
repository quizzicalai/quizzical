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
from sqlalchemy import text

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


async def _generate_with_null_retry(prompt: str, **kwargs: Any) -> str | None:
    """Call ``_client.generate`` and, on a None result, re-issue the same
    prompt up to ``_null_retry_attempts()`` more times.

    A None return from ``_client.generate`` means FAL responded but yielded no
    usable URL (exhausted internal retries / NSFW redaction / empty result).
    Re-issuing the identical prompt is worthwhile because the underlying causes
    are frequently nondeterministic (transient upstream load, sampler-dependent
    safety trips). Never raises — any exception from ``generate`` propagates the
    same way it did before this wrapper existed; callers already guard the
    pipeline against that.
    """
    url = await _client.generate(prompt, **kwargs)
    if url:
        return url
    attempts = _null_retry_attempts()
    for i in range(attempts):
        logger.info(
            "image.null_retry",
            attempt=i + 1, of=attempts, prompt_prefix=(prompt or "")[:48],
        )
        url = await _client.generate(prompt, **kwargs)
        if url:
            return url
    return None


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


async def _url_alive(url: str, *, timeout_s: float = 3.0) -> bool:
    """Cheap HEAD probe. Returns True iff the URL responds 2xx/3xx.

    Used to gate FAL regeneration when the DB already has an image URL --
    avoids spending FAL credits regenerating an asset that is still served
    by the upstream CDN.
    """
    if not url:
        return False
    try:
        import httpx  # local import keeps cold-start light
        async with httpx.AsyncClient(timeout=timeout_s, follow_redirects=True) as client:
            resp = await client.head(url)
            return 200 <= resp.status_code < 400
    except Exception as e:
        logger.info("image.head_probe.fail", url=url, error=str(e))
        return False


async def generate_character_images(
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

    # Hitlist #5 — cap the PAID fan-out. Canonical archetype sets run 16–26
    # unique characters; uncapped that is ~$0.18–0.30 of FAL spend for 56px cast
    # thumbnails. We slice the cast to ``max_character_images`` BEFORE the gather.
    # The list is already ordered (the agent emits the primary/most-relevant
    # archetypes first), so the leading slice keeps the cast that matters most.
    # The synopsis-hero and winning-result hero images are SEPARATE pipeline
    # calls (generate_synopsis_image / generate_result_image) and are never
    # affected by this cap. Cache-hit characters within the slice still skip the
    # paid call below, so the cap bounds the worst case, not the common one.
    cap = _max_character_images()
    if cap > 0 and len(unique) > cap:
        logger.info(
            "image.character.fanout_capped",
            session_id=str(session_id),
            requested=len(unique),
            cap=cap,
        )
        unique = unique[:cap]

    sem = asyncio.Semaphore(_get_concurrency())
    style = _style_suffix()
    neg = _negative_prompt()

    # Branded topics (TV/film/book/game IP) route through the multi-rung
    # fallback ladder so the character actually looks like themselves. For
    # non-branded archetype topics ("Greek God", "Pokémon Type") the source
    # name adds no information and the legacy descriptive prompt is fine.
    is_branded = bool((analysis or {}).get("is_media", False))
    source_name = (category or "").strip()

    async def _one(profile: CharacterProfile) -> tuple[str, str | None, bool]:
        # Reuse-cache: if the DB already has a live URL for this character,
        # ship it directly. This is the hot path for precomputed packs and
        # for any returning user on a topic we've generated before. A cache hit
        # makes NO paid FAL call, so it is not counted toward FAL spend.
        existing = await _get_character_url(profile.name)
        if existing and await _url_alive(existing):
            # Make sure this session's character_set JSONB snapshot carries
            # the URL even though we didn't regenerate.
            await _refresh_character_set_image(
                session_id=session_id, name=profile.name, url=existing
            )
            logger.info(
                "image.character.cache_hit",
                name=profile.name, session_id=str(session_id),
            )
            return profile.name, existing, False

        seed = image_tools.derive_seed(session_id, profile.name)
        async with sem:
            if is_branded:
                url = await _generate_character_with_brand_fallback(
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
                    return profile.name, None, False
                # P1 — re-issue on a clean None (FAL gave no usable image) before
                # persisting a permanent null. Fail-open: still None after budget.
                url = await _generate_with_null_retry(
                    spec["prompt"], negative_prompt=spec.get("negative_prompt"),
                    seed=seed,
                )
        if url:
            await _persist_character_url(name=profile.name, url=url)
            await _refresh_character_set_image(
                session_id=session_id, name=profile.name, url=url
            )
        # Counted as paid whenever we issued FAL calls (i.e. not a cache hit),
        # regardless of whether they produced a usable image — FAL bills the call.
        return profile.name, url, True

    results = await asyncio.gather(*[_one(c) for c in unique], return_exceptions=False)

    # Hitlist #2/#5 — record FAL image spend into the daily cents breaker. Only
    # genuinely-paid generations count (cache hits made no FAL call). Best-effort
    # / fail-open: a metering fault must never affect the image pipeline.
    paid = sum(1 for _, _, was_paid in results if was_paid)
    await _record_image_spend(paid, session_id)

    return {name: url for name, url, _ in results}


async def _generate_character_with_brand_fallback(
    *,
    name: str,
    source: str,
    style_suffix: str,
    negative_prompt: str,
    seed: int,
    image_size: dict[str, int] | None = None,
) -> str | None:
    """Three-rung FAL ladder for branded characters.

    1. Literal: ``"<name> from <source>"`` (let FAL handle licensing).
    2. LLM-described physical prompt (no branded/licensed items).
    3. LLM-described stricter prompt (no proper nouns at all).

    Returns the first successful https URL, or ``None`` if every rung
    returned no image. Never raises.
    """
    # Lazy import keeps this hot-path module testable without litellm at
    # import time and avoids a circular import via app.services.llm_service.
    from app.services import character_describer  # local

    # Rung 1 — literal name + source.
    spec1 = image_tools.build_branded_attempt_prompt(
        name=name, source=source,
        style_suffix=style_suffix, negative_prompt=negative_prompt,
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
    url = await _generate_with_null_retry(spec1["prompt"], **kwargs)
    if url:
        return url
    logger.info("image.brand.rung1.empty", name=name, source=source)

    # Rung 2 — LLM physical description (no branded items).
    desc = await character_describer.describe_character_physically(
        name=name, source=source, strict_level=0,
    )
    if desc:
        spec2 = image_tools.build_descriptive_attempt_prompt(
            description=desc,
            style_suffix=style_suffix, negative_prompt=negative_prompt,
        )
        url = await _generate_with_null_retry(spec2["prompt"], **kwargs)
        if url:
            return url
        logger.info("image.brand.rung2.empty", name=name, source=source)

    # Rung 3 — stricter LLM description (no proper nouns at all).
    desc2 = await character_describer.describe_character_physically(
        name=name, source=source, strict_level=1,
    )
    if desc2:
        spec3 = image_tools.build_descriptive_attempt_prompt(
            description=desc2,
            style_suffix=style_suffix, negative_prompt=negative_prompt,
        )
        url = await _generate_with_null_retry(spec3["prompt"], **kwargs)
        if url:
            return url
        logger.info("image.brand.rung3.empty", name=name, source=source)

    return None


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
    url = await _client.generate(
        spec["prompt"], negative_prompt=spec.get("negative_prompt"),
        seed=image_tools.derive_seed(session_id, "__synopsis__"),
        # Landscape hero card (frontend renders w-full h-64 object-cover); square
        # source would crop top/bottom. 16:9 matches the container aspect.
        image_size={"width": 1024, "height": 576},
    )
    # Hitlist #2 — the synopsis hero is a paid FAL call; record it in the daily
    # cents breaker (best-effort / fail-open).
    await _record_image_spend(1, session_id)
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
    url = await _client.generate(
        spec["prompt"], negative_prompt=spec.get("negative_prompt"),
        seed=image_tools.derive_seed(session_id, "__result__"),
        # Square hero on the results page — the result card frames a single
        # subject (the matched character/outcome) and reads better as a
        # portrait. The FE renders this with `aspect-square` so source and
        # display containers agree and there is no cropping.
        image_size={"width": 1024, "height": 1024},
    )
    # Hitlist #2 — the winning-result hero is a paid FAL call; record it.
    await _record_image_spend(1, session_id)
    if url:
        await _persist_result_image(session_id=session_id, url=url)
    return url


async def _record_image_spend(n_images: int, session_id: UUID) -> None:
    """Record ``n_images`` FAL image calls into the daily cents breaker. Best-
    effort / fail-open — a metering fault must never affect the image pipeline."""
    if n_images <= 0:
        return
    try:
        from app.services import cost_meter
        await cost_meter.record_fal_image_cost(n_images)
    except Exception:
        logger.debug("image.cost_record.fail", session_id=str(session_id))
