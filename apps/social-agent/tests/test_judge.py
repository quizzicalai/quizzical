"""Judge verdict parsing: refuse-by-default on anything imperfect."""
import json

from social_agent.judge import (
    APPROVE_MIN_QUALITY,
    build_judge_user_prompt,
    parse_judge_response,
)


def _verdict(index=0, approve=True, quality=9, on_brand=True,
             conscientious=True, relevant=True, reason="great"):
    return {
        "index": index, "approve": approve, "quality": quality,
        "on_brand": on_brand, "conscientious": conscientious,
        "relevant": relevant, "reason": reason,
    }


def _raw(*verdicts):
    return json.dumps({"verdicts": list(verdicts)})


def test_clean_approval_parses():
    out = parse_judge_response(_raw(_verdict()), 1, kind="reply")
    assert out[0].approve
    assert out[0].quality == 9


def test_empty_response_rejects_all():
    out = parse_judge_response("", 3)
    assert len(out) == 3
    assert not any(v.approve for v in out)


def test_malformed_json_rejects_all():
    out = parse_judge_response("{not json at all", 2)
    assert not any(v.approve for v in out)


def test_missing_verdict_for_index_rejects_that_index():
    out = parse_judge_response(_raw(_verdict(index=0)), 2)
    assert out[0].approve
    assert not out[1].approve
    assert "no verdict" in out[1].reason


def test_quality_below_floor_rejects_even_if_approved():
    out = parse_judge_response(
        _raw(_verdict(quality=APPROVE_MIN_QUALITY - 1)), 1
    )
    assert not out[0].approve


def test_conscientious_false_rejects():
    out = parse_judge_response(_raw(_verdict(conscientious=False)), 1)
    assert not out[0].approve


def test_on_brand_false_rejects():
    out = parse_judge_response(_raw(_verdict(on_brand=False)), 1)
    assert not out[0].approve


def test_missing_booleans_reject():
    raw = json.dumps({"verdicts": [{"index": 0, "approve": True, "quality": 9}]})
    out = parse_judge_response(raw, 1)
    assert not out[0].approve


def test_reply_requires_explicit_relevance():
    v = _verdict()
    del v["relevant"]
    assert not parse_judge_response(_raw(v), 1, kind="reply")[0].approve
    # for standalone posts, omitted 'relevant' (replies-only field) is fine
    assert parse_judge_response(_raw(v), 1, kind="post")[0].approve


def test_post_ignores_explicit_relevant_false():
    # Models inconsistently emit relevant=false for standalone posts (there is
    # no target to be relevant to); that must not veto an otherwise-good post.
    out = parse_judge_response(_raw(_verdict(relevant=False)), 1, kind="post")
    assert out[0].approve
    # ...but a reply with relevant=false is still rejected.
    out = parse_judge_response(_raw(_verdict(relevant=False)), 1, kind="reply")
    assert not out[0].approve


def test_string_booleans_are_tolerated():
    out = parse_judge_response(
        _raw(_verdict(approve="true", on_brand="yes", conscientious="TRUE")), 1, kind="reply"
    )
    assert out[0].approve


def test_markdown_fenced_json_is_tolerated():
    raw = "```json\n" + _raw(_verdict()) + "\n```"
    assert parse_judge_response(raw, 1, kind="reply")[0].approve


def test_out_of_range_index_ignored():
    out = parse_judge_response(_raw(_verdict(index=7)), 1)
    assert not out[0].approve


def test_prompt_includes_target_for_replies():
    prompt = build_judge_user_prompt(
        [{"text": "quack", "target_text": "I love quizzes", "target_author": "sam"}],
        "reply",
    )
    assert "I love quizzes" in prompt
    assert "@sam" in prompt
    assert "REPLIES" in prompt
