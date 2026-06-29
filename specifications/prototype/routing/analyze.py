"""Post-hoc analysis: at a chosen tau, list the false positives + misses so we
can talk honestly about WHERE routing fails (adversarial near-misses etc.)."""
import json
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
DATA = HERE.parent / "data"


def show(eval_file: str, tau: float) -> None:
    res = json.loads((DATA / eval_file).read_text(encoding="utf-8"))
    fp_wrong, fp_abstract, misses, hits = [], [], [], []
    for r in res["per_item"]:
        shown = r["top_sim"] >= tau
        if r["expected"]:
            if shown:
                (hits if r["top_concept"] in r["expected"] else fp_wrong).append(r)
            else:
                misses.append(r)
        else:
            if shown:
                fp_abstract.append(r)
    print(f"\n=== {res['backend']} | captions={res['caption_mode']} | tau={tau} ===")
    print(f"hits={len(hits)} wrong-icon FPs={len(fp_wrong)} "
          f"abstract-string FPs={len(fp_abstract)} misses(no-icon)={len(misses)}")
    print("\n-- WRONG-ICON false positives (showed an off-topic icon) --")
    for r in fp_wrong:
        print(f"  [{r['kind']:>8}] {r['text']!r} -> {r['top_concept']} (sim {r['top_sim']})  "
              f"expected {r['expected']}")
    print("\n-- ABSTRACT-string false positives (should have shown NOTHING) --")
    for r in fp_abstract:
        print(f"  [{r['kind']:>8}] {r['text']!r} -> {r['top_concept']} (sim {r['top_sim']})")


if __name__ == "__main__":
    show(sys.argv[1] if len(sys.argv) > 1 else "eval_local_rich.json",
         float(sys.argv[2]) if len(sys.argv) > 2 else 0.66)
