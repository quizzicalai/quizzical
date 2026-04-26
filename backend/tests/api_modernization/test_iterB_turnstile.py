"""Iter B — verify_turnstile hardening: hoist inline import + add timeout.

Two issues in ``app/api/dependencies.py::verify_turnstile``:

1. ``import json`` lives inside the function body, paying repeat-import
   lookup cost on every protected request.
2. ``httpx.AsyncClient()`` is created per call with **no timeout**. A slow
   or stuck Cloudflare endpoint would block the request worker
   indefinitely (the global default is no timeout when ``timeout`` is
   omitted). This is a denial-of-service hazard on the auth path.
"""

from __future__ import annotations

import ast
import pathlib

import pytest


def _verify_turnstile_node() -> ast.AsyncFunctionDef:
    from app.api import dependencies as deps

    src = pathlib.Path(deps.__file__).read_text(encoding="utf-8")
    tree = ast.parse(src)
    for node in ast.walk(tree):
        if isinstance(node, ast.AsyncFunctionDef) and node.name == "verify_turnstile":
            return node
    raise AssertionError("verify_turnstile not found in dependencies.py")


def test_verify_turnstile_has_no_inner_imports() -> None:
    node = _verify_turnstile_node()
    inner = [
        n
        for n in ast.walk(node)
        if isinstance(n, (ast.Import, ast.ImportFrom))
    ]
    assert not inner, (
        f"verify_turnstile must not import inside the function body; found: "
        f"{[ast.dump(n) for n in inner]}"
    )


def test_verify_turnstile_passes_timeout_to_httpx_client() -> None:
    """The httpx.AsyncClient(...) call must include an explicit timeout kwarg
    so an unresponsive Cloudflare endpoint cannot wedge the worker.
    """
    node = _verify_turnstile_node()
    found_call = None
    for n in ast.walk(node):
        if isinstance(n, ast.Call):
            func = n.func
            # Match either AsyncClient(...) or httpx.AsyncClient(...).
            if isinstance(func, ast.Attribute) and func.attr == "AsyncClient":
                found_call = n
                break
            if isinstance(func, ast.Name) and func.id == "AsyncClient":
                found_call = n
                break
    assert found_call is not None, "verify_turnstile must instantiate httpx.AsyncClient"

    kwarg_names = {kw.arg for kw in found_call.keywords}
    assert "timeout" in kwarg_names, (
        "httpx.AsyncClient(...) inside verify_turnstile must pass an explicit "
        f"`timeout=` kwarg; saw kwargs={kwarg_names!r}"
    )


@pytest.mark.asyncio
async def test_verify_turnstile_local_bypass_returns_true(monkeypatch) -> None:
    """Sanity: when ENABLE_TURNSTILE is False, the dependency short-circuits
    to True regardless of request body, no httpx call required.
    """
    from types import SimpleNamespace

    from app.api import dependencies as deps

    monkeypatch.setattr(deps, "settings", SimpleNamespace(ENABLE_TURNSTILE=False))

    class _FakeReq:
        async def body(self) -> bytes:  # pragma: no cover - not invoked
            return b""

    assert await deps.verify_turnstile(_FakeReq()) is True  # type: ignore[arg-type]
