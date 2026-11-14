"""Tests for personas, the scheduler, and scenario building on the canonical_small fixture."""
from __future__ import annotations

import json
import random
import re
from collections import defaultdict
from pathlib import Path

import pytest

from dw.common.chat import schema_id_of, split_steps, to_openai
from dw.common.config import CFG
from dw.common.schema import Message, Session
from dw.sim.personas import ALL_PERSONAS, base_personas, make_personas
from dw.sim.scenario import Scenario, build_scenario, oa_offsets

FIX = Path(__file__).parent / "fixtures" / "canonical_small"
ORIGIN = float(CFG["sim"]["epoch_start"])


def _build(split: str, training: bool = False, **kw) -> Scenario:
    return build_scenario(split, 301, "small", training=training, data_dir=FIX, **kw)


@pytest.fixture(scope="module")
def holdout() -> Scenario:
    return _build("holdout")


@pytest.fixture(scope="module")
def train() -> Scenario:
    return _build("train", training=True)


@pytest.fixture(scope="module")
def val() -> Scenario:
    return _build("val")


def _of(sc: Scenario, persona: str):
    return [r for r in sc.requests if r.persona == persona]


def _first_user(body: dict) -> str:
    return next(m["content"] for m in body["messages"] if m["role"] == "user")


def _all_user_text(sc: Scenario, persona: str) -> list[str]:
    return [m["content"] for r in _of(sc, persona) for m in r.body["messages"] if m["role"] == "user"]


def test_all_personas_and_scale(holdout: Scenario) -> None:
    counts: dict[str, int] = defaultdict(int)
    for label in holdout.labels.values():
        counts[label["persona"]] += 1
        assert label["role"] in ("normal", "attack")
    assert dict(counts) == CFG["sim"]["scales"]["small"]


def test_time_order_and_origin(holdout: Scenario) -> None:
    times = [r.sim_time for r in holdout.requests]
    assert times == sorted(times)
    assert times[0] >= ORIGIN


def test_keys_random_and_no_persona_names(holdout: Scenario) -> None:
    seen: set[int] = set()
    for r in holdout.requests:
        assert re.fullmatch(r"acct-[0-9a-f]{12}", r.key)
        if id(r.body) in seen:
            continue
        seen.add(id(r.body))
        text = json.dumps(r.body)
        for name in ALL_PERSONAS:
            assert name not in text
        assert "persona" not in r.body and "label" not in r.body


def test_x_replay_is_the_recorded_step(holdout: Scenario) -> None:
    for r in holdout.requests[:500]:
        step = [Message(**m) for m in r.body["x_replay"]["step"]]
        assert step and all(m.role in ("assistant", "tool_call") for m in step)
        assert set(r.body) <= {"model", "messages", "tools", "temperature", "n", "max_tokens",
                               "reasoning_effort", "user", "x_replay"}


def test_openai_prefix_slices_match_full_conversion() -> None:
    sessions = [Session.model_validate_json(line) for line in (FIX / "splits" / "val.jsonl").read_text().splitlines()[:60]]
    for s in sessions:
        full = to_openai(s.messages, "standard_agent")
        offsets = oa_offsets(s.messages)
        for start, _ in split_steps(s.messages):
            assert to_openai(s.messages[:start], "standard_agent") == full[:offsets[start]]


def _task(body: dict) -> str:
    return re.search(r"Issue (\S+):", _first_user(body)).group(1)


def test_hydra_attempts_of_one_task_on_different_accounts(holdout: Scenario) -> None:
    accounts: dict[str, list[str]] = defaultdict(list)
    seen: set[str] = set()
    for r in _of(holdout, "hydra"):
        if r.session_ref not in seen:
            seen.add(r.session_ref)
            accounts[_task(r.body)].append(r.key)
    assert any(len(a) > 1 for a in accounts.values())
    for task, accts in accounts.items():
        assert len(accts) == len(set(accts)), task


def test_hydra_cron_starts_are_synchronized(holdout: Scenario) -> None:
    first: dict[str, float] = {}
    for r in _of(holdout, "hydra"):
        first.setdefault(r.key, r.sim_time)
    jitter = CFG["sim"]["personas"]["hydra"]["start_jitter_s"]
    assert max(first.values()) - ORIGIN <= jitter + CFG["sim"]["copy_spread_s"]


def _copies(sc: Scenario, persona: str) -> list[int]:
    n: dict[tuple[str, int], int] = defaultdict(int)
    for r in _of(sc, persona):
        n[(r.session_ref, len(r.body["messages"]))] += 1
    return list(n.values())


def test_best_of_n(holdout: Scenario) -> None:
    """Hydra sends best-of-n copies (+1 retry at most). Other personas send 1 copy, or 2 when they retry."""
    lo, hi = CFG["sim"]["personas"]["hydra"]["best_of_n"]
    assert all(lo <= c <= hi + 1 for c in _copies(holdout, "hydra"))
    stealth = _copies(holdout, "stealth")
    assert set(stealth) <= {1, 2} and 0 < stealth.count(2) < len(stealth)
    for persona in ("solo_dev", "saas_app", "benchmark_runner"):
        copies = _copies(holdout, persona)
        assert set(copies) <= {1, 2} and copies.count(1) > 0.8 * len(copies)


def test_attack_request_parameters(holdout: Scenario) -> None:
    """Request parameters overlap between personas: no single value identifies a persona."""
    cap = CFG["sim"]["max_tokens_cap"]
    hydra = _of(holdout, "hydra")
    lo, hi = CFG["sim"]["personas"]["hydra"]["temperature"]
    assert all(lo <= r.body["temperature"] <= hi for r in hydra)
    assert all(schema_id_of(r.body["tools"]) == "standard_agent" for r in hydra)
    # The rates are per account. Some seeds give no "high" account in a small scenario, so use three seeds.
    more = [build_scenario("holdout", seed, "small", data_dir=FIX) for seed in (302, 303)]
    for persona in ("hydra", "solo_dev"):
        rows = _of(holdout, persona) + [r for sc in more for r in _of(sc, persona)]
        efforts = {r.body.get("reasoning_effort") for r in rows}
        tokens = {r.body["max_tokens"] == cap for r in rows}
        assert efforts == {"high", None} and tokens == {True, False}, persona
    for r in _of(holdout, "solo_dev"):
        assert "user" not in r.body and 0.0 <= r.body["temperature"] <= 1.0


def test_schema_ids(holdout: Scenario) -> None:
    expected = {"solo_dev": "standard_agent", "saas_app": "saas_custom", "benchmark_runner": "bench_harness",
                "hydra": "standard_agent", "fake_env": "standard_agent", "stealth": "standard_agent"}
    for persona, sid in expected.items():
        assert {schema_id_of(r.body["tools"]) for r in _of(holdout, persona)} == {sid}


def test_saas_template_and_stable_user(holdout: Scenario) -> None:
    reqs = _of(holdout, "saas_app")
    assert all(r.body.get("user", "").startswith("eu-") for r in reqs)
    users = {r.session_ref: r.body["user"] for r in reqs}
    for r in reqs:
        assert r.body["user"] == users[r.session_ref]
    assert all(t.startswith("[DevPilot]") for t in _all_user_text(holdout, "saas_app"))
    lo, hi = CFG["sim"]["personas"]["saas_app"]["end_users_per_account"]
    assert 1 < len(set(users.values())) <= hi


def test_benchmark_registration(holdout: Scenario) -> None:
    bench = [a for a, lab in holdout.labels.items() if lab["persona"] == "benchmark_runner"]
    assert {r["account_id"] for r in holdout.registrations} == set(bench)
    assert all(r["declared_use"] == "benchmark" and r["quota"] > 0 for r in holdout.registrations)


def test_fake_env_uses_fake_outputs(holdout: Scenario) -> None:
    reqs = _of(holdout, "fake_env")
    listed = {json.loads(line)["session_id"].split("-")[2] for line in (FIX / "synth" / "fake_tool_outputs.jsonl").read_text().splitlines()
              if json.loads(line)["split"] == "holdout"}
    assert {_task(r.body).split("-")[1] for r in reqs} <= listed
    assert any(m["role"] == "tool" and m["content"] == "All 12 tests passed." for r in reqs for m in r.body["messages"])


def test_templates_by_split(holdout: Scenario, train: Scenario, val: Scenario) -> None:
    def ids(sc: Scenario) -> set[str]:
        return {re.match(r"\[T(\d)\]", _first_user(r.body)).group(1) for r in _of(sc, "hydra")}
    assert ids(holdout) <= {"5", "6"}
    assert ids(train) <= {"1", "2", "3", "4"} and ids(val) <= {"1", "2", "3", "4"}
    assert all(not t.startswith("[T") for t in _all_user_text(holdout, "stealth"))


def test_banks_by_split(holdout: Scenario, train: Scenario) -> None:
    hold_text = " ".join(json.dumps(r.body["messages"]) for r in holdout.requests[::3])
    train_text = " ".join(json.dumps(r.body["messages"]) for r in train.requests[::3])
    assert "[B]" in hold_text and "[A]" not in hold_text
    assert "[A]" in train_text and "[B]" not in train_text


def test_full_stealth_only_in_holdout(holdout: Scenario, train: Scenario, val: Scenario) -> None:
    hold = _all_user_text(holdout, "stealth")
    assert any(t.startswith("hey, quick one") for t in hold)                     # paraphrase
    assert any("ok that worked" in t for t in hold)                              # fake follow-up
    assert any(t.startswith("in my app") for t in hold)                          # SWE-chat mixing
    for sc in (train, val):
        light = _all_user_text(sc, "stealth")
        assert light and not any(t.startswith(("hey, quick one", "in my app")) or "ok that worked" in t for t in light)
    trained_holdout = _all_user_text(_build("holdout", training=True), "stealth")
    assert not any(t.startswith("hey, quick one") for t in trained_holdout)


def test_scenario_reads_only_its_split(holdout: Scenario, val: Scenario) -> None:
    hold = " ".join(json.dumps(r.body["messages"]) for r in holdout.requests)
    assert not re.search(r"(train|val|xh)-\d", hold)
    vtext = " ".join(json.dumps(r.body["messages"]) for r in val.requests)
    assert not re.search(r"(train|holdout|xh)-\d", vtext)


def test_xharness_hydra() -> None:
    sc = _build("holdout", xharness=True, personas=["hydra"])
    assert {lab["persona"] for lab in sc.labels.values()} == {"hydra"}
    assert all(_task(r.body).startswith("xh-") for r in sc.requests)


def test_final_flag(holdout: Scenario) -> None:
    last: dict[str, object] = {}
    for r in holdout.requests:
        last[r.session_ref] = r
    finals = [r for r in holdout.requests if r.final]
    assert finals and all(last[r.session_ref].body is r.body for r in finals)


def test_deterministic_and_persona_subset_stable() -> None:
    a = _build("holdout", personas=["hydra"])
    b = _build("holdout")
    assert [r.key for r in a.requests] == [r.key for r in b.requests if r.persona == "hydra"]


def test_training_randomization_changes_parameters() -> None:
    base = base_personas(full_stealth=False)
    rand = make_personas("train", True, random.Random(1))
    assert any(rand[p] != base[p] for p in ALL_PERSONAS)
    assert not rand["stealth"].paraphrase and rand["stealth"].swechat_mix == 0.0
    assert make_personas("holdout", False, random.Random(1))["stealth"].paraphrase
    assert not make_personas("val", False, random.Random(1))["stealth"].paraphrase
