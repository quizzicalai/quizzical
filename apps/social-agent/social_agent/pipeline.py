"""Orchestration: precompute, profile-post cycle, reply cycle.

Every path shares the same non-negotiable gates, in order:
  1. deterministic filters   (window, visibility, sensitivity prefilter)
  2. uniqueness gate         (exact + semantic, vs ALL past posts/replies)
  3. strong-judge gate       (gpt-4o class; refuse-by-default)
  4. write-budget guard      (X free tier ~500 writes/mo; we cap lower)
Only then does anything reach the X client — which in dry-run mode logs and
stores but posts nothing.
"""
from __future__ import annotations

import logging
import random
import uuid
from typing import Any

import asyncpg

from . import db
from .config import Settings
from .discovery import (
    TOPIC,
    TREND,
    DiscoveredCandidate,
    SearchDirective,
    fallback_topic_directives,
    parse_direction_plan,
    run_directives,
    should_trend_flavor_post,
)
from .generator import (
    fetch_event_summary,
    fetch_trends,
    generate_direction_plan,
    generate_post_candidates,
    generate_replies,
)
from .judge import JudgeVerdict, build_judge_user_prompt, JUDGE_SYSTEM_PROMPT, parse_judge_response
from .llm import LLMClient
from .search import DEFAULT_QUERY, SearchProvider
from .textutils import fits_tweet, normalize_for_dedup, render_with_link
from .uniqueness import UniquenessGate
from .visibility import (
    TweetCandidate,
    VisibilityPolicy,
    sensitivity_prefilter,
    visibility_check,
)
from .windowing import window_start, within_window

log = logging.getLogger("social_agent.pipeline")


async def judge_candidates(
    llm: LLMClient,
    model: str,
    candidates: list[dict[str, Any]],
    kind: str,
) -> list[JudgeVerdict]:
    """Run the strong judge over a batch; parsing refuses by default."""
    if not candidates:
        return []
    user = build_judge_user_prompt(candidates, kind)
    try:
        raw = await llm.chat_json(model, JUDGE_SYSTEM_PROMPT, user, temperature=0.0)
    except Exception:  # noqa: BLE001 — judge unavailable = nothing posts
        log.exception("judge call failed — refusing entire batch by default")
        raw = ""
    return parse_judge_response(raw, len(candidates), kind=kind)


async def _load_gate(pool: asyncpg.Pool) -> UniquenessGate:
    norms, embs = await db.load_dedup_corpus(pool)
    return UniquenessGate(existing_norms=norms, existing_embeddings=embs)


# =============================================================================
# Precompute
# =============================================================================

async def run_precompute(
    pool: asyncpg.Pool,
    llm: LLMClient,
    settings: Settings,
    count: int,
    *,
    event_mode: bool = False,
    budget_usd: float = 4.0,
    batch_size: int = 25,
) -> dict[str, Any]:
    """Generate `count` unique, judged profile posts and store them as planned.

    Iterates generate -> dedup -> judge -> insert until `count` accepted or
    the attempt/budget caps are hit. Uniqueness is enforced vs ALL existing
    non-rejected rows AND within the run itself.
    """
    gate = await _load_gate(pool)
    avoid = [t for _, t in gate.existing_embeddings][-200:]
    accepted = 0
    rejected_judge = 0
    rejected_dup = 0
    batches = 0
    max_batches = max(4, (count // batch_size + 1) * 4)

    event_summary = None
    if event_mode:
        event_summary = await fetch_event_summary(llm)
        if event_summary:
            log.info("event mode: %s", event_summary[:200])

    while accepted < count and batches < max_batches:
        if llm.usage.approx_cost_usd > budget_usd:
            log.warning("LLM budget cap ($%.2f) reached; stopping", budget_usd)
            break
        batches += 1
        want = min(batch_size, count - accepted)
        candidates = await generate_post_candidates(
            llm, settings.gen_model, want, avoid, event_summary
        )
        if not candidates:
            continue

        # --- uniqueness (exact + semantic), also within this batch ---------
        texts = [c["text"] for c in candidates]
        embeddings = await llm.embed(settings.embed_model, texts)
        survivors: list[dict[str, Any]] = []
        for cand, emb in zip(candidates, embeddings):
            norm = normalize_for_dedup(cand["text"])
            res = gate.check(norm, emb)
            if not res.unique:
                rejected_dup += 1
                await db.insert_post(
                    pool, kind="post", status="rejected", text=cand["text"],
                    text_norm=norm, embedding=emb,
                    rejected_reason=f"uniqueness: {res.reason}",
                )
                continue
            cand["_norm"], cand["_emb"] = norm, emb
            gate.admit(norm, emb, cand["text"])  # dedup within the batch too
            survivors.append(cand)

        # --- strong judge ---------------------------------------------------
        for i in range(0, len(survivors), 10):
            chunk = survivors[i : i + 10]
            verdicts = await judge_candidates(llm, settings.judge_model, chunk, "post")
            for cand, verdict in zip(chunk, verdicts):
                payload = {
                    "title": cand["profile_title"],
                    "description": cand["profile_description"],
                    "category": cand["category"],
                }
                if verdict.approve:
                    post_id = await db.insert_post(
                        pool, kind="post", status="planned", text=cand["text"],
                        text_norm=cand["_norm"], embedding=cand["_emb"],
                        profile_payload=payload,
                        event_tag=cand.get("event_tag") if event_summary else None,
                        judge_verdicts=[verdict.to_dict()],
                    )
                    if post_id:
                        accepted += 1
                        avoid.append(cand["text"])
                else:
                    rejected_judge += 1
                    # un-admit not needed: rejected texts may never post, and
                    # keeping them in the gate only makes us MORE unique.
                    await db.insert_post(
                        pool, kind="post", status="rejected", text=cand["text"],
                        text_norm=cand["_norm"], embedding=cand["_emb"],
                        profile_payload=payload,
                        judge_verdicts=[verdict.to_dict()],
                        rejected_reason=f"judge: {verdict.reason}",
                    )
    summary = {
        "accepted": accepted,
        "rejected_by_judge": rejected_judge,
        "rejected_as_duplicate": rejected_dup,
        "batches": batches,
        "approx_llm_cost_usd": round(llm.usage.approx_cost_usd, 4),
    }
    log.info("precompute done: %s", summary)
    return summary


# =============================================================================
# Profile-post cycle (every 12h)
# =============================================================================

async def run_post_cycle(
    pool: asyncpg.Pool,
    llm: LLMClient,
    settings: Settings,
    x_client,
    *,
    force_event: bool = False,
    provider_name: str = "",
) -> dict[str, Any]:
    """Take the next planned post, double-check it with the judge, mint a real
    shareable profile, and post (or dry-run log) it.

    In posts-only mode (``provider_name == "no-search"``) the trend-led
    direction can't express itself through replies, so roughly every third
    profile post is trend/event flavored instead (see
    discovery.should_trend_flavor_post).
    """
    expired = await db.expire_stale_event_posts(pool)
    if expired:
        log.info("skipped %d stale event-flavored posts (event window passed)", expired)

    flavored = should_trend_flavor_post(
        force_event=force_event,
        events_enabled=settings.events_enabled,
        provider_name=provider_name,
        roll=random.random(),
    )
    if flavored:
        log.info("post cycle: trying a trend/event-flavored post this round")
        made = await run_precompute(
            pool, llm, settings, 1, event_mode=True, budget_usd=0.5, batch_size=3
        )
        if force_event and not made["accepted"]:
            log.warning("event-mode generation produced nothing; using planned pool")

    for _attempt in range(5):
        row = await db.next_planned_post(pool, prefer_event=flavored and _attempt == 0)
        if row is None:
            log.warning("planned pool empty — topping up with a fresh precompute batch")
            topped = await run_precompute(pool, llm, settings, 5, budget_usd=1.0, batch_size=5)
            if not topped["accepted"]:
                return {"posted": False, "reason": "no planned posts and top-up failed"}
            continue

        post_id, body = row["id"], row["text"]
        payload = row["profile_payload"]
        if isinstance(payload, str):
            import json as _json
            payload = _json.loads(payload)
        if not payload or not payload.get("title"):
            await db.mark_status(pool, post_id, "rejected", "missing profile payload")
            continue

        # Double-check gate at post time (owner requirement: evaluate before
        # ANY post; content was judged at precompute, judge it again now).
        verdicts = await judge_candidates(
            llm, settings.judge_model, [{"text": body}], "post"
        )
        verdict = verdicts[0] if verdicts else None
        if verdict is None or not verdict.approve:
            reason = verdict.reason if verdict else "judge unavailable"
            log.info("post-time judge rejected planned post %s: %s", post_id, reason)
            await db.mark_status(pool, post_id, "rejected", f"post-time judge: {reason}")
            continue

        # Mint the real, verifiable shareable result page (reuse the profile
        # if a previous dry-run of this row already minted one).
        existing = await db.get_profile(pool, row["profile_id"]) if row["profile_id"] else None
        if existing:
            profile_id = existing["id"]
            session_id = existing["session_id"]
            share_url = existing["share_url"]
        else:
            profile_id, session_id, share_url = await db.mint_profile(
                pool,
                title=payload["title"],
                description=payload["description"],
                category=payload.get("category", "quafel personalities"),
                site_base=settings.site_base,
            )
            await db.attach_profile(pool, post_id, profile_id)
        final_text = render_with_link(body, share_url)
        if not fits_tweet(final_text):
            await db.mark_status(pool, post_id, "rejected", "rendered text exceeds tweet limit")
            continue

        # Write-budget guard (free tier ~500 writes/mo; we stay under 450).
        if not x_client.dry_run:
            used = await db.writes_this_month(pool)
            if used >= settings.max_writes_per_month:
                return {"posted": False, "reason": f"monthly write budget reached ({used})"}

        tweet_id = await x_client.post_tweet(final_text)
        if x_client.dry_run:
            await db.state_set(pool, "last_dry_run_post", {"post_id": str(post_id), "text": final_text})
            log.info("[DRY-RUN] post cycle complete — row stays planned; nothing posted")
            return {
                "posted": False, "dry_run": True, "post_id": str(post_id),
                "session_id": str(session_id), "share_url": share_url,
                "would_post_text": final_text,
                "judge": verdict.to_dict(),
            }
        if tweet_id is None:
            return {"posted": False, "reason": "X API write failed (see logs)"}
        await db.mark_posted(
            pool, post_id, posted_text=final_text, posted_tweet_id=tweet_id, profile_id=profile_id
        )
        return {
            "posted": True, "tweet_id": tweet_id, "post_id": str(post_id),
            "session_id": str(session_id), "share_url": share_url, "text": final_text,
        }
    return {"posted": False, "reason": "no planned post survived the post-time gates"}


# =============================================================================
# Reply cycle (every 4h — only posts from the last 4h are considered)
# =============================================================================

async def filter_reply_targets(
    pool: asyncpg.Pool,
    candidates: list[TweetCandidate],
    settings: Settings,
    now=None,
    policy: VisibilityPolicy | None = None,
) -> tuple[list[TweetCandidate], list[dict[str, str]]]:
    """Deterministic filters: recency window, visibility, sensitivity, dedup."""
    kept: list[TweetCandidate] = []
    skipped: list[dict[str, str]] = []
    for t in candidates:
        if t.created_at and not within_window(t.created_at, now=now, window_hours=settings.reply_every_hours):
            skipped.append({"tweet_id": t.tweet_id, "reason": "outside recency window"})
            continue
        vis = visibility_check(t, policy)
        if not vis.engage:
            skipped.append({"tweet_id": t.tweet_id, "reason": f"visibility: {vis.reason}"})
            continue
        sens = sensitivity_prefilter(t.text)
        if sens.sensitive:
            skipped.append({
                "tweet_id": t.tweet_id,
                "reason": f"sensitivity prefilter: {', '.join(sens.matched[:3])}",
            })
            continue
        if await db.already_replied_target(pool, t.tweet_id):
            skipped.append({"tweet_id": t.tweet_id, "reason": "already replied to this tweet"})
            continue
        if await db.author_in_cooldown(pool, t.author_username, settings.author_cooldown_days):
            skipped.append({"tweet_id": t.tweet_id, "reason": "author in cooldown"})
            continue
        kept.append(t)
    return kept, skipped


async def build_discovery_plan(
    pool: asyncpg.Pool,
    llm: LLMClient,
    settings: Settings,
    *,
    base_query: str = DEFAULT_QUERY,
) -> tuple[list[SearchDirective], str | None]:
    """Plan BOTH discovery directions for this cycle (owner requirement).

    - trend-led: web-search probe for today's lighthearted trends, turned
      into playful personality angles + search terms by the planner LLM.
    - topic-led: silly topics picked FIRST (sampled from the banked pool +
      one freshly invented), turned into search terms.
    The evergreen personality-chatter query always rides along as a topic-led
    directive (this is the pre-dual-discovery behavior, preserved).
    """
    trends_text: str | None = None
    if settings.reply_trends_enabled:
        trends_text = await fetch_trends(llm)
        if trends_text:
            log.info("trend probe: %s", " | ".join(trends_text.splitlines())[:300])
        else:
            log.info("trend probe: no suitable trends this cycle")

    banked = await db.sample_banked_topics(pool, 12)
    directives: list[SearchDirective] = []
    try:
        raw = await generate_direction_plan(llm, settings.gen_model, trends_text, banked)
        directives = parse_direction_plan(raw)
    except Exception:  # noqa: BLE001 — planner failure must not kill the cycle
        log.exception("direction planner failed; falling back to banked topics")

    if not any(d.direction == TOPIC for d in directives):
        directives += fallback_topic_directives(banked)
    directives.append(
        SearchDirective(
            direction=TOPIC,
            label="personality-chatter",
            angle="generic silly personality-quiz banter",
            raw_query=base_query,
        )
    )
    log.info(
        "discovery plan: %s",
        "; ".join(f"{d.direction}/{d.label} -> {d.query()[:80]!r}" for d in directives),
    )
    return directives, trends_text


async def run_reply_cycle(
    pool: asyncpg.Pool,
    llm: LLMClient,
    settings: Settings,
    x_client,
    provider: SearchProvider,
    *,
    query: str = DEFAULT_QUERY,
    now=None,
) -> dict[str, Any]:
    start = window_start(now=now, window_hours=settings.reply_every_hours)

    # Dual-direction discovery -> ONE ranked pool (dedup by tweet id, with
    # direction tags merged) BEFORE the unchanged gate gauntlet.
    directives, _trends = await build_discovery_plan(pool, llm, settings, base_query=query)
    pool_cands, dstats = await run_directives(provider, directives, start)
    if not pool_cands:
        return {
            "replied": 0, "provider": provider.name, "discovery": dstats,
            "reason": "no candidates (free tier has no search — see README tier notes)"
            if provider.name == "no-search" else "search returned nothing in window",
        }
    by_id: dict[str, DiscoveredCandidate] = {c.tweet.tweet_id: c for c in pool_cands}

    kept, skipped = await filter_reply_targets(
        pool, [c.tweet for c in pool_cands], settings, now=now
    )
    log.info("reply targets: %d kept, %d skipped (of merged pool %d)",
             len(kept), len(skipped), len(pool_cands))
    if not kept:
        return {"replied": 0, "provider": provider.name, "discovery": dstats, "skipped": skipped}

    # A few finalists so we have alternatives if the judge refuses some.
    # `kept` preserves the merged pool's rank order.
    finalists = kept[:4]
    metas = [by_id[t.tweet_id] for t in finalists]
    angles = ["; ".join(m.angles) for m in metas]
    drafts = await generate_replies(llm, settings.gen_model, finalists, angles=angles)

    gate = await _load_gate(pool)
    results: list[dict[str, Any]] = []
    replied = 0
    for idx, target in enumerate(finalists):
        if replied >= settings.replies_per_cycle:
            break
        meta = metas[idx]
        event_tag = meta.labels[0] if meta.primary_direction == TREND else None
        text = drafts.get(idx)
        if not text:
            results.append({"tweet_id": target.tweet_id, "outcome": "no draft generated",
                            "directions": meta.directions})
            continue
        norm = normalize_for_dedup(text)
        emb = (await llm.embed(settings.embed_model, [text]))[0]
        uniq = gate.check(norm, emb)
        if not uniq.unique:
            await db.insert_post(
                pool, kind="reply", status="rejected", text=text, text_norm=norm,
                embedding=emb, target_tweet_id=target.tweet_id,
                target_tweet_text=target.text, target_author=target.author_username,
                event_tag=event_tag, judge_verdicts=[meta.meta()],
                rejected_reason=f"uniqueness: {uniq.reason}",
            )
            results.append({"tweet_id": target.tweet_id, "outcome": f"uniqueness: {uniq.reason}",
                            "directions": meta.directions})
            continue

        verdicts = await judge_candidates(
            llm, settings.judge_model,
            [{"text": text, "target_text": target.text, "target_author": target.author_username}],
            "reply",
        )
        verdict = verdicts[0]
        if not verdict.approve:
            await db.insert_post(
                pool, kind="reply", status="rejected", text=text, text_norm=norm,
                embedding=emb, target_tweet_id=target.tweet_id,
                target_tweet_text=target.text, target_author=target.author_username,
                event_tag=event_tag,
                judge_verdicts=[verdict.to_dict(), meta.meta()],
                rejected_reason=f"judge: {verdict.reason}",
            )
            results.append({"tweet_id": target.tweet_id,
                            "outcome": f"judge rejected: {verdict.reason}",
                            "directions": meta.directions})
            continue

        if not x_client.dry_run:
            used = await db.writes_this_month(pool)
            if used >= settings.max_writes_per_month:
                results.append({"tweet_id": target.tweet_id, "outcome": "monthly write budget reached"})
                break

        tweet_id = await x_client.reply_to(text, target.tweet_id)
        status = "planned" if x_client.dry_run else ("posted" if tweet_id else "planned")
        post_id = await db.insert_post(
            pool, kind="reply", status=status, text=text, text_norm=norm,
            embedding=emb, target_tweet_id=target.tweet_id,
            target_tweet_text=target.text, target_author=target.author_username,
            event_tag=event_tag,
            judge_verdicts=[verdict.to_dict(), meta.meta()],
        )
        gate.admit(norm, emb, text)
        if not x_client.dry_run and tweet_id and post_id:
            await db.mark_posted(pool, post_id, posted_text=text, posted_tweet_id=tweet_id)
        replied += 1
        log.info("reply sourced by %s (labels: %s)", "+".join(meta.directions),
                 ", ".join(meta.labels))
        results.append({
            "tweet_id": target.tweet_id,
            "outcome": "[DRY-RUN] would reply" if x_client.dry_run else "replied",
            "text": text,
            "directions": meta.directions,
            "discovery": meta.meta()["discovery"],
            "judge": verdict.to_dict(),
        })
    return {
        "replied": replied, "provider": provider.name, "discovery": dstats,
        "dry_run": x_client.dry_run, "results": results, "skipped": skipped,
    }
