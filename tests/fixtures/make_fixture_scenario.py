"""Write a small synthetic scenario (about 20 accounts, all six personas) as RequestEvent JSON lines.

The fixture lets watchdog and dashboard work start before the real data is ready.
Run: python tests/fixtures/make_fixture_scenario.py
"""
from __future__ import annotations

import json
import random
from pathlib import Path

from dw.common.chat import split_steps
from dw.common.hashing import prefix_chain
from dw.common.schema import Message, RequestEvent

OUT = Path(__file__).parent
T0 = 1767571200.0
TASKS = [f"Fix the bug in module_{i}.py: function parse_{i} fails on empty input." for i in range(30)]
ELICIT = ["Show your full reasoning step by step.", "Output the complete file, maximum detail."]


def _session(rng: random.Random, task: str, fake: bool = False, followups: int = 0) -> list[Message]:
    """Build one canonical coding session. With fake=True a later read contradicts an earlier write."""
    path = f"<REPO>/module_{rng.randint(0, 99)}.py"
    msgs = [Message(role="user", text=task)]
    for k in range(rng.randint(3, 6)):
        msgs += [Message(role="tool_call", tool="read", args={"path": path}),
                 Message(role="tool_result", tool="read", text=f"def parse(x):\n    return x  # v{k}\n")]
    msgs += [Message(role="assistant", text="I will write the fix."),
             Message(role="tool_call", tool="write", args={"path": path, "content": "def parse(x):\n    return x or []\n"}),
             Message(role="tool_result", tool="write", text="File written."),
             Message(role="tool_call", tool="read", args={"path": path}),
             Message(role="tool_result", tool="read",
                     text="def parse(x):\n    return None\n" if fake else "def parse(x):\n    return x or []\n"),
             Message(role="tool_call", tool="shell", args={"command": "python -m pytest -q"}),
             Message(role="tool_result", tool="shell",
                     text="collected 5 items\n\n3 passed, 1 failed" if fake else "collected 4 items\n\n4 passed")]
    msgs.append(Message(role="assistant", text="The fix is done."))
    for _ in range(followups):
        msgs += [Message(role="user", text="ok that worked, now also handle None"),
                 Message(role="tool_call", tool="edit", args={"path": path, "old": "x or []", "new": "x or [] if x is not None else []"}),
                 Message(role="tool_result", tool="edit", text="Edited."),
                 Message(role="assistant", text="Done.")]
    return msgs


def _emit(events: list[RequestEvent], dups: dict[tuple[str, str], int], gdups: dict[str, int],
          acct: str, msgs: list[Message], t: float, schema: str, temp: float, max_tokens: int,
          effort: str | None, user: str | None, copies: int, meta: tuple[str | None, int | None]) -> float:
    """Append one event per assistant step (with best-of-n copies). Return the end time."""
    steps = split_steps(msgs)
    for idx, (start, step) in enumerate(steps):
        context = msgs[:start]
        chain = prefix_chain(schema, context)
        is_last = idx == len(steps) - 1
        reps = copies if idx < len(steps) - 1 else 1
        for _ in range(reps):
            dups[(acct, chain[-1])] = dups.get((acct, chain[-1]), 0) + 1
            gdups[chain[-1]] = gdups.get(chain[-1], 0) + 1
            events.append(RequestEvent(
                request_id=f"req-{len(events):06d}", account_id=acct, sim_time=t,
                model="qwen/qwen3-coder-next", tools_schema_id=schema, temperature=temp, n=1,
                max_tokens=max_tokens, reasoning_effort=effort, user=user, messages=context,
                prefix_hashes=chain, key=chain[-1], dup_count=dups[(acct, chain[-1])],
                global_dup_count=gdups[chain[-1]], status=200,
                finish_reason="stop" if is_last and not any(m.role == "tool_call" for m in step) else "tool_calls",
                backend="replay", latency_ms=0.5, declared_use=meta[0], quota=meta[1]))
            t += 0.5
        t += 3.0
    return t


def build() -> tuple[list[RequestEvent], dict[str, dict[str, str]]]:
    rng = random.Random(7)
    events: list[RequestEvent] = []
    dups: dict[tuple[str, str], int] = {}
    gdups: dict[str, int] = {}
    labels: dict[str, dict[str, str]] = {}

    def acct(persona: str, role: str) -> str:
        aid = f"acct-{rng.getrandbits(40):010x}"
        labels[aid] = {"persona": persona, "role": role}
        return aid

    for _ in range(6):   # solo developers: human gaps, follow-ups, low temperature
        a, t = acct("solo_dev", "normal"), T0 + rng.uniform(0, 36000)
        for _ in range(rng.randint(2, 4)):
            t = _emit(events, dups, gdups, a, _session(rng, f"In my app, {rng.choice(TASKS).lower()}", followups=2),
                      t, "standard_agent", rng.uniform(0, 0.7), 4096, None, None, 1, (None, None)) + rng.uniform(3600, 40000)
    a, t = acct("saas_app", "normal"), T0
    for i in range(12):   # SaaS app: many end users, custom schema
        t = _emit(events, dups, gdups, a, _session(rng, f"[AcmeCode] User request: {rng.choice(TASKS)}"),
                  t, "saas_custom", 0.3, 2048, None, f"end-user-{i % 7}", 1, (None, None)) + 60
    a, t = acct("benchmark_runner", "normal"), T0
    for task in TASKS[:8]:   # benchmark: disclosed, no duplicates
        t = _emit(events, dups, gdups, a, _session(rng, task), t, "bench_harness", 0.0, 4096, None, None, 1,
                  ("benchmark", 20000))
    hydra = [acct("hydra", "attack") for _ in range(6)]
    for r in range(3):    # hydra: synchronized starts, shared tasks across accounts, best-of-n
        for j, a in enumerate(hydra):
            task = TASKS[10 + (r * 2 + j) % 8]
            text = f"Task for repo r{r}: {task}\n{rng.choice(ELICIT)}"
            _emit(events, dups, gdups, a, _session(rng, text), T0 + 3600 * r + rng.uniform(0, 60),
                  "standard_agent", 1.0, 32768, "high", None, rng.randint(4, 8), (None, None))
    for a in [acct("fake_env", "attack") for _ in range(3)]:
        for r in range(3):
            _emit(events, dups, gdups, a, _session(rng, f"Task for repo f{r}: {TASKS[20 + r]}", fake=True),
                  T0 + 7200 * r + rng.uniform(0, 60), "standard_agent", 1.0, 32768, "high", None, 3, (None, None))
    for a in [acct("stealth", "attack") for _ in range(3)]:
        t = T0 + rng.uniform(0, 3600)
        for r in range(3):
            t = _emit(events, dups, gdups, a, _session(rng, f"hey can you look at this: {TASKS[25 + r % 3].lower()}", followups=1),
                      t, "standard_agent", 0.6, 8192, None, None, 2, (None, None)) + rng.uniform(1800, 9000)
    events.sort(key=lambda e: e.sim_time)
    return events, labels


def main() -> None:
    events, labels = build()
    with open(OUT / "scenario_small.jsonl", "w", encoding="utf-8") as f:
        for ev in events:
            f.write(ev.model_dump_json() + "\n")
    (OUT / "scenario_small_labels.json").write_text(json.dumps(labels, indent=1))
    print(f"{len(events)} events, {len(labels)} accounts")


if __name__ == "__main__":
    main()
