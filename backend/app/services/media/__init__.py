"""§21 Phase 5 — media services package.

Two providers ship in Phase 5:

- `fal`   — pass-through; the upstream CDN URL is stored directly in
            `media_assets.storage_uri` and returned to the client. No
            byte rehost, no `bytes_blob`.
- `local` — bytes persisted in `media_assets.bytes_blob`; the served URI
            is `/api/media/{asset_id}` (handled by
            `app/api/endpoints/media.py`). Strong content-hash ETag +
            immutable cache header (`AC-PRECOMP-IMG-3`,
            `AC-PRECOMP-PERF-4`).

Azure Blob (Phase 12) plugs in via the same `MediaProvider` protocol.
"""
