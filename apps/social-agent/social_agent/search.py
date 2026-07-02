"""Search providers for the reply pipeline, behind one interface.

The X API tier decision (see README) is surfaced HERE:
- 'x'       : X API v2 recent search — requires paid Basic tier (~$200/mo).
- 'fixture' : local JSON file of tweets (demos, tests, dry-run transcripts).
- 'none'    : NO-SEARCH fallback — the bot stays posts-only and says so.
- 'auto'    : x if bearer token + explicitly enabled, else fixture if a path
              is configured, else none.
"""
from __future__ import annotations

import json
import logging
from datetime import datetime
from pathlib import Path
from typing import Protocol

from .config import Settings
from .visibility import TweetCandidate

log = logging.getLogger("social_agent.search")

# Recent personality-adjacent chatter; excludes RTs/replies, English only.
DEFAULT_QUERY = (
    '("personality quiz" OR "personality test" OR "which character am i" '
    'OR "personality type" OR mbti OR enneagram OR "16 personalities") '
    "-is:retweet -is:reply -is:quote lang:en"
)


class SearchProvider(Protocol):
    name: str

    async def search(self, query: str, start_time: datetime) -> list[TweetCandidate]: ...


class XSearchProvider:
    name = "x-recent-search"

    def __init__(self, x_client) -> None:  # XClientProtocol
        self._x = x_client

    async def search(self, query: str, start_time: datetime) -> list[TweetCandidate]:
        return await self._x.recent_search(query, start_time)


class FixtureSearchProvider:
    """Serves candidate tweets from a JSON file (list of TweetCandidate dicts)."""

    name = "fixture"

    def __init__(self, path: str | Path) -> None:
        self._path = Path(path)

    async def search(self, query: str, start_time: datetime) -> list[TweetCandidate]:
        raw = json.loads(self._path.read_text(encoding="utf-8"))
        return [TweetCandidate(**item) for item in raw]


class NoSearchProvider:
    """Posts-only mode: search unavailable on the X free tier."""

    name = "no-search"

    async def search(self, query: str, start_time: datetime) -> list[TweetCandidate]:
        log.warning(
            "Reply discovery skipped: recent search requires the X Basic tier "
            "(~$200/mo). Running posts-only. Set X_BEARER_TOKEN + "
            "SOCIAL_X_SEARCH_ENABLED=true once upgraded, or point "
            "SOCIAL_FIXTURE_PATH at a JSON file to demo the reply pipeline."
        )
        return []


def make_search_provider(settings: Settings, x_client) -> SearchProvider:
    mode = settings.search_mode
    if mode == "auto":
        if settings.x_search_enabled and settings.x_bearer_token:
            mode = "x"
        elif settings.fixture_path:
            mode = "fixture"
        else:
            mode = "none"
    if mode == "x":
        return XSearchProvider(x_client)
    if mode == "fixture":
        return FixtureSearchProvider(settings.fixture_path)
    return NoSearchProvider()
