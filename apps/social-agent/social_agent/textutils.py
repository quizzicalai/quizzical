"""Pure text helpers: normalization for exact-dedup, tweet length budgeting.

Stdlib-only on purpose — unit tests import this module without the app venv.
"""
from __future__ import annotations

import re
import unicodedata

# The literal placeholder pre-computed post bodies carry; replaced with the
# share link at post time. Uniqueness is enforced on the body (placeholder
# intact) so two posts that differ only by their link still count as dupes.
LINK_PLACEHOLDER = "{link}"

# X wraps every URL in t.co, which always counts as 23 characters.
TCO_URL_LEN = 23

# Hard X limit is 280 weighted chars. We budget conservatively (emoji/CJK
# count double under X's weighting rules, which we approximate below).
MAX_TWEET_LEN = 280
SAFE_TWEET_LEN = 270  # our own safety margin

_URL_RE = re.compile(r"https?://\S+")
_WS_RE = re.compile(r"\s+")


def normalize_for_dedup(text: str) -> str:
    """Canonical form used for the exact-uniqueness gate.

    Lowercase, accent-fold, strip URLs and the link placeholder, collapse
    whitespace, drop punctuation. Two posts that differ only in casing,
    punctuation or their link are the *same* post for uniqueness purposes.
    """
    text = text.replace(LINK_PLACEHOLDER, " ")
    text = _URL_RE.sub(" ", text)
    text = unicodedata.normalize("NFKD", text)
    text = "".join(ch for ch in text if not unicodedata.combining(ch))
    text = text.lower()
    # Apostrophes are removed (not spaced) so "I'm" == "im"; all other
    # punctuation becomes whitespace.
    text = text.replace("'", "").replace("’", "")
    text = re.sub(r"[^a-z0-9\s]", " ", text)
    return _WS_RE.sub(" ", text).strip()


def _weighted_len(text: str) -> int:
    """Approximate X's weighted character count for non-URL text.

    Most Latin/general text counts 1; wide (CJK) and emoji-ish codepoints
    count 2. This is an over-approximation in some corners which is fine —
    we only ever use it to stay UNDER the limit.
    """
    total = 0
    for ch in text:
        if unicodedata.east_asian_width(ch) in ("W", "F") or ord(ch) > 0x2100:
            total += 2
        else:
            total += 1
    return total


def tweet_len(text: str) -> int:
    """Effective X length: every URL (and the link placeholder) counts as 23."""
    n_urls = len(_URL_RE.findall(text)) + text.count(LINK_PLACEHOLDER)
    stripped = _URL_RE.sub("", text).replace(LINK_PLACEHOLDER, "")
    return _weighted_len(stripped) + n_urls * TCO_URL_LEN


def fits_tweet(text: str, limit: int = SAFE_TWEET_LEN) -> bool:
    return tweet_len(text) <= limit


def render_with_link(body: str, share_url: str) -> str:
    """Substitute the placeholder (or append the link if absent)."""
    if LINK_PLACEHOLDER in body:
        return body.replace(LINK_PLACEHOLDER, share_url)
    return f"{body.rstrip()} {share_url}"
