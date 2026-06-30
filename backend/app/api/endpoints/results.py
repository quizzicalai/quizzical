# backend/app/api/endpoints/results.py
"""
API Endpoint for retrieving quiz results.

Includes:
- ``GET /result/{result_id}`` — JSON result for the SPA (unchanged contract).
- ``GET /result-meta/{result_id}`` — tiny SSR HTML document with per-result
  OpenGraph + Twitter-card tags so social crawlers (Facebook / Twitter /
  LinkedIn / iMessage / Slack) render a rich share card instead of the generic
  SPA shell. Humans hitting this URL are immediately bounced to the SPA result
  page via a meta-refresh + JS redirect, so it is safe to route either the
  ``/result/{id}`` path or just bot user-agents here (see
  ``frontend/staticwebapp.config.json``). P1 (audit Virality §A).
"""
import html
import os
from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Request, status
from fastapi.responses import HTMLResponse

from app.models.api import ShareableResultResponse

# FIX: The ResultService is now correctly defined in the database service module.
from app.services.database import ResultService

# Create an API router for the results endpoint
router = APIRouter(
    prefix="/result",
    tags=["Results"]
)

# ---------------------------------------------------------------------------
# Per-result OG / SSR meta (P1 Virality §A)
# ---------------------------------------------------------------------------

# Generic fallbacks — kept in sync with frontend/index.html so a crawler that
# fails to load a specific result still gets a sensible Quafel card rather than
# nothing. The default OG image path MUST match the asset the operator supplies
# at frontend/public/og-image.png (see README / PR notes).
_SITE_NAME = "Quafel"
_DEFAULT_TITLE = "Quafel"
_DEFAULT_DESCRIPTION = "Engaging AI-powered quizzes."
_DEFAULT_OG_IMAGE_PATH = "/og-image.png"

# Hard caps so a hostile/oversized stored result can never bloat the crawler
# response or smuggle markup. Values are escaped *and* truncated.
_MAX_TITLE_LEN = 120
_MAX_DESC_LEN = 300


def _public_base_url(request: Request) -> str:
    """Best-effort public origin for absolute canonical / og:url / og:image.

    Order of preference:
      1. ``PUBLIC_SITE_URL`` env (e.g. ``https://quafel.app``) — set this in
         production so cards point at the real front-end domain rather than the
         API host. Trailing slash is stripped.
      2. The forwarded/observed request origin (works behind SWA which proxies
         the same origin to the API).

    Never raises; falls back to the request base URL.
    """
    explicit = (os.getenv("PUBLIC_SITE_URL") or "").strip().rstrip("/")
    if explicit:
        return explicit
    # request.base_url is like "https://host/"; strip trailing slash.
    return str(request.base_url).rstrip("/")


def _abs_url(base: str, maybe_url: str | None, default_path: str) -> str:
    """Resolve an image URL to an absolute URL.

    - Already-absolute ``http(s)://`` URLs are returned unchanged.
    - Relative/empty values are joined onto ``base`` (falling back to the
      default OG image path). Crawlers require absolute og:image URLs.
    """
    candidate = (maybe_url or "").strip()
    if candidate.startswith("http://") or candidate.startswith("https://"):
        return candidate
    if not candidate:
        candidate = default_path
    if not candidate.startswith("/"):
        candidate = "/" + candidate
    return f"{base}{candidate}"


def _truncate(text: str, limit: int) -> str:
    text = " ".join((text or "").split())  # collapse whitespace/newlines
    if len(text) <= limit:
        return text
    return text[: max(0, limit - 1)].rstrip() + "…"


def _render_meta_html(
    *,
    title: str,
    description: str,
    image_url: str,
    canonical_url: str,
    redirect_path: str,
) -> str:
    """Render the minimal crawler HTML document.

    Every interpolated value is HTML-escaped. The document redirects humans to
    the SPA via ``<meta http-equiv="refresh">`` plus a JS fallback.
    """
    e_title = html.escape(title, quote=True)
    e_desc = html.escape(description, quote=True)
    e_image = html.escape(image_url, quote=True)
    e_canonical = html.escape(canonical_url, quote=True)
    e_site = html.escape(_SITE_NAME, quote=True)
    # redirect target is built from our own canonical_url; escape for both the
    # meta-refresh content attr and the JS string assignment.
    e_redirect_attr = html.escape(redirect_path, quote=True)
    # For the JS string, single-quote-escape so it cannot break out of the literal.
    js_redirect = redirect_path.replace("\\", "\\\\").replace("'", "\\'")

    return (
        "<!doctype html>\n"
        '<html lang="en">\n'
        "<head>\n"
        '<meta charset="utf-8" />\n'
        '<meta name="viewport" content="width=device-width, initial-scale=1" />\n'
        f"<title>{e_title}</title>\n"
        f'<meta name="description" content="{e_desc}" />\n'
        f'<link rel="canonical" href="{e_canonical}" />\n'
        # OpenGraph
        '<meta property="og:type" content="website" />\n'
        f'<meta property="og:site_name" content="{e_site}" />\n'
        f'<meta property="og:title" content="{e_title}" />\n'
        f'<meta property="og:description" content="{e_desc}" />\n'
        f'<meta property="og:image" content="{e_image}" />\n'
        f'<meta property="og:url" content="{e_canonical}" />\n'
        # Twitter card
        '<meta name="twitter:card" content="summary_large_image" />\n'
        f'<meta name="twitter:title" content="{e_title}" />\n'
        f'<meta name="twitter:description" content="{e_desc}" />\n'
        f'<meta name="twitter:image" content="{e_image}" />\n'
        # Bounce humans to the SPA result page.
        f'<meta http-equiv="refresh" content="0; url={e_redirect_attr}" />\n'
        "</head>\n"
        "<body>\n"
        f'<p>Redirecting to your result… <a href="{e_redirect_attr}">Continue</a></p>\n'
        f"<script>location.replace('{js_redirect}');</script>\n"
        "</body>\n"
        "</html>\n"
    )


@router.get(
    "/{result_id}",
    response_model=ShareableResultResponse,
    summary="Get a quiz result by its ID",
    description="Retrieves the detailed character profile and results for a completed quiz session.",
)
async def get_result(
    result_id: UUID,
    # FIX: Use Annotated to resolve B008 linting error regarding function calls in defaults
    result_service: Annotated[ResultService, Depends(ResultService)],
) -> ShareableResultResponse:
    """
    Handles the retrieval of a quiz result.

    - **result_id**: The unique identifier for the quiz result.
    - **result_service**: Dependency injection for the result service.

    Returns the result profile or raises a 404 error if not found.
    """
    result = await result_service.get_result_by_id(result_id)
    if not result:
        from app.core.error_codes import QF_RESULT_NOT_FOUND

        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail={
                "detail": "Result not found. It may have expired or never existed.",
                "code": QF_RESULT_NOT_FOUND,
            },
        )
    return result


# NOTE: declared on a SEPARATE router instance because this router is mounted
# with prefix="/result"; the meta endpoint lives at a sibling path
# "/result-meta/{id}". Re-using the same router would force "/result/meta-..."
# which would collide with the UUID path param above.
meta_router = APIRouter(tags=["Results"])


@meta_router.get(
    "/result-meta/{result_id}",
    response_class=HTMLResponse,
    include_in_schema=False,
    summary="Crawler-facing OG/SSR meta for a shared result",
)
async def get_result_meta(
    result_id: UUID,
    request: Request,
    result_service: Annotated[ResultService, Depends(ResultService)],
) -> HTMLResponse:
    """Return a tiny HTML doc with per-result OG/Twitter tags for crawlers.

    Fail-safe by design: any lookup failure (missing result, DB error) yields a
    *generic* Quafel card with HTTP 200 — we never 500 a crawler, because a 5xx
    makes Facebook/Twitter drop the card entirely. Humans are redirected to the
    SPA result page.
    """
    base = _public_base_url(request)
    # SPA route for humans (and the canonical URL we advertise to crawlers).
    canonical_url = f"{base}/result/{result_id}"
    redirect_path = f"/result/{result_id}"

    title = _DEFAULT_TITLE
    description = _DEFAULT_DESCRIPTION
    image_url = _abs_url(base, None, _DEFAULT_OG_IMAGE_PATH)

    try:
        result = await result_service.get_result_by_id(result_id)
    except Exception:
        result = None

    if result is not None:
        # result is a ShareableResultResponse (title/description/image_url).
        raw_title = (getattr(result, "title", None) or "").strip()
        raw_desc = (getattr(result, "description", None) or "").strip()
        raw_image = getattr(result, "image_url", None)
        if raw_title:
            title = _truncate(raw_title, _MAX_TITLE_LEN)
        if raw_desc:
            description = _truncate(raw_desc, _MAX_DESC_LEN)
        image_url = _abs_url(base, raw_image, _DEFAULT_OG_IMAGE_PATH)

    body = _render_meta_html(
        title=title,
        description=description,
        image_url=image_url,
        canonical_url=canonical_url,
        redirect_path=redirect_path,
    )
    # Short cache so crawlers can re-fetch updated cards (e.g. once the result
    # image finishes generating) without hammering the API.
    return HTMLResponse(
        content=body,
        status_code=status.HTTP_200_OK,
        headers={"Cache-Control": "public, max-age=300"},
    )
