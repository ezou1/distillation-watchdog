"""Split separation test: no shared groups, repositories, or task keys between splits, and the manifest hashes match."""
from __future__ import annotations

import json

import pytest

from dw.common.config import ROOT, path
from dw.common.schema import Message, Session
from dw.data.normalize import read_jsonl
from dw.data.split import SPLITS, assign, sha256_file, xharness_filter


def _s(i: int, group: str, repo: str, task: str | None = None, source: str = "openswe") -> Session:
    return Session(session_id=f"s{i}", source=source, group_id=group, repo_key=repo, task_key=task,  # type: ignore[arg-type]
                   messages=[Message(role="user", text="x")])


def _keys(sessions: list[Session]) -> tuple[set, set, set]:
    return ({s.group_id for s in sessions}, {s.repo_key for s in sessions},
            {s.task_key for s in sessions if s.task_key})


def _assert_disjoint(parts: dict[str, list[Session]]) -> None:
    for a in SPLITS:
        for b in SPLITS:
            if a < b:
                for ka, kb in zip(_keys(parts[a]), _keys(parts[b])):
                    assert not ka & kb, f"{a} and {b} share {sorted(ka & kb)[:3]}"


def test_assign_synthetic() -> None:
    sessions = [_s(i, f"u{i % 30}", f"r{i % 17}", source="swechat") for i in range(200)]
    sessions += [_s(1000 + i, f"o{i % 40}", f"o{i % 40}", task=f"t{i % 60}") for i in range(200)]
    parts = assign(sessions, seed=1)
    assert sum(len(v) for v in parts.values()) == len(sessions)
    _assert_disjoint(parts)


def test_xharness_filter() -> None:
    train = [_s(1, "g", "r1", "t1")]
    xs = [_s(2, "g2", "r1"), _s(3, "g3", "r9", "t1"), _s(4, "g4", "r5", "t5")]
    assert [s.session_id for s in xharness_filter(xs, train)] == ["s4"]


def test_real_manifest() -> None:
    manifest_path = path("splits") / "manifest.json"
    if not manifest_path.exists():
        pytest.skip("no real manifest")
    manifest = json.loads(manifest_path.read_text())
    parts = {sp: list(read_jsonl(path("splits") / f"{sp}.jsonl")) for sp in SPLITS}
    _assert_disjoint(parts)
    groups = manifest["groups"]
    for a in SPLITS:
        for b in SPLITS:
            if a < b:
                assert not set(groups[a]) & set(groups[b])
    for name, info in manifest["files"].items():
        assert sha256_file(ROOT / info["path"]) == info["sha256"], name
    xs = list(read_jsonl(path("splits") / "xharness.jsonl"))
    _, repos, tasks = _keys(parts["train"] + parts["val"])
    assert not {s.repo_key for s in xs} & repos
    assert all(s.source == "openswe_xharness" for s in xs)
