"""X (Twitter) API v2 client + the dry-run stand-in.

Tier reality (2026, document for the owner — see README):
- FREE tier: ~500 WRITES/month, app-level. Enough for our cadence
  (2 profile posts + up to 6 replies per day ≈ 240 writes/mo) but NO
  recent-search access.
- BASIC tier (~$200/mo): adds GET /2/tweets/search/recent (reads) — required
  for the reply pipeline's discovery step.

Writes use OAuth 1.0a user context; recent search uses the app bearer token.
"""
from __future__ import annotations

import logging
from dataclasses import dataclass
from datetime import datetime
from typing import Any, Protocol

import httpx

from .oauth1 import authorization_header
from .visibility import TweetCandidate

log = logging.getLogger("social_agent.x")

API = "https://api.twitter.com/2"


class XClientProtocol(Protocol):
    dry_run: bool

    async def post_tweet(self, text: str) -> str | None: ...
    async def reply_to(self, text: str, in_reply_to: str) -> str | None: ...
    async def recent_search(self, query: str, start_time: datetime, max_results: int = 50) -> list[TweetCandidate]: ...


@dataclass
class XCredentials:
    api_key: str
    api_secret: str
    access_token: str
    access_secret: str
    bearer_token: str = ""


class XClient:
    """Real client. Instantiated only when credentials exist AND dry-run is off."""

    dry_run = False

    def __init__(self, creds: XCredentials, timeout: float = 30.0):
        self._creds = creds
        self._timeout = timeout

    def _auth_header(self, method: str, url: str, query: dict[str, str] | None = None) -> str:
        return authorization_header(
            method,
            url,
            consumer_key=self._creds.api_key,
            consumer_secret=self._creds.api_secret,
            token=self._creds.access_token,
            token_secret=self._creds.access_secret,
            query_params=query,
        )

    async def _create_tweet(self, payload: dict[str, Any]) -> str | None:
        url = f"{API}/tweets"
        headers = {
            "Authorization": self._auth_header("POST", url),
            "Content-Type": "application/json",
        }
        async with httpx.AsyncClient(timeout=self._timeout) as client:
            resp = await client.post(url, json=payload, headers=headers)
        if resp.status_code == 429:
            log.warning("X write rate-limited (429): %s", resp.text[:300])
            return None
        if resp.status_code >= 300:
            log.error("X write failed (%s): %s", resp.status_code, resp.text[:300])
            return None
        tweet_id = (resp.json().get("data") or {}).get("id")
        log.info("posted tweet id=%s", tweet_id)
        return tweet_id

    async def post_tweet(self, text: str) -> str | None:
        return await self._create_tweet({"text": text})

    async def reply_to(self, text: str, in_reply_to: str) -> str | None:
        return await self._create_tweet(
            {"text": text, "reply": {"in_reply_to_tweet_id": in_reply_to}}
        )

    async def recent_search(
        self, query: str, start_time: datetime, max_results: int = 50
    ) -> list[TweetCandidate]:
        """GET /2/tweets/search/recent — PAID (Basic) tier only."""
        if not self._creds.bearer_token:
            log.warning("recent_search called without X_BEARER_TOKEN; returning []")
            return []
        params = {
            "query": query,
            "max_results": str(min(max(10, max_results), 100)),
            "start_time": start_time.strftime("%Y-%m-%dT%H:%M:%SZ"),
            "tweet.fields": "created_at,public_metrics,lang,author_id",
            "expansions": "author_id",
            "user.fields": "username,public_metrics",
        }
        headers = {"Authorization": f"Bearer {self._creds.bearer_token}"}
        async with httpx.AsyncClient(timeout=self._timeout) as client:
            resp = await client.get(f"{API}/tweets/search/recent", params=params, headers=headers)
        if resp.status_code == 403:
            log.error(
                "recent search returned 403 — this endpoint requires the paid "
                "Basic tier (~$200/mo). Falling back to no results."
            )
            return []
        if resp.status_code >= 300:
            log.error("recent search failed (%s): %s", resp.status_code, resp.text[:300])
            return []
        body = resp.json()
        users = {
            u["id"]: u for u in (body.get("includes") or {}).get("users", [])
        }
        out: list[TweetCandidate] = []
        for t in body.get("data", []) or []:
            metrics = t.get("public_metrics") or {}
            author = users.get(t.get("author_id", ""), {})
            a_metrics = author.get("public_metrics") or {}
            out.append(
                TweetCandidate(
                    tweet_id=t["id"],
                    text=t.get("text", ""),
                    author_id=t.get("author_id", ""),
                    author_username=author.get("username", ""),
                    author_followers=int(a_metrics.get("followers_count", 0)),
                    reply_count=int(metrics.get("reply_count", 0)),
                    like_count=int(metrics.get("like_count", 0)),
                    retweet_count=int(metrics.get("retweet_count", 0)),
                    created_at=t.get("created_at", ""),
                    lang=t.get("lang", "en"),
                )
            )
        return out


class DryRunXClient:
    """Posts nothing. Logs exactly what WOULD be posted (owner requirement)."""

    dry_run = True

    def __init__(self) -> None:
        self.would_have_posted: list[dict[str, str]] = []

    async def post_tweet(self, text: str) -> str | None:
        self.would_have_posted.append({"kind": "post", "text": text})
        log.info("[DRY-RUN] would POST tweet:\n%s", text)
        return None

    async def reply_to(self, text: str, in_reply_to: str) -> str | None:
        self.would_have_posted.append(
            {"kind": "reply", "text": text, "in_reply_to": in_reply_to}
        )
        log.info("[DRY-RUN] would REPLY to %s:\n%s", in_reply_to, text)
        return None

    async def recent_search(
        self, query: str, start_time: datetime, max_results: int = 50
    ) -> list[TweetCandidate]:
        log.info("[DRY-RUN] recent_search unavailable without credentials; returning []")
        return []
