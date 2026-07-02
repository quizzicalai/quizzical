"""Dry-run behavior: default-on without keys, forceable with keys, and the
DryRunXClient never emits network traffic (it has no HTTP client at all)."""
import asyncio

from social_agent.config import normalize_pg_dsn, resolve_dry_run
from social_agent.x_client import DryRunXClient


# --- resolve_dry_run matrix -------------------------------------------------

def test_no_keys_is_always_dry_run():
    assert resolve_dry_run(None, have_all_x_keys=False) is True
    assert resolve_dry_run("false", have_all_x_keys=False) is True  # can't override
    assert resolve_dry_run("0", have_all_x_keys=False) is True


def test_keys_present_goes_live_by_default():
    assert resolve_dry_run(None, have_all_x_keys=True) is False


def test_keys_present_but_flag_forces_dry_run():
    assert resolve_dry_run("true", have_all_x_keys=True) is True
    assert resolve_dry_run("1", have_all_x_keys=True) is True


def test_explicit_false_with_keys_is_live():
    assert resolve_dry_run("false", have_all_x_keys=True) is False


def test_garbage_flag_value_defaults_to_live_when_keys_present():
    assert resolve_dry_run("banana", have_all_x_keys=True) is False


# --- DryRunXClient ------------------------------------------------------------

def test_dry_run_client_posts_nothing_and_records_everything():
    client = DryRunXClient()
    assert client.dry_run is True

    async def run():
        tweet_id = await client.post_tweet("hello world {link}")
        reply_id = await client.reply_to("nice quiz", in_reply_to="12345")
        found = await client.recent_search("q", start_time=None)
        return tweet_id, reply_id, found

    tweet_id, reply_id, found = asyncio.run(run())
    assert tweet_id is None  # no tweet id: nothing was posted
    assert reply_id is None
    assert found == []
    assert client.would_have_posted == [
        {"kind": "post", "text": "hello world {link}"},
        {"kind": "reply", "text": "nice quiz", "in_reply_to": "12345"},
    ]


# --- DSN normalization (config plumbing used by the app venv) -----------------

def test_dsn_normalization():
    assert normalize_pg_dsn("postgresql+psycopg://u:p@h:5432/db?sslmode=require") == (
        "postgresql://u:p@h:5432/db?sslmode=require"
    )
    assert normalize_pg_dsn("postgresql+asyncpg://u@h/db") == "postgresql://u@h/db"
    assert normalize_pg_dsn("postgres://u@h/db") == "postgresql://u@h/db"
    assert normalize_pg_dsn("postgresql://u@h/db") == "postgresql://u@h/db"
