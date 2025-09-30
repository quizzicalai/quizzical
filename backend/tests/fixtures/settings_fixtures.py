# backend/tests/fixtures/settings_fixtures.py

import pytest
from app.core.config import settings

@pytest.fixture
def quiz_settings(monkeypatch):
    def _apply(*, baseline_n=None, max_options=None):
        if baseline_n is not None:
            monkeypatch.setattr(settings.quiz, "baseline_questions_n", baseline_n, raising=False)
        if max_options is not None:
            monkeypatch.setattr(settings.quiz, "max_options_m", max_options, raising=False)
    return _apply
