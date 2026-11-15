"""Tests for the leak rate of the offline evaluation and its README table."""
from __future__ import annotations

from types import SimpleNamespace

from dw.eval import readme
from dw.eval.run_eval import leakage_by_campaign
from dw.sim.offline import OfflineResult
from dw.sim.scenario import Scenario, SimRequest


def _req(account: str, persona: str, session: str, final: bool) -> SimRequest:
    return SimRequest(0.0, account, {}, persona, session, final)


def _sent(r: SimRequest, status: int) -> dict:
    return {"account": r.key, "persona": r.persona, "status": status, "sim_time": r.sim_time,
            "final": r.final, "session": r.session_ref}


def test_leak_rate_uses_the_planned_sessions() -> None:
    # hydra: 4 sessions of 2 steps on 2 accounts. The final step of h1 goes out as two copies.
    h = {f"h{i}": [_req(f"acct-h{i % 2}", "hydra", f"h{i}", False), _req(f"acct-h{i % 2}", "hydra", f"h{i}", True)]
         for i in range(4)}
    h["h1"].append(_req("acct-h1", "hydra", "h1", True))
    # solo_dev: 2 sessions that complete, and one that ends on a tool call (it can never leak).
    s = {"s0": [_req("acct-s", "solo_dev", "s0", True)], "s1": [_req("acct-s", "solo_dev", "s1", True)],
         "s2": [_req("acct-s", "solo_dev", "s2", False)]}
    scen = Scenario(requests=[r for rs in (*h.values(), *s.values()) for r in rs], labels={}, registrations=[])

    result = OfflineResult()
    # h0 is served in full. h1 is served in full, with both copies of its final step. h2 is refused at its first
    # step, so the sender never tries its final step. h3 is refused at its final step.
    result.requests += [_sent(r, 200) for r in h["h0"]] + [_sent(r, 200) for r in h["h1"]]
    result.requests += [_sent(h["h2"][0], 403)]
    result.requests += [_sent(h["h3"][0], 200), _sent(h["h3"][1], 403)]
    result.requests += [_sent(r, 200) for rs in s.values() for r in rs]

    worker = SimpleNamespace(accounts={"acct-h0": SimpleNamespace(campaign_id="camp-1"),
                                       "acct-h1": SimpleNamespace(campaign_id="camp-1"),
                                       "acct-s": SimpleNamespace(campaign_id=None)})
    out = leakage_by_campaign(scen, result, worker)

    hydra = out["by_persona"]["hydra"]
    # The old ratio was served final steps over tried final steps: 3 of 4, and it did not see h2 at all.
    assert hydra == {"leaked_trajectories": 2, "planned_trajectories": 4, "leaked_share": 0.5}
    assert out["by_persona"]["solo_dev"] == {"leaked_trajectories": 2, "planned_trajectories": 2, "leaked_share": 1.0}
    assert out["by_campaign"] == {"camp-1": hydra, "none": out["by_persona"]["solo_dev"]}
    assert "planned_trajectories" in out["note"]


def test_a_persona_that_leaks_nothing_still_has_a_row() -> None:
    reqs = [_req("a", "hydra", "h0", False), _req("a", "hydra", "h0", True)]
    result = OfflineResult(requests=[_sent(reqs[0], 403)])
    out = leakage_by_campaign(Scenario(reqs, {}, []), result, SimpleNamespace(accounts={}))
    assert out["by_persona"] == {"hydra": {"leaked_trajectories": 0, "planned_trajectories": 1, "leaked_share": 0.0}}


def test_readme_table_shows_the_rate_and_keeps_old_reports_readable() -> None:
    new = readme._leakage_table({"hydra": {"leaked_trajectories": 3, "planned_trajectories": 120, "leaked_share": 0.025},
                                 "stealth": {"leaked_trajectories": 65, "planned_trajectories": 67, "leaked_share": 0.9701}})
    assert "| Hydra | 2.5% | 3 | 120 |" in new and "| Stealth | 97.0% | 65 | 67 |" in new
    # A report written before the leak rate has no planned total. It keeps its two count columns and gets no percentage.
    old = readme._leakage_table({"stealth": {"served_trajectories": 65, "possible_trajectories": 67}})
    assert "| Stealth | 65 | 67 |" in old and "%" not in old
