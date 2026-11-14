"""Fit timing parameters from SWE-chat train sessions only and write data/timing_fit.json.

The fit gives lognormal parameters for user think time and agent step gaps, the session start hour,
the sessions per user per active day, and the days between returns to the same repository.
"""
from __future__ import annotations

import json
import math
from collections import Counter, defaultdict
from typing import Iterable

import numpy as np

from dw.common.config import CFG, path
from dw.common.schema import Session
from dw.data.normalize import read_jsonl

DAY = 86400.0


def lognormal(values: Iterable[float]) -> dict[str, float]:
    """Return lognormal parameters (mu, sigma of the log) and the sample size."""
    logs = np.log(np.array([v for v in values if v > 0], dtype=float))
    if logs.size == 0:
        return {"mu": 0.0, "sigma": 1.0, "n": 0}
    return {"mu": float(logs.mean()), "sigma": float(logs.std()), "n": int(logs.size),
            "median": float(math.exp(np.median(logs)))}


def gaps(s: Session) -> tuple[list[float], list[float], list[float]]:
    """Return (think, model, tool) gaps in seconds of one session.

    think: from the last agent turn to the next user turn. model: from a user turn or tool result
    to the next assistant turn or tool call. tool: from a tool call to its result.
    """
    think, model, tool = [], [], []
    t, msgs = s.real_times or [], s.messages
    for i in range(1, min(len(t), len(msgs))):
        gap, prev, cur = t[i] - t[i - 1], msgs[i - 1].role, msgs[i].role
        if cur == "user" and prev != "user":
            think.append(gap)
        elif cur in ("assistant", "tool_call") and prev in ("user", "tool_result"):
            model.append(gap)
        elif cur == "tool_result" and prev == "tool_call":
            tool.append(gap)
    return think, model, tool


def fit(sessions: list[Session]) -> dict:
    """Fit all timing parameters from sessions that have real timestamps."""
    cfg = CFG["split"]
    think: list[float] = []
    model: list[float] = []
    tool: list[float] = []
    hours: Counter[int] = Counter()
    per_day: Counter[tuple[str, int]] = Counter()
    repo_days: dict[tuple[str, str], set[int]] = defaultdict(set)
    for s in sessions:
        if not s.real_times:
            continue
        th, mo, to = gaps(s)
        think += [g for g in th if g <= cfg["timing_max_think_s"]]
        model += [g for g in mo if g <= cfg["timing_max_step_s"]]
        tool += [g for g in to if g <= cfg["timing_max_step_s"]]
        start = s.real_times[0]
        hours[int(start // 3600) % 24] += 1
        day = int(start // DAY)
        per_day[(s.group_id, day)] += 1
        repo_days[(s.group_id, s.repo_key)].add(day)
    step = model + tool
    total = sum(hours.values()) + 24
    returns = [b - a for days in repo_days.values() for a, b in zip(sorted(days), sorted(days)[1:])]
    counts = np.array(list(per_day.values()) or [1], dtype=float)
    return {
        "think_time": lognormal(think),
        "step_gap": lognormal(step),
        "model_gap": lognormal(model),
        "tool_gap": lognormal(tool),
        "start_hour_probs": [(hours[h] + 1) / total for h in range(24)],
        "sessions_per_active_day": {"mean": float(counts.mean()), "median": float(np.median(counts)),
                                    "p90": float(np.percentile(counts, 90)), "max": float(counts.max())},
        "return_gap_days": lognormal(returns),
        "n_sessions": sum(1 for s in sessions if s.real_times),
        "hour_basis": "UTC",
    }


def main() -> None:
    train = [s for s in read_jsonl(path("splits") / "train.jsonl") if s.source == "swechat"]
    result = fit(train)
    path("timing_fit").write_text(json.dumps(result, indent=1))
    print(json.dumps({k: result[k] for k in ("think_time", "step_gap", "return_gap_days")}))


if __name__ == "__main__":
    main()
