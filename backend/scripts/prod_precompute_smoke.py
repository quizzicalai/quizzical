"""Production precompute smoke check (`AC-PRECOMP-OBJ-3`).

Confirms that the deployed API has at least ``--min-packs`` published
topic packs and that a representative sample of slugs resolves to a HIT
via the alias / slug lookup path. Designed to be invoked by CI after a
deploy or seed step:

    python -m scripts.prod_precompute_smoke \\
        --api-url https://api-quizzical-dev.... \\
        --min-packs 512 \\
        --sample-slugs hogwarts-house disney-princess

Exits 0 on success, 1 on any failure. Never logs the operator token.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import os
import sys
from typing import Any

import httpx

DEFAULT_API_URL = (
    "https://api-quizzical-dev.whitesea-815b33ea.westus2.azurecontainerapps.io"
)
HEALTHZ_PATH = "/api/v1/healthz/precompute"
SUGGEST_PATH = "/api/v1/topics/suggest"
TIMEOUT_S = 15.0


async def _fetch_health(client: httpx.AsyncClient, token: str) -> dict[str, Any]:
    resp = await client.get(
        HEALTHZ_PATH, headers={"Authorization": f"Bearer {token}"}
    )
    if resp.status_code != 200:
        raise SystemExit(
            f"healthz/precompute returned {resp.status_code}: {resp.text[:200]}"
        )
    return resp.json()


async def _check_suggest(
    client: httpx.AsyncClient, slug_fragment: str
) -> list[dict[str, str]]:
    """Hit the public typeahead with the first few chars of a known slug.

    Returns the result list (empty on miss). Uses a 4-char prefix because
    the endpoint enforces ``len(q) >= 2`` and most slugs are kebab-case
    (``hogwarts-house`` → ``hogw``).
    """
    q = slug_fragment.split("-")[0][:8]
    if len(q) < 2:
        return []
    resp = await client.get(SUGGEST_PATH, params={"q": q})
    if resp.status_code != 200:
        return []
    body = resp.json() or {}
    return list(body.get("results") or [])


async def _amain(argv: list[str] | None = None) -> int:
    args = _parse_args(argv)

    token = (os.getenv(args.token_env) or "").strip()
    if not token:
        print(
            f"ERROR: env var {args.token_env} is empty — cannot call /healthz/precompute",
            file=sys.stderr,
        )
        return 1

    failures: list[str] = []
    async with httpx.AsyncClient(base_url=args.api_url, timeout=TIMEOUT_S) as client:
        health = await _fetch_health(client, token)
        published = int(health.get("packs_published", 0))
        print(json.dumps({"packs_published": published}))

        if published < args.min_packs:
            failures.append(
                f"packs_published={published} below required {args.min_packs}"
            )

        for slug in args.sample_slugs:
            results = await _check_suggest(client, slug)
            slugs_returned = {r.get("slug") for r in results}
            if slug not in slugs_returned:
                failures.append(
                    f"sample slug {slug!r} not found via /topics/suggest "
                    f"(got {sorted(slugs_returned)[:5]})"
                )

    if failures:
        print("\nProduction precompute smoke FAILED:", file=sys.stderr)
        for f in failures:
            print(f"  - {f}", file=sys.stderr)
        return 1
    print("Production precompute smoke PASSED")
    return 0


def _parse_args(argv: list[str] | None) -> argparse.Namespace:
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--api-url", default=DEFAULT_API_URL)
    p.add_argument("--min-packs", type=int, default=1)
    p.add_argument(
        "--sample-slugs",
        nargs="*",
        default=[],
        help="Slugs to verify resolve via /topics/suggest typeahead.",
    )
    p.add_argument(
        "--token-env",
        default="OPERATOR_TOKEN",
        help="Env var containing the operator bearer token.",
    )
    return p.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    return asyncio.run(_amain(argv))


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
