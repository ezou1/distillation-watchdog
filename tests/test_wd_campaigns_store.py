"""Tests for campaign detection on crafted accounts and for the EventStore round trip."""
from __future__ import annotations

import itertools
import math
from collections import Counter
from pathlib import Path

from dw.common.config import CFG
from dw.common.schema import Message
from dw.watchdog.campaigns import Profile, build_graph, find_campaigns, schema_rarity
from dw.watchdog.store import EventStore
from test_wd_stubs import make_event


def _campaign_member(i: int) -> Profile:
    # Same 4 tasks, bursts in the same minutes, a rare tool schema.
    minutes = Counter({m: 5 for m in (10, 11, 30, 31, 50)})
    minutes[5 + i] += 1
    return Profile(tasks={f"task{k}": f"r-{i}-{k}" for k in range(4)}, minutes=minutes, schemas={"custom_rare"})


def _normal(i: int) -> Profile:
    return Profile(tasks={f"own{i}-{k}": f"n-{i}-{k}" for k in range(3)},
                   minutes=Counter({100 * i + k: 1 for k in range(5)}), schemas={"standard_agent"})


def _profiles() -> dict[str, Profile]:
    profiles = {f"atk{i}": _campaign_member(i) for i in range(5)}
    profiles.update({f"dev{i}": _normal(i) for i in range(4)})
    profiles.update({f"bg{i}": Profile(schemas={"standard_agent"}, minutes=Counter({0: 1})) for i in range(20)})
    return profiles


def test_campaign_is_found_and_blocks_low_score_members() -> None:
    profiles = _profiles()
    scores = {f"atk{i}": 0.9 for i in range(4)} | {"atk4": 0.35} | {f"dev{i}": 0.5 for i in range(4)}
    cands = sorted(scores)
    g = build_graph(cands, profiles, scores)
    assert g.has_edge("atk0", "atk1") and not g.has_edge("dev0", "dev1")
    edge = g.edges["atk0", "atk1"]
    assert set(edge["types"]) == {"shared_task", "sync", "rare_schema"}
    assert "4 shared tasks" in edge["evidence"]
    numbers = itertools.count(1)
    camps = find_campaigns(g, profiles, scores, {}, lambda: next(numbers))
    assert list(camps) == ["cmp-0001"]
    camp = camps["cmp-0001"]
    assert camp.members == [f"atk{i}" for i in range(5)]
    assert camp.state == "block"
    mean_score = (0.9 * 4 + 0.35) / 5
    assert math.isclose(camp.score, mean_score * math.log1p(5) + camp.mean_edge_weight, abs_tol=1e-2)
    assert camp.evidence[0].detector == "campaign" and camp.evidence[1].request_ids
    # The same members keep the same ID in the next run.
    again = find_campaigns(g, profiles, scores, camps, lambda: next(numbers))
    assert list(again) == ["cmp-0001"]



def _saas_account(i: int) -> Profile:
    # One product on several accounts: its own tool schema, its own end users' tasks, no shared timing.
    return Profile(tasks={f"saas{i}-{k}": f"s-{i}-{k}" for k in range(6)},
                   minutes=Counter({17 * i + 40 * k: 1 for k in range(6)}), schemas={"saas_custom"})


def test_a_shared_rare_schema_alone_makes_no_edge() -> None:
    """Three accounts of one SaaS app share only their tool schema. They are not a campaign."""
    profiles = _profiles() | {f"saas{i}": _saas_account(i) for i in range(3)}
    # Many other accounts on the lab, as when the attack personas run: the schema is very rare.
    profiles.update({f"other{i}": Profile(schemas={"standard_agent"}, minutes=Counter({0: 1})) for i in range(120)})
    assert schema_rarity(profiles)["saas_custom"] > 3.5
    # The worst case before this change: every SaaS account just above the candidate threshold.
    scores = {f"saas{i}": CFG["campaign"]["candidate_threshold"] + 0.01 for i in range(3)}
    g = build_graph(sorted(scores), profiles, scores)
    assert g.number_of_edges() == 0
    assert find_campaigns(g, profiles, scores, {}, lambda: 1) == {}


def test_a_shared_rare_schema_still_strengthens_a_real_link() -> None:
    """Attack accounts that share tasks and timing keep the schema weight on their edge."""
    profiles = _profiles()
    scores = {f"atk{i}": 0.9 for i in range(5)}
    g = build_graph(sorted(scores), profiles, scores)
    edge = g.edges["atk0", "atk1"]
    assert edge["types"]["rare_schema"] > 0
    assert math.isclose(edge["weight"], sum(edge["types"].values()))


def test_no_campaign_below_min_size() -> None:
    profiles = _profiles()
    scores = {"atk0": 0.9, "atk1": 0.9, "dev0": 0.5}
    g = build_graph(sorted(scores), profiles, scores)
    assert find_campaigns(g, profiles, scores, {}, lambda: 1) == {}


def test_schema_rarity() -> None:
    rarity = schema_rarity(_profiles())
    assert rarity["standard_agent"] < 0.3 < rarity["custom_rare"]


def test_event_store_round_trip(tmp_path: Path) -> None:
    db = tmp_path / "events.sqlite"
    store = EventStore(db, memory_max=2, flush_s=60.0)
    events = [make_event(f"r{i}", "a", [Message(role="user", text=f"task {i} " * 50)], float(i)) for i in range(4)]
    for ev in events:
        store.append(ev)
    store.append(make_event("drop1", "a", [Message(role="user", text="x")]), dropped=True)
    assert "r0" not in store.memory and len(store.memory) == 2
    assert store.get("r0") == events[0]            # from SQLite (flushes the batch first)
    assert store.count() == 5
    raw = store.conn.execute("SELECT body FROM events WHERE request_id='r1'").fetchone()[0]
    assert len(raw) < len(events[1].model_dump_json())  # zlib-compressed
    assert store.conn.execute("PRAGMA journal_mode").fetchone()[0] == "wal"
    store.close()
    again = EventStore(db)
    assert again.get("r3") == events[3] and again.get("drop1") is not None
    again.close()
    mem = EventStore(None)
    mem.append(events[0])
    assert mem.get("r0") == events[0] and mem.get("zzz") is None
