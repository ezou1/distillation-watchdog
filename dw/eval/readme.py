"""Write the Results and Leakage controls sections of README.md from reports/*.json.

The README must not contain numbers that a person typed. Run this after scripts/eval.sh.
"""
from __future__ import annotations

import json
import re
from typing import Any

from dw.common.config import ROOT, path

REPORTS = path("reports")
PERSONAS = ["solo_dev", "saas_app", "benchmark_runner", "hydra", "fake_env", "stealth"]
TITLES = {"solo_dev": "Solo developer", "saas_app": "SaaS app", "benchmark_runner": "Benchmark runner",
          "hydra": "Hydra", "fake_env": "Fake environment", "stealth": "Stealth"}


def _load(name: str) -> dict[str, Any]:
    p = REPORTS / name
    return json.loads(p.read_text()) if p.exists() else {}


def _f(x: Any, nd: int = 3) -> str:
    if x is None:
        return "n/a"
    if isinstance(x, float):
        if x.is_integer() and abs(x) >= 1:
            return f"{int(x):,}"
        return f"{x:,.1f}" if abs(x) >= 100 else f"{x:.{nd}f}"
    if isinstance(x, int):
        return f"{x:,}"
    return str(x)


def _table(head: list[str], rows: list[list[Any]]) -> str:
    out = ["| " + " | ".join(head) + " |", "|" + "---|" * len(head)]
    out += ["| " + " | ".join(_f(c) if not isinstance(c, str) else c for c in r) + " |" for r in rows]
    return "\n".join(out)


def results(m: dict[str, Any], compute: dict[str, Any], load: dict[str, Any], cost: dict[str, Any]) -> str:
    sc, roc, pr = m["scenario"], m["account_roc"], m["per_request_vs_per_account"]
    parts = [
        f"Holdout scenario: {_f(sc['requests'])} requests, {_f(sc['accounts'])} accounts, "
        f"{_f(sc['scored_accounts'])} accounts with enough requests to score (scale `{sc['scale']}`, seed {sc['seed']}).",
        "",
        "### Per-request versus per-account",
        "",
        _table(["Measure", "Value"], [
            ["Per-request model AUC", pr["request_auc"]],
            ["Per-request threshold (from validation)", pr["request_threshold_from_val"]],
            ["Per-request false positive rate at this threshold", pr["request_fpr"]],
            ["Per-request recall at this threshold", pr["request_recall"]],
            [f"False request flags per day at {pr['requests_per_day']:,} requests", pr["false_request_flags_per_day"]],
            ["Account model AUC", roc["auc"]],
            ["Normal accounts in the holdout", pr["normal_accounts"]],
            ["Falsely blocked normal accounts", pr["falsely_blocked_normal_accounts"]],
            ["Attack accounts blocked (share)", pr["attack_account_recall"]],
        ]),
        "",
        "![Per-request versus per-account](reports/per_request_vs_account.png)",
        "",
        "### Accounts per persona",
        "",
        "\"Account level\" uses only the account score. \"Final\" includes the campaign propagation.",
        "",
        _table(["Persona", "Accounts", "Blocked (account level)", "Blocked (final)", "Review or block (final)"],
               [[TITLES[p], v["accounts"], v["account_level_block"], v["final_block"], v["final_review_or_block"]]
                for p in PERSONAS if (v := m["per_persona"].get(p))]),
        "",
        "![ROC and precision-recall](reports/roc_pr.png)",
        "",
        "### Time to block and leakage",
        "",
        _table(["Persona", "Accounts", "Blocked", "Median requests to block", "Median simulated minutes to block"],
               [[TITLES[p], v["accounts"], v["blocked"], v["median_requests_to_block"], v["median_sim_minutes_to_block"]]
                for p, v in m["time_to_block"]["per_persona"].items()]),
        "",
        _table(["Persona", "Complete trajectories served", "Complete trajectories tried"],
               [[TITLES.get(p, p), v["served_trajectories"], v["possible_trajectories"]]
                for p, v in sorted(m["time_to_block"]["leakage"]["by_persona"].items())]),
        "",
        f"Campaign effect on stealth: account-level recall {_f(m['campaign_effect']['stealth_recall_account_level'])}, "
        f"recall after campaigns {_f(m['campaign_effect']['stealth_recall_after_campaigns'])}, "
        f"{len(m['campaign_effect']['campaigns'])} campaigns found.",
        "",
        "### D4 elicitation on bank B (phrases not seen in training)",
        "",
        _table(["Model", "Precision", "Recall", "False positive rate"],
               [[name, v["precision"], v["recall"], v["fpr"]] for name, v in
                (("Classifier", m["elicitation_bank_b"]["classifier"]),
                 ("Keyword baseline", m["elicitation_bank_b"]["keyword_baseline"]))]),
        "",
        "### D6 environment",
        "",
        _table(["Group", "Contradictions per 100 tool results", "Sessions with a contradiction", "Mean realism score"],
               [[g, v["d6_contradictions_per_100"], v["d6_contradiction_session_frac"], v["d6_fake_prob_mean"]]
                for g, v in (("Fake-environment accounts", m["fake_env"]["fake_env"]), ("Other accounts", m["fake_env"]["others"]))]),
        "",
        f"The realism classifier flags {_f(m['fake_env'].get('realism_fpr_on_real_swechat_holdout'))} of "
        f"{_f(m['fake_env'].get('realism_real_outputs_n'))} real SWE-chat tool outputs in the holdout.",
        "",
        "### Compute",
        "",
    ]
    tr, ev = compute.get("train", {}), compute.get("eval", {})
    rows = [["Training, wall time (s)", tr.get("train_total_s")], ["Training, CPU time (s)", tr.get("train_total_cpu_s")],
            ["Evaluation, wall time (s)", ev.get("wall_s")], ["Evaluation, requests per second", ev.get("requests_per_s")]]
    if load:
        rows += [["Lab gateway load test, requests per second", load.get("rps")],
                 ["Lab gateway `handle()` p95 (ms)", load.get("lab_handle_p95_ms")]]
    if cost:
        rows.append(["OpenRouter cost, total (USD)", cost.get("total_cost_usd")])
    parts.append(_table(["Measure", "Value"], rows))
    return "\n".join(parts)


def controls(c: dict[str, Any]) -> str:
    fp, pp, ch, lp, ab = (c.get(k, {}) for k in ("format_probe", "assistant_prose_probe", "cross_harness",
                                                  "label_permutation", "feature_ablation"))
    hydra = lambda key: (ch.get(key) or {}).get("hydra", {}).get("recall_block")  # noqa: E731
    rows = [
        ["1. Scaffold denylist", f"{_f(c['scaffold_denylist']['dirty_records_total'])} dirty records", "0",
         "pass" if c["scaffold_denylist"]["pass"] else "fail"],
        ["2. Format probe (source from formatting)", f"AUC {_f(fp.get('auc_holdout'))}", f"below {fp.get('target_below')}",
         "pass" if fp.get("pass") else "fail"],
        ["3. Assistant-prose probe (source from prose)", f"AUC {_f(pp.get('auc_holdout'))}", "high (expected)", "info"],
        ["4. Cross-harness (hydra on SWE-agent data)", f"hydra recall {_f(hydra('normal'))} (normal), {_f(hydra('xharness'))} (other harness)",
         "same recall", "pass" if hydra("xharness") is not None and hydra("xharness") >= (hydra("normal") or 0) - 0.05 else "fail"],
        ["5. Label permutation", f"mean AUC {_f(lp.get('mean_auc'))} (std {_f(lp.get('std_auc'))}), p = {_f(lp.get('p_value'))}",
         "near 0.5", "see note"],
    ]
    parts = [_table(["Control", "Result", "Target", "Status"], rows), ""]
    parts.append("6. Feature-family ablation (fit on train, thresholds on validation, results on holdout):")
    parts.append("")
    parts.append(_table(["Feature set", "Features", "Holdout AUC", "Recall at block", "False positive rate at block"],
                        [[k, v["n_features"], v["holdout_auc"], v.get("holdout_recall_at_block"), v.get("holdout_fpr_at_block")]
                         for k, v in (ab.get("variants") or {}).items()]))
    parts.append("")
    parts.append("![Ablation](reports/controls_ablation.png)")
    top = ", ".join(f"`{t['name']}`" for t in (fp.get("top_features") or [])[:6])
    if top:
        parts += ["", f"Largest format-probe features: {top}."]
    return "\n".join(parts)


def _replace(text: str, tag: str, body: str) -> str:
    pattern = re.compile(rf"(<!-- {tag}:START -->\n).*?(<!-- {tag}:END -->)", re.S)
    return pattern.sub(lambda mt: mt.group(1) + body + "\n" + mt.group(2), text)


def main() -> None:
    readme = ROOT / "README.md"
    text = readme.read_text()
    m = _load("metrics.json")
    compute, load, cost = _load("compute.json"), _load("load.json"), _load("llm_cost.json")
    text = _replace(text, "RESULTS", results(m, compute, load, cost))
    text = _replace(text, "CONTROLS", controls(_load("controls.json")))
    readme.write_text(text)
    print("README.md results and controls written from reports/")


if __name__ == "__main__":
    main()
