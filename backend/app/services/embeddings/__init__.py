"""Embeddings utilities (Phase 7)."""

from app.services.embeddings.cache import get_or_compute_embedding, text_hash

__all__ = ["get_or_compute_embedding", "text_hash"]
