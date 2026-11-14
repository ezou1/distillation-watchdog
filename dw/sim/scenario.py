"""Build a simulation scenario: accounts, timed requests, labels, and registrations.

The function reads one split file and the matching synthetic assets. Each assistant step of a session
becomes one request. The recorded step goes in the private `x_replay` field. Labels stay in `Scenario.labels`.
"""
from __future__ import annotations

import itertools
import json
import logging
import math
import random
import statistics
from collections import defaultdict
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Iterable, Iterator, Optional

from dw.common.chat import split_steps, to_openai, tool_defs
from dw.common.config import CFG, path
from dw.common.hashing import salted_id
from dw.common.schema import Message, Session
from dw.sim import scheduler
from dw.sim.personas import ALL_PERSONAS, Persona, make_personas
from dw.sim.scheduler import TimingFit

log = logging.getLogger("dw.sim")


@dataclass
class SimRequest:
    sim_time: float
    key: str                  # bearer key = account ID
    body: dict                # OpenAI request body, with private "x_replay": {"step": [Message dicts]}
    persona: str              # label, used only by the sender counters and evaluation
    session_ref: str          # simulator session ID (label side, never sent to the lab)
    final: bool               # True if this step ends the trajectory at the model stop


@dataclass
class Scenario:
    requests: list[SimRequest]               # sorted by sim_time
    labels: dict[str, dict[str, str]]        # account_id -> {"persona": ..., "role": "normal"|"attack"}
    registrations: list[dict]                # bodies for POST /admin/accounts


# ---------------------------------------------------------------- input files

@dataclass
class Assets:
    """Everything one scenario reads from disk."""

    sessions: list[Session]
    xharness: list[Session]
    fit: TimingFit
    fakes: dict[str, dict[int, str]]          # session_id -> message_index -> fake text
    stealth: dict[str, dict[str, Any]]        # session_id -> {"paraphrase", "followups"}
    elicit: list[str]
    benign: list[str]
    templates: list[str]


def _jsonl(file: Path) -> Iterable[str]:
    if not file.exists():
        log.warning("missing file %s", file)
        return []
    with open(file, encoding="utf-8") as f:
        return [line for line in f if line.strip()]


def _json(file: Path, default: Any) -> Any:
    if not file.exists():
        log.warning("missing file %s", file)
        return default
    return json.loads(file.read_text(encoding="utf-8"))


def load_assets(split: str, data_dir: Optional[Path], xharness: bool) -> Assets:
    """Read the split file and the synthetic assets for this split only."""
    splits = data_dir / "splits" if data_dir else path("splits")
    synth = data_dir / "synth" if data_dir else path("synth")
    fit_file = data_dir / "timing_fit.json" if data_dir else path("timing_fit")
    fakes: dict[str, dict[int, str]] = defaultdict(dict)
    for line in _jsonl(synth / "fake_tool_outputs.jsonl"):
        row = json.loads(line)
        if row["split"] == split:
            fakes[row["session_id"]][int(row["message_index"])] = row["fake_text"]
    stealth = {r["session_id"]: r for r in map(json.loads, _jsonl(synth / "stealth.jsonl")) if r["split"] == split}
    bank = _json(synth / ("bank_b.json" if split == "holdout" else "bank_a.json"), {"elicit": [], "benign": []})
    template_split = "holdout" if split == "holdout" else "train"
    templates = [t["text"] for t in _json(synth / "templates.json", []) if t["split"] == template_split]
    return Assets(
        sessions=[Session.model_validate_json(line) for line in _jsonl(splits / f"{split}.jsonl")],
        xharness=[Session.model_validate_json(line) for line in _jsonl(splits / "xharness.jsonl")] if xharness else [],
        fit=TimingFit.load(fit_file), fakes=dict(fakes), stealth=stealth,
        elicit=[e["text"] for e in bank["elicit"]], benign=[b["text"] for b in bank["benign"]],
        templates=templates)


# ---------------------------------------------------------------- data pools

def _mean(r: tuple[float, float]) -> float:
    return (r[0] + r[1]) / 2


def _randint(rng: random.Random, r: tuple[float, float]) -> int:
    return rng.randint(round(r[0]), max(round(r[0]), round(r[1])))


def _partition(items: list[Any], demands: dict[str, float], rest: str) -> dict[str, list[Any]]:
    """Cut a shuffled list into disjoint shares in demand order. The `rest` share gets all items left.

    A share never takes more than half of the items that are left, so later shares are never empty.
    """
    out: dict[str, list[Any]] = {}
    start = 0
    for name, demand in demands.items():
        size = min(math.ceil(demand), (len(items) - start) // 2) if demand > 0 else 0
        out[name] = items[start:start + size]
        start += size
    out[rest] = items[start:]
    return out


@dataclass
class Pools:
    """Disjoint data shares per persona. Built once for all six personas, so selections stay stable."""

    swechat: dict[str, list[list[Session]]]     # persona -> sessions grouped by user
    openswe: dict[str, list[list[Session]]]     # persona -> trajectories grouped by task
    fake: list[list[Session]]
    stealth: list[list[Session]]
    xharness: list[list[Session]]


def _grouped(sessions: list[Session], key: str) -> list[list[Session]]:
    groups: dict[str, list[Session]] = defaultdict(list)
    for s in sessions:
        groups[getattr(s, key) or s.session_id].append(s)
    return [sorted(g, key=lambda s: s.session_id) for _, g in sorted(groups.items())]


def build_pools(assets: Assets, personas: dict[str, Persona], counts: dict[str, int], rng: random.Random) -> Pools:
    swe = [s for s in assets.sessions if s.source == "swechat"]
    special = set(assets.fakes) | set(assets.stealth)
    ose = [s for s in assets.sessions if s.source == "openswe" and s.session_id not in special]
    users = _grouped(swe, "group_id")
    tasks = _grouped(ose, "task_key")
    rng.shuffle(users)
    rng.shuffle(tasks)
    per_user = statistics.fmean(len(u) for u in users) if users else 1.0
    per_task = statistics.fmean(len(t) for t in tasks) if tasks else 1.0
    st = personas["stealth"]
    swe_demand = {   # in users; saas_app gets the rest
        "solo_dev": counts["solo_dev"],
        "stealth": counts["stealth"] * _mean(st.sessions_per_account) * st.swechat_mix / per_user,
    }
    ose_demand = {   # in tasks; hydra gets the rest
        "benchmark_runner": counts["benchmark_runner"] * _mean(personas["benchmark_runner"].sessions_per_account),
        "stealth": counts["stealth"] * _mean(st.sessions_per_account) / per_task if st.data == "hydra" else 0,
    }
    by_id = {s.session_id: s for s in assets.sessions}
    return Pools(
        swechat=_partition(users, swe_demand, "saas_app"), openswe=_partition(tasks, ose_demand, "hydra"),
        fake=_grouped([by_id[i] for i in sorted(assets.fakes) if i in by_id], "task_key"),
        stealth=_grouped([by_id[i] for i in sorted(assets.stealth) if i in by_id], "task_key"),
        xharness=_grouped(assets.xharness, "task_key"))


# ---------------------------------------------------------------- session selection per account

Assigned = list[list[tuple[Session, Optional[str]]]]     # per account: (session, end-user field)


def _select_users(groups: list[list[Session]], n: int, p: Persona, rng: random.Random) -> Assigned:
    """solo_dev: one account per real user, with that user's own sessions."""
    return [[(s, None) for s in g[:_randint(rng, p.sessions_per_account)]] for g in groups[:n]]


def _select_saas(groups: list[list[Session]], n: int, p: Persona, rng: random.Random) -> Assigned:
    """saas_app: many real users folded into one account. The `user` field is stable per real user."""
    out: Assigned = []
    it = iter(groups)
    for _ in range(n):
        chosen = [g for _, g in zip(range(_randint(rng, p.end_users or (1, 1))), it)]
        pairs = [(s, "eu-" + salted_id(s.group_id, "saas")[:10]) for g in chosen for s in g]
        rng.shuffle(pairs)
        out.append(pairs[:_randint(rng, p.sessions_per_account)])
    return out


def _select_bench(tasks: list[list[Session]], n: int, p: Persona, rng: random.Random) -> Assigned:
    """benchmark_runner: a set of tasks per account, 1 to 3 different attempts each, no duplicates."""
    out: Assigned = []
    it = iter(tasks)
    for _ in range(n):
        chosen = [t for _, t in zip(range(_randint(rng, p.sessions_per_account)), it)]
        out.append([(s, None) for t in chosen for s in t[:_randint(rng, p.attempts)]])
    return out


def _select_spread(tasks: list[list[Session]], n: int, p: Persona, rng: random.Random) -> Assigned:
    """hydra, fake_env, stealth: the attempts of one task go to different accounts.

    Each task goes to the accounts with the most free capacity, so session k of every account
    belongs to the same start wave.
    """
    capacity = [_randint(rng, p.sessions_per_account) for _ in range(n)]
    out: Assigned = [[] for _ in range(n)]
    for turn, trajectories in enumerate(tasks):
        free = sorted((i for i in range(n) if capacity[i] > 0), key=lambda i: (-capacity[i], (i - turn) % n))
        if not free:
            break
        for i, s in zip(free, trajectories):
            out[i].append((s, None))
            capacity[i] -= 1
    return out


def _mix_swechat(assigned: Assigned, users: list[list[Session]], mix: float, rng: random.Random) -> Assigned:
    """Full stealth: add real SWE-chat sessions on the same accounts (about `mix` of all sessions)."""
    pool = [s for g in users for s in g]
    rng.shuffle(pool)
    it = iter(pool)
    for sessions in assigned:
        extra = round(len(sessions) * mix / (1 - mix)) if mix < 1 else 0
        sessions.extend((s, None) for _, s in zip(range(extra), it))
        rng.shuffle(sessions)
    return assigned


def select(p: Persona, pools: Pools, n: int, rng: random.Random, xharness: bool) -> Assigned:
    if p.data == "swechat":
        return _select_users(pools.swechat["solo_dev"], n, p, rng)
    if p.data == "saas":
        return _select_saas(pools.swechat["saas_app"], n, p, rng)
    if p.data == "bench":
        return _select_bench(pools.openswe["benchmark_runner"], n, p, rng)
    if p.data == "fake":
        return _select_spread(pools.fake, n, p, rng)
    if p.data == "stealth":
        assigned = _select_spread(pools.stealth, n, p, rng)
        return _mix_swechat(assigned, pools.swechat["stealth"], p.swechat_mix, rng)
    tasks = pools.xharness if (xharness and p.name == "hydra") else pools.openswe[p.name]
    return _select_spread(tasks, n, p, rng)


# ---------------------------------------------------------------- text transforms

def _files(messages: list[Message]) -> str:
    seen: list[str] = []
    for m in messages:
        p = (m.args or {}).get("path")
        if m.role == "tool_call" and p and p not in seen:
            seen.append(str(p))
    return ", ".join(seen[:3])


def _fill(template: str, session: Session, issue: str) -> str:
    return (template.replace("{repo}", session.group_id).replace("{files}", _files(session.messages))
            .replace("{issue}", issue))


def _followups(messages: list[Message], texts: list[str]) -> list[Message]:
    """Add fake follow-up user turns. The answer reuses the last prose-only step, or the session ends."""
    prose = [step for _, step in split_steps(messages) if all(m.role == "assistant" for m in step)]
    out = list(messages)
    for text in texts:
        out.append(Message(role="user", text=text))
        if not prose:
            break
        out.extend(m.model_copy() for m in prose[-1])
    return out


def transform(session: Session, p: Persona, assets: Assets, rng: random.Random) -> list[Message]:
    """Apply the persona text changes to a copy of the canonical messages."""
    msgs = [m.model_copy() for m in session.messages]
    if p.data == "fake":
        for index, text in assets.fakes.get(session.session_id, {}).items():
            if index < len(msgs) and msgs[index].role == "tool_result":
                msgs[index].text = text
    first = next((m for m in msgs if m.role == "user"), None)
    synth = assets.stealth.get(session.session_id) if p.paraphrase else None
    if synth and first:
        first.text = synth["paraphrase"]
        k = rng.randint(round(p.followups[0]), round(p.followups[1]))
        msgs = _followups(msgs, list(synth.get("followups", []))[:k])
    if p.attacker_template and assets.templates and first:
        first.text = _fill(rng.choice(assets.templates), session, first.text)
    phrases, rate = (assets.elicit, p.elicit_rate) if p.role == "attack" else (assets.benign, p.benign_rate)
    for m in msgs:
        if m.role != "user":
            continue
        if phrases and rng.random() < rate:
            m.text = m.text + "\n\n" + rng.choice(phrases)
        if p.product_template:
            m.text = p.product_template.replace("{prompt}", m.text)
    return msgs


# ---------------------------------------------------------------- requests

def oa_offsets(messages: list[Message]) -> list[int]:
    """offsets[i] = number of OpenAI messages that to_openai() makes from messages[:i].

    A tool call joins the assistant message before it. Every other message makes one new message.
    """
    offsets = [0]
    for i, m in enumerate(messages):
        joins = m.role == "tool_call" and i > 0 and messages[i - 1].role in ("assistant", "tool_call")
        offsets.append(offsets[-1] + (0 if joins else 1))
    return offsets


@dataclass
class AccountParams:
    key: str
    temperature: float
    max_tokens: int
    effort: Optional[str]
    fit: TimingFit
    persona: Persona


def _account_params(key: str, p: Persona, fit: TimingFit, rng: random.Random) -> AccountParams:
    lo, hi = int(p.max_tokens[0]), int(p.max_tokens[1])
    max_tokens = lo if lo == hi else rng.randrange(lo, hi + 1, 1024)
    if rng.random() < p.cap_rate:
        max_tokens = int(CFG["sim"]["max_tokens_cap"])
    effort = "high" if rng.random() < p.effort_rate else None
    return AccountParams(
        key=key, temperature=round(rng.uniform(*p.temperature), 2), max_tokens=max_tokens, effort=effort,
        fit=fit.perturbed(rng, p.timing_jitter) if p.timing_jitter else fit, persona=p)


def session_requests(msgs: list[Message], times: list[float], a: AccountParams, user: Optional[str],
                     ref: str, origin: float, rng: random.Random) -> list[SimRequest]:
    """One request per assistant step. Best-of-n sends the same body n times close together."""
    p = a.persona
    steps = split_steps(msgs)
    oa = to_openai(msgs, p.schema_id)
    offsets = oa_offsets(msgs)
    tools = _TOOLS[p.schema_id]
    spread = CFG["sim"]["copy_spread_s"]
    out: list[SimRequest] = []
    for idx, ((start, step), t) in enumerate(zip(steps, times)):
        body: dict[str, Any] = {"model": _MODEL, "messages": oa[:offsets[start]], "tools": tools,
                                "temperature": a.temperature, "n": 1, "max_tokens": a.max_tokens}
        if a.effort:
            body["reasoning_effort"] = a.effort
        if user:
            body["user"] = user
        body["x_replay"] = {"step": [m.model_dump() for m in step]}
        final = idx == len(steps) - 1 and all(m.role != "tool_call" for m in step)
        copies = _randint(rng, p.best_of_n) + int(rng.random() < p.retry_rate)
        for c in range(copies):
            when = origin + t + (rng.uniform(0, spread) if c else 0.0)
            out.append(SimRequest(when, a.key, body, p.name, ref, final))
    return out


_MODEL: str = CFG["models"]["lab_model"]
_TOOLS: dict[str, list[dict[str, Any]]] = {sid: tool_defs(sid) for sid in ("standard_agent", "saas_custom", "bench_harness")}


def _new_key(rng: random.Random, used: set[str]) -> str:
    while True:
        key = f"acct-{rng.getrandbits(48):012x}"
        if key not in used:
            used.add(key)
            return key


@dataclass
class _Build:
    requests: list[SimRequest] = field(default_factory=list)
    labels: dict[str, dict[str, str]] = field(default_factory=dict)
    registrations: list[dict] = field(default_factory=list)
    keys: set[str] = field(default_factory=set)
    refs: Iterator[int] = field(default_factory=lambda: itertools.count(1))


def _build_persona(out: _Build, p: Persona, assigned: Assigned, assets: Assets, days: int,
                   origin: float, rng: random.Random) -> None:
    gap = tuple(CFG["sim"]["bench_gap_s"])
    for sessions in assigned:
        key = _new_key(rng, out.keys)
        out.labels[key] = {"persona": p.name, "role": p.role}
        if p.declared_use:
            out.registrations.append({"account_id": key, "declared_use": p.declared_use, "quota": p.quota})
        a = _account_params(key, p, assets.fit, rng)
        texts = [(transform(s, p, assets, rng), user) for s, user in sessions]
        flags = [[start > 0 and msgs[start - 1].role == "user" for start, _ in split_steps(msgs)] for msgs, _ in texts]
        plans = scheduler.plan(p.timing, flags, a.fit, rng, days, p.cron_interval_s, p.start_jitter_s, gap)
        for (msgs, user), times in zip(texts, plans):
            ref = f"sess-{next(out.refs):06d}"
            out.requests.extend(session_requests(msgs, times, a, user, ref, origin, rng))


def build_scenario(split: str, seed: int, scale: str, personas: list[str] | None = None,
                   training: bool = False, xharness: bool = False, data_dir: Path | None = None) -> Scenario:
    """Build the scenario for one split. Reads only that split file and the matching synthetic assets."""
    if split not in ("train", "val", "holdout"):
        raise ValueError(f"unknown split {split}")
    rng = random.Random(f"{seed}|{split}|{scale}|{training}")
    assets = load_assets(split, data_dir, xharness)
    configs = make_personas(split, training, rng)
    counts = {name: int(CFG["sim"]["scales"][scale][name]) for name in ALL_PERSONAS}
    pools = build_pools(assets, configs, counts, rng)
    days = int(CFG["sim"]["sim_days"][scale])
    origin = float(CFG["sim"]["epoch_start"])
    out = _Build()
    for name in personas or ALL_PERSONAS:
        prng = random.Random(f"{seed}|{split}|{scale}|{training}|{name}")
        assigned = select(configs[name], pools, counts[name], prng, xharness)
        _build_persona(out, configs[name], [a for a in assigned if a], assets, days, origin, prng)
    out.requests.sort(key=lambda r: r.sim_time)
    return Scenario(requests=out.requests, labels=out.labels, registrations=out.registrations)
