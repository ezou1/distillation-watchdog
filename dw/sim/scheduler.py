"""One scheduler for all personas: session start times and step times in simulation seconds.

Human timing comes from data/timing_fit.json. Times are offsets from the scenario origin.
Only the timing mode and the parameters differ between personas.
"""
from __future__ import annotations

import json
import random
from dataclasses import dataclass, replace
from pathlib import Path

DAY_S = 86400.0


@dataclass(frozen=True)
class TimingFit:
    """Lognormal timing parameters fit on SWE-chat train sessions (seconds or days)."""

    think_mu: float
    think_sigma: float
    step_mu: float
    step_sigma: float
    hour_probs: list[float]
    sessions_per_day: float
    return_mu: float
    return_sigma: float

    @classmethod
    def load(cls, path: Path) -> "TimingFit":
        d = json.loads(path.read_text(encoding="utf-8"))
        return cls(
            think_mu=d["think_time"]["mu"], think_sigma=d["think_time"]["sigma"],
            step_mu=d["step_gap"]["mu"], step_sigma=d["step_gap"]["sigma"],
            hour_probs=list(d["start_hour_probs"]), sessions_per_day=d["sessions_per_active_day"]["mean"],
            return_mu=d["return_gap_days"]["mu"], return_sigma=d["return_gap_days"]["sigma"])

    def perturbed(self, rng: random.Random, amount: float) -> "TimingFit":
        """A copy with the lognormal mu values moved by up to +- amount (per-account variation)."""
        return replace(self, think_mu=self.think_mu + rng.uniform(-amount, amount),
                       step_mu=self.step_mu + rng.uniform(-amount, amount))


def _start_second(fit: TimingFit, rng: random.Random) -> float:
    """A start time inside one day from the hour-of-day distribution."""
    hour = rng.choices(range(24), weights=fit.hour_probs)[0]
    return hour * 3600.0 + rng.uniform(0, 3600.0)


def human_starts(n: int, days: int, fit: TimingFit, rng: random.Random) -> list[float]:
    """Active days with lognormal return gaps; a few sessions per active day at human hours."""
    starts: list[float] = []
    day = rng.uniform(0, 1)
    while len(starts) < n:
        per_day = max(1, round(rng.expovariate(1.0 / max(fit.sessions_per_day, 1e-6))))
        for _ in range(min(per_day, n - len(starts))):
            starts.append((int(day) % days) * DAY_S + _start_second(fit, rng))
        day += max(1.0, rng.lognormvariate(fit.return_mu, fit.return_sigma))
    return sorted(starts)


def spread_starts(n: int, days: int, fit: TimingFit, rng: random.Random) -> list[float]:
    """Many end users: any day, human hours of the day."""
    return sorted(rng.randrange(days) * DAY_S + _start_second(fit, rng) for _ in range(n))


def wave_starts(n: int, interval: float, jitter: float, rng: random.Random) -> list[float]:
    """Cron-like waves: session k starts at wave k plus a small jitter."""
    return [k * interval + rng.uniform(0, jitter) for k in range(n)]


def step_times(start: float, after_user: list[bool], fit: TimingFit, rng: random.Random) -> list[float]:
    """Times of the steps of one session. A step after a user turn waits a think time, else a step gap."""
    times: list[float] = []
    t = start
    for i, user_turn in enumerate(after_user):
        if i > 0:
            mu, sigma = (fit.think_mu, fit.think_sigma) if user_turn else (fit.step_mu, fit.step_sigma)
            t += rng.lognormvariate(mu, sigma)
        times.append(t)
    return times


def plan(timing: str, sessions: list[list[bool]], fit: TimingFit, rng: random.Random, days: int,
         interval: float = 0.0, jitter: float = 0.0, gap: tuple[float, float] = (0.0, 0.0)) -> list[list[float]]:
    """Step times for all sessions of one account.

    `sessions[k][i]` is True if step i of session k follows a user turn.
    Timing modes: human, spread, cron, sequential (each session starts after the previous one ends).
    """
    n = len(sessions)
    if timing == "sequential":
        out: list[list[float]] = []
        t = rng.uniform(0, 3600.0)
        for flags in sessions:
            times = step_times(t, flags, fit, rng)
            out.append(times)
            t = (times[-1] if times else t) + rng.uniform(*gap)
        return out
    if timing == "human":
        starts = human_starts(n, days, fit, rng)
    elif timing == "spread":
        starts = spread_starts(n, days, fit, rng)
    elif timing == "cron":
        starts = wave_starts(n, interval, jitter, rng)
    else:
        raise ValueError(f"unknown timing mode {timing}")
    return [step_times(s, flags, fit, rng) for s, flags in zip(starts, sessions)]

