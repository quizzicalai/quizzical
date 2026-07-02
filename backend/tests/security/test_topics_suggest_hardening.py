"""§21 Phase 6 — `/topics/suggest` hardening (`AC-PRECOMP-SEC-3`)."""

from __future__ import annotations

import uuid

import pytest

from app.main import API_PREFIX
from app.models.db import Topic

API = API_PREFIX.rstrip("/")
URL = f"{API}/topics/suggest"


async def _seed_topics(session, n: int, prefix: str = "Cat"):
    for i in range(n):
        session.add(
            Topic(
                id=uuid.uuid4(),
                slug=f"{prefix.lower()}-{i}-{uuid.uuid4().hex[:6]}",
                display_name=f"{prefix} {i}",
            )
        )
    await session.commit()


@pytest.mark.anyio
@pytest.mark.usefixtures("override_redis_dep", "override_db_dependency")
async def test_query_below_2_chars_rejected(async_client):
    resp = await async_client.get(URL, params={"q": "a"})
    assert resp.status_code == 422


@pytest.mark.anyio
@pytest.mark.usefixtures("override_redis_dep", "override_db_dependency")
async def test_query_whitespace_only_rejected(async_client):
    resp = await async_client.get(URL, params={"q": "   "})
    assert resp.status_code == 422


@pytest.mark.anyio
@pytest.mark.usefixtures("override_redis_dep", "override_db_dependency")
async def test_max_8_results(async_client, sqlite_db_session):
    await _seed_topics(sqlite_db_session, 20, prefix="Cat")
    resp = await async_client.get(URL, params={"q": "Cat"})
    assert resp.status_code == 200
    body = resp.json()
    assert len(body["results"]) <= 8


@pytest.mark.anyio
@pytest.mark.usefixtures("override_redis_dep", "override_db_dependency")
async def test_60_per_minute_rate_limit(async_client, monkeypatch):
    """When the bucket is exhausted, /topics/suggest returns 429 with
    `Retry-After`. Capacity is wired to 60/minute per `AC-PRECOMP-SEC-3`."""

    from app.api.endpoints import topics as topics_mod
    from app.security.rate_limit import RateLimitResult

    # Force the limiter to throttle deterministically.
    async def _throttle(self, key, *, now_s=None):
        return RateLimitResult(allowed=False, remaining=0, retry_after_s=42)

    monkeypatch.setattr(topics_mod.RateLimiter, "check", _throttle)
    # Sanity: the constant matches the AC.
    assert topics_mod.RATE_LIMIT_PER_MINUTE == 60

    resp = await async_client.get(URL, params={"q": "ab"})
    assert resp.status_code == 429
    assert resp.headers.get("retry-after") == "42"


# ---------------------------------------------------------------------------
# Deep-review #23 — LIKE-metacharacter escaping. Raw `%`/`_` in `q` must be
# treated LITERALLY, not as SQL wildcards. Before the fix, `q="%"` interpolated
# straight into the ILIKE pattern and matched EVERY row (a full-table scan on a
# public per-keystroke endpoint). After escaping, `%`/`_` only match the literal
# character, so a wildcard query returns just the rows that literally contain it.
# ---------------------------------------------------------------------------
@pytest.mark.anyio
@pytest.mark.usefixtures("override_redis_dep", "override_db_dependency")
async def test_percent_wildcard_is_escaped_not_matching_all_rows(
    async_client, sqlite_db_session
):
    # 12 rows with NO '%' in their names. A wildcard `q` must NOT match them all.
    await _seed_topics(sqlite_db_session, 12, prefix="Zeta")
    resp = await async_client.get(URL, params={"q": "%%"})  # >= 2 chars, both '%'
    assert resp.status_code == 200
    body = resp.json()
    # '%' is escaped to a literal; none of the "Zeta N" names contain '%', so the
    # prefix match "%%%" (literal %% then wildcard) returns nothing. The bug would
    # have returned up to 8 (the cap) unrelated rows.
    assert body["results"] == []


@pytest.mark.anyio
@pytest.mark.usefixtures("override_redis_dep", "override_db_dependency")
async def test_underscore_wildcard_is_escaped(async_client, sqlite_db_session):
    # Seed a name that literally contains an underscore, plus decoys without one.
    await _seed_topics(sqlite_db_session, 5, prefix="Plain")
    sqlite_db_session.add(
        Topic(
            id=uuid.uuid4(),
            slug=f"under-score-{uuid.uuid4().hex[:6]}",
            display_name="A_B literal underscore",
        )
    )
    await sqlite_db_session.commit()

    # `_` as a wildcard would match "AXB..."; escaped, it only matches a literal
    # underscore. Query the literal-underscore prefix and expect exactly that row.
    resp = await async_client.get(URL, params={"q": "A_B"})
    assert resp.status_code == 200
    names = [r["display_name"] for r in resp.json()["results"]]
    assert names == ["A_B literal underscore"]


@pytest.mark.anyio
@pytest.mark.usefixtures("override_redis_dep", "override_db_dependency")
async def test_literal_prefix_still_matches_normally(async_client, sqlite_db_session):
    """Regression guard: the escaping must not break ordinary prefix matching."""
    await _seed_topics(sqlite_db_session, 3, prefix="Alpha")
    resp = await async_client.get(URL, params={"q": "Alp"})
    assert resp.status_code == 200
    names = [r["display_name"] for r in resp.json()["results"]]
    assert names and all(n.startswith("Alpha") for n in names)
