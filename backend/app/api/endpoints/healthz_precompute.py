"""§21 Phase 10 — operator-only `/healthz/precompute` (`AC-PRECOMP-OBJ-3`).

Surfaces published-pack count, 24-h hit/miss rates, and the top missed
topic slugs to operators for seeding-decision support. Gated by the same
operator authentication used by `/admin/precompute/*`.
"""

from __future__ import annotations

from typing import Annotated, Any

from fastapi import APIRouter, Depends
from pydantic import BaseModel
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.dependencies import (
    OperatorPrincipal,
    get_db_session,
    get_redis_client,
    require_operator,
)
from app.models.db import TopicPack
from app.services.precompute import telemetry

router = APIRouter(prefix="/healthz", tags=["healthz", "precompute"])


class PrecomputeHealth(BaseModel):
    packs_published: int
    hits_24h: int
    misses_24h: int
    hit_rate_24h: float
    miss_rate_24h: float
    top_misses_24h: list[dict]


@router.get("/precompute", response_model=PrecomputeHealth)
async def precompute_health(
    db: Annotated[AsyncSession, Depends(get_db_session)],
    redis: Annotated[Any, Depends(get_redis_client)],
    _: Annotated[OperatorPrincipal, Depends(require_operator)],
) -> PrecomputeHealth:
    n_packs = (
        await db.execute(
            select(func.count(TopicPack.id)).where(TopicPack.status == "published")
        )
    ).scalar_one()
    snap = await telemetry.get_24h_snapshot(redis)
    return PrecomputeHealth(
        packs_published=int(n_packs or 0),
        hits_24h=snap["hits_24h"],
        misses_24h=snap["misses_24h"],
        hit_rate_24h=snap["hit_rate_24h"],
        miss_rate_24h=snap["miss_rate_24h"],
        top_misses_24h=snap["top_misses_24h"],
    )
