"""Build the training data of the context model (the text parts of D4 and D6) as JSONL in data/context/.

Two tasks, one example per line: {"task", "text", "label", "source", "kind"}.

- realism: a tool call and its output. Label 1: the output was written by the helper model (fake-env asset).
  Label 0: a real output. The fakes exist only for Open-SWE sessions, so the label-0 examples also come from
  Open-SWE only (matched by tool kind). Otherwise the model learns "Open-SWE or SWE-chat", not "fake or real".
  Real SWE-chat outputs go to a separate check set: the share that the model flags measures source leakage.
- elicitation: a user prompt. Label 1: the prompt contains an elicitation phrase. Label 0: the prompt alone or
  with a benign request. Prompts come from both sources. A share of all prompts (both labels) gets the SaaS
  product wrapper, so the wrapper text carries no label.

train and val use bank A. The holdout set (`--holdout`) uses bank B and is built only for the final evaluation.
"""
from __future__ import annotations

import argparse
import json
import random
from collections import Counter, defaultdict
from pathlib import Path
from typing import Any, Iterator

from dw.common.config import CFG, path
from dw.common.schema import Session
from dw.common.context import call_index, prompt_text, realism_text
from dw.train.train_all import first_user, load_bank, place

OUT = path("data") / "context"
CTX = CFG["context_model"]


def sessions(split: str) -> Iterator[Session]:
    with open(path("splits") / f"{split}.jsonl", encoding="utf-8") as f:
        for line in f:
            yield Session.model_validate_json(line)


def realism(split: str, rng: random.Random) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    """(examples, SWE-chat check set) for one split."""
    fakes: dict[str, dict[int, str]] = defaultdict(dict)
    for line in open(path("synth") / "fake_tool_outputs.jsonl", encoding="utf-8"):
        r = json.loads(line)
        if r["split"] == split and r["fake_text"].strip():
            fakes[r["session_id"]][int(r["message_index"])] = r["fake_text"]
    pos: list[dict[str, Any]] = []
    real: dict[str, list[dict[str, Any]]] = {"openswe": [], "swechat": []}
    for s in sessions(split):
        calls = call_index(s.messages)
        for i, m in enumerate(s.messages):
            if m.role != "tool_result":
                continue
            call = s.messages[calls[i]] if i in calls else None
            kind = (call.tool if call is not None else None) or "other"
            tool, cargs, has = (call.tool, call.args, True) if call is not None else (None, None, False)
            if i in fakes.get(s.session_id, {}):
                pos.append({"task": "realism", "text": realism_text(tool, cargs, fakes[s.session_id][i], has),
                            "label": 1, "source": s.source, "kind": kind})
            elif m.text.strip():
                real[s.source].append({"task": "realism", "text": realism_text(tool, cargs, m.text, has), "label": 0,
                                       "source": s.source, "kind": kind})
    by_kind: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for ex in real["openswe"]:
        by_kind[ex["kind"]].append(ex)
    neg: list[dict[str, Any]] = []
    for kind, n in Counter(ex["kind"] for ex in pos).items():
        pool = by_kind.get(kind, [])
        neg += rng.sample(pool, min(len(pool), CTX["realism_neg_ratio"] * n))
    check = rng.sample(real["swechat"], min(len(real["swechat"]), CTX["swechat_check"]))
    return pos + neg, check


def elicitation(split: str, bank_name: str, rng: random.Random) -> list[dict[str, Any]]:
    bank = load_bank(bank_name)
    prompts = [(s.source, first_user(s)) for s in sessions(split) if first_user(s)]
    rng.shuffle(prompts)
    wrapper = CFG["sim"]["personas"]["saas_app"]["product_template"]
    out: list[dict[str, Any]] = []
    for source, p in prompts[: CTX["elicit_prompts"][split]]:
        for label in (1, 0):
            if label:
                text = place(p, rng.choice(bank["elicit"]), rng)
            else:
                text = place(p, rng.choice(bank["benign"]), rng) if rng.random() < 0.5 else p
            if rng.random() < CTX["wrapper_frac"]:
                text = wrapper.replace("{prompt}", text)
            out.append({"task": "elicitation", "text": prompt_text(text), "label": label, "source": source,
                        "kind": "prompt"})
    return out


def write(name: str, rows: list[dict[str, Any]]) -> None:
    OUT.mkdir(parents=True, exist_ok=True)
    with open(OUT / f"{name}.jsonl", "w", encoding="utf-8") as f:
        for r in rows:
            f.write(json.dumps(r, ensure_ascii=False) + "\n")
    counts = Counter((r["task"], r["label"], r["source"]) for r in rows)
    print(name, len(rows), dict(sorted(counts.items())))


def build(holdout: bool) -> None:
    """Write train and val (and the holdout test set if `holdout`). The same seed gives the same files."""
    rng = random.Random(CTX["seed"])
    for split in ("train", "val") + (("holdout",) if holdout else ()):
        rows, check = realism(split, rng)
        rows += elicitation(split, "bank_b" if split == "holdout" else "bank_a", rng)
        rng.shuffle(rows)
        write(split, rows)
        if split != "train":
            write(f"{split}_swechat_check", check)


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--holdout", action="store_true", help="also build the holdout test set (final evaluation only)")
    build(ap.parse_args().holdout)


if __name__ == "__main__":
    main()
