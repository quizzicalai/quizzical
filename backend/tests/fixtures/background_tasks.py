# backend/tests/fixtures/background_tasks.py

"""
BackgroundTasks capture fixture.

FastAPI's `BackgroundTasks.add_task` schedules callables to run after the response
is returned. In unit tests, it's usually undesirable to actually run these tasks.

What this provides:
- capture_background_tasks: patches BackgroundTasks.add_task so calls are recorded
  as tuples (func, args, kwargs). Tests can assert tasks were scheduled with the
  right parameters, without executing them.
"""

from __future__ import annotations

from typing import Callable, List, Tuple

import pytest
from fastapi import BackgroundTasks


@pytest.fixture(scope="function")
def capture_background_tasks(monkeypatch: pytest.MonkeyPatch) -> List[Tuple[Callable, tuple, dict]]:
    """
    Usage:

        def test_proceed_schedules_work(client, capture_background_tasks, override_redis_dep):
            resp = await client.post("/api/v1/quiz/proceed", json={"quiz_id": quiz_id})
            assert resp.status_code == 202
            assert len(capture_background_tasks) == 1
            func, args, kwargs = capture_background_tasks[0]
            assert callable(func)

    Returns:
        A list that fills with (func, args, kwargs) per `add_task` call.
    """
    scheduled: List[Tuple[Callable, tuple, dict]] = []

    def _fake_add_task(self, func: Callable, *args, **kwargs):
        scheduled.append((func, args, kwargs))

    monkeypatch.setattr(BackgroundTasks, "add_task", _fake_add_task, raising=True)
    return scheduled
