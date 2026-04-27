# app/services/image_pipeline.py
"""Background-task orchestration for FAL image generation (§7.8.5).

All public functions are intended to be scheduled via FastAPI ``BackgroundTasks``
**after** the primary persistence call has returned. They never block the
user-visible response and never raise to the caller.

Persistence model:
- ``characters.image_url`` is updated only when its current value is NULL
  (we never overwrite a curated image).
- ``session_history.character_set`` is a JSONB snapshot; we refresh the
  ``image_url`` of every element whose ``name`` matches.
- ``session_history.category_synopsis`` and ``session_history.final_result``
  are JSONB; we set ``image_url`` via ``jsonb_set``.
"""
from __future__ import annotations

import asyncio
from contextlib import asynccontextmanager
from typing import Any, Dict, List, Optional
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
            await session.execute(
                text(
                    "UPDATE characters SET image_url = :url, last_updated_at = now() "
                    "WHERE name = :name AND image_url IS NULL"
                ),
                {"url": url, "name": name},
            )
            await session.commit()
        except Exception as e:
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
                                 THEN jsonb_set(elem, '{image_url}', to_jsonb(:url::text))
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
                                  '{image_url}', to_jsonb(:url::text), true),
                        last_updated_at = now()
                    WHERE session_id = :sid
                    """
                ),
                {"sid": str(session_id), "url": url},
            )
            await session.commit()
        except Exception as e:
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
                        ELSE jsonb_set(final_result, '{image_url}', to_jsonb(:url::text), true)
                    END,
                    last_updated_at = now()
                    WHERE session_id = :sid
                    """
                ),
                {"sid": str(session_id), "url": url},
            )
            await session.commit()
        except Exception as e:
            logger.info("image.persist.result.fail",
                        session_id=str(session_id), error=str(e))


# ---------------------------------------------------------------------------
# Public orchestration
# ---------------------------------------------------------------------------

async def generate_character_images(
    *,
    session_id: UUID,
    characters: List[CharacterProfile],
    category: str,
    analysis: Optional[Dict[str, Any]] = None,
) -> Dict[str, Optional[str]]:
    """Fan out FAL calls (bounded by semaphore). Returns ``{name: Optional[url]}``."""
    if not _enabled() or not characters:
        return {}

    # Dedup by name preserving order.
    seen: set[str] = set()
    unique: List[CharacterProfile] = []
    for c in characters:
        n = getattr(c, "name", None)
        if n and n not in seen:
            seen.add(n)
            unique.append(c)

    sem = asyncio.Semaphore(_get_concurrency())
    style = _style_suffix()
    neg = _negative_prompt()

    async def _one(profile: CharacterProfile) -> tuple[str, Optional[str]]:
        try:
            spec = image_tools.build_character_image_prompt(
                profile, category=category, analysis=analysis or {},
                style_suffix=style, negative_prompt=neg,
            )
        except Exception as e:
            logger.info("image.character.prompt_build.fail",
                        name=profile.name, error=str(e))
            return profile.name, None
        async with sem:
            url = await _client.generate(
                spec["prompt"], negative_prompt=spec.get("negative_prompt"),
            )
        if url:
            await _persist_character_url(name=profile.name, url=url)
            await _refresh_character_set_image(
                session_id=session_id, name=profile.name, url=url
            )
        return profile.name, url

    results = await asyncio.gather(*[_one(c) for c in unique], return_exceptions=False)
    return {name: url for name, url in results}


async def generate_synopsis_image(
    *,
    session_id: UUID,
    synopsis: Synopsis,
    category: str,
    analysis: Optional[Dict[str, Any]] = None,
) -> Optional[str]:
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
    )
    if url:
        await _persist_synopsis_image(session_id=session_id, url=url)
    return url


async def generate_result_image(
    *,
    session_id: UUID,
    result: FinalResult,
    category: str,
    character_set: List[Dict[str, Any]],
    analysis: Optional[Dict[str, Any]] = None,
) -> Optional[str]:
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
    )
    if url:
        await _persist_result_image(session_id=session_id, url=url)
    return url
