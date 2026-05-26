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
    """Return the current ``characters.image_url`` for ``name``, or None."""
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

    sem = asyncio.Semaphore(_get_concurrency())
    style = _style_suffix()
    neg = _negative_prompt()

    # Branded topics (TV/film/book/game IP) route through the multi-rung
    # fallback ladder so the character actually looks like themselves. For
    # non-branded archetype topics ("Greek God", "Pokémon Type") the source
    # name adds no information and the legacy descriptive prompt is fine.
    is_branded = bool((analysis or {}).get("is_media", False))
    source_name = (category or "").strip()

    async def _one(profile: CharacterProfile) -> tuple[str, str | None]:
        # Reuse-cache: if the DB already has a live URL for this character,
        # ship it directly. This is the hot path for precomputed packs and
        # for any returning user on a topic we've generated before.
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
            return profile.name, existing

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
                    return profile.name, None
                url = await _client.generate(
                    spec["prompt"], negative_prompt=spec.get("negative_prompt"),
                    seed=seed,
                )
        if url:
            await _persist_character_url(name=profile.name, url=url)
            await _refresh_character_set_image(
                session_id=session_id, name=profile.name, url=url
            )
        return profile.name, url

    results = await asyncio.gather(*[_one(c) for c in unique], return_exceptions=False)
    return dict(results)


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
    url = await _client.generate(spec1["prompt"], **kwargs)
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
        url = await _client.generate(spec2["prompt"], **kwargs)
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
        url = await _client.generate(spec3["prompt"], **kwargs)
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
    if url:
        await _persist_result_image(session_id=session_id, url=url)
    return url
