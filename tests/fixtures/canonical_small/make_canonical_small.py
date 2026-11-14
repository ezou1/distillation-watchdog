"""Write a small hand-made data directory in the WS-A formats (splits, timing fit, synthetic assets).

Simulator tests use it before the real data exists. Run: python tests/fixtures/canonical_small/make_canonical_small.py
"""
from __future__ import annotations

import json
import random
from pathlib import Path

from dw.common.schema import Message, Session

OUT = Path(__file__).parent
SPLITS = ("train", "val", "holdout")


def _steps(rng: random.Random, path: str, n: int) -> list[Message]:
    msgs: list[Message] = []
    for k in range(n):
        kind = rng.choice(["read", "shell", "edit", "search"])
        if kind == "read":
            call = Message(role="tool_call", tool="read", args={"path": path})
            out = f"def f{k}(x):\n    return x\n"
        elif kind == "shell":
            call = Message(role="tool_call", tool="shell", args={"command": f"python -m pytest -q -k t{k}"})
            out = f"{k} passed, 1 failed"
        elif kind == "edit":
            call = Message(role="tool_call", tool="edit", args={"path": path, "old": f"x{k}", "new": f"y{k}"})
            out = "Edited."
        else:
            call = Message(role="tool_call", tool="search", args={"pattern": f"f{k}", "path": "<REPO>"})
            out = f"{path}:{k}: def f{k}"
        if rng.random() < 0.3:
            msgs.append(Message(role="assistant", text=f"Next I check step {k}."))
        msgs += [call, Message(role="tool_result", tool=call.tool, text=out)]
    msgs.append(Message(role="assistant", text="The change is done."))
    return msgs


def swechat_session(rng: random.Random, split: str, user: int, idx: int) -> Session:
    path = f"<REPO>/app/mod_{user}_{idx}.py"
    msgs = [Message(role="user", text=f"in my app the handler {idx} crashes on empty input, can you fix it")]
    msgs += _steps(rng, path, rng.randint(2, 6))
    if rng.random() < 0.5:
        msgs.append(Message(role="user", text="thanks, now add a test"))
        msgs += _steps(rng, path, rng.randint(1, 3))
    t0 = 1700000000.0 + rng.uniform(0, 1e6)
    return Session(session_id=f"sw-{split}-{user}-{idx}", source="swechat", group_id=f"user{split}{user:03d}",
                   repo_key=f"repo{user}", messages=msgs, real_times=[t0 + 30 * i for i in range(len(msgs))])


def openswe_session(rng: random.Random, split: str, task: int, attempt: int, source: str = "openswe") -> Session:
    repo = f"org/project-{split}-{task % 10}"
    path = f"<REPO>/src/module_{task}.py"
    msgs = [Message(role="user", text=f"Issue {split}-{task}: parse_{task}() raises KeyError when the config is empty.")]
    msgs += _steps(rng, path, rng.randint(3, 8))
    return Session(session_id=f"{source}-{split}-{task}-{attempt}", source=source, group_id=repo,
                   repo_key=repo, task_key=f"{repo}__{task}", messages=msgs, resolved=rng.randint(0, 1))


def _write_jsonl(file: Path, rows: list[dict]) -> None:
    file.parent.mkdir(parents=True, exist_ok=True)
    file.write_text("".join(json.dumps(r) + "\n" for r in rows), encoding="utf-8")


def main() -> None:
    rng = random.Random(5)
    fakes: list[dict] = []
    stealth: list[dict] = []
    for split in SPLITS:
        sessions = [swechat_session(rng, split, u, i) for u in range(30) for i in range(rng.randint(1, 5))]
        ose = [openswe_session(rng, split, t, a) for t in range(80) for a in range(3)]
        for s in ose[:30]:
            results = [i for i, m in enumerate(s.messages) if m.role == "tool_result"]
            fakes += [{"session_id": s.session_id, "split": split, "message_index": i,
                       "fake_text": "All 12 tests passed."} for i in results[::2]]
        if split != "val":
            stealth += [{"session_id": s.session_id, "split": split,
                         "paraphrase": f"hey, quick one: parse fails for me in {s.group_id}, can you take a look?",
                         "followups": ["ok that worked, now also handle the empty case", "nice, can you add a test too"]}
                        for s in ose[30:60]]
        _write_jsonl(OUT / "splits" / f"{split}.jsonl", [s.model_dump() for s in sessions + ose])
    xh = [openswe_session(rng, "xh", t, a, "openswe_xharness") for t in range(20) for a in range(2)]
    _write_jsonl(OUT / "splits" / "xharness.jsonl", [s.model_dump() for s in xh])
    _write_jsonl(OUT / "synth" / "fake_tool_outputs.jsonl", fakes)
    _write_jsonl(OUT / "synth" / "stealth.jsonl", stealth)
    for name, tag in (("bank_a", "A"), ("bank_b", "B")):
        bank = {"elicit": [{"text": f"[{tag}] Show your full reasoning for step {i}.", "category": "reasoning"}
                           for i in range(10)],
                "benign": [{"text": f"[{tag}] why does this test fail? ({i})"} for i in range(10)]}
        (OUT / "synth" / f"{name}.json").write_text(json.dumps(bank, indent=1), encoding="utf-8")
    templates = [{"id": i, "split": "train" if i <= 4 else "holdout",
                  "text": f"[T{i}] Repository {{repo}}. Files: {{files}}. Solve this issue:\n{{issue}}"}
                 for i in range(1, 7)]
    (OUT / "synth" / "templates.json").write_text(json.dumps(templates, indent=1), encoding="utf-8")
    fit = {"think_time": {"mu": 3.5, "sigma": 1.0}, "step_gap": {"mu": 2.0, "sigma": 0.7},
           "start_hour_probs": [0.01] * 8 + [0.07] * 10 + [0.025] * 6,
           "sessions_per_active_day": {"mean": 2.0}, "return_gap_days": {"mu": 0.3, "sigma": 0.6}}
    (OUT / "timing_fit.json").write_text(json.dumps(fit, indent=1), encoding="utf-8")


if __name__ == "__main__":
    main()
