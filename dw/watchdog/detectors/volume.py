"""D7 volume: industrial scale in simulation time (request rate, concurrent sessions, active hours, `user` field).

The simulator sets these timings. Thus these features come from the simulator, not from real data.
"""
from __future__ import annotations

import math
from collections import Counter

from dw.common.schema import AccountState, DetectorView, Evidence
from dw.watchdog.detectors.base import new_evidence
from dw.watchdog.detectors.common import DCFG, allow_evidence, ratio, scratch


class VolumeDetector:
    name = "d7_volume"

    def __init__(self) -> None:
        self.cfg = DCFG["d7"]

    def observe(self, view: DetectorView, acct: AccountState) -> list[Evidence]:
        st = scratch(acct, self.name)
        if not st:
            st.update(n=0, hours=Counter(), windows={}, n_user=0, users=set())
        st["n"] += 1
        hour = math.floor(view.sim_time / 3600.0)
        st["hours"][hour] += 1
        window = math.floor(view.sim_time / self.cfg["concurrency_window_s"])
        st["windows"].setdefault(window, set()).add(view.session_id)
        if view.user is not None:
            st["n_user"] += 1
            st["users"].add(view.user)
        if st["hours"][hour] == self.cfg["hour_evidence_requests"] and allow_evidence(st, "hour"):
            return [new_evidence(self.name, view, weight=0.5, summary=(
                f"The account sent {st['hours'][hour]} requests in one simulated hour "
                f"with {len(st['windows'][window])} concurrent sessions."))]
        return []

    def features(self, acct: AccountState) -> dict[str, float]:
        st = acct.scratch.get(self.name) or {}
        hours: Counter[int] = st.get("hours", Counter())
        active = [h for h, c in hours.items() if c >= self.cfg["active_hour_min_requests"]]
        days = {h // 24 for h in active}
        conc = [len(s) for s in st.get("windows", {}).values()]
        n = st.get("n", 0)
        return {
            "d7_req_per_active_hour": ratio(n, len(active)),
            "d7_max_concurrent_sessions": float(max(conc, default=0)),
            "d7_mean_concurrent_sessions": ratio(sum(conc), len(conc)),
            "d7_active_hours_per_day": ratio(len(active), len(days)),
            "d7_user_field_frac": ratio(st.get("n_user", 0), n),
            "d7_distinct_users": float(len(st.get("users", ()))),
        }
