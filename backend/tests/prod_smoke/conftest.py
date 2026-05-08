"""Prod-smoke conftest.

Tests in this package hit the deployed Container App over the public
internet. They are gated behind ``PROD_SMOKE_BASE_URL``; the suite is
silently skipped when the env var is missing so that local ``pytest``
runs and CI matrices that don't opt in stay green.

These tests deliberately do not stub anything. They prove that the
production wiring (Cloudflare Turnstile secret in Key Vault,
Container App revision env, real Cloudflare ``siteverify`` round trip,
FastAPI dependency, error-shape contract) is correctly assembled.
"""
from __future__ import annotations

import os

import pytest


@pytest.fixture(scope="session")
def prod_base_url() -> str:
    base = (os.getenv("PROD_SMOKE_BASE_URL") or "").rstrip("/")
    if not base:
        pytest.skip("PROD_SMOKE_BASE_URL not set; prod-smoke tests skipped")
    return base
