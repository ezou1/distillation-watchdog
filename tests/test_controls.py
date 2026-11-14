"""Unit tests for the leakage controls on synthetic data: format features, denylist, permutation, family mapping."""
from __future__ import annotations

import json
import random
from pathlib import Path

from dw.eval import controls as c

FIX = Path(__file__).parent / "fixtures" / "canonical_small"


def _session(source: str, user: str, tool: str, prose: str = "ok") -> dict:
    return {"session_id": "s", "source": source, "messages": [
        {"role": "user", "text": user}, {"role": "tool_call", "text": ""},
        {"role": "tool_result", "text": tool}, {"role": "assistant", "text": prose}]}


def test_text_format_features_detects_formatting() -> None:
    plain = c.text_format_features("fix the bug please")
    fancy = c.text_format_features("# Title\r\n- item\r\n```\n{\"a\": 1}\n```\n<REPO>/x.py ü\n")
    assert plain["md_heading"] == 0 and fancy["md_heading"] > 0
    assert plain["crlf_lines"] == 0 and fancy["crlf_lines"] > 0
    assert fancy["md_fence"] > 0 and fancy["json_keys"] > 0 and fancy["path_repo"] > 0
    assert fancy["char_non_ascii"] > 0 and fancy["ends_newline"] == 1.0
    assert set(plain) == set(fancy)


def test_session_format_features_names_are_stable() -> None:
    a = c.session_format_features(_session("swechat", "hi", "out"))
    b = c.session_format_features({"source": "openswe", "messages": [{"role": "assistant", "text": "x"}]})
    assert set(a) == set(b)
    assert all(v == 0.0 for k, v in b.items() if not k.endswith("count_log"))


def test_format_probe_separates_and_ignores_content() -> None:
    rng = random.Random(0)
    def make(n: int, crlf: bool) -> list[dict]:
        eol = "\r\n" if crlf else "\n"
        return [_session("openswe" if crlf else "swechat", f"task {rng.random()}{eol}more", f"line{eol}line")
                for _ in range(n)]
    train, test = make(30, True) + make(30, False), make(10, True) + make(10, False)
    assert c.format_probe(train, test)["auc_holdout"] > 0.95
    same = [_session(s, f"task {rng.random()}", "out") for s in ["openswe", "swechat"] * 20]
    assert c.format_probe(same[:30], same[30:])["auc_holdout"] <= 0.75


def test_denylist_counts_json_escaped_strings(tmp_path: Path) -> None:
    f = tmp_path / "x.jsonl"
    rows = [_session("swechat", "clean text", "out"), _session("openswe", "a <system-reminder>b", "OBSERVATION:\nls")]
    f.write_text("\n".join(json.dumps(r) for r in rows) + "\n")
    out = c.denylist_file(f)
    assert out["records"] == 2 and out["dirty_records"] == 1
    assert set(out["hits"]) == {"<system-reminder>", "OBSERVATION:\n"}


def test_denylist_fixture_splits_are_clean() -> None:
    out = c.denylist(FIX)
    assert out["files"] and out["dirty_records_total"] == 0


def test_sample_split_caps_each_source() -> None:
    rows = c.sample_split(FIX / "splits" / "train.jsonl", 5, seed=1)
    sources = [r["source"] for r in rows]
    assert all(sources.count(s) <= 5 for s in set(sources)) and len(set(sources)) == 2


def _table(n: int, seed: int, signal: bool = True) -> c.Table:
    rng = random.Random(seed)
    y = [i % 2 for i in range(n)]
    rows = [{"d1_dup_rate": rng.gauss(2.0 * yi if signal else 0.0, 1.0), "d2_followup_rate": rng.gauss(0, 1),
             "d7_req_per_active_hour": rng.gauss(yi, 1.0)} for yi in y]
    return c.Table([f"a{seed}-{i}" for i in range(n)], rows, y, ["hydra" if yi else "solo_dev" for yi in y],
                   [None] * n, [None] * n)


def test_permutation_auc_near_chance() -> None:
    out = c.permutation_test(_table(400, 1), _table(400, 2), runs=40, seed=0)
    assert len(out["holdout_aucs"]) == 40 and abs(out["mean_auc"] - 0.5) < 0.12


def test_ablation_real_vs_simulated() -> None:
    out = c.ablation(_table(300, 1), _table(200, 2), _table(200, 3))
    v = out["variants"]
    assert v["all"]["holdout_auc"] > 0.8
    assert v["drop_d1"]["n_features"] == 2 and v["strict_real_data"]["n_features"] == 1
    assert v["drop_d1"]["holdout_auc"] < v["all"]["holdout_auc"]
    assert out["feature_origin"]["d1_dup_rate"]["origin"] == c.SIM


def test_family_mapping() -> None:
    assert c.feature_origin("d1_branch_frac") == c.REAL and c.spec_real_data("d1_branch_frac")
    assert c.feature_origin("d1_dup_rate") == c.SIM and not c.spec_real_data("d1_dup_rate")
    assert c.feature_origin("d4_elicit_mean") == c.SYNTH and not c.spec_real_data("d4_elicit_mean")
    assert c.feature_origin("d4_mean_temp") == c.SIM
    assert c.feature_origin("d7_anything_new") == c.SIM
    assert c.spec_real_data("d6_fake_prob_mean")
    assert all(name.split("_")[0] in c.DETECTORS for name in c.FEATURE_ORIGIN)
    subsets = c.ablation_subsets(list(c.FEATURE_ORIGIN))
    assert not any(n.startswith("d7_") for n in subsets["spec_real_data (B10 6a)"])
    assert all(c.feature_origin(n) == c.SIM for n in subsets["simulator_only"])


def test_recall_by_persona_uses_saved_thresholds() -> None:
    train = _table(300, 1)
    model = c.am.fit(train.rows, train.y)
    model.thresholds = {"block": 0.99, "review": 0.0}
    out = c.recall_by_persona(model, _table(100, 2))
    assert set(out) == {"hydra"} and out["hydra"]["worker_state_block"] is None
    assert abs(out["hydra"]["recall_block"] + out["hydra"]["share_review_only"] - 1.0) < 1e-3


def test_source_reliance_follows_the_source_only_when_the_score_does() -> None:
    """Normal accounts from two sources: the AUC is high only if the score depends on the source."""
    train = _table(300, 1)
    model = c.am.fit(train.rows, train.y)
    model.thresholds = {"block": 0.99, "review": 0.5}
    rng = random.Random(3)
    n = 200
    for leak, check in ((False, lambda a: 0.35 < a < 0.65), (True, lambda a: a > 0.9)):
        src = [float(i % 2) for i in range(n)]          # 1.0 = SWE-chat, 0.0 = Open-SWE
        rows = [{"d1_dup_rate": rng.gauss(2.0 * (1 - s) if leak else 0.0, 0.5), "d2_followup_rate": 0.0,
                 "d7_req_per_active_hour": 0.0} for s in src]
        table = c.Table([f"n{i}" for i in range(n)], rows, [0] * n, ["solo_dev"] * n, [None] * n, src)
        out = c.source_reliance(model, table)
        assert out["normal_openswe"]["accounts"] == out["normal_swechat"]["accounts"] == n // 2
        assert check(out["normal_auc_openswe_vs_swechat"]), out
