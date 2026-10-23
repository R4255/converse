"""Verifies the held-out utterances are unseen: fuzzy similarity < THRESHOLD to every fine-tuning sentence and
every main-suite sentence.

    uv run python -m evals.check_heldout       (needs finetune/data/*.jsonl; regenerate with finetune/gen_data.py)
"""

import json
import sys
from pathlib import Path

from rapidfuzz import fuzz, process

from evals.heldout import HELDOUT
from evals.tasks import TASKS

THRESHOLD = 70
DATA = Path(__file__).resolve().parents[2] / "finetune" / "data"


def training_sentences() -> list[str]:
    out = []
    for name in ("train.jsonl", "valid.jsonl"):
        for line in open(DATA / name):
            msgs = json.loads(line)["messages"]
            out += [m["content"].split("[user]\n")[-1].strip().lower() for m in msgs if m["role"] == "user"
                    and not m["content"].startswith("[system]")]
    return sorted(set(out))


def main() -> int:
    train = training_sentences()
    main_suite = sorted({t["say"].lower() for task in TASKS for t in task["turns"]})
    worst, bad = 0.0, 0
    print(f"{'task':12} {'train':>5} {'main':>5}  utterance  (closest seen sentence)")
    for task in HELDOUT:
        for turn in task["turns"]:
            u = turn["say"].lower()
            t_match, t_score, _ = process.extractOne(u, train, scorer=fuzz.ratio)
            m_match, m_score, _ = process.extractOne(u, main_suite, scorer=fuzz.ratio)
            score = max(t_score, m_score)
            worst = max(worst, score)
            flag = "  <-- too close" if score >= THRESHOLD else ""
            closest = t_match if t_score >= m_score else m_match
            print(f"{task['id']:12} {t_score:5.0f} {m_score:5.0f}  {u!r}  ({closest!r}){flag}")
            bad += score >= THRESHOLD
    n = sum(len(t["turns"]) for t in HELDOUT)
    print(f"\n{len(HELDOUT)} tasks / {n} utterances; {len(train)} training sentences, {len(main_suite)} main-suite sentences")
    print(f"max similarity {worst:.0f} (threshold {THRESHOLD}); {bad} too close")
    return 1 if bad else 0


if __name__ == "__main__":
    sys.exit(main())
