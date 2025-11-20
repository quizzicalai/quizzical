# backend/tests/fixtures/settings_fixtures.py

import pytest
from app.core.config import settings

@pytest.fixture
def quiz_settings(monkeypatch):
    """
    Fixture to override quiz-specific settings for tests.
    
    Allows dynamic modification of quiz constraints (e.g. question counts)
    during a test execution. Since `settings` is a singleton Pydantic model,
    monkeypatch is used to ensure changes are reverted after the test.
    """
    def _apply(*, baseline_n=None, max_options=None):
        # settings.quiz is an instance of QuizConfig (Pydantic model)
        if baseline_n is not None:
            monkeypatch.setattr(settings.quiz, "baseline_questions_n", baseline_n, raising=False)
        if max_options is not None:
            monkeypatch.setattr(settings.quiz, "max_options_m", max_options, raising=False)
    return _apply