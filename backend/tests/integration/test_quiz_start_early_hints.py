"""§21 Phase 4 — `/quiz/start` HIT path emits 103-Early-Hints `Link` header.

When the precompute resolver finds a published pack for the incoming
topic, the cache layer hydrates a `ResolvedPack` and we attach a
`Link: <uri>; rel=preload; as=image` header so the client can warm
media fetches in parallel with synopsis rendering. (`AC-PRECOMP-PERF-3`).

Starlette doesn't support a real 103 informational response from a sync
handler today, so we attach the same `Link` header to the final 201
response — semantically identical for the browser's preload pipeline.

These tests pin two contracts:

- HIT with non-empty `storage_uris` → `Link` header set, with
  `rel=preload` + `as=image` directives.
- HIT with empty `storage_uris` → no `Link` header (Universal-G5: don't
  add headers users wouldn't otherwise see).
- OFF (default) → no `Link` header, no behavioural change.
"""

from __future__ import annotations

import uuid

import pytest

from app.core.config import settings
from app.main import API_PREFIX
from app.services.precompute import cache as pack_cache
from app.services.precompute.cache import ResolvedPack
from tests.helpers.sample_payloads import start_quiz_payload

API = API_PREFIX.rstrip("/")


def _resolved_pack_with_uris(uris: tuple[str, ...]) -> ResolvedPack:
    return ResolvedPack(
        topic_id=str(uuid.uuid4()),
        pack_id=str(uuid.uuid4()),
        version=2,
        synopsis_id=str(uuid.uuid4()),
        character_set_id=str(uuid.uuid4()),
        baseline_question_set_id=str(uuid.uuid4()),
        storage_uris=uris,
    )


class _StubResolution:
    def __init__(self, topic_id: str, pack_id: str) -> None:
        self.topic_id = topic_id
        self.pack_id = pack_id
        self.via = "exact"
        self.similarity = 1.0


@pytest.mark.anyio
@pytest.mark.usefixtures(
    "use_fake_agent_graph",
    "turnstile_bypass",
    "override_redis_dep",
    "override_db_dependency",
)
async def test_hit_path_emits_link_preload_header(
    client,
    monkeypatch,
):
    """`AC-PRECOMP-PERF-3` — HIT with media URIs yields a `Link` header."""

    monkeypatch.setattr(settings.precompute, "enabled", True)

    pack = _resolved_pack_with_uris(("/api/v1/media/x.png", "/api/v1/media/y.png"))

    # Force the lookup to HIT.
    from app.services.precompute import lookup as lookup_mod

    async def _fake_resolve(self, _category):  # noqa: ANN001
        return _StubResolution(pack.topic_id, pack.pack_id)

    monkeypatch.setattr(lookup_mod.PrecomputeLookup, "resolve_topic", _fake_resolve)

    # Force `cache.get_or_fill` to return our stub pack (skip DB hydrate).
    async def _fake_get_or_fill(_redis, _topic_id, _fill_fn, **_kw):
        return pack

    monkeypatch.setattr(pack_cache, "get_or_fill", _fake_get_or_fill)

    payload = start_quiz_payload(topic="Cats")
    resp = await client.post(f"{API}/quiz/start?_a=test&_k=test", json=payload)

    assert resp.status_code == 201, resp.text
    link = resp.headers.get("link") or resp.headers.get("Link")
    assert link is not None, "expected Link header on HIT"
    assert "rel=preload" in link
    assert "as=image" in link
    assert "/api/v1/media/x.png" in link
    assert "/api/v1/media/y.png" in link


@pytest.mark.anyio
@pytest.mark.usefixtures(
    "use_fake_agent_graph",
    "turnstile_bypass",
    "override_redis_dep",
    "override_db_dependency",
)
async def test_hit_path_without_uris_omits_link_header(
    client,
    monkeypatch,
):
    """No URIs → no `Link` header (don't pollute the response)."""

    monkeypatch.setattr(settings.precompute, "enabled", True)
    pack = _resolved_pack_with_uris(())

    from app.services.precompute import lookup as lookup_mod

    async def _fake_resolve(self, _category):  # noqa: ANN001
        return _StubResolution(pack.topic_id, pack.pack_id)

    monkeypatch.setattr(lookup_mod.PrecomputeLookup, "resolve_topic", _fake_resolve)

    async def _fake_get_or_fill(_redis, _topic_id, _fill_fn, **_kw):
        return pack

    monkeypatch.setattr(pack_cache, "get_or_fill", _fake_get_or_fill)

    payload = start_quiz_payload(topic="Cats")
    resp = await client.post(f"{API}/quiz/start?_a=test&_k=test", json=payload)

    assert resp.status_code == 201, resp.text
    assert resp.headers.get("link") is None
    assert resp.headers.get("Link") is None


@pytest.mark.anyio
@pytest.mark.usefixtures(
    "use_fake_agent_graph",
    "turnstile_bypass",
    "override_redis_dep",
    "override_db_dependency",
)
async def test_off_path_emits_no_link_header(client):
    """Universal-G5: with the flag OFF, /quiz/start is byte-for-byte
    unchanged — no `Link` header, no precompute side effects."""

    assert settings.precompute.enabled is False

    payload = start_quiz_payload(topic="Cats")
    resp = await client.post(f"{API}/quiz/start?_a=test&_k=test", json=payload)

    assert resp.status_code == 201, resp.text
    assert resp.headers.get("link") is None
    assert resp.headers.get("Link") is None
