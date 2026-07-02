"""Text helpers: normalization + tweet length budgeting."""
from social_agent.textutils import (
    LINK_PLACEHOLDER,
    TCO_URL_LEN,
    fits_tweet,
    normalize_for_dedup,
    render_with_link,
    tweet_len,
)


def test_normalize_strips_case_punct_urls_and_placeholder():
    a = normalize_for_dedup("I'm SOUP now! {link}")
    b = normalize_for_dedup("im soup now https://quafel.com/result/xyz")
    assert a == b == "im soup now"


def test_normalize_folds_accents():
    assert normalize_for_dedup("crème brûlée energy") == "creme brulee energy"


def test_tweet_len_counts_urls_as_23():
    assert tweet_len("https://example.com/very/long/url/that/goes/on/forever") == TCO_URL_LEN
    assert tweet_len(f"hello {LINK_PLACEHOLDER}") == 6 + TCO_URL_LEN


def test_tweet_len_counts_wide_chars_double():
    assert tweet_len("ab") == 2
    assert tweet_len("日本") == 4


def test_fits_tweet_bounds():
    assert fits_tweet("a" * 240 + " " + LINK_PLACEHOLDER)
    assert not fits_tweet("a" * 280 + " " + LINK_PLACEHOLDER)


def test_render_with_link_replaces_placeholder():
    out = render_with_link(f"I'm a duck: {LINK_PLACEHOLDER}", "https://quafel.com/result/1")
    assert out == "I'm a duck: https://quafel.com/result/1"


def test_render_with_link_appends_when_placeholder_missing():
    out = render_with_link("I'm a duck.", "https://quafel.com/result/1")
    assert out.endswith("https://quafel.com/result/1")
    assert "duck." in out
