"""CLI entry point.

    python -m social_agent init-db                 # create tables (idempotent)
    python -m social_agent precompute --count 200  # pre-generate judged posts
    python -m social_agent post-profile [--event]  # one profile-post cycle
    python -m social_agent reply-cycle             # one reply cycle
    python -m social_agent status                  # inventory + cadence info
    python -m social_agent verify-share --id <id>  # check live share link
    python -m social_agent serve                   # long-running scheduler

Dry-run is automatic while X keys are absent; `--dry-run` forces it even with
keys present. See README for Windows Task Scheduler setup.
"""
from __future__ import annotations

import argparse
import asyncio
import json
import logging
import os
import sys

from . import db
from .config import Settings, load_settings
from .llm import LLMClient
from .pipeline import run_post_cycle, run_precompute, run_reply_cycle
from .search import make_search_provider
from .x_client import DryRunXClient, XClient, XCredentials


def _setup_logging(verbose: bool) -> None:
    logging.basicConfig(
        level=logging.DEBUG if verbose else logging.INFO,
        format="%(asctime)s %(levelname)-7s %(name)s: %(message)s",
        stream=sys.stdout,
    )
    logging.getLogger("httpx").setLevel(logging.WARNING)
    logging.getLogger("openai").setLevel(logging.WARNING)


def _make_x_client(settings: Settings):
    if settings.dry_run:
        return DryRunXClient()
    return XClient(
        XCredentials(
            api_key=settings.x_api_key,
            api_secret=settings.x_api_secret,
            access_token=settings.x_access_token,
            access_secret=settings.x_access_secret,
            bearer_token=settings.x_bearer_token,
        )
    )


async def _amain(args: argparse.Namespace) -> int:
    settings = load_settings()
    if args.dry_run:
        settings.dry_run = True
    log = logging.getLogger("social_agent")

    if not settings.database_url:
        log.error("DATABASE_URL / SOCIAL_DATABASE_URL is not set (see .env.example)")
        return 2

    pool = await db.connect_pool(settings.database_url)
    try:
        await db.ensure_schema(pool)

        if args.command == "init-db":
            print("schema ensured (social_profiles, social_posts, social_bot_state)")
            return 0

        if args.command == "status":
            s = await db.stats(pool)
            s["dry_run"] = settings.dry_run
            s["x_keys_present"] = settings.have_all_x_keys
            s["search_mode"] = settings.search_mode
            for key in ("last_post_cycle_at", "last_reply_cycle_at"):
                s[key] = await db.state_get(pool, key)
            print(json.dumps(s, indent=2, default=str))
            return 0

        if args.command == "verify-share":
            import httpx
            url = f"{settings.api_base}/api/v1/result/{args.id}"
            async with httpx.AsyncClient(timeout=30) as client:
                resp = await client.get(url)
            print(f"GET {url} -> {resp.status_code}")
            if resp.status_code == 200:
                print(json.dumps(resp.json(), indent=2)[:2000])
                return 0
            print(resp.text[:500])
            return 1

        if not settings.openai_api_key:
            log.error("OPENAI_API_KEY is not set (see .env.example)")
            return 2
        llm = LLMClient(settings.openai_api_key, embed_dim=settings.embed_dim)
        x_client = _make_x_client(settings)

        if args.command == "precompute":
            summary = await run_precompute(
                pool, llm, settings, args.count,
                event_mode=args.event, budget_usd=args.budget,
            )
            print(json.dumps(summary, indent=2))
            return 0 if summary["accepted"] > 0 else 1

        if args.command == "post-profile":
            # provider_name tells the cycle whether we're in posts-only mode
            # (no search tier) so the trend direction can flavor posts instead.
            provider = make_search_provider(settings, x_client)
            result = await run_post_cycle(
                pool, llm, settings, x_client,
                force_event=args.event, provider_name=provider.name,
            )
            print(json.dumps(result, indent=2))
            return 0 if (result.get("posted") or result.get("dry_run")) else 1

        if args.command == "reply-cycle":
            provider = make_search_provider(settings, x_client)
            result = await run_reply_cycle(pool, llm, settings, x_client, provider)
            print(json.dumps(result, indent=2))
            return 0

        if args.command == "serve":
            from .scheduler import serve
            provider = make_search_provider(settings, x_client)
            await serve(pool, llm, settings, x_client, provider)
            return 0

        log.error("unknown command %s", args.command)
        return 2
    finally:
        await pool.close()


def main() -> None:
    parser = argparse.ArgumentParser(prog="social_agent", description="quafel social bot")
    parser.add_argument("--verbose", action="store_true")
    parser.add_argument("--dry-run", action="store_true",
                        help="force dry-run even if X keys are configured")
    sub = parser.add_subparsers(dest="command", required=True)

    sub.add_parser("init-db")
    sub.add_parser("status")
    sub.add_parser("serve")

    p = sub.add_parser("precompute")
    p.add_argument("--count", type=int, default=100)
    p.add_argument("--budget", type=float, default=4.0, help="max LLM spend (USD)")
    p.add_argument("--event", action="store_true", help="theme the batch on a current event")

    p = sub.add_parser("post-profile")
    p.add_argument("--event", action="store_true", help="try a current-events themed post")

    sub.add_parser("reply-cycle")

    p = sub.add_parser("verify-share")
    p.add_argument("--id", required=True, help="result/session UUID to verify")

    args = parser.parse_args()
    _setup_logging(args.verbose)
    if os.name == "nt":
        asyncio.set_event_loop_policy(asyncio.WindowsSelectorEventLoopPolicy())
    raise SystemExit(asyncio.run(_amain(args)))


if __name__ == "__main__":
    main()
