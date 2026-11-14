"""Split canonical sessions into train, validation and holdout by group, and write the manifest.

Groups go to splits by session count. Each repository and task then belongs to the split that uses it most,
and sessions of other splits that use it are dropped, so no group, repository or task is in two splits.
The cross-harness slice is written separately and keeps only sessions with no repository or task in train or validation.
"""
from __future__ import annotations

import hashlib
import json
import random
from collections import Counter, defaultdict
from pathlib import Path

from dw.common.config import CFG, path
from dw.common.schema import Session
from dw.data.normalize import read_jsonl, write_jsonl

SPLITS = ("train", "val", "holdout")


def assign_groups(sessions: list[Session], seed: int) -> dict[str, str]:
    """Map group ID to split. Per source, the largest groups go first to the split that is most below its ratio."""
    out: dict[str, str] = {}
    ratios = {k: CFG["split"][k] for k in SPLITS}
    by_source: dict[str, Counter[str]] = defaultdict(Counter)
    for s in sessions:
        by_source[s.source][s.group_id] += 1
    for source in sorted(by_source):
        sizes = by_source[source]
        groups = sorted(sizes)
        random.Random(f"{seed}:{source}").shuffle(groups)
        groups.sort(key=lambda g: -sizes[g])
        total, filled = sum(sizes.values()), Counter[str]()
        for g in groups:
            split = max(SPLITS, key=lambda k: ratios[k] * total - filled[k])
            out[g] = split
            filled[split] += sizes[g]
    return out


def _owners(sessions: list[Session], split_of: dict[str, str]) -> dict[str, str]:
    """Map each repository key and task key to the split with the most sessions that use it."""
    use: dict[str, Counter[str]] = defaultdict(Counter)
    for s in sessions:
        for key in ("r:" + s.repo_key, "t:" + (s.task_key or "")):
            use[key][split_of[s.group_id]] += 1
    return {k: max(SPLITS, key=lambda sp: c[sp]) for k, c in use.items()}


def assign(sessions: list[Session], seed: int) -> dict[str, list[Session]]:
    """Return the sessions of each split. Sessions that would share a repository or task are dropped."""
    split_of = assign_groups(sessions, seed)
    owner = _owners(sessions, split_of)
    out: dict[str, list[Session]] = {k: [] for k in SPLITS}
    for s in sessions:
        split = split_of[s.group_id]
        if owner["r:" + s.repo_key] == split and (not s.task_key or owner["t:" + s.task_key] == split):
            out[split].append(s)
    return out


def xharness_filter(xs: list[Session], used: list[Session]) -> list[Session]:
    """Keep cross-harness sessions with no repository or task in the given sessions."""
    repos = {s.repo_key for s in used}
    tasks = {s.task_key for s in used if s.task_key}
    return [s for s in xs if s.repo_key not in repos and s.task_key not in tasks]


def sha256_file(p: Path) -> str:
    h = hashlib.sha256()
    with open(p, "rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def main() -> None:
    canon, out_dir = path("canonical"), path("splits")
    seed = int(CFG["split"]["seed"])
    sessions = [s for name in ("swechat", "openswe") for s in read_jsonl(canon / f"{name}.jsonl")]
    parts = assign(sessions, seed)
    xs = xharness_filter(list(read_jsonl(canon / "openswe_xharness.jsonl")), parts["train"] + parts["val"])
    manifest: dict = {"seed": seed, "groups": {}, "files": {}, "counts": {},
                      "dropped": len(sessions) - sum(len(v) for v in parts.values())}
    for name, items in [*parts.items(), ("xharness", xs)]:
        items.sort(key=lambda s: s.session_id)
        f = out_dir / f"{name}.jsonl"
        write_jsonl(items, f)
        manifest["files"][name] = {"path": str(f.relative_to(path("data").parent)), "sha256": sha256_file(f)}
        manifest["counts"][name] = dict(Counter(s.source for s in items))
        if name in SPLITS:
            manifest["groups"][name] = sorted({s.group_id for s in items})
    (out_dir / "manifest.json").write_text(json.dumps(manifest, indent=1))
    print(json.dumps({"counts": manifest["counts"], "dropped": manifest["dropped"]}))


if __name__ == "__main__":
    main()
