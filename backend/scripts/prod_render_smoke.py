"""Production heavy render smoke — full quiz walk against a live API.

Walks a real quiz session against the deployed API to verify that
pre-populated content (synopsis + characters + baseline questions +
images + final result) renders correctly end-to-end.

Designed to run after every deploy and on a nightly schedule so any
regression in pre-populated content (broken image URLs, missing
characters, malformed questions, agent fallback unexpectedly triggered)
fails CI rather than only failing real user traffic.

Steps for each ``--slug``:

  1. POST ``/api/v1/quiz/start`` with the slug's display name as
     ``category``. Assert 201 + synopsis present + ≥ 1 character.
  2. POST ``/api/v1/quiz/proceed`` to unlock baseline question
     generation. Assert 202.
  3. Poll GET ``/api/v1/quiz/status/{quizId}`` until a ``"question"``
     payload is returned (or until --max-poll-s elapses).
  4. Loop: answer the next question (POST ``/api/v1/quiz/next`` with
     ``option_index=0``), poll status, until a ``"result"`` payload is
     returned. Assert title + description present.
  5. GET ``/api/v1/quiz/{quizId}/media`` and assert at least one
     character has a non-null ``image_url`` (pre-populated content
     should always have character images).

Usage:

    python -m scripts.prod_render_smoke \
        --api-url https://api-quizzical-dev.... \
        --slugs hogwarts-house disney-princess

Exit codes:
  0 — all walks completed successfully
  1 — any failure (asserts, timeouts, HTTP errors)

Never logs OPERATOR_TOKEN or any secret. Bearer token is read from
``OPERATOR_TOKEN`` env (only used by the optional pre-flight pack-count
gate; the public quiz endpoints are unauthenticated).
"""

from __future__ import annotations

import argparse
import asyncio
import json
import os
import sys
import time
from dataclasses import dataclass, field
from typing import Any

import httpx

DEFAULT_API_URL = (
    "https://api-quizzical-dev.whitesea-815b33ea.westus2.azurecontainerapps.io"
)
DEFAULT_TURNSTILE_TOKEN = "1x00000000000000000000AA"  # Cloudflare always-pass test token
DEFAULT_SLUGS = ("hogwarts-house", "disney-princess")

START_PATH = "/api/v1/quiz/start"
PROCEED_PATH = "/api/v1/quiz/proceed"
NEXT_PATH = "/api/v1/quiz/next"
STATUS_FMT = "/api/v1/quiz/status/{quiz_id}"
MEDIA_FMT = "/api/v1/quiz/{quiz_id}/media"

DEFAULT_MAX_POLL_S = 60.0
DEFAULT_POLL_INTERVAL_S = 1.0
DEFAULT_MAX_QUESTIONS = 12
DEFAULT_TIMEOUT_S = 30.0


@dataclass
class WalkResult:
    slug: str
    ok: bool
    quiz_id: str | None = None
    questions_answered: int = 0
    final_title: str | None = None
    character_image_count: int = 0
    elapsed_s: float = 0.0
    failures: list[str] = field(default_factory=list)


async def _resolve_display_name(
    client: httpx.AsyncClient, slug: str
) -> str:
    """Best-effort: hit /topics/suggest with a slug prefix and return the
    matching display_name. Falls back to a humanised slug on miss."""
    q = slug.split("-")[0][:8]
    try:
        resp = await client.get("/api/v1/topics/suggest", params={"q": q})
        if resp.status_code == 200:
            for row in (resp.json() or {}).get("results") or []:
                if row.get("slug") == slug:
                    return str(row.get("display_name") or slug)
    except httpx.HTTPError:
        pass
    return slug.replace("-", " ").title()


async def _start_quiz(
    client: httpx.AsyncClient, *, category: str, turnstile_token: str
) -> dict[str, Any]:
    resp = await client.post(
        START_PATH,
        json={
            "category": category,
            "cf-turnstile-response": turnstile_token,
        },
    )
    if resp.status_code != 201:
        raise AssertionError(
            f"/quiz/start expected 201 for category={category!r}, "
            f"got {resp.status_code}: {resp.text[:300]}"
        )
    return resp.json()


async def _proceed(client: httpx.AsyncClient, *, quiz_id: str) -> None:
    resp = await client.post(PROCEED_PATH, json={"quizId": quiz_id})
    if resp.status_code != 202:
        raise AssertionError(
            f"/quiz/proceed expected 202, got {resp.status_code}: "
            f"{resp.text[:300]}"
        )


async def _poll_status(
    client: httpx.AsyncClient,
    *,
    quiz_id: str,
    known_questions_count: int,
    max_wait_s: float,
    poll_interval_s: float,
) -> dict[str, Any]:
    """Poll /quiz/status until a non-'processing' payload arrives or we
    exceed max_wait_s. Returns the final payload."""
    deadline = time.monotonic() + max_wait_s
    last_body: dict[str, Any] = {"status": "processing"}
    while time.monotonic() < deadline:
        resp = await client.get(
            STATUS_FMT.format(quiz_id=quiz_id),
            params={"known_questions_count": known_questions_count},
        )
        if resp.status_code != 200:
            raise AssertionError(
                f"/quiz/status expected 200, got {resp.status_code}: "
                f"{resp.text[:300]}"
            )
        body = resp.json()
        last_body = body
        if body.get("status") != "processing":
            return body
        await asyncio.sleep(poll_interval_s)
    raise AssertionError(
        f"/quiz/status did not leave 'processing' within {max_wait_s}s; "
        f"last body={json.dumps(last_body)[:300]}"
    )


async def _answer_question(
    client: httpx.AsyncClient, *, quiz_id: str, question_index: int
) -> None:
    resp = await client.post(
        NEXT_PATH,
        json={
            "quizId": quiz_id,
            "questionIndex": question_index,
            "optionIndex": 0,
        },
    )
    if resp.status_code not in (200, 202):
        raise AssertionError(
            f"/quiz/next expected 202, got {resp.status_code}: "
            f"{resp.text[:300]}"
        )


async def _fetch_media(
    client: httpx.AsyncClient, *, quiz_id: str
) -> dict[str, Any]:
    resp = await client.get(MEDIA_FMT.format(quiz_id=quiz_id))
    if resp.status_code != 200:
        raise AssertionError(
            f"/quiz/{{id}}/media expected 200, got {resp.status_code}: "
            f"{resp.text[:300]}"
        )
    return resp.json()


async def _walk_one(
    client: httpx.AsyncClient,
    *,
    slug: str,
    turnstile_token: str,
    max_poll_s: float,
    poll_interval_s: float,
    max_questions: int,
    require_character_images: bool,
) -> WalkResult:
    started = time.monotonic()
    result = WalkResult(slug=slug, ok=False)
    try:
        display_name = await _resolve_display_name(client, slug)
        # 1) Start
        start_body = await _start_quiz(
            client, category=display_name, turnstile_token=turnstile_token,
        )
        quiz_id = str(start_body.get("quizId") or "")
        if not quiz_id:
            raise AssertionError("/quiz/start response missing quizId")
        result.quiz_id = quiz_id

        initial = start_body.get("initialPayload") or {}
        if initial.get("type") != "synopsis":
            raise AssertionError(
                f"initialPayload.type expected 'synopsis', got {initial.get('type')!r}"
            )
        synopsis = initial.get("data") or {}
        if not synopsis.get("title") or not synopsis.get("summary"):
            raise AssertionError(
                "synopsis missing title/summary — pre-populated content is incomplete"
            )

        chars_payload = start_body.get("charactersPayload") or {}
        chars = chars_payload.get("data") or []
        if not chars:
            raise AssertionError(
                "charactersPayload empty — pre-populated content is incomplete"
            )

        # 2) Proceed
        await _proceed(client, quiz_id=quiz_id)

        # 3 + 4) Poll + answer loop
        answered = 0
        while answered < max_questions:
            status_body = await _poll_status(
                client,
                quiz_id=quiz_id,
                known_questions_count=answered,
                max_wait_s=max_poll_s,
                poll_interval_s=poll_interval_s,
            )
            payload_type = status_body.get("type")
            if payload_type == "result":
                final = status_body.get("data") or {}
                if not final.get("title") or not final.get("description"):
                    raise AssertionError(
                        "final result missing title/description"
                    )
                result.final_title = str(final.get("title"))
                break
            if payload_type != "question":
                raise AssertionError(
                    f"/quiz/status returned unexpected type={payload_type!r}: "
                    f"{json.dumps(status_body)[:300]}"
                )
            qdata = status_body.get("data") or {}
            opts = qdata.get("options") or []
            if not qdata.get("text") or not opts:
                raise AssertionError(
                    f"question payload missing text or options: "
                    f"{json.dumps(qdata)[:300]}"
                )
            await _answer_question(
                client, quiz_id=quiz_id, question_index=answered,
            )
            answered += 1
            result.questions_answered = answered
        else:
            raise AssertionError(
                f"quiz did not finish within {max_questions} questions"
            )

        # 5) Media snapshot
        media = await _fetch_media(client, quiz_id=quiz_id)
        media_chars = media.get("characters") or []
        with_images = [c for c in media_chars if c.get("imageUrl") or c.get("image_url")]
        result.character_image_count = len(with_images)
        if require_character_images and not with_images:
            raise AssertionError(
                "no character images returned by /media — "
                "pre-populated content has no rendered character art"
            )

        result.ok = True
    except AssertionError as exc:
        result.failures.append(str(exc))
    except httpx.HTTPError as exc:
        result.failures.append(f"HTTP error: {exc!r}")
    except Exception as exc:  # noqa: BLE001
        result.failures.append(f"unexpected error: {exc!r}")
    finally:
        result.elapsed_s = round(time.monotonic() - started, 2)
    return result


async def _amain(argv: list[str] | None = None) -> int:
    args = _parse_args(argv)
    turnstile_token = (
        os.getenv("PROD_SMOKE_TURNSTILE_TOKEN") or args.turnstile_token
    )

    failures: list[str] = []
    walk_results: list[WalkResult] = []
    async with httpx.AsyncClient(
        base_url=args.api_url, timeout=args.timeout_s
    ) as client:
        # Optional pre-flight: quick pack-count check via /healthz/precompute
        # if an operator token is available. Cheap fail-fast for env misconfig.
        token = (os.getenv("OPERATOR_TOKEN") or "").strip()
        if token and args.min_packs > 0:
            try:
                resp = await client.get(
                    "/api/v1/healthz/precompute",
                    headers={"Authorization": f"Bearer {token}"},
                )
                if resp.status_code != 200:
                    failures.append(
                        f"healthz/precompute returned {resp.status_code}"
                    )
                else:
                    published = int(resp.json().get("packs_published", 0))
                    print(json.dumps({"packs_published": published}))
                    if published < args.min_packs:
                        failures.append(
                            f"packs_published={published} < required {args.min_packs}"
                        )
            except httpx.HTTPError as exc:
                failures.append(f"healthz/precompute fetch failed: {exc!r}")

        for idx, slug in enumerate(args.slugs):
            if idx > 0 and args.inter_slug_delay_s > 0:
                # Space out heavy walks to avoid tripping production rate
                # limits on shared per-IP buckets.
                await asyncio.sleep(args.inter_slug_delay_s)
            walk = await _walk_one(
                client,
                slug=slug,
                turnstile_token=turnstile_token,
                max_poll_s=args.max_poll_s,
                poll_interval_s=args.poll_interval_s,
                max_questions=args.max_questions,
                require_character_images=not args.allow_missing_images,
            )
            walk_results.append(walk)
            print(json.dumps({
                "slug": walk.slug,
                "ok": walk.ok,
                "quiz_id": walk.quiz_id,
                "questions_answered": walk.questions_answered,
                "character_images": walk.character_image_count,
                "elapsed_s": walk.elapsed_s,
                "failures": walk.failures,
            }))
            if not walk.ok:
                failures.extend(f"{walk.slug}: {f}" for f in walk.failures)

    if failures:
        print("\nProduction render smoke FAILED:", file=sys.stderr)
        for f in failures:
            print(f"  - {f}", file=sys.stderr)
        return 1
    print("\nProduction render smoke PASSED ({} walks)".format(len(walk_results)))
    return 0


def _parse_args(argv: list[str] | None) -> argparse.Namespace:
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--api-url", default=DEFAULT_API_URL)
    p.add_argument(
        "--slugs",
        nargs="+",
        default=list(DEFAULT_SLUGS),
        help="Topic slugs to walk (default: a small set of known starter slugs).",
    )
    p.add_argument(
        "--turnstile-token",
        default=DEFAULT_TURNSTILE_TOKEN,
        help="Turnstile token to send. Default is Cloudflare's always-pass test token.",
    )
    p.add_argument(
        "--min-packs",
        type=int,
        default=0,
        help="If > 0 and OPERATOR_TOKEN is set, pre-flight assert packs_published >= N.",
    )
    p.add_argument("--max-poll-s", type=float, default=DEFAULT_MAX_POLL_S)
    p.add_argument("--poll-interval-s", type=float, default=DEFAULT_POLL_INTERVAL_S)
    p.add_argument("--max-questions", type=int, default=DEFAULT_MAX_QUESTIONS)
    p.add_argument("--timeout-s", type=float, default=DEFAULT_TIMEOUT_S)
    p.add_argument(
        "--inter-slug-delay-s",
        type=float,
        default=0.0,
        help="Seconds to sleep between slug walks. Use to avoid prod rate limits.",
    )
    p.add_argument(
        "--allow-missing-images",
        action="store_true",
        help="Do not fail when /media returns zero character images.",
    )
    return p.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    return asyncio.run(_amain(argv))


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
