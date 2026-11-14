"""Campaign detection (section B8.5): an account graph of candidate accounts and Louvain communities.

Edges: shared tasks, shared template skeleton lines, synchronized activity, and a shared rare tool schema.
A campaign with a score above the block threshold blocks all members, also members below the account threshold.
"""
from __future__ import annotations

import itertools
import math
import re
from collections import Counter
from dataclasses import dataclass, field
from typing import Callable, Iterable, Optional

import networkx as nx
import numpy as np

from dw.common.config import CFG
from dw.common.hashing import text_hash
from dw.common.schema import DetectorView, Evidence

SkeletonFn = Callable[[str], Iterable[str]]

_MASKS: list[tuple[re.Pattern[str], str]] = [
    (re.compile(r"\"[^\"]*\"|'[^']*'|`[^`]*`"), "<STR>"),
    (re.compile(r"\S*/\S*"), "<PATH>"),
    (re.compile(r"\b\w*\d\w*\b"), "<NUM>"),
    (re.compile(r"\b[A-Za-z]+(?:[._][A-Za-z0-9]+)+\b|\b[a-z]+[A-Z]\w*\b"), "<ID>"),
]


def simple_skeleton_lines(text: str) -> list[str]:
    """Fallback if D5 is not available: masked lines of a prompt with enough words."""
    lines = []
    for line in text.splitlines():
        for pattern, repl in _MASKS:
            line = pattern.sub(repl, line)
        line = " ".join(line.lower().split())
        if len(line.split()) >= CFG["campaign"]["skeleton_min_words"]:
            lines.append(line)
    return lines


try:  # D5 owns the skeleton masking; use the same lines for the campaign graph
    from dw.watchdog.detectors.templates import skeleton_lines
except ImportError:  # pragma: no cover - only while D5 is not built
    skeleton_lines = simple_skeleton_lines  # type: ignore[assignment]


@dataclass
class Profile:
    """What the campaign graph needs about one account. The worker updates it for every request."""

    tasks: dict[str, str] = field(default_factory=dict)       # first-user-message hash -> request_id
    skeletons: dict[str, str] = field(default_factory=dict)   # skeleton line hash -> request_id
    minutes: Counter[int] = field(default_factory=Counter)    # sim-time minute -> requests
    schemas: set[str] = field(default_factory=set)


def update_profile(profile: Profile, view: DetectorView, skeleton: SkeletonFn = skeleton_lines) -> None:
    """Add one request to the profile. Tasks and skeletons come from the first user message of a new session."""
    profile.minutes[int(view.sim_time // 60)] += 1
    profile.schemas.add(view.tools_schema_id)
    if not view.new_session:
        return
    first = next((m.text for m in view.messages if m.role == "user" and m.text), None)
    if first is None:
        return
    profile.tasks.setdefault(text_hash(first), view.request_id)
    for line in skeleton(first):
        profile.skeletons.setdefault(text_hash(line), view.request_id)


@dataclass
class Campaign:
    """One detected campaign."""

    id: str
    members: list[str]
    edges: list[dict]
    score: float
    state: str
    mean_member_score: float
    mean_edge_weight: float
    top_edge_types: list[str]
    evidence: list[Evidence] = field(default_factory=list)
    template: Optional[str] = None


def _sync_matrix(ids: list[str], profiles: dict[str, Profile]) -> np.ndarray:
    """Pearson correlation of per-minute request counts in simulation time."""
    lo = min(min(profiles[a].minutes) for a in ids)
    hi = max(max(profiles[a].minutes) for a in ids)
    counts = np.zeros((len(ids), hi - lo + 1))
    for row, a in enumerate(ids):
        for minute, c in profiles[a].minutes.items():
            counts[row, minute - lo] = c
    if counts.shape[1] < 2:
        return np.zeros((len(ids), len(ids)))
    with np.errstate(invalid="ignore", divide="ignore"):
        corr = np.corrcoef(counts)
    return np.nan_to_num(corr)


def _edge(a: Profile, b: Profile, corr: float, schema_rarity: dict[str, float]) -> tuple[dict[str, float], str]:
    """Typed edge weights between two accounts and a one-sentence evidence string."""
    w = CFG["campaign"]["edge_weights"]
    types: dict[str, float] = {}
    parts: list[str] = []
    n_tasks = len(a.tasks.keys() & b.tasks.keys())
    if n_tasks:
        types["shared_task"] = w["shared_task"] * math.log1p(n_tasks)
        parts.append(f"{n_tasks} shared tasks")
    n_skel = len(a.skeletons.keys() & b.skeletons.keys())
    if n_skel:
        types["shared_skeleton"] = w["shared_skeleton"] * math.log1p(n_skel)
        parts.append(f"{n_skel} shared template lines")
    if corr > CFG["campaign"]["sync_corr_min"]:
        types["sync"] = w["sync"] * corr
        parts.append(f"per-minute correlation {corr:.2f}")
    rarity = max((schema_rarity.get(s, 0.0) for s in a.schemas & b.schemas), default=0.0)
    if rarity > 0:
        types["rare_schema"] = w["rare_schema"] * rarity
        parts.append(f"shared rare tool schema (rarity {rarity:.1f})")
    return types, "; ".join(parts)


def schema_rarity(profiles: dict[str, Profile]) -> dict[str, float]:
    """log(N_accounts / N_accounts_with_schema) over all accounts."""
    counts = Counter(s for p in profiles.values() for s in p.schemas)
    n = len(profiles)
    return {s: math.log(n / c) for s, c in counts.items()}


def build_graph(candidates: list[str], profiles: dict[str, Profile], scores: dict[str, float]) -> nx.Graph:
    """Graph of candidate accounts. Edges below campaign.min_edge_weight are left out."""
    g = nx.Graph()
    for a in candidates:
        g.add_node(a, score=scores.get(a, 0.0))
    if len(candidates) < 2:
        return g
    rarity = schema_rarity(profiles)
    corr = _sync_matrix(candidates, profiles)
    for i, j in itertools.combinations(range(len(candidates)), 2):
        a, b = candidates[i], candidates[j]
        types, text = _edge(profiles[a], profiles[b], float(corr[i, j]), rarity)
        weight = sum(types.values())
        if weight >= CFG["campaign"]["min_edge_weight"]:
            g.add_edge(a, b, weight=weight, types=types, evidence=text)
    return g


def _state(score: float) -> str:
    if score > CFG["campaign"]["block_threshold"]:
        return "block"
    if score > CFG["campaign"]["review_threshold"]:
        return "review"
    return "normal"


def _edge_rows(sub: nx.Graph) -> list[dict]:
    return [{"source": a, "target": b, "weight": round(d["weight"], 3),
             "types": {k: round(v, 3) for k, v in d["types"].items()}, "evidence": d["evidence"]}
            for a, b, d in sub.edges(data=True)]


def _top_types(edges: list[dict]) -> list[str]:
    total: Counter[str] = Counter()
    for e in edges:
        total.update(e["types"])
    return [t for t, _ in total.most_common(2)]


def _campaign_evidence(cid: str, members: list[str], profiles: dict[str, Profile], summary: str) -> list[Evidence]:
    """One summary item plus one item for each of the most shared tasks."""
    shared: Counter[str] = Counter(t for a in members for t in profiles[a].tasks)
    top = [(t, c) for t, c in shared.most_common(CFG["campaign"]["evidence_tasks"]) if c > 1]
    first_rids = [r for t, _ in top[:1] for a in members if (r := profiles[a].tasks.get(t))]
    items = [Evidence(id=f"ev-{cid}-0", detector="campaign", account_id=members[0],
                      request_ids=first_rids[:10], message_index=0 if first_rids else None, summary=summary)]
    for k, (task, count) in enumerate(top, start=1):
        rids = [r for a in members if (r := profiles[a].tasks.get(task))]
        items.append(Evidence(
            id=f"ev-{cid}-{k}", detector="campaign", account_id=members[0], request_ids=rids[:10],
            message_index=0, summary=f"The same task (hash {task[:8]}) was sent by {count} member accounts."))
    return items


def _make_campaign(cid: str, members: list[str], g: nx.Graph, profiles: dict[str, Profile],
                   scores: dict[str, float]) -> Campaign:
    sub = g.subgraph(members)
    edges = _edge_rows(sub)
    mean_score = float(np.mean([scores.get(a, 0.0) for a in members]))
    mean_w = float(np.mean([e["weight"] for e in edges])) if edges else 0.0
    score = mean_score * math.log1p(len(members)) + mean_w
    top = _top_types(edges)
    summary = (f"{len(members)} accounts form one group: mean account score {mean_score:.2f}, "
               f"{len(edges)} links with mean weight {mean_w:.2f}, main links {', '.join(top) or 'none'}.")
    return Campaign(id=cid, members=sorted(members), edges=edges, score=round(score, 3), state=_state(score),
                    mean_member_score=round(mean_score, 3), mean_edge_weight=round(mean_w, 3),
                    top_edge_types=top, evidence=_campaign_evidence(cid, sorted(members), profiles, summary))


def _match_id(members: set[str], previous: dict[str, Campaign], used: set[str]) -> Optional[str]:
    """Keep the ID of the earlier campaign with the largest member overlap (more than half)."""
    best, best_j = None, 0.5
    for cid, old in previous.items():
        if cid in used:
            continue
        old_set = set(old.members)
        jaccard = len(members & old_set) / len(members | old_set)
        if jaccard > best_j:
            best, best_j = cid, jaccard
    return best


def find_campaigns(g: nx.Graph, profiles: dict[str, Profile], scores: dict[str, float],
                   previous: dict[str, Campaign], next_number: Callable[[], int]) -> dict[str, Campaign]:
    """Louvain communities with at least campaign.min_size accounts become campaigns."""
    if g.number_of_edges() == 0:
        return {}
    communities = nx.community.louvain_communities(g, weight="weight", seed=CFG["campaign"]["louvain_seed"])
    result: dict[str, Campaign] = {}
    for members in sorted(communities, key=len, reverse=True):
        if len(members) < CFG["campaign"]["min_size"]:
            continue
        cid = _match_id(set(members), previous, set(result)) or f"cmp-{next_number():04d}"
        result[cid] = _make_campaign(cid, list(members), g, profiles, scores)
    return result
