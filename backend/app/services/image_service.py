# app/services/image_service.py
"""FAL.ai image generation client (§7.8).

Speed-first design:
- Single async ``generate(prompt)`` returns ``Optional[str]`` and never raises.
- Hard ``asyncio.wait_for`` timeout per call.
- Module-level ``asyncio.Semaphore`` bounds in-flight FAL calls.
- ``image_gen.enabled = False`` short-circuits without invoking ``fal_client``.

The ``FAL_KEY`` env var is read by ``fal_client`` directly. We accept the
legacy aliases ``FAL_AI_KEY`` and ``FAL_AI_API_KEY`` (used by elf-BE) and
mirror them onto ``FAL_KEY`` at import time.
"""
from __future__ import annotations

import asyncio
import os
import re
from typing import Any, Dict, Optional

import structlog

# ---- Env aliasing: mirror legacy var names onto FAL_KEY ----
for _alias in ("FAL_AI_KEY", "FAL_AI_API_KEY"):
    if not os.getenv("FAL_KEY") and os.getenv(_alias):
        os.environ["FAL_KEY"] = os.environ[_alias]

import fal_client  # noqa: E402  (imported after env aliasing)

from app.core.config import settings  # noqa: E402
from app.services.retry import retry_async  # noqa: E402

logger = structlog.get_logger(__name__)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _image_gen_enabled() -> bool:
    cfg = getattr(settings, "image_gen", None)
    return bool(getattr(cfg, "enabled", False)) if cfg else False


def _default_model() -> str:
    cfg = getattr(settings, "image_gen", None)
    return getattr(cfg, "model", "fal-ai/flux/schnell") if cfg else "fal-ai/flux/schnell"


def _default_image_size() -> Dict[str, int]:
    cfg = getattr(settings, "image_gen", None)
    sz = getattr(cfg, "image_size", None) if cfg else None
    if isinstance(sz, dict) and "width" in sz and "height" in sz:
        return {"width": int(sz["width"]), "height": int(sz["height"])}
    return {"width": 512, "height": 512}


def _default_steps() -> int:
    cfg = getattr(settings, "image_gen", None)
    return int(getattr(cfg, "num_inference_steps", 2)) if cfg else 2


def _default_timeout() -> float:
    cfg = getattr(settings, "image_gen", None)
    return float(getattr(cfg, "timeout_s", 15.0)) if cfg else 15.0


def _concurrency() -> int:
    cfg = getattr(settings, "image_gen", None)
    return max(1, int(getattr(cfg, "concurrency", 4))) if cfg else 4


# §16.2 — Transient-error classification for FAL retry.
# FAL's exception surface is unstable across versions; we treat anything
# that looks like a network/server/rate-limit hiccup as transient.
_TRANSIENT_MSG_RE = re.compile(
    r"\b(429|rate.?limit|503|502|504|timeout|timed?\s*out|connection|temporarily)\b",
    re.IGNORECASE,
)


def _is_fal_transient(exc: BaseException) -> bool:
    if isinstance(exc, asyncio.TimeoutError):
        return True
    # ConnectionError covers ConnectionRefused/Reset/Aborted; OSError is the
    # broader socket family. We deliberately do NOT include plain Exception.
    if isinstance(exc, (ConnectionError, OSError)):
        return True
    msg = str(exc) or exc.__class__.__name__
    return bool(_TRANSIENT_MSG_RE.search(msg))


# Process-wide semaphore lazily created so the value can change before first use.
_sem: Optional[asyncio.Semaphore] = None
_sem_capacity: int = 0


def _get_semaphore() -> asyncio.Semaphore:
    global _sem, _sem_capacity
    cap = _concurrency()
    if _sem is None or cap != _sem_capacity:
        _sem = asyncio.Semaphore(cap)
        _sem_capacity = cap
    return _sem


# ---------------------------------------------------------------------------
# Client
# ---------------------------------------------------------------------------

class FalImageClient:
    """Thin async wrapper around ``fal_client.subscribe_async``."""

    async def generate(
        self,
        prompt: str,
        *,
        negative_prompt: Optional[str] = None,
        model: Optional[str] = None,
        image_size: Optional[Dict[str, int]] = None,
        timeout_s: Optional[float] = None,
        num_inference_steps: Optional[int] = None,
        seed: Optional[int] = None,
    ) -> Optional[str]:
        if not _image_gen_enabled():
            return None
        if not prompt or not prompt.strip():
            return None

        the_model = model or _default_model()
        the_size = image_size or _default_image_size()
        the_timeout = float(timeout_s) if timeout_s is not None else _default_timeout()
        the_steps = num_inference_steps if num_inference_steps is not None else _default_steps()

        args: Dict[str, Any] = {
            "prompt": prompt,
            "image_size": the_size,
            "num_inference_steps": the_steps,
            "enable_safety_checker": True,
        }
        if negative_prompt:
            args["negative_prompt"] = negative_prompt
        if seed is not None:
            # AC-IMG-STYLE-4 — pin RNG so re-renders of the same (session, subject)
            # are visually identical and a quiz's image set sits in a related
            # seed neighbourhood for cross-image cohesion.
            args["seed"] = int(seed) & 0xFFFFFFFF

        sem = _get_semaphore()
        retry_cfg = getattr(getattr(settings, "image_gen", None), "retry", None)
        max_attempts = int(getattr(retry_cfg, "max_attempts", 2)) if retry_cfg else 2
        base_ms = int(getattr(retry_cfg, "base_ms", 200)) if retry_cfg else 200
        cap_ms = int(getattr(retry_cfg, "cap_ms", 1500)) if retry_cfg else 1500

        def _on_retry(attempt: int, exc: BaseException, delay_s: float) -> None:
            logger.info(
                "image.fal.retrying",
                attempt=attempt,
                next_delay_s=round(delay_s, 3),
                model=the_model,
                error=str(exc),
            )

        async def _call() -> Any:
            async with sem:
                return await asyncio.wait_for(
                    fal_client.subscribe_async(the_model, arguments=args),
                    timeout=the_timeout,
                )

        try:
            resp = await retry_async(
                _call,
                is_transient=_is_fal_transient,
                max_attempts=max_attempts,
                base_ms=base_ms,
                cap_ms=cap_ms,
                on_retry=_on_retry,
            )
        except asyncio.TimeoutError:
            logger.info("image.fal.timeout", model=the_model, timeout_s=the_timeout)
            return None
        except Exception as e:  # never raise to caller (fail-open contract)
            logger.info(
                "image.fal.retries_exhausted" if max_attempts > 1 else "image.fal.fail",
                model=the_model,
                error=str(e),
            )
            return None

        try:
            images = (resp or {}).get("images") if isinstance(resp, dict) else None
            if not images:
                return None
            url = images[0].get("url") if isinstance(images[0], dict) else None
            return url or None
        except Exception:
            return None


# Process-wide singleton for convenience (matches the pattern in image_pipeline).
_client_singleton = FalImageClient()
