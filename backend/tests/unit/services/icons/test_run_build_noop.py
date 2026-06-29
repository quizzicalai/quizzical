"""run_build flag-off no-op tests.

Proves that wiring the icon hook into ``builder.run_build`` does NOT change the
build path when the flag is off:
  - the artefact handed to ``evaluate_fn`` / ``persist_fn`` is the SAME object
    the generator produced (icon hook returns it unchanged),
  - no icon fields are attached,
  - the embedder / binder / fastembed are never imported by a build run.
"""

from __future__ import annotations

import subprocess
import sys
import textwrap

import pytest

from app.models.db import PrecomputeJob, Topic
from app.services.precompute import builder, jobs
from app.services.precompute.evaluator import EvaluatorResult
from tests.fixtures.db_fixtures import sqlite_db_session  # noqa: F401

pytestmark = pytest.mark.anyio


async def _seed(session) -> tuple[Topic, PrecomputeJob]:
    t = Topic(slug="t", display_name="T", policy_status="allowed")
    session.add(t)
    await session.flush()
    j = await jobs.enqueue(session, topic_id=t.id)
    return t, j


def _result(score: int, *, tier="cheap") -> EvaluatorResult:
    return EvaluatorResult(score=score, tier=tier, blocking_reasons=())


async def test_run_build_flag_off_artefact_unchanged(sqlite_db_session, monkeypatch):
    # Belt-and-suspenders: force the flag off regardless of YAML.
    from app.core.config import settings
    monkeypatch.setattr(settings.images, "qa_icons_enabled", False, raising=False)

    t, j = await _seed(sqlite_db_session)

    produced = {
        "questions": [
            {"question_text": "a rocket launches into orbit",
             "options": [{"text": "a fire type with a burning tail"}]}
        ]
    }
    seen_in_evaluate = {}
    seen_in_persist = {}

    async def gen(topic, tier):
        return (produced, 5)

    async def ev(artefact, tier, pass_score, two_judge):
        seen_in_evaluate["obj"] = artefact
        return _result(8, tier=tier)

    async def persist(topic, artefact, result):
        seen_in_persist["obj"] = artefact

    out = await builder.run_build(
        sqlite_db_session, topic=t, job=j,
        generate_fn=gen, evaluate_fn=ev, persist_fn=persist,
        daily_budget_usd=5.0, default_pass_score=7,
    )
    await sqlite_db_session.commit()

    assert out.status == "succeeded"
    # The hook returned the SAME artefact object to evaluate + persist.
    assert seen_in_evaluate["obj"] is produced
    assert seen_in_persist["obj"] is produced
    # Nothing was attached to the Q&A.
    q = produced["questions"][0]
    assert "icon_id" not in q
    assert "icon_id" not in q["options"][0]


def test_importing_builder_does_not_import_embedder():
    """The builder module wires in the icon hook, but importing it (and running
    the flag-off hook path) must NOT pull in the embedder / binder / fastembed.

    Asserted in a CLEAN interpreter so it is not polluted by other tests that
    legitimately import the embedder. This is the import-side of the strict
    no-op contract for the build path.
    """
    script = textwrap.dedent(
        """
        import sys, asyncio
        # Importing the builder pulls in the icon hook (top-level import).
        from app.services.precompute import builder  # noqa: F401
        for mod in ("fastembed",
                    "app.services.icons.embedder",
                    "app.services.icons.binder"):
            assert mod not in sys.modules, f"LEAKED on import: {mod}"

        # And executing the flag-off hook path stays just as clean.
        from app.services.icons.hook import maybe_bind_icons
        class _Imgs:
            qa_icons_enabled = False
            tau = 0.5
            query_prefix = ""
        class _S:
            images = _Imgs()
        art = {"questions": [{"question_text": "a rocket", "options": [{"text": "fire"}]}]}
        out, n = asyncio.run(maybe_bind_icons(db=None, artefact=art, settings_obj=_S()))
        assert n == 0 and out is art
        assert "icon_id" not in art["questions"][0]["options"][0]
        for mod in ("fastembed",
                    "app.services.icons.embedder",
                    "app.services.icons.binder"):
            assert mod not in sys.modules, f"LEAKED on flag-off call: {mod}"
        print("RUN_BUILD_NOOP_OK")
        """
    )
    proc = subprocess.run(
        [sys.executable, "-c", script],
        capture_output=True,
        text=True,
    )
    assert proc.returncode == 0, f"stdout={proc.stdout!r}\nstderr={proc.stderr[-3000:]!r}"
    assert "RUN_BUILD_NOOP_OK" in proc.stdout
