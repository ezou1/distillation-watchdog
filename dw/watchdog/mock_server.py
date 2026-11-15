"""Mock watchdog for dashboard work (docs/API.md section 4).

The mock replays the fixture traffic in tests/fixtures/scenario_small.jsonl.
It computes simple features from the traffic, a heuristic score, evidence, campaigns, and a graph.
It does not read the persona labels. The scores are plausible, not correct.
It serves static/ the same way as the real watchdog: GET / returns static/dashboard.html.

Run: python -m dw.watchdog.mock_server [--port 8010] [--prefill 0.2] [--batch 25] [--tick 1.0]
"""
from __future__ import annotations

import argparse
import asyncio
import io
import json
import math
import random
from collections import defaultdict
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Any, Optional

from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import FileResponse, JSONResponse, Response, StreamingResponse
from fastapi.staticfiles import StaticFiles

from dw.common.config import CFG, ROOT, salt
from dw.common.hashing import pseudonym, text_hash
from dw.common.react_app import DEFAULT_DIST, mount_react_app

FIXTURE = ROOT / "tests" / "fixtures" / "scenario_small.jsonl"
STATIC = ROOT / "static"
APP_DIST = DEFAULT_DIST  # the React build, served at /app
REPORTS = ROOT / "reports"
YOU_ID = "acct-agentchat"

# Heuristic model: logit = BIAS + sum(w * (x - population mean)).
WEIGHTS = {
    "d1_dup_rate": 6.0,
    "d1_max_dup": 0.35,
    "d2_cross_account_dup_rate": 4.0,
    "d3_fs_contradiction_rate": 1.0,
    "d4_temperature": 3.0,
    "d4_max_tokens_frac": 2.5,
    "d7_log_req_per_active_hour": 0.6,
    "c_shared_task_frac": 2.0,
}
BIAS = -1.2
T_REVIEW, T_BLOCK, MIN_REQUESTS = 0.5, 0.9, 5
EDGE_MIN_WEIGHT = 0.8
CAMPAIGN_BLOCK = 1.4


def sigmoid(x: float) -> float:
    return 1.0 / (1.0 + math.exp(-max(-30.0, min(30.0, x))))


def first_user_text(ev: dict) -> str:
    for m in ev["messages"]:
        if m["role"] == "user":
            return m["text"]
    return ""


def fs_contradiction(ev: dict) -> Optional[tuple[int, str]]:
    """Return (message index, path) when two reads of one path give different content."""
    seen: dict[str, str] = {}
    msgs = ev["messages"]
    for i, m in enumerate(msgs):
        if m["role"] == "tool_call" and m["tool"] == "read" and m.get("args"):
            path = str(m["args"].get("path", ""))
            if i + 1 < len(msgs) and msgs[i + 1]["role"] == "tool_result":
                text = msgs[i + 1]["text"]
                if path in seen and seen[path] != text:
                    return i + 1, path
                seen[path] = text
    return None


class MockWatchdog:
    """In-memory state of the mock. One tick replays one batch of fixture events."""

    def __init__(self, fixture: Path = FIXTURE, batch: int = 25, tick_s: float = 1.0, seed: int = 7):
        self.batch, self.tick_s = batch, tick_s
        self.rng = random.Random(seed)
        self.events = [json.loads(line) for line in fixture.read_text(encoding="utf-8").splitlines() if line.strip()]
        self.events.sort(key=lambda e: e["sim_time"])
        self._add_you_account()
        self.req_by_id = {e["request_id"]: e for e in self.events}
        self.salt = salt()
        self._contra_cache: dict[str, bool] = {}
        self.evidence_all = self._build_evidence()
        self.reset()

    # ---------- setup ----------
    def _add_you_account(self) -> None:
        """Add the agent-chat account: a few slow, normal requests copied from the first account."""
        src = self.events[0]["account_id"]
        own = [e for e in self.events if e["account_id"] == src][:12]
        t0 = self.events[0]["sim_time"]
        for i, e in enumerate(own):
            c = json.loads(json.dumps(e))
            c["request_id"] = f"req-you-{i:04d}"
            c["account_id"] = YOU_ID
            c["sim_time"] = t0 + 600.0 * (i + 1)
            c["tools_schema_id"] = "standard_agent"
            c["user"] = None
            c["key"] = f"you-key-{i}"
            c["prefix_hashes"] = [c["key"]]
            c["dup_count"] = c["global_dup_count"] = 1
            c["messages"] = [dict(x) for x in c["messages"]]
            for x in c["messages"]:
                if x["role"] == "user":
                    x["text"] = "Write a small function slugify(title) in utils.py and a test for it."
                    break
            self.events.append(c)
        self.events.sort(key=lambda e: e["sim_time"])

    def _build_evidence(self) -> list[dict]:
        out: list[dict] = []
        by_acct: dict[str, list[dict]] = defaultdict(list)
        for e in self.events:
            by_acct[e["account_id"]].append(e)

        def add(det: str, ev: dict, idx: Optional[int], summary: str, weight: float) -> None:
            out.append({"id": f"ev-{len(out) + 1:07d}", "detector": det, "account_id": ev["account_id"],
                        "request_ids": [ev["request_id"]], "message_index": idx, "summary": summary,
                        "weight": weight, "sim_time": ev["sim_time"]})

        for acct, evs in by_acct.items():
            reported_dup = 0
            for e in evs:
                last = len(e["messages"]) - 1
                if e["dup_count"] >= 3 and e["dup_count"] > reported_dup:
                    reported_dup = e["dup_count"]
                    add("d1_redundancy", e, last,
                        f"The same context was sent {e['dup_count']} times at temperature {e['temperature']:.1f}.", 1.0)
                if e["global_dup_count"] > e["dup_count"] and e["global_dup_count"] >= 3:
                    add("d2_cross_account", e, last,
                        f"The same context came {e['global_dup_count']} times from more than one account.", 0.8)
                c = fs_contradiction(e) if e["temperature"] >= 0.9 and e["dup_count"] > 1 else None
                if c is not None:
                    add("d3_fs_contradiction", e, c[0],
                        f"Shadow file-system contradiction: two reads of {c[1]} gave different content.", 0.9)
            hi = [e for e in evs if (e["max_tokens"] or 0) >= 32768 and e["temperature"] >= 1.0]
            if len(hi) >= 10:
                add("d4_params", hi[0], 0,
                    f"{len(hi)} requests ask for max_tokens 32768 at temperature 1.0.", 0.6)
        # Keep the list readable: at most 8 items per detector per account.
        count: dict[tuple[str, str], int] = defaultdict(int)
        kept = []
        for ev in out:
            k = (ev["account_id"], ev["detector"])
            count[k] += 1
            if count[k] <= 8:
                kept.append(ev)
        for i, ev in enumerate(kept):
            ev["id"] = f"ev-{i + 1:07d}"
        return kept

    def reset(self) -> None:
        self.cursor = 0
        self.served = 0
        self.blocked = 0
        self.rate_history: list[dict] = []
        self.last_rps = 0.0
        self.sim_time = self.events[0]["sim_time"]
        self.seen: dict[str, list[dict]] = defaultdict(list)
        self.states: dict[str, str] = {}
        self.history: dict[str, list[list[float]]] = defaultdict(list)
        self.accounts: dict[str, dict] = {}
        self.campaigns: dict[str, dict] = {}
        self.campaign_of: dict[str, str] = {}
        self.edges: list[dict] = []
        self.pending: list[tuple[str, dict]] = []
        self.blocklist_version = 0
        self.casefile_cache: dict[str, dict] = {}
        self.ingested = 0
        self._task_epoch = -1

    # ---------- replay ----------
    def advance(self, n: int) -> None:
        """Replay n fixture events (wrap at the end) and update all state."""
        served = blocked = 0
        for _ in range(n):
            if self.cursor >= len(self.events):
                # The replay is complete. Keep traffic moving with the last account mix.
                e = self.rng.choice(self.events)
            else:
                e = self.events[self.cursor]
                self.cursor += 1
                self.seen[e["account_id"]].append(e)
                self.sim_time = e["sim_time"]
            if self.states.get(e["account_id"]) == "block":
                blocked += 1
            else:
                served += 1
        if self.cursor >= len(self.events):
            self.sim_time += 60.0
        self.served += served
        self.blocked += blocked
        self.last_rps = (served + blocked) / max(self.tick_s, 1e-6)
        self.rate_history.append({"t": self.sim_time, "served": served, "blocked": blocked})
        self.rate_history = self.rate_history[-300:]
        self.recompute()

    def recompute(self) -> None:
        feats = {a: self._features(evs) for a, evs in self.seen.items() if evs}
        if not feats:
            return
        names = list(WEIGHTS)
        mean = {k: sum(f[k] for f in feats.values()) / len(feats) for k in names}
        for a, f in feats.items():
            contrib = {k: WEIGHTS[k] * (f[k] - mean[k]) for k in names}
            score = sigmoid(BIAS + sum(contrib.values()))
            n = len(self.seen[a])
            declared = self.seen[a][-1].get("declared_use")
            self.accounts[a] = {"id": a, "pseudonym": pseudonym(a, self.salt), "you": a == YOU_ID,
                                "score": round(score, 3), "requests": n, "declared_use": declared,
                                "features": f, "contributions": contrib}
        # Percentiles against the population.
        for k in names:
            vals = sorted(acc["features"][k] for acc in self.accounts.values())
            for acc in self.accounts.values():
                below = sum(1 for v in vals if v < acc["features"][k])
                equal = sum(1 for v in vals if v == acc["features"][k])
                acc.setdefault("pct", {})[k] = round(100.0 * (below + 0.5 * equal) / len(vals), 1)
        self._campaigns()
        for a, acc in self.accounts.items():
            state = self._state(acc)
            old = self.states.get(a)
            acc["state"] = state
            acc["campaign_id"] = self.campaign_of.get(a)
            self.states[a] = state
            hist = self.history[a]
            if not hist or abs(hist[-1][1] - acc["score"]) > 1e-3 or self.sim_time - hist[-1][0] > 300:
                hist.append([self.sim_time, acc["score"]])
                del hist[:-60]
            if state != old and state in ("review", "block"):
                reason = self._reasons(acc)[0] if self._reasons(acc) else ""
                self.pending.append(("block" if state == "block" else "flag",
                                     {"account_id": a, "pseudonym": acc["pseudonym"], "state": state,
                                      "score": acc["score"], "reason": reason}))
                if state == "block":
                    self.blocklist_version += 1

    def _features(self, evs: list[dict]) -> dict[str, float]:
        n = len(evs)
        span_h = max((evs[-1]["sim_time"] - evs[0]["sim_time"]) / 3600.0, 0.25)
        tasks = [text_hash(first_user_text(e)) for e in evs]
        return {
            "d1_dup_rate": sum(1 for e in evs if e["dup_count"] > 1) / n,
            "d1_max_dup": float(max(e["dup_count"] for e in evs)),
            "d2_cross_account_dup_rate": sum(1 for e in evs if e["global_dup_count"] > e["dup_count"]) / n,
            "d3_fs_contradiction_rate": sum(1 for e in evs if self._contra(e) and e["temperature"] >= 0.9) / n,
            "d4_temperature": sum(e["temperature"] for e in evs) / n,
            "d4_max_tokens_frac": sum((e["max_tokens"] or 0) for e in evs) / n / 32768.0,
            "d7_log_req_per_active_hour": math.log1p(n / span_h),
            "c_shared_task_frac": self._shared_frac(tasks, evs[0]["account_id"]),
        }

    def _contra(self, e: dict) -> bool:
        rid = e["request_id"]
        if rid not in self._contra_cache:
            self._contra_cache[rid] = fs_contradiction(e) is not None
        return self._contra_cache[rid]

    def _shared_frac(self, tasks: list[str], acct: str) -> float:
        if self._task_epoch != self.cursor:
            owners: dict[str, set] = defaultdict(set)
            for a, evs in self.seen.items():
                for e in evs:
                    owners[text_hash(first_user_text(e))].add(a)
            self._task_owners, self._task_epoch = owners, self.cursor
        uniq = set(tasks)
        return sum(1 for t in uniq if len(self._task_owners[t]) > 1) / max(len(uniq), 1)

    def _state(self, acc: dict) -> str:
        if acc["requests"] < MIN_REQUESTS:
            return "insufficient"
        if acc["declared_use"]:
            return "disclosed"
        cid = self.campaign_of.get(acc["id"])
        if cid and self.campaigns[cid]["state"] == "block":
            return "block"
        if acc["score"] >= T_BLOCK:
            return "block"
        if acc["score"] >= T_REVIEW:
            return "review"
        return "normal"

    def _reasons(self, acc: dict) -> list[str]:
        top = sorted(acc["contributions"].items(), key=lambda kv: -kv[1])[:3]
        return [f"{k} {v:+.1f}" for k, v in top if v > 0]

    def _campaigns(self) -> None:
        """Link accounts by shared tasks and shared contexts. Components of 3+ become campaigns."""
        tasks: dict[str, set] = {}
        keys: dict[str, set] = {}
        mins: dict[str, list[int]] = {}
        for a, evs in self.seen.items():
            tasks[a] = {text_hash(first_user_text(e)) for e in evs}
            keys[a] = {e["key"] for e in evs}
            mins[a] = [int(e["sim_time"] // 60) for e in evs]
        cand = [a for a, acc in self.accounts.items()
                if acc["requests"] >= MIN_REQUESTS and not acc["declared_use"]]
        edges = []
        for i, a in enumerate(cand):
            for b in cand[i + 1:]:
                st = len(tasks[a] & tasks[b])
                sk = len(keys[a] & keys[b])
                ma, mb = set(mins[a]), set(mins[b])
                sync = len(ma & mb) / max(min(len(ma), len(mb)), 1)
                types = {}
                if st:
                    types["shared_task"] = round(min(1.0, st / 5.0), 2)
                if sk:
                    types["shared_context"] = round(min(1.0, sk / 3.0), 2)
                if sync > 0.3:
                    types["sync"] = round(sync, 2)
                w = round(sum(types.values()) * 1.2, 2)
                both_hot = min(self.accounts[a]["score"], self.accounts[b]["score"]) >= T_REVIEW
                if w >= EDGE_MIN_WEIGHT and (st >= 2 or (both_hot and sync > 0.5)):
                    edges.append({"source": a, "target": b, "weight": w, "types": types,
                                  "evidence": f"{st} shared tasks; {sk} shared contexts; per-minute overlap {sync:.2f}"})
        self.edges = edges
        # Connected components.
        parent = {a: a for a in cand}

        def find(x: str) -> str:
            while parent[x] != x:
                parent[x] = parent[parent[x]]
                x = parent[x]
            return x
        for e in edges:
            parent[find(e["source"])] = find(e["target"])
        comps: dict[str, list[str]] = defaultdict(list)
        for a in cand:
            comps[find(a)].append(a)
        groups = sorted((sorted(m) for m in comps.values() if len(m) >= 3), key=lambda m: -len(m))
        # Keep campaign ids stable: reuse the id of the old campaign with the largest overlap.
        old = dict(self.campaigns)
        new: dict[str, dict] = {}
        self.campaign_of = {}
        next_id = max([int(c.split("-")[1]) for c in old] + [0]) + 1
        for members in groups:
            best = max(old.items(), key=lambda kv: len(set(kv[1]["member_ids"]) & set(members)), default=None)
            if best and set(best[1]["member_ids"]) & set(members) and best[0] not in new:
                cid = best[0]
            else:
                cid, next_id = f"cmp-{next_id:04d}", next_id + 1
            m_edges = [e for e in edges if e["source"] in members and e["target"] in members]
            mean_score = sum(self.accounts[a]["score"] for a in members) / len(members)
            mean_w = sum(e["weight"] for e in m_edges) / max(len(m_edges), 1)
            score = round(mean_score * 2.0 + mean_w * 0.5, 2)
            type_tot: dict[str, float] = defaultdict(float)
            for e in m_edges:
                for k, v in e["types"].items():
                    type_tot[k] += v
            shared = set.intersection(*[tasks[a] for a in members]) if members else set()
            c = {"id": cid, "member_ids": members, "size": len(members), "score": score,
                 "state": "block" if score >= CAMPAIGN_BLOCK else "review",
                 "mean_member_score": round(mean_score, 3), "mean_edge_weight": round(mean_w, 2),
                 "top_edge_types": [k for k, _ in sorted(type_tot.items(), key=lambda kv: -kv[1])][:3],
                 "edges": m_edges,
                 "evidence": [{"id": f"ev-c{cid[4:]}01", "detector": "campaign", "account_id": None,
                               "request_ids": [self.seen[members[0]][0]["request_id"]], "message_index": 0,
                               "summary": f"{len(members)} accounts share tasks; {len(shared)} tasks are common to all.",
                               "weight": 1.0, "sim_time": self.sim_time},
                              {"id": f"ev-c{cid[4:]}02", "detector": "campaign", "account_id": None,
                               "request_ids": [self.seen[members[-1]][0]["request_id"]], "message_index": 0,
                               "summary": f"{len(m_edges)} links with mean weight {mean_w:.2f}; top link types: "
                                          + ", ".join(k for k, _ in sorted(type_tot.items(), key=lambda kv: -kv[1])[:2]) + ".",
                               "weight": 0.8, "sim_time": self.sim_time}]}
            if cid not in old or old[cid]["size"] != c["size"] or old[cid]["state"] != c["state"]:
                self.pending.append(("campaign", {"id": cid, "size": c["size"], "score": c["score"], "state": c["state"]}))
            new[cid] = c
            for a in members:
                self.campaign_of[a] = cid
        self.campaigns = new

    # ---------- API views ----------
    def summary(self) -> dict:
        by_state = {s: 0 for s in ("insufficient", "normal", "review", "block", "disclosed")}
        for acc in self.accounts.values():
            by_state[acc["state"]] += 1
        return {"sim_time": self.sim_time, "rps": round(self.last_rps, 1), "served": self.served,
                "blocked": self.blocked, "accounts": by_state, "campaigns": len(self.campaigns),
                "queue_depth": 0, "dropped": 0, "rate_history": self.rate_history[-120:]}

    def row(self, acc: dict) -> dict:
        return {"id": acc["id"], "pseudonym": acc["pseudonym"], "you": acc["you"], "score": acc["score"],
                "state": acc["state"], "requests": acc["requests"], "campaign_id": acc["campaign_id"],
                "reasons": self._reasons(acc), "history": self.history[acc["id"]]}

    def evidence_for(self, acct: str) -> list[dict]:
        return [e for e in self.evidence_all if e["account_id"] == acct and e["sim_time"] <= self.sim_time]

    def account(self, acct: str) -> dict:
        acc = self.accounts.get(acct)
        if acc is None:
            raise KeyError(acct)
        r = self.row(acc)
        del r["reasons"]
        r["declared_use"] = acc["declared_use"]
        r["features"] = [{"name": k, "value": round(v, 4), "percentile": acc["pct"][k]} for k, v in acc["features"].items()]
        r["contributions"] = sorted(({"name": k, "value": round(v, 3)} for k, v in acc["contributions"].items()),
                                    key=lambda c: -abs(c["value"]))
        r["evidence"] = self.evidence_for(acct)
        return r

    def find_evidence(self, ev_id: str) -> dict:
        for e in self.evidence_all:
            if e["id"] == ev_id:
                return e
        for c in self.campaigns.values():
            for e in c["evidence"]:
                if e["id"] == ev_id:
                    return e
        raise KeyError(ev_id)

    def evidence_detail(self, ev_id: str) -> dict:
        ev = self.find_evidence(ev_id)
        req = self.req_by_id[ev["request_ids"][0]]
        msgs = [{"index": i, **m} for i, m in enumerate(req["messages"])]
        idx = ev.get("message_index")
        msg = msgs[idx] if idx is not None and 0 <= idx < len(msgs) else None
        return {"evidence": {k: ev[k] for k in ("id", "detector", "summary", "request_ids", "message_index")},
                "request": {"request_id": req["request_id"], "account_id": req["account_id"],
                            "pseudonym": pseudonym(req["account_id"], self.salt), "sim_time": req["sim_time"],
                            "temperature": req["temperature"], "n": req["n"], "max_tokens": req["max_tokens"],
                            "tools_schema_id": req["tools_schema_id"], "messages": msgs},
                "message": msg}

    def campaign_list(self) -> list[dict]:
        keys = ("id", "size", "score", "state", "mean_member_score", "mean_edge_weight", "top_edge_types")
        return [{k: c[k] for k in keys} for c in sorted(self.campaigns.values(), key=lambda c: -c["score"])]

    def campaign(self, cid: str) -> dict:
        c = self.campaigns[cid]
        members = [{"id": a, "pseudonym": self.accounts[a]["pseudonym"], "score": self.accounts[a]["score"],
                    "state": self.accounts[a]["state"]} for a in c["member_ids"]]
        return {"id": cid, "size": c["size"], "score": c["score"], "state": c["state"], "members": members,
                "edges": c["edges"], "evidence": c["evidence"]}

    def graph(self) -> dict:
        ids = {e["source"] for e in self.edges} | {e["target"] for e in self.edges}
        ids |= {a for a, acc in self.accounts.items() if acc["state"] in ("review", "block")}
        nodes = [{"id": a, "pseudonym": self.accounts[a]["pseudonym"], "score": self.accounts[a]["score"],
                  "state": self.accounts[a]["state"], "campaign_id": self.accounts[a]["campaign_id"],
                  "you": self.accounts[a]["you"]} for a in sorted(ids)]
        edges = [{k: e[k] for k in ("source", "target", "weight", "types")} for e in self.edges]
        return {"nodes": nodes, "edges": edges}

    def casefile(self, kind: str, cid: str) -> dict:
        key = f"{kind}:{cid}"
        if key in self.casefile_cache:
            return {**self.casefile_cache[key], "cached": True}
        if kind == "campaign":
            c = self.campaign(cid)
            ev = c["evidence"]
            names = ", ".join(m["pseudonym"] for m in c["members"][:6])
            text = (f"## Summary\nThe campaign {cid} has {c['size']} accounts and the score {c['score']} [{ev[0]['id']}].\n\n"
                    f"## Findings\n- The accounts {names} send the same tasks [{ev[0]['id']}].\n"
                    f"- The links between the accounts are strong [{ev[1]['id']}].\n\n"
                    f"## Recommendation\nBlock all member accounts. Keep the evidence for review.\n")
        else:
            acc = self.account(cid)
            ev = acc["evidence"]
            if not ev:
                out = {"id": cid, "status": "fallback", "cached": False, "text": "", "cited": [], "evidence": []}
                self.casefile_cache[key] = out
                return out
            lines = "\n".join(f"- {e['summary']} [{e['id']}]" for e in ev[:6])
            text = (f"## Summary\nThe account {acc['pseudonym']} has the score {acc['score']:.2f} and the state "
                    f"{acc['state']} [{ev[0]['id']}].\n\n## Findings\n{lines}\n\n"
                    f"## Recommendation\nAn investigator must examine the cited requests.\n")
        cited = [e["id"] for e in ev if f"[{e['id']}]" in text]
        out = {"id": cid, "status": "ok", "cached": False, "text": text, "cited": cited,
               "evidence": [{"id": e["id"], "summary": e["summary"]} for e in ev]}
        self.casefile_cache[key] = out
        return out

    def blocklist(self) -> dict:
        accts = {a: {"reason": f"score {acc['score']:.2f}: " + ", ".join(self._reasons(acc)[:2]), "case_id": a}
                 for a, acc in self.accounts.items() if acc["state"] == "block"}
        return {"version": self.blocklist_version, "accounts": accts}

    def take_events(self) -> list[tuple[str, dict]]:
        out, self.pending = self.pending, []
        return out


# ---------- evaluation payload ----------
def _mock_png(title: str, kind: str) -> bytes:
    import matplotlib
    matplotlib.use("Agg")
    import matplotlib.pyplot as plt
    fig, ax = plt.subplots(figsize=(6, 4), dpi=80)
    if kind == "roc":
        xs = [i / 50 for i in range(51)]
        ax.plot(xs, [x ** 0.15 for x in xs], label="account level")
        ax.plot(xs, [x ** 0.5 for x in xs], label="per request")
        ax.plot([0, 1], [0, 1], "k--", lw=0.8)
        ax.set_xlabel("false positive rate")
        ax.set_ylabel("true positive rate")
        ax.legend()
    else:
        ax.bar(["per request\n(false blocks/day)", "per account\n(false blocks)"], [4200, 1], color=["#c62828", "#2e7d32"])
        ax.set_yscale("log")
    ax.set_title(title + " (MOCK)")
    fig.tight_layout()
    buf = io.BytesIO()
    fig.savefig(buf, format="png")
    plt.close(fig)
    return buf.getvalue()


MOCK_IMAGES = {"mock_roc_pr.png": ("ROC", "roc"), "mock_per_request_vs_account.png": ("Per request vs per account", "bar")}


def eval_payload() -> dict:
    def load(name: str) -> Optional[dict]:
        p = REPORTS / name
        try:
            return json.loads(p.read_text(encoding="utf-8")) if p.exists() else None
        except (OSError, ValueError):
            return None
    real = {k: load(f"{k}.json") for k in ("metrics", "controls", "compute", "load")}
    pngs = sorted(p.name for p in REPORTS.glob("*.png")) if REPORTS.exists() else []
    mock = _mock_eval()
    out = {k: real[k] if real[k] is not None else mock[k] for k in real}
    out["images"] = [f"/reports/{p}" for p in pngs] if pngs else mock["images"]
    return out


def _mock_eval() -> dict:
    return {
        "metrics": {
            "note": "MOCK numbers from the mock watchdog",
            "account_level": {"roc_auc": 0.991, "pr_auc": 0.974, "threshold_review": 0.5, "threshold_block": 0.9},
            "recall_per_attack_persona": {"hydra": 1.0, "stealth": 0.33, "fake_env": 1.0},
            "false_positives_per_normal_persona": {"solo_dev": {"accounts": 120, "flagged": 1, "blocked": 0},
                                                   "saas_app": {"accounts": 10, "flagged": 2, "blocked": 0},
                                                   "benchmark_runner": {"accounts": 5, "flagged": 0, "blocked": 0}},
            "campaign_effect": {"stealth_recall_account_level": 0.33, "stealth_recall_after_campaign": 1.0},
            "time_to_block": {"median_requests": 41, "median_sim_minutes": 12.5, "leaked_trajectories_per_campaign": [3, 7]},
            "per_request_vs_per_account": {"per_request_fpr_at_best_threshold": 0.00042,
                                           "requests_per_day": 10000000,
                                           "falsely_blocked_requests_per_day": 4200,
                                           "falsely_blocked_normal_accounts": 1},
            "keyword_baseline_vs_classifier_bank_b": [{"method": "keyword", "recall": 0.41, "fpr": 0.08},
                                                      {"method": "classifier", "recall": 0.88, "fpr": 0.02}],
        },
        "controls": {"group_split_no_overlap": True, "scaffold_stripped": True,
                     "shuffled_label_auc": 0.51, "source_only_auc": 0.55},
        "compute": {"training_time_s": 84.2, "evaluation_time_s": 31.0, "llm_cost_usd": 1.73},
        "load": {"throughput_rps": 2150, "p99_ingest_ms": 4.1},
        "images": [f"/reports/{name}" for name in MOCK_IMAGES],
    }


# ---------- app ----------
def create_app(mock: Optional[MockWatchdog] = None, prefill: float = 0.0, run_ticker: bool = True) -> FastAPI:
    m = mock or MockWatchdog()
    if prefill > 0:
        target = int(len(m.events) * min(prefill, 1.0))
        while m.cursor < target:
            m.advance(min(m.batch, target - m.cursor))
    subscribers: set[asyncio.Queue] = set()

    async def ticker() -> None:
        while True:
            await asyncio.sleep(m.tick_s)
            m.advance(m.batch)
            items = [("summary", m.summary())] + m.take_events()
            for q in list(subscribers):
                for it in items:
                    if q.qsize() < 500:
                        q.put_nowait(it)

    @asynccontextmanager
    async def lifespan(_: FastAPI):
        task = asyncio.create_task(ticker()) if run_ticker else None
        yield
        if task:
            task.cancel()

    app = FastAPI(title="Mock watchdog", lifespan=lifespan)
    app.state.mock = m

    @app.get("/")
    def index() -> FileResponse:
        return FileResponse(STATIC / "dashboard.html")

    app.mount("/static", StaticFiles(directory=STATIC), name="static")
    mount_react_app(app, APP_DIST)

    @app.get("/reports/{name}")
    def report_file(name: str) -> Response:
        p = REPORTS / name
        if name.endswith(".png") and "/" not in name and p.is_file():
            return FileResponse(p)
        if name in MOCK_IMAGES:
            return Response(_mock_png(*MOCK_IMAGES[name]), media_type="image/png")
        raise HTTPException(404, "no such report file")

    @app.post("/ingest")
    async def ingest(req: Request) -> dict:
        body = await req.json()
        n = len(body.get("events", []))
        m.ingested += n
        return {"accepted": n, "dropped": 0}

    @app.get("/blocklist")
    def blocklist() -> dict:
        return m.blocklist()

    @app.get("/api/summary")
    def summary() -> dict:
        return m.summary()

    @app.get("/api/accounts")
    def accounts(state: str = "", sort: str = "score", limit: int = 50) -> dict:
        rows = [m.row(a) for a in m.accounts.values() if not state or a["state"] == state]
        if sort == "requests":
            rows.sort(key=lambda r: -r["requests"])
        elif sort == "last_seen":
            rows.sort(key=lambda r: -(r["history"][-1][0] if r["history"] else 0))
        else:
            rows.sort(key=lambda r: -r["score"])
        return {"accounts": rows[:max(0, limit)]}

    @app.get("/api/accounts/{acct}")
    def account(acct: str) -> dict:
        try:
            return m.account(acct)
        except KeyError:
            raise HTTPException(404, "no such account")

    @app.get("/api/evidence/{ev_id}")
    def evidence(ev_id: str) -> dict:
        try:
            return m.evidence_detail(ev_id)
        except KeyError:
            raise HTTPException(404, "no such evidence")

    @app.get("/api/campaigns")
    def campaigns() -> dict:
        return {"campaigns": m.campaign_list()}

    @app.get("/api/campaigns/{cid}")
    def campaign(cid: str) -> dict:
        if cid not in m.campaigns:
            raise HTTPException(404, "no such campaign")
        return m.campaign(cid)

    @app.post("/api/campaigns/{cid}/casefile")
    def campaign_casefile(cid: str) -> dict:
        if cid not in m.campaigns:
            raise HTTPException(404, "no such campaign")
        return m.casefile("campaign", cid)

    @app.post("/api/accounts/{acct}/casefile")
    def account_casefile(acct: str) -> dict:
        if acct not in m.accounts:
            raise HTTPException(404, "no such account")
        return m.casefile("account", acct)

    @app.get("/api/graph")
    def graph() -> dict:
        return m.graph()

    @app.get("/api/stream")
    async def stream(req: Request) -> StreamingResponse:
        q: asyncio.Queue = asyncio.Queue()
        q.put_nowait(("summary", m.summary()))
        subscribers.add(q)

        async def gen():
            try:
                while True:
                    try:
                        kind, data = await asyncio.wait_for(q.get(), timeout=15.0)
                        yield f"event: {kind}\ndata: {json.dumps(data)}\n\n"
                    except asyncio.TimeoutError:
                        yield ": keep-alive\n\n"
                    if await req.is_disconnected():
                        break
            finally:
                subscribers.discard(q)
        return StreamingResponse(gen(), media_type="text/event-stream",
                                 headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"})

    @app.get("/api/eval")
    def eval_() -> JSONResponse:
        return JSONResponse(eval_payload())

    @app.post("/admin/reset")
    def reset() -> dict:
        m.reset()
        return {"ok": True}

    return app


def main() -> None:
    ap = argparse.ArgumentParser(description="Mock watchdog for the dashboard.")
    ap.add_argument("--port", type=int, default=CFG["ports"]["watchdog"])
    ap.add_argument("--host", default="127.0.0.1")
    ap.add_argument("--prefill", type=float, default=0.2, help="fraction of the fixture to replay at start")
    ap.add_argument("--batch", type=int, default=25, help="fixture events per tick")
    ap.add_argument("--tick", type=float, default=1.0, help="seconds per tick")
    a = ap.parse_args()
    import uvicorn
    app = create_app(MockWatchdog(batch=a.batch, tick_s=a.tick), prefill=a.prefill)
    print(f"Mock watchdog: http://{a.host}:{a.port}/")
    uvicorn.run(app, host=a.host, port=a.port, log_level="warning")


if __name__ == "__main__":
    main()
