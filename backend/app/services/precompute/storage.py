"""§21 Phase 12 — pluggable media storage providers.

Today's deployments serve images either from `local` (DB blob via
`/api/v1/media/{id}`) or from a third-party `fal` URL stored verbatim in
`storage_uri`. Phase 12 introduces an Azure Blob provider plus a
**dual-write** resolver so that the migration from `local` → `blob` can
happen incrementally without breaking any reader.

Provider abstraction is intentionally minimal — every provider only
needs to:
- `upload(*, content_hash, data, content_type)` → storage_uri
- `resolve(asset)` → URL string the FE can fetch

Concrete providers:
- `LocalProvider`        — `/api/v1/media/{asset.id}`, bytes in DB
- `FalProvider`          — pass-through; `resolve` returns `asset.storage_uri`
- `AzureBlobProvider`    — Azure Blob Storage; `resolve` returns the
                            container URL `"<base>/<container>/<key>"`
- `DualWriteResolver`    — prefers blob, falls back to local during the
                            7-day transition window (`AC-PRECOMP-MIGR-1`)

Azure SDK is loaded lazily so importing this module never fails when
the optional `azure-storage-blob` dependency is absent.
"""

from __future__ import annotations

from typing import Any, Protocol, runtime_checkable

from app.core.config import settings
from app.models.db import MediaAsset


@runtime_checkable
class StorageProvider(Protocol):
    name: str

    async def upload(
        self, *, content_hash: str, data: bytes, content_type: str
    ) -> str:
        """Persist `data` and return its storage_uri."""
        ...

    def resolve(self, asset: MediaAsset) -> str:
        """Return a URL suitable for FE fetch."""
        ...


# ---------------------------------------------------------------------------
# Local — bytes live in `media_assets.bytes_blob`; FE fetches via API route.
# ---------------------------------------------------------------------------


class LocalProvider:
    name = "local"

    async def upload(self, *, content_hash: str, data: bytes, content_type: str) -> str:
        # Caller writes to MediaAsset.bytes_blob directly; storage_uri is the
        # API path of the not-yet-known asset id. We return the well-known
        # template; the caller must format with the asset.id post-insert.
        return "/api/v1/media/{id}"

    def resolve(self, asset: MediaAsset) -> str:
        return f"/api/v1/media/{asset.id}"


# ---------------------------------------------------------------------------
# Fal — third-party URL persisted in `storage_uri`.
# ---------------------------------------------------------------------------


class FalProvider:
    name = "fal"

    async def upload(self, *, content_hash: str, data: bytes, content_type: str) -> str:
        raise NotImplementedError("fal provider is read-only — uploads happen out-of-band")

    def resolve(self, asset: MediaAsset) -> str:
        return asset.storage_uri


# ---------------------------------------------------------------------------
# Azure Blob.
# ---------------------------------------------------------------------------


class AzureBlobConfigError(RuntimeError):
    """Raised when Azure config is missing at the moment of an operation."""


class AzureBlobProvider:
    """Azure Blob Storage provider.

    Construction is cheap and never imports the Azure SDK. SDK import is
    deferred to the first `upload` call so unit tests, environments
    without the SDK, and CI builds without `azure-storage-blob` installed
    can still import this module.
    """

    name = "blob"

    def __init__(
        self,
        *,
        base_url: str,
        container: str,
        credential: Any | None = None,
        client_factory: Any | None = None,
    ) -> None:
        if not base_url or not container:
            raise AzureBlobConfigError(
                "AzureBlobProvider requires base_url and container"
            )
        self._base_url = base_url.rstrip("/")
        self._container = container
        self._credential = credential
        self._client_factory = client_factory  # for tests; injects mock SDK

    @classmethod
    def from_settings(cls) -> "AzureBlobProvider":
        ms = getattr(getattr(settings, "media_storage", None), "blob", None)
        base_url = getattr(ms, "base_url", None) if ms else None
        container = getattr(ms, "container", None) if ms else None
        if not base_url or not container:
            raise AzureBlobConfigError(
                "media_storage.blob.{base_url,container} not configured"
            )
        return cls(base_url=base_url, container=container)

    async def upload(
        self, *, content_hash: str, data: bytes, content_type: str
    ) -> str:
        """Upload `data` keyed by `content_hash`; idempotent on re-upload.

        Returns the public storage_uri (`base_url/container/content_hash`).
        """
        client = self._build_client()
        try:
            blob_client = client.get_blob_client(
                container=self._container, blob=content_hash
            )
            from azure.core.exceptions import (  # local import — optional dep
                ResourceExistsError,
            )

            try:
                await blob_client.upload_blob(
                    data,
                    overwrite=False,
                    content_settings=_make_content_settings(content_type),
                )
            except ResourceExistsError:
                # Idempotent — content addressed.
                pass
        finally:
            close = getattr(client, "close", None)
            if close is not None:
                try:
                    await close()
                except Exception:
                    pass
        return f"{self._base_url}/{self._container}/{content_hash}"

    def resolve(self, asset: MediaAsset) -> str:
        # storage_uri was populated at upload time; just hand it back.
        return asset.storage_uri

    def _build_client(self) -> Any:
        if self._client_factory is not None:
            return self._client_factory()
        try:
            from azure.storage.blob.aio import (  # type: ignore[import-not-found]
                BlobServiceClient,
            )
        except Exception as exc:  # pragma: no cover - exercised by tests via factory
            raise AzureBlobConfigError(
                "azure-storage-blob is not installed; add it to runtime deps "
                "before enabling the blob provider"
            ) from exc
        if self._credential is None:
            return BlobServiceClient(account_url=self._base_url)
        return BlobServiceClient(
            account_url=self._base_url, credential=self._credential
        )


def _make_content_settings(content_type: str) -> Any:
    try:
        from azure.storage.blob import ContentSettings  # type: ignore[import-not-found]
    except Exception:
        return None
    return ContentSettings(content_type=content_type)


# ---------------------------------------------------------------------------
# Dual-write resolver — `AC-PRECOMP-MIGR-1`.
# ---------------------------------------------------------------------------


class DualWriteResolver:
    """During the 7-day transition window:
    - prefer the blob URL when `asset.storage_provider in {"blob","blob+cdn"}`;
    - fall back to local when blob is unavailable;
    - never break readers when the provider mapping changes.
    """

    def __init__(
        self,
        *,
        local: LocalProvider | None = None,
        blob: AzureBlobProvider | None = None,
        fal: FalProvider | None = None,
    ) -> None:
        self._local = local or LocalProvider()
        self._blob = blob
        self._fal = fal or FalProvider()

    def resolve(self, asset: MediaAsset) -> str:
        provider = (asset.storage_provider or "").lower()
        if provider in ("blob", "blob+cdn"):
            if self._blob is not None:
                return self._blob.resolve(asset)
            # blob desired but provider missing → fall back to bytes route
            # only if bytes are still present (dual-write window).
            if asset.bytes_blob is not None:
                return self._local.resolve(asset)
            # Last resort: return whatever's in storage_uri so the FE has
            # a chance to render — never raise from a resolver.
            return asset.storage_uri
        if provider == "local":
            return self._local.resolve(asset)
        if provider == "fal":
            return self._fal.resolve(asset)
        return asset.storage_uri


__all__ = [
    "AzureBlobConfigError",
    "AzureBlobProvider",
    "DualWriteResolver",
    "FalProvider",
    "LocalProvider",
    "StorageProvider",
]
