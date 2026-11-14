"""The watchdog worker: session rebuild, detector calls, account scores, campaigns, and the block list.

The same Worker runs live (in the server) and offline (training and evaluation with DirectSink).
"""
from __future__ import annotations

import itertools
import logging
from collections import Counter, deque
from pathlib import Path
from typing import Any, Optional

from dw.common.config import CFG, path as cfg_path, salt
from dw.common.hashing import pseudonym
from dw.common.schema import AccountState, DetectorView, Evidence, RequestEvent
from dw.common.sink import Blocklist
from dw.watchdog import account_model, policy, rules
from dw.watchdog.account_model import LogitModel
from dw.watchdog.campaigns import Campaign, Profile, build_graph, find_campaigns, update_profile
from dw.watchdog.detectors.base import Detector, make_view
from dw.watchdog.store import EventStore

log = logging.getLogger("dw.watchdog")

_pseudonyms: dict[str, str] = {}


def display_name(account_id: str) -> str:
    """Pseudonym of an account for the UI (cached)."""
    name = _pseudonyms.get(account_id)
    if name is None:
        name = _pseudonyms[account_id] = pseudonym(account_id, salt())
    return name


def load_detectors(models_dir: Path) -> list[Detector]:
    """All detectors from WS-C1. Without the package, the worker runs with no detectors."""
    try:
        from dw.watchdog.detectors import build_detectors  # type: ignore[attr-defined]
    except ImportError:
        log.warning("dw.watchdog.detectors.build_detectors is not available; no detectors run")
        return []
    return list(build_detectors(models_dir))


def find_session(acct: AccountState, ev: RequestEvent) -> tuple[Optional[str], bool]:
    """Session of a request from the prefix chain (section B8.2). Returns (session_id or None, duplicate).

    The same full key is a duplicate in the same session. Else the longest prefix that is the full key
    of an earlier request continues that session.
    """
    sid = acct.keys.get(ev.key)
    if sid is not None:
        return sid, True
    for h in reversed(ev.prefix_hashes[:-1]):
        sid = acct.keys.get(h)
        if sid is not None:
            return sid, False
    return None, False


class Worker:
    """Live state of all accounts. See docs/CONTRACTS.md section 3."""

    def __init__(self, models_dir: Path | None = None, store: EventStore | None = None,
                 score: bool = True, detectors: list[Detector] | None = None,
                 record_requests: bool = False, snapshots: bool = False) -> None:
        self.models_dir = models_dir or cfg_path("models")
        self.store = store
        self.detectors: list[Detector] = load_detectors(self.models_dir) if detectors is None else detectors
        self.model: LogitModel | None = (
            account_model.load(self.models_dir / account_model.ACCOUNT_MODEL_FILE) if score else None)
        self.score = score and self.model is not None
        self.rules: Optional[dict[str, float]] = rules.load(self.models_dir) if score else None
        self.record_requests = record_requests
        # training rows: (account_id, request count, features) at the account.snapshot_requests counts
        self.snapshot_at = set(CFG["account"]["snapshot_requests"]) if snapshots else set()
        self._elicitation = next((d for d in self.detectors if hasattr(d, "request_score")), None)
        self._campaign_numbers = itertools.count(1)
        self.reset()

    # ---- state -------------------------------------------------------------------------------

    def reset(self) -> None:
        """Clear all live state (accounts, campaigns, block list, counters)."""
        self.accounts: dict[str, AccountState] = {}
        self.profiles: dict[str, Profile] = {}
        self.campaigns: dict[str, Campaign] = {}
        self.graph_nodes: list[str] = []
        self.graph_edges: list[dict] = []
        self.evidence_index: dict[str, Evidence] = {}
        self.served = 0
        self.blocked = 0
        self.sim_time = 0.0
        self.rate: dict[int, list[int]] = {}           # sim-time bucket -> [served, blocked]
        self.recent_blocks: deque[dict] = deque(maxlen=CFG["watchdog"]["recent_blocks"])
        self.request_rows: list[tuple[str, str, dict[str, float]]] = []
        self.snapshots: list[tuple[str, int, dict[str, float]]] = []
        self._blocklist: Blocklist = {}
        self.blocklist_version = 0
        if self.store is not None:
            self.store.clear_memory()

    def _account(self, ev: RequestEvent) -> AccountState:
        acct = self.accounts.get(ev.account_id)
        if acct is None:
            acct = self.accounts[ev.account_id] = AccountState(
                account_id=ev.account_id, first_seen=ev.sim_time, last_seen=ev.sim_time)
            self.profiles[ev.account_id] = Profile()
        if ev.declared_use:
            acct.declared_use, acct.quota = ev.declared_use, ev.quota
        return acct

    # ---- ingest ------------------------------------------------------------------------------

    def ingest(self, ev: RequestEvent) -> None:
        """Rebuild the session, call observe() of all detectors, and update counters."""
        acct = self._account(ev)
        self.sim_time = max(self.sim_time, ev.sim_time)
        acct.last_seen = max(acct.last_seen, ev.sim_time)
        served = ev.status == 200
        self._count(ev.sim_time, served)
        if self.store is not None:
            self.store.append(ev)
        if not served:
            acct.n_blocked += 1
            return
        acct.n_requests += 1
        view = self._view(acct, ev)
        for det in self.detectors:
            self._add_evidence(acct, det.observe(view, acct))
        update_profile(self.profiles[ev.account_id], view)
        if self.record_requests:
            self.request_rows.append((ev.request_id, ev.account_id, self.request_features(view)))
        acct.dirty = True
        if acct.n_requests in self.snapshot_at:
            self.snapshots.append((acct.account_id, acct.n_requests, dict(self._features(acct))))

    def _view(self, acct: AccountState, ev: RequestEvent) -> DetectorView:
        sid, _dup = find_session(acct, ev)
        new = sid is None
        if sid is None:
            sid = f"s{len(acct.sessions)}"
            acct.sessions[sid] = {"start": ev.sim_time, "requests": 0, "first_request_id": ev.request_id}
        stats = acct.sessions[sid]
        step = stats["requests"]
        stats["requests"] = step + 1
        stats["last"] = ev.sim_time
        acct.keys[ev.key] = sid
        return make_view(ev, sid, step, new)

    def _count(self, sim_time: float, served: bool) -> None:
        bucket = int(sim_time // CFG["watchdog"]["rate_bucket_s"])
        row = self.rate.setdefault(bucket, [0, 0])
        if served:
            self.served += 1
            row[0] += 1
        else:
            self.blocked += 1
            row[1] += 1
        if len(self.rate) > CFG["watchdog"]["rate_history_max"]:
            del self.rate[min(self.rate)]

    def _add_evidence(self, acct: AccountState, items: list[Evidence]) -> None:
        for item in items:
            acct.evidence.append(item)
            self.evidence_index[item.id] = item
        excess = len(acct.evidence) - CFG["watchdog"]["evidence_per_account_max"]
        if excess > 0:
            for old in acct.evidence[:excess]:
                self.evidence_index.pop(old.id, None)
            del acct.evidence[:excess]

    def request_features(self, view: DetectorView) -> dict[str, float]:
        """Per-request model features of one request (D4 gives the elicitation score)."""
        elicit = float(self._elicitation.request_score(view)) if self._elicitation else 0.0  # type: ignore[attr-defined]
        return account_model.request_features(view, elicit)

    # ---- scoring -----------------------------------------------------------------------------

    def _features(self, acct: AccountState) -> dict[str, float]:
        merged: dict[str, float] = {}
        for det in self.detectors:
            merged.update(det.features(acct))
            drain = getattr(det, "drain_evidence", None)     # evidence from a batch of context-model scores
            for item in drain() if drain else []:
                owner = self.accounts.get(item.account_id)
                if owner is not None:
                    self._add_evidence(owner, [item])
        acct.features = merged
        return merged

    def _score(self, acct: AccountState) -> None:
        if acct.n_requests < CFG["account"]["min_requests"]:
            return
        if self.rules is not None:
            acct.rules_level = rules.level(acct.features, self.rules, acct.declared_use)
        if not self.score or self.model is None:
            return
        acct.score = self.model.predict(acct.features)
        acct.contributions = self.model.contributions(acct.features)
        acct.score_history.append((acct.last_seen, round(acct.score, 4)))
        del acct.score_history[:-CFG["watchdog"]["history_max"]]

    def _apply_policy(self, acct: AccountState) -> Optional[dict]:
        """Set the state. Return a flag or block event if the state became more severe."""
        camp = self.campaigns.get(acct.campaign_id) if acct.campaign_id else None
        old = acct.state
        complete = self.model is None or all(n in acct.features for n in self.model.names)
        acct.state = policy.decide(acct.n_requests, acct.score, self.model.thresholds if self.model else None,
                                   camp.state if camp else None, acct.declared_use, acct.quota, acct.rules_level,
                                   model_complete=complete)
        if acct.state in ("review", "block") and policy.worst(acct.state, old) != old:
            return self.state_event(acct)
        return None

    def state_event(self, acct: AccountState) -> dict:
        kind = "block" if acct.state == "block" else "flag"
        event = {"type": kind, "account_id": acct.account_id, "pseudonym": display_name(acct.account_id),
                 "state": acct.state, "score": round(acct.score or 0.0, 4), "reason": self.main_reason(acct),
                 "sim_time": acct.last_seen}
        if kind == "block":
            self.recent_blocks.appendleft(event)
        return event

    def rescore(self) -> list[dict]:
        """Features, score, and policy for dirty accounts only. Returns flag and block events."""
        events = []
        for acct in self.accounts.values():
            if not acct.dirty:
                continue
            self._features(acct)
            self._score(acct)
            acct.dirty = False
            event = self._apply_policy(acct)
            if event:
                events.append(event)
        self._rebuild_blocklist()
        return events

    def feature_table(self) -> dict[str, dict[str, float]]:
        """account_id -> features for all accounts (training and evaluation)."""
        for acct in self.accounts.values():
            if acct.dirty or not acct.features:
                self._features(acct)
        return {aid: dict(acct.features) for aid, acct in self.accounts.items()}

    # ---- campaigns ---------------------------------------------------------------------------

    def strength(self, acct: AccountState) -> float:
        """Suspicion of one account for the campaign graph: the model score, or 1.0 / 0.5 for a rules block / review."""
        return max(acct.score or 0.0, _RULES_STRENGTH.get(acct.rules_level or "normal", 0.0))

    def candidates(self) -> list[str]:
        threshold = CFG["campaign"]["candidate_threshold"]
        return sorted(a for a, acct in self.accounts.items() if self.strength(acct) > threshold)

    def run_campaigns(self) -> list[dict]:
        """Account graph of candidates and Louvain. Returns campaign events and new block events."""
        ids = self.candidates()
        scores = {a: self.strength(self.accounts[a]) for a in ids}
        g = build_graph(ids, self.profiles, scores)
        self.graph_nodes = ids
        self.graph_edges = [{"source": a, "target": b, "weight": round(d["weight"], 3),
                             "types": {k: round(v, 3) for k, v in d["types"].items()}}
                            for a, b, d in g.edges(data=True)]
        old = self.campaigns
        self.campaigns = find_campaigns(g, self.profiles, scores, old, lambda: next(self._campaign_numbers))
        return self._apply_campaigns(old)

    def _apply_campaigns(self, old: dict[str, Campaign]) -> list[dict]:
        events: list[dict] = []
        if old:
            self.evidence_index = {k: v for k, v in self.evidence_index.items() if not k.startswith("ev-cmp-")}
        for acct in self.accounts.values():
            acct.campaign_id = None
        for camp in self.campaigns.values():
            for item in camp.evidence:
                self.evidence_index[item.id] = item
            for a in camp.members:
                self.accounts[a].campaign_id = camp.id
            prev = old.get(camp.id)
            if prev is None or prev.state != camp.state or prev.members != camp.members:
                events.append({"type": "campaign", "id": camp.id, "size": len(camp.members),
                               "score": camp.score, "state": camp.state})
        for acct in self.accounts.values():
            event = self._apply_policy(acct)
            if event:
                events.append(event)
        self._rebuild_blocklist()
        return events

    # ---- block list --------------------------------------------------------------------------

    def main_reason(self, acct: AccountState) -> str:
        camp = self.campaigns.get(acct.campaign_id) if acct.campaign_id else None
        if camp is not None and camp.state == "block":
            return f"campaign {camp.id}: {', '.join(camp.top_edge_types) or 'linked accounts'}"
        if acct.rules_level == "block" and policy.score_level(acct.score, self.model.thresholds if self.model else None) != "block":
            return f"rules: {rules.RCFG['feature']} {rules.value(acct.features):.2f} (repeated contexts)"
        top = top_reasons(acct, 2)
        return f"score {acct.score or 0.0:.2f}: {', '.join(top) if top else 'account features'}"

    def _rebuild_blocklist(self) -> None:
        new: Blocklist = {}
        for acct in self.accounts.values():
            if acct.state != "block":
                continue
            camp = self.campaigns.get(acct.campaign_id) if acct.campaign_id else None
            case_id = camp.id if camp is not None and camp.state == "block" else acct.account_id
            new[acct.account_id] = {"reason": self.main_reason(acct), "case_id": case_id}
        if new != self._blocklist:
            self._blocklist = new
            self.blocklist_version += 1

    def blocklist(self) -> Blocklist:
        """account_id -> {reason, case_id}. A blocked campaign gives every member with case_id = campaign ID."""
        return self._blocklist

    def rate_history(self) -> list[dict[str, Any]]:
        size = CFG["watchdog"]["rate_bucket_s"]
        return [{"t": float(b * size), "served": s, "blocked": k} for b, (s, k) in sorted(self.rate.items())]

    def state_counts(self) -> dict[str, int]:
        counts = Counter(a.state for a in self.accounts.values())
        return {s: counts.get(s, 0) for s in ("insufficient", "normal", "review", "block", "disclosed")}


_RULES_STRENGTH = {"block": 1.0, "review": 0.5}


def top_reasons(acct: AccountState, k: int = 3) -> list[str]:
    """Top positive contributions ("name +2.1"). Without a model: the detectors with the most evidence."""
    if acct.contributions:
        pos = sorted(((n, v) for n, v in acct.contributions.items() if v > 0), key=lambda p: -p[1])
        return [f"{n} +{v:.1f}" for n, v in pos[:k]]
    counts = Counter(e.detector for e in acct.evidence)
    return [f"{d} x{c}" for d, c in counts.most_common(k)]
