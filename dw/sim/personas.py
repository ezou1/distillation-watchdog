"""Persona parameters for the six simulated account types.

One Persona class holds the parameters. The values come from the `sim.personas` section of config.yaml.
Training scenarios widen the numeric ranges (domain randomization) and use a light stealth persona.
"""
from __future__ import annotations

import random
from dataclasses import dataclass, fields, replace
from typing import Any, Optional

from dw.common.config import CFG

ROLE_PERSONAS: dict[str, list[str]] = {
    "normal": ["solo_dev", "saas_app", "benchmark_runner"],
    "attack": ["hydra", "fake_env", "stealth"],
}
ALL_PERSONAS: list[str] = ROLE_PERSONAS["normal"] + ROLE_PERSONAS["attack"]
TITLES: dict[str, str] = {
    "solo_dev": "Solo developers", "saas_app": "SaaS app", "benchmark_runner": "Benchmark runner",
    "hydra": "Hydra", "fake_env": "Fake-env", "stealth": "Stealth",
}

Range = tuple[float, float]


@dataclass(frozen=True)
class Persona:
    """Behavior parameters of one persona. All personas use the same scheduler and request builder."""

    name: str
    role: str                       # "normal" | "attack"
    data: str                       # swechat | saas | bench | hydra | fake | stealth
    schema_id: str
    timing: str                     # human | spread | sequential | cron
    sessions_per_account: Range
    temperature: Range
    max_tokens: Range
    best_of_n: Range = (1, 1)
    retry_rate: Range = (0.0, 0.0)  # per account: chance that one request is sent one more time (retry, regenerate)
    effort_rate: float = 0.0        # chance that an account sends reasoning_effort = "high"
    cap_rate: float = 0.0           # chance that an account sets max_tokens to the cap
    elicit_rate: float = 0.0
    benign_rate: float = 0.0
    attacker_template: bool = False
    product_template: Optional[str] = None
    end_users: Optional[Range] = None
    declared_use: Optional[str] = None
    quota: Optional[int] = None
    attempts: Range = (1, 1)
    swechat_mix: float = 0.0
    paraphrase: bool = False
    followups: Range = (0, 0)
    cron_interval_s: float = 0.0
    start_jitter_s: float = 0.0
    timing_jitter: float = 0.0


def _rng(value: Any) -> Range:
    """Turn a config value (number or [low, high]) into a range."""
    if isinstance(value, (list, tuple)):
        return float(value[0]), float(value[1])
    return float(value), float(value)


def _attack(name: str, data: str, c: dict[str, Any]) -> Persona:
    """Hydra and fake-env share one shape. Only the config values differ."""
    return Persona(
        name=name, role="attack", data=data, schema_id="standard_agent", timing="cron",
        sessions_per_account=_rng(c["sessions_per_account"]), temperature=_rng(c["temperature"]),
        max_tokens=_rng(CFG["sim"]["normal_max_tokens"]), best_of_n=_rng(c["best_of_n"]),
        **_params(c), elicit_rate=c["elicit_rate"], attacker_template=True,
        cron_interval_s=float(c["cron_interval_s"]), start_jitter_s=float(c["start_jitter_s"]))


def _params(c: dict[str, Any]) -> dict[str, float]:
    """Request-parameter rates of one persona. Every persona has the same three rates (overlapping values)."""
    return {"retry_rate": _rng(c.get("retry_rate", 0.0)), "effort_rate": float(c.get("effort_rate", 0.0)),
            "cap_rate": float(c.get("cap_rate", 0.0))}


def base_personas(full_stealth: bool) -> dict[str, Persona]:
    """Return the six persona configurations from config.yaml."""
    sim = CFG["sim"]
    p = sim["personas"]
    normal_tokens = _rng(sim["normal_max_tokens"])
    benign = sim["benign_explain_rate"]
    st = p["stealth"]
    return {
        "solo_dev": Persona(
            name="solo_dev", role="normal", data="swechat", schema_id="standard_agent", timing="human",
            sessions_per_account=_rng(p["solo_dev"]["sessions_per_account"]),
            temperature=_rng(p["solo_dev"]["temperature"]), max_tokens=normal_tokens, benign_rate=benign,
            **_params(p["solo_dev"])),
        "saas_app": Persona(
            name="saas_app", role="normal", data="saas", schema_id="saas_custom", timing="spread",
            sessions_per_account=_rng(p["saas_app"]["sessions_per_account"]),
            temperature=_rng(p["saas_app"]["temperature"]), max_tokens=normal_tokens, benign_rate=benign,
            **_params(p["saas_app"]),
            product_template=p["saas_app"]["product_template"], end_users=_rng(p["saas_app"]["end_users_per_account"])),
        "benchmark_runner": Persona(
            name="benchmark_runner", role="normal", data="bench", schema_id="bench_harness", timing="sequential",
            sessions_per_account=_rng(p["benchmark_runner"]["tasks_per_account"]),
            temperature=_rng(p["benchmark_runner"]["temperature"]), max_tokens=normal_tokens,
            benign_rate=benign, declared_use="benchmark", **_params(p["benchmark_runner"]), quota=int(p["benchmark_runner"]["quota"]),
            attempts=_rng(p["benchmark_runner"]["attempts"])),
        "hydra": _attack("hydra", "hydra", p["hydra"]),
        "fake_env": _attack("fake_env", "fake", p["fake_env"]),
        "stealth": Persona(
            name="stealth", role="attack", data="stealth" if full_stealth else "hydra",
            schema_id="standard_agent", timing="human",
            sessions_per_account=_rng(st["sessions_per_account"]), temperature=_rng(st["temperature"]),
            max_tokens=normal_tokens, best_of_n=_rng(st["best_of_n"]), **_params(st), elicit_rate=st["elicit_rate"],
            swechat_mix=st["swechat_mix"] if full_stealth else 0.0, paraphrase=full_stealth,
            followups=_rng(st["followups"]) if full_stealth else (0, 0), timing_jitter=float(st["timing_jitter"])),
    }


def _widen(value: Any, rng: random.Random, frac: float) -> Any:
    """Scale a number or both ends of a range by a random factor in [1 - frac, 1 + frac]."""
    if isinstance(value, tuple):
        low, high = (_widen(v, rng, frac) for v in value)
        return min(low, high), max(low, high)
    if isinstance(value, float):
        return value * rng.uniform(1 - frac, 1 + frac)
    return value


_RANDOMIZED = ("sessions_per_account", "temperature", "best_of_n", "elicit_rate", "benign_rate",
               "retry_rate", "effort_rate", "cap_rate",
               "start_jitter_s", "cron_interval_s", "timing_jitter", "end_users", "attempts")


def randomize(persona: Persona, rng: random.Random, frac: float) -> Persona:
    """Domain randomization for training scenarios. Rates stay in [0, 1], temperatures in [0, 2]."""
    changes = {f.name: _widen(getattr(persona, f.name), rng, frac)
               for f in fields(persona) if f.name in _RANDOMIZED and getattr(persona, f.name) is not None}
    lo, hi = changes["temperature"]
    changes["temperature"] = (max(0.0, lo), min(2.0, hi))
    for key in ("elicit_rate", "benign_rate", "effort_rate", "cap_rate"):
        changes[key] = min(1.0, changes[key])
    lo, hi = changes["retry_rate"]
    changes["retry_rate"] = (min(1.0, lo), min(1.0, hi))
    return replace(persona, **changes)


def make_personas(split: str, training: bool, rng: random.Random) -> dict[str, Persona]:
    """Persona configurations for a scenario.

    Full stealth (paraphrased prompts, follow-ups, SWE-chat sessions) in the holdout. With `train.full_stealth`,
    also in the train and validation scenarios: then the attack accounts in training do not all have one user turn
    per session, so the model cannot learn "one user turn = attack" (a source shortcut, R1-M2).
    """
    full = (split == "holdout" and not training) or bool(CFG["train"].get("full_stealth", False))
    personas = base_personas(full_stealth=full)
    if training:
        frac = CFG["sim"]["train_randomization"]
        personas = {name: randomize(p, rng, frac) for name, p in personas.items()}
    return personas
