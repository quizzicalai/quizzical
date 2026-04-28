"""§19.1 AC-QUALITY-R2-IMPORT — narrow import guards in agent modules.

These tests pin the contract that optional-import try/except blocks catch
only ImportError. A bare `except Exception` would silently swallow real
SyntaxError/AttributeError bugs in the imported module, so this guard is a
regression target.
"""
from __future__ import annotations

import inspect
import re

import pytest


def _source(module) -> str:
    return inspect.getsource(module)


def _find_optional_import_clause(src: str, symbol: str) -> str:
    """Return the `except ...:` clause text for the try/except wrapping `symbol`."""
    # Find the try-block that imports the symbol.
    match = re.search(
        rf"try:[^\n]*\n(?:[ \t]+.*\n)*?[ \t]+(?:.*\b{re.escape(symbol)}\b.*)\n"
        rf"(?:[ \t]+.*\n)*?(except [^\n]+):",
        src,
    )
    assert match, f"Could not locate try/except clause guarding {symbol}"
    return match.group(1)


def test_graph_optional_tool_import_guard_is_narrow():
    """AC-QUALITY-R2-IMPORT-1: graph.py must catch only ImportError for the
    optional batch character tool. Catching `Exception` would mask real bugs."""
    from app.agent import graph as graph_mod

    src = _source(graph_mod)
    clause = _find_optional_import_clause(src, "draft_character_profiles")
    assert "ImportError" in clause, (
        f"Expected `except ImportError:` for optional tool, got: {clause!r}"
    )
    assert "Exception" not in clause, (
        f"Bare `except Exception:` is forbidden for import guards: {clause!r}"
    )


def test_canonical_sets_settings_import_guard_is_narrow():
    """AC-QUALITY-R2-IMPORT-1: canonical_sets.py must catch only ImportError
    when soft-importing settings."""
    from app.agent import canonical_sets

    src = _source(canonical_sets)
    clause = _find_optional_import_clause(src, "settings")
    assert "ImportError" in clause, (
        f"Expected `except ImportError:` for settings import, got: {clause!r}"
    )
    assert "except Exception" not in clause, (
        f"Bare `except Exception:` is forbidden: {clause!r}"
    )


@pytest.mark.parametrize(
    "module_path,bound_name",
    [
        ("app.agent.graph", "tool_draft_character_profiles"),
        ("app.agent.canonical_sets", "settings"),
    ],
)
def test_module_imports_succeed(module_path: str, bound_name: str):
    """AC-QUALITY-R2-IMPORT-2: narrowing the guard must not break the happy
    path — modules still import and the soft-imported name is bound."""
    import importlib

    mod = importlib.import_module(module_path)
    assert hasattr(mod, bound_name)
