"""Model + size-aware FAL image cost model (blackbox fix #3).

Prior to this, FAL image spend was metered with a SINGLE flat constant
(``LiveCostGuard.fal_image_cost_usd`` = ``$0.011`` and the ledger's
``cost_per_image_usd``). That constant is now WRONG for the mixed-model
pipeline:

  * a 256×256 FLUX **schnell** cast thumbnail costs ~$0.0002, and
  * a 1024×1024 FLUX **dev** hero costs ~$0.025.

Metering both at a flat $0.011 simultaneously *over*-bills the cheap thumbnails
(by ~50×) and *under*-bills the dev heroes (by ~2.3×), so neither the daily
cents-breaker nor the lifetime $150 FAL ledger meters TRUE spend.

This module derives a per-image cost from the model id + render size using a
per-megapixel rate (true pixel area, no round-up — matching the figures the
owner quoted). FAL publishes:

  * FLUX **schnell** — $0.003 per megapixel (`fal.ai/models/fal-ai/flux/schnell`)
  * FLUX **dev** — $0.025 per megapixel (`fal.ai/models/fal-ai/flux/dev`)

Worked examples (true-area MP × rate):
  * schnell 256×256  = 0.0655 MP × $0.003 ≈ $0.000197  (≈ $0.0002)
  * schnell 512×512  = 0.262  MP × $0.003 ≈ $0.000786
  * dev    1024×576  = 0.590  MP × $0.025 ≈ $0.0148
  * dev    1024×1024 = 1.049  MP × $0.025 ≈ $0.0262   (≈ $0.025)

An unknown model id falls back to the schnell rate (the cheap default), so a
mis-typed model can never silently over-bill the breaker into a false trip.
Pure functions, no IO — safe to import from both the cost meter and the ledger.
"""

from __future__ import annotations

# Per-megapixel USD rates by FAL model id. Keys are matched by substring
# (case-folded) so both ``fal-ai/flux/dev`` and a future ``fal-ai/flux/dev/x``
# resolve to the dev rate. Order matters: more specific keys first.
_PER_MP_USD: tuple[tuple[str, float], ...] = (
    ("flux/dev", 0.025),
    ("flux/schnell", 0.003),
)
# Fallback when the model id matches none of the above — the cheap schnell rate,
# so an unrecognised model under-bills rather than over-trips the breaker.
_DEFAULT_PER_MP_USD: float = 0.003

# Default render size when a caller doesn't supply one (the cast-thumb default).
_DEFAULT_SIZE: dict[str, int] = {"width": 256, "height": 256}


def _per_mp_usd(model: str | None) -> float:
    m = (model or "").strip().lower()
    if not m:
        return _DEFAULT_PER_MP_USD
    for key, rate in _PER_MP_USD:
        if key in m:
            return rate
    return _DEFAULT_PER_MP_USD


def _megapixels(image_size: dict[str, int] | None) -> float:
    sz = image_size if isinstance(image_size, dict) else _DEFAULT_SIZE
    try:
        w = int(sz.get("width", _DEFAULT_SIZE["width"]))
        h = int(sz.get("height", _DEFAULT_SIZE["height"]))
    except (TypeError, ValueError):
        w, h = _DEFAULT_SIZE["width"], _DEFAULT_SIZE["height"]
    if w <= 0 or h <= 0:
        w, h = _DEFAULT_SIZE["width"], _DEFAULT_SIZE["height"]
    return (w * h) / 1_000_000.0


def image_cost_usd(
    *, model: str | None = None, image_size: dict[str, int] | None = None
) -> float:
    """USD cost of ONE FAL image for ``model`` at ``image_size`` (true-area MP ×
    per-MP rate). Never raises; an unknown model falls back to the schnell rate
    and a missing/invalid size to the 256×256 cast-thumb default."""
    return _megapixels(image_size) * _per_mp_usd(model)


def image_cost_micros(
    *, model: str | None = None, image_size: dict[str, int] | None = None
) -> int:
    """Per-image cost in micro-cents (1 cent = 1000 micros) — the LOSSLESS unit
    the FAL ledger sums + cap-checks in. At least 1 micro for any positive-rate
    image so a non-zero-cost render always consumes some budget."""
    usd = image_cost_usd(model=model, image_size=image_size)
    micros = int(round(usd * 100_000))
    return max(1, micros) if usd > 0 else 0
