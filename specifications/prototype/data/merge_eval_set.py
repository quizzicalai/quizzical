"""Merge the original 126-item set (qa_labeled.json) with the new expanded
dual-perspective set (qa_labeled_v2.json) into a single master eval set
(qa_labeled_master.json), deduped by text.

The original set had a single `expected` list (one strict annotator). We carry it
in as BOTH expected_strict and expected_lenient (i.e. that annotator's judgement is
treated as agreeing with itself) and tag it abstractness from whether expected==[].
Items present in v2 win (they have the richer dual-perspective labels)."""
from __future__ import annotations

import json
from pathlib import Path

HERE = Path(__file__).resolve().parent


def main() -> None:
    v1 = json.loads((HERE / "qa_labeled.json").read_text(encoding="utf-8"))["items"]
    v2 = json.loads((HERE / "qa_labeled_v2.json").read_text(encoding="utf-8"))["items"]

    by_text: dict[str, dict] = {}

    # v1 first (lower priority)
    for it in v1:
        exp = it["expected"]
        n_words = len(it["text"].split())
        len_bucket = "short" if n_words <= 2 else ("medium" if n_words <= 6 else "long")
        abstractness = "abstract" if not exp else "concrete"
        by_text[it["text"]] = {
            "text": it["text"],
            "kind": it["kind"],
            "category": "orig",
            "abstractness": abstractness,
            "len_bucket": len_bucket,
            "expected_strict": sorted(set(exp)),
            "expected_lenient": sorted(set(exp)),
            "source": "v1",
        }

    # v2 overrides
    for it in v2:
        d = dict(it)
        d["source"] = "v2"
        by_text[it["text"]] = d

    items = list(by_text.values())
    out = {
        "_comment": (
            "MASTER eval set = original 126 (carried in as strict==lenient single "
            "annotator) UNION expanded dual-perspective v2, deduped by text. Use "
            "expected_strict / expected_lenient to bracket precision/coverage between "
            "a pessimistic and an optimistic annotator."
        ),
        "n_items": len(items),
        "items": items,
    }
    (HERE / "qa_labeled_master.json").write_text(json.dumps(out, indent=2), encoding="utf-8")

    from collections import Counter
    print(f"wrote qa_labeled_master.json with {len(items)} items "
          f"(v1={sum(1 for i in items if i['source']=='v1')}, "
          f"v2={sum(1 for i in items if i['source']=='v2')})")
    print("by kind:        ", dict(Counter(i["kind"] for i in items)))
    print("by abstractness:", dict(Counter(i["abstractness"] for i in items)))
    print("strict no-icon: ", sum(1 for i in items if not i["expected_strict"]))
    print("lenient no-icon:", sum(1 for i in items if not i["expected_lenient"]))


if __name__ == "__main__":
    main()
