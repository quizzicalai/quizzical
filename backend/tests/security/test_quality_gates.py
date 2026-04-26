"""
Iteration 1 — Quality gates: ruff and bandit must stay clean for `app/`.

These tests are the agent's regression guard against linting and
high/medium-severity security regressions slipping into the codebase.
"""
from __future__ import annotations

import json
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

BACKEND_ROOT = Path(__file__).resolve().parents[2]
APP_DIR = BACKEND_ROOT / "app"


def _has_tool(name: str) -> bool:
    return shutil.which(name) is not None or _resolve_in_venv(name) is not None


def _resolve_in_venv(name: str) -> str | None:
    candidates = [
        Path(sys.executable).parent / f"{name}.exe",
        Path(sys.executable).parent / name,
    ]
    for c in candidates:
        if c.exists():
            return str(c)
    return None


@pytest.mark.skipif(not _has_tool("ruff"), reason="ruff not installed")
def test_app_passes_ruff():
    """`ruff check app` must report zero errors."""
    ruff = _resolve_in_venv("ruff") or "ruff"
    proc = subprocess.run(
        [ruff, "check", str(APP_DIR), "--output-format=concise"],
        capture_output=True,
        text=True,
        cwd=str(BACKEND_ROOT),
    )
    assert proc.returncode == 0, (
        f"ruff failed:\nSTDOUT:\n{proc.stdout}\nSTDERR:\n{proc.stderr}"
    )


@pytest.mark.skipif(not _has_tool("bandit"), reason="bandit not installed")
def test_app_passes_bandit_high_and_medium():
    """bandit must find zero High or Medium severity issues in `app/`."""
    bandit = _resolve_in_venv("bandit") or "bandit"
    proc = subprocess.run(
        [bandit, "-r", str(APP_DIR), "-f", "json", "-q"],
        capture_output=True,
        text=True,
        cwd=str(BACKEND_ROOT),
    )
    # bandit returns nonzero when it finds issues; we only fail on High/Medium.
    if not proc.stdout.strip():
        # Tolerate bandit output going to stderr only when no issues exist.
        return
    try:
        report = json.loads(proc.stdout)
    except json.JSONDecodeError:
        pytest.fail(f"Could not parse bandit JSON: {proc.stdout[:400]}")

    bad = [
        r
        for r in report.get("results", [])
        if r.get("issue_severity") in {"HIGH", "MEDIUM"}
    ]
    assert not bad, (
        "Bandit found high/medium severity issues:\n"
        + "\n".join(
            f"- {r['test_id']} {r['issue_severity']} in "
            f"{r['filename']}:{r['line_number']} -> {r['issue_text']}"
            for r in bad
        )
    )
