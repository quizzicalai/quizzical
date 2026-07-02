"""Dual-direction discovery: planning parse, query building, merged-pool
dedup + ranking, mocked both-direction search, and fallbacks."""
import asyncio
import json
from datetime import datetime, timezone

from social_agent.discovery import (
    MAX_TOPIC_DIRECTIVES,
    MAX_TREND_DIRECTIVES,
    TOPIC,
    TREND,
    SearchDirective,
    build_x_query,
    fallback_topic_directives,
    merge_and_rank,
    parse_direction_plan,
    rank_score,
    run_directives,
    should_trend_flavor_post,
)
from social_agent.visibility import TweetCandidate

NOW = datetime(2026, 7, 2, 12, 0, 0, tzinfo=timezone.utc)


def _tweet(tid="1", likes=50, followers=1000, replies=5, text="hi") -> TweetCandidate:
    return TweetCandidate(
        tweet_id=tid, text=text, author_id="a", author_username="u",
        author_followers=followers, reply_count=replies, like_count=likes,
        retweet_count=0, created_at="", lang="en",
    )


# --- plan parsing -------------------------------------------------------------

def _plan(trend=None, topic=None):
    return json.dumps({
        "trend_directives": trend if trend is not None else [],
        "topic_directives": topic if topic is not None else [],
    })


def test_parse_plan_happy_path():
    raw = _plan(
        trend=[{"label": "fifa-final", "angle": "which team am I", "terms": ["fifa", "world cup final"]}],
        topic=[{"label": "ducks", "angle": "what type of duck are you", "terms": ["ducks"]}],
    )
    plan = parse_direction_plan(raw)
    assert [d.direction for d in plan] == [TREND, TOPIC]
    assert plan[0].label == "fifa-final"
    assert plan[0].terms == ["fifa", "world cup final"]


def test_parse_plan_malformed_is_empty_not_fatal():
    assert parse_direction_plan("") == []
    assert parse_direction_plan("{oops") == []
    assert parse_direction_plan(json.dumps({"trend_directives": "nope"})) == []


def test_parse_plan_drops_junk_items_and_caps_counts():
    trend = [{"label": f"t{i}", "angle": "a", "terms": ["x"]} for i in range(5)]
    trend.insert(0, {"label": "", "angle": "a", "terms": ["x"]})   # no label -> dropped
    trend.insert(0, {"label": "no-terms", "angle": "a", "terms": []})  # no terms -> dropped
    topic = [{"label": f"p{i}", "angle": "a", "terms": ["y"]} for i in range(9)]
    plan = parse_direction_plan(_plan(trend=trend, topic=topic))
    assert sum(1 for d in plan if d.direction == TREND) == MAX_TREND_DIRECTIVES
    assert sum(1 for d in plan if d.direction == TOPIC) == MAX_TOPIC_DIRECTIVES
    assert all(d.label and d.terms for d in plan)


def test_parse_plan_tolerates_markdown_fences():
    raw = "```json\n" + _plan(topic=[{"label": "soup", "angle": "a", "terms": ["soup"]}]) + "\n```"
    assert parse_direction_plan(raw)[0].label == "soup"


# --- query building -----------------------------------------------------------

def test_build_x_query_quotes_phrases_and_joins():
    q = build_x_query(["fifa", "world cup final"])
    assert q == '(fifa OR "world cup final") -is:retweet -is:reply lang:en'


def test_build_x_query_single_term_and_empty():
    assert build_x_query(["ducks"]) == "ducks -is:retweet -is:reply lang:en"
    assert build_x_query([]) == ""
    assert build_x_query(["", "  "]) == ""


def test_directive_raw_query_overrides_terms():
    d = SearchDirective(TOPIC, "base", "angle", terms=["ignored"], raw_query="RAW QUERY")
    assert d.query() == "RAW QUERY"


# --- ranking + merged-pool dedup ----------------------------------------------

def test_rank_score_prefers_engagement_and_penalizes_burial():
    assert rank_score(_tweet(likes=500)) > rank_score(_tweet(likes=5))
    assert rank_score(_tweet(replies=140)) < rank_score(_tweet(replies=2))


def test_merge_dedups_by_tweet_id_and_merges_direction_tags():
    t = _tweet(tid="42")
    d_trend = SearchDirective(TREND, "fifa", "team angle", terms=["fifa"])
    d_topic = SearchDirective(TOPIC, "ducks", "duck angle", terms=["ducks"])
    pool = merge_and_rank([(t, d_trend), (t, d_topic)])
    assert len(pool) == 1
    assert pool[0].directions == [TREND, TOPIC]
    assert pool[0].labels == ["fifa", "ducks"]
    assert pool[0].angles == ["team angle", "duck angle"]
    assert pool[0].primary_direction == TREND
    meta = pool[0].meta()["discovery"]
    assert meta["directions"] == [TREND, TOPIC]


def test_merge_ranks_pool_descending():
    hot = _tweet(tid="hot", likes=900, followers=5000)
    cold = _tweet(tid="cold", likes=1, followers=60)
    d = SearchDirective(TOPIC, "x", "a", terms=["x"])
    pool = merge_and_rank([(cold, d), (hot, d)])
    assert [c.tweet.tweet_id for c in pool] == ["hot", "cold"]


# --- both directions mocked through run_directives ------------------------------

class FakeProvider:
    """Returns canned results per query; records the queries it was asked."""

    name = "fake"

    def __init__(self, results_by_label):
        self.results_by_label = results_by_label
        self.queries = []

    async def search(self, query, start_time):
        self.queries.append(query)
        for label, results in self.results_by_label.items():
            if label in query:
                return results
        return []


def test_run_directives_merges_both_directions():
    shared = _tweet(tid="both", likes=100)
    provider = FakeProvider({
        "fifa": [_tweet(tid="t1", likes=10), shared],
        "ducks": [_tweet(tid="p1", likes=20), shared],
    })
    directives = [
        SearchDirective(TREND, "fifa", "team angle", terms=["fifa"]),
        SearchDirective(TOPIC, "ducks", "duck angle", terms=["ducks"]),
    ]
    pool, stats = asyncio.run(run_directives(provider, directives, NOW))
    assert stats["trend_found"] == 2
    assert stats["topic_found"] == 2
    assert stats["merged_pool"] == 3  # 4 raw finds, 1 shared tweet deduped
    both = next(c for c in pool if c.tweet.tweet_id == "both")
    assert both.directions == [TREND, TOPIC]
    assert len(provider.queries) == 2  # one search per directive


def test_run_directives_survives_a_failing_search():
    class ExplodingProvider(FakeProvider):
        async def search(self, query, start_time):
            if "boom" in query:
                raise RuntimeError("search down")
            return await super().search(query, start_time)

    provider = ExplodingProvider({"ducks": [_tweet(tid="p1")]})
    directives = [
        SearchDirective(TREND, "boom", "a", terms=["boom"]),
        SearchDirective(TOPIC, "ducks", "a", terms=["ducks"]),
    ]
    pool, stats = asyncio.run(run_directives(provider, directives, NOW))
    assert stats["trend_found"] == 0
    assert stats["topic_found"] == 1
    assert [c.tweet.tweet_id for c in pool] == ["p1"]


def test_run_directives_empty_pool_when_no_search():
    class NoResults(FakeProvider):
        pass

    provider = NoResults({})
    directives = [SearchDirective(TOPIC, "ducks", "a", terms=["ducks"])]
    pool, stats = asyncio.run(run_directives(provider, directives, NOW))
    assert pool == []
    assert stats["merged_pool"] == 0


# --- fallbacks ------------------------------------------------------------------

def test_fallback_topic_directives_from_banked_pool():
    ds = fallback_topic_directives(["Antique Hardware Personalities", "Sandwich Types"], k=2)
    assert len(ds) == 2
    assert all(d.direction == TOPIC for d in ds)
    assert ds[0].terms == ["antique hardware"]  # stopwords stripped
    assert "sandwich" in ds[1].terms[0]


def test_fallback_topic_directives_when_bank_empty_uses_builtins():
    ds = fallback_topic_directives([], k=2)
    assert len(ds) == 2
    assert all(d.terms for d in ds)


# --- posts-only trend flavoring --------------------------------------------------

def test_trend_flavor_forced_or_enabled_always_wins():
    assert should_trend_flavor_post(
        force_event=True, events_enabled=False, provider_name="x-recent-search", roll=0.99)
    assert should_trend_flavor_post(
        force_event=False, events_enabled=True, provider_name="x-recent-search", roll=0.99)


def test_trend_flavor_in_posts_only_mode_is_probabilistic():
    kwargs = dict(force_event=False, events_enabled=False, provider_name="no-search")
    assert should_trend_flavor_post(roll=0.10, **kwargs)
    assert not should_trend_flavor_post(roll=0.90, **kwargs)


def test_trend_flavor_off_when_search_available_and_not_enabled():
    assert not should_trend_flavor_post(
        force_event=False, events_enabled=False, provider_name="x-recent-search", roll=0.0)
