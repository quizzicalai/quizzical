"""Defensive Turnstile token validation: reject non-strings and oversized blobs."""
from __future__ import annotations

import pytest
from fastapi import HTTPException

from app.api.dependencies import _validate_turnstile_token


def test_missing_token_rejected_400() -> None:
    with pytest.raises(HTTPException) as exc:
        _validate_turnstile_token(None)
    assert exc.value.status_code == 400


def test_empty_token_rejected_400() -> None:
    with pytest.raises(HTTPException) as exc:
        _validate_turnstile_token("")
    assert exc.value.status_code == 400


def test_non_string_token_rejected_400() -> None:
    with pytest.raises(HTTPException) as exc:
        _validate_turnstile_token(12345)
    assert exc.value.status_code == 400
    assert "string" in exc.value.detail.lower()


def test_oversized_token_rejected_400() -> None:
    with pytest.raises(HTTPException) as exc:
        _validate_turnstile_token("A" * 5000)
    assert exc.value.status_code == 400
    assert "too large" in exc.value.detail.lower()


def test_normal_token_passes() -> None:
    # Should not raise for a realistic token.
    _validate_turnstile_token("0.QFa3xj_typical_turnstile_token_format")
