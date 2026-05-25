"""Regression: ``app.api.endpoints.admin_precompute`` must not depend on
``scripts/`` at import time or at request time.

Why this exists
---------------
On 2026-05-25 the production ``POST /api/v1/admin/precompute/import``
endpoint returned HTTP 500 with ``ModuleNotFoundError: No module named
'scripts'`` because the endpoint did ``from scripts.import_packs import …``
inside the handler but the runtime container image excludes
``backend/scripts/`` (via ``.dockerignore``). The seed workflow had been
silently failing for every prod deploy as a result.

The fix moved the importer to ``app.services.precompute.pack_importer``.
This test statically scans the endpoint source for any ``scripts.``
reference so the regression cannot recur unnoticed.
"""

from __future__ import annotations

import ast
from pathlib import Path

import app.api.endpoints.admin_precompute as endpoint_module


def _collect_import_targets(tree: ast.AST) -> list[str]:
    """Return every fully-qualified module name referenced by ``import`` or
    ``from ... import`` statements anywhere in the tree (top-level **and**
    inside function/method bodies — the original bug was a lazy import).
    """
    targets: list[str] = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for alias in node.names:
                targets.append(alias.name)
        elif isinstance(node, ast.ImportFrom):
            if node.module:
                targets.append(node.module)
    return targets


def test_admin_precompute_endpoint_does_not_import_scripts() -> None:
    """The endpoint module — including any lazy imports inside handlers —
    must never reference the ``scripts`` package, because the production
    runtime image does not ship it.
    """
    source_path = Path(endpoint_module.__file__)
    tree = ast.parse(source_path.read_text(encoding="utf-8"))
    imports = _collect_import_targets(tree)
    offending = [
        name for name in imports if name == "scripts" or name.startswith("scripts.")
    ]
    assert not offending, (
        "admin_precompute endpoint must not import from `scripts/` — the prod "
        "container image excludes that directory and the handler will 500 with "
        f"ModuleNotFoundError. Offending imports: {offending}"
    )
