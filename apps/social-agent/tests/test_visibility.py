"""Visibility heuristic + sensitivity prefilter."""
from social_agent.visibility import (
    TweetCandidate,
    VisibilityPolicy,
    sensitivity_prefilter,
    visibility_check,
)


def _tweet(**kw) -> TweetCandidate:
    base = dict(
        tweet_id="1", text="I love personality quizzes", author_id="a",
        author_username="user", author_followers=1000, reply_count=5,
        like_count=50, retweet_count=1, created_at="", lang="en",
    )
    base.update(kw)
    return TweetCandidate(**base)


def test_normal_post_is_engageable():
    v = visibility_check(_tweet())
    assert v.engage


def test_zero_visibility_account_skipped():
    v = visibility_check(_tweet(author_followers=3))
    assert not v.engage
    assert "zero-visibility" in v.reason


def test_buried_under_replies_skipped():
    v = visibility_check(_tweet(reply_count=4823))
    assert not v.engage
    assert "buried" in v.reason


def test_mega_viral_skipped():
    v = visibility_check(_tweet(like_count=90_000))
    assert not v.engage


def test_non_english_skipped_by_default():
    assert not visibility_check(_tweet(lang="de")).engage


def test_policy_is_tunable():
    lax = VisibilityPolicy(min_followers=1, max_reply_count=10_000)
    assert visibility_check(_tweet(author_followers=3, reply_count=5000), lax).engage


def test_sensitivity_prefilter_blocks_grief():
    r = sensitivity_prefilter(
        "took a personality test in therapy, working through my dad's death"
    )
    assert r.sensitive
    assert any("death" in m for m in r.matched)


def test_sensitivity_prefilter_blocks_politics():
    assert sensitivity_prefilter("my MBTI says I should vote for the maga candidate").sensitive


def test_sensitivity_prefilter_case_insensitive():
    assert sensitivity_prefilter("My DEPRESSION shapes my personality type").sensitive


def test_sensitivity_prefilter_passes_lighthearted():
    r = sensitivity_prefilter(
        "just found out I'm an INFP and it explains my 47 unfinished craft projects"
    )
    assert not r.sensitive
