"""§21 Phase 9 — `POST /admin/precompute/import` operator endpoint."""

from __future__ import annotations

import json
import uuid

import pytest
from sqlalchemy import select

from app.core.config import settings
from app.main import API_PREFIX
from app.models.db import Topic, TopicAlias, TopicPack
from scripts.import_packs import sign_archive

API = API_PREFIX.rstrip("/")
URL = f"{API}/admin/precompute/import"

SECRET = "import-endpoint-test-" + "x" * 48


def _make_archive(slug: str = "ep-test") -> bytes:
    doc = {
        "packs": [
            {
                "topic": {"slug": slug, "display_name": slug.replace("-", " ").title()},
                "aliases": [slug.replace("-", " ")],
                "synopsis": {
                    "content_hash": "syn-" + uuid.uuid4().hex,
                    "body": {"text": "endpoint test synopsis"},
                },
                "character_set": {
                    "composition_hash": "cs-" + uuid.uuid4().hex,
                    "composition": {"character_ids": []},
                },
                "baseline_question_set": {
                    "composition_hash": "bqs-" + uuid.uuid4().hex,
                    "composition": {"question_ids": []},
                },
                "version": 1,
                "built_in_env": "starter",
            }
        ]
    }
    return json.dumps(doc).encode("utf-8")


@pytest.fixture()
def operator_token(monkeypatch):
    tok = "z" * 48
    monkeypatch.setenv("OPERATOR_TOKEN", tok)
    monkeypatch.setenv("FLAG_HMAC_SECRET", "y" * 48)
    monkeypatch.setenv("PRECOMPUTE_HMAC_SECRET", SECRET)
    monkeypatch.setattr(settings.app, "environment", "development", raising=False)
    return tok


@pytest.mark.anyio
@pytest.mark.usefixtures("override_redis_dep", "override_db_dependency")
async def test_import_endpoint_rejects_missing_signature(
    async_client, operator_token
):
    payload = _make_archive("nosig")
    resp = await async_client.post(
        URL,
        content=payload,
        headers={
            "Authorization": f"Bearer {operator_token}",
            "Content-Type": "application/octet-stream",
        },
    )
    assert resp.status_code == 401
    assert "X-Archive-Signature" in resp.json()["detail"]


@pytest.mark.anyio
@pytest.mark.usefixtures("override_redis_dep", "override_db_dependency")
async def test_import_endpoint_rejects_bad_signature(
    async_client, operator_token
):
    payload = _make_archive("badsig")
    resp = await async_client.post(
        URL,
        content=payload,
        headers={
            "Authorization": f"Bearer {operator_token}",
            "X-Archive-Signature": "0" * 64,
            "Content-Type": "application/octet-stream",
        },
    )
    assert resp.status_code == 401


@pytest.mark.anyio
@pytest.mark.usefixtures("override_redis_dep", "override_db_dependency")
async def test_import_endpoint_requires_operator_bearer(async_client, monkeypatch):
    monkeypatch.setenv("OPERATOR_TOKEN", "z" * 48)
    monkeypatch.setenv("PRECOMPUTE_HMAC_SECRET", SECRET)
    monkeypatch.setattr(settings.app, "environment", "development", raising=False)
    payload = _make_archive("noauth")
    sig = sign_archive(payload, secret=SECRET)
    resp = await async_client.post(
        URL,
        content=payload,
        headers={
            "X-Archive-Signature": sig,
            "Content-Type": "application/octet-stream",
        },
    )
    assert resp.status_code in (401, 403)


@pytest.mark.anyio
@pytest.mark.usefixtures("override_redis_dep", "override_db_dependency")
async def test_import_endpoint_inserts_pack(
    async_client, sqlite_db_session, operator_token
):
    payload = _make_archive("happy")
    sig = sign_archive(payload, secret=SECRET)
    resp = await async_client.post(
        URL,
        content=payload,
        headers={
            "Authorization": f"Bearer {operator_token}",
            "X-Archive-Signature": sig,
            "Content-Type": "application/octet-stream",
        },
    )
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["packs_inserted"] == 1
    assert body["skipped_db_not_empty"] == 0

    topic = (
        await sqlite_db_session.execute(select(Topic).where(Topic.slug == "happy"))
    ).scalar_one()
    pack = (
        await sqlite_db_session.execute(select(TopicPack).where(TopicPack.topic_id == topic.id))
    ).scalar_one()
    assert topic.current_pack_id == pack.id

    aliases = (
        await sqlite_db_session.execute(
            select(TopicAlias).where(TopicAlias.topic_id == topic.id)
        )
    ).scalars().all()
    assert len(aliases) == 1


@pytest.mark.anyio
@pytest.mark.usefixtures("override_redis_dep", "override_db_dependency")
async def test_import_endpoint_skips_when_db_not_empty(
    async_client, sqlite_db_session, operator_token
):
    payload = _make_archive("first")
    sig = sign_archive(payload, secret=SECRET)
    r1 = await async_client.post(
        URL,
        content=payload,
        headers={
            "Authorization": f"Bearer {operator_token}",
            "X-Archive-Signature": sig,
            "Content-Type": "application/octet-stream",
        },
    )
    assert r1.status_code == 200
    assert r1.json()["packs_inserted"] == 1

    payload2 = _make_archive("second")
    sig2 = sign_archive(payload2, secret=SECRET)
    r2 = await async_client.post(
        URL,
        content=payload2,
        headers={
            "Authorization": f"Bearer {operator_token}",
            "X-Archive-Signature": sig2,
            "Content-Type": "application/octet-stream",
        },
    )
    assert r2.status_code == 200
    assert r2.json()["skipped_db_not_empty"] == 1
    assert r2.json()["packs_inserted"] == 0


@pytest.mark.anyio
@pytest.mark.usefixtures("override_redis_dep", "override_db_dependency")
async def test_import_endpoint_allows_large_archive(
    async_client, operator_token, monkeypatch
):
    """Admin import path uses ADMIN_IMPORT_MAX_BODY_BYTES, not the 256 KiB default."""
    # Build a payload that exceeds the default 256 KiB but fits the 32 MiB admin limit.
    large_payload = _make_archive("large-pack")
    # Pad the payload to ~300 KiB (just over the default limit).
    large_payload += b" " * (300 * 1024 - len(large_payload))
    sig = sign_archive(large_payload, secret=SECRET)
    resp = await async_client.post(
        URL,
        content=large_payload,
        headers={
            "Authorization": f"Bearer {operator_token}",
            "X-Archive-Signature": sig,
            "Content-Type": "application/octet-stream",
        },
    )
    # Should not be rejected with 413 — the endpoint has a higher limit.
    assert resp.status_code != 413, f"Got 413 — admin import limit not applied: {resp.text}"
