"""Offline evaluation on the holdout scenario. Writes reports/metrics.json and PNG charts.

The holdout scenario runs through gateway.handle() and the worker with DirectSink, as in live mode.
"""
from __future__ import annotations

import json
import math
import time
from collections import defaultdict
from typing import Any

import matplotlib
import numpy as np
from sklearn.metrics import precision_recall_curve, roc_auc_score, roc_curve

from dw.common import embed
from dw.common.config import CFG, path
from dw.sim.offline import OfflineResult, run_offline
from dw.sim.scenario import Scenario, build_scenario
from dw.train.train_all import EMB_CACHE, MODELS, save_features
from dw.watchdog import account_model as am, rules
from dw.watchdog.worker import Worker

matplotlib.use("Agg")
import matplotlib.pyplot as plt  # noqa: E402

REPORTS = path("reports")
NORMAL = ["solo_dev", "saas_app", "benchmark_runner"]
ATTACK = ["hydra", "fake_env", "stealth"]
COLORS = {"solo_dev": "#1b7f3b", "saas_app": "#2a6fdb", "benchmark_runner": "#7a4fbf",
          "hydra": "#c62828", "fake_env": "#ef6c00", "stealth": "#6d4c41"}


def run_holdout() -> tuple[Scenario, Worker, OfflineResult, float]:
    t0 = time.time()
    scen = build_scenario("holdout", CFG["account"]["holdout_seed"], CFG["eval"]["scale"])
    worker = Worker(models_dir=MODELS, score=True, record_requests=True)
    result = run_offline(scen, worker)
    return scen, worker, result, time.time() - t0


def account_table(scen: Scenario, worker: Worker) -> list[dict[str, Any]]:
    """One row per labelled account with enough requests: persona, label, score, state."""
    rows = []
    for aid, label in scen.labels.items():
        acct = worker.accounts.get(aid)
        if acct is None or acct.n_requests < CFG["account"]["min_requests"] or acct.score is None:
            continue
        rows.append({"id": aid, "persona": label["persona"], "y": int(label["role"] == "attack"),
                     "score": acct.score, "rules_level": acct.rules_level or "normal",
                     "rules_value": rules.value(acct.features), "state": acct.state, "campaign": acct.campaign_id,
                     "requests": acct.n_requests, "features": acct.features})
    return rows


def persona_metrics(rows: list[dict[str, Any]], thresholds: dict[str, float]) -> dict[str, Any]:
    """Recall per attack persona and false positives per normal persona, at account level and after campaigns."""
    out: dict[str, Any] = {}
    for persona in ATTACK + NORMAL:
        group = [r for r in rows if r["persona"] == persona]
        n = len(group)
        rules_block = sum(r["rules_level"] == "block" for r in group)
        model_block = sum(r["score"] >= thresholds["block"] for r in group)
        acct_block = sum(r["score"] >= thresholds["block"] or r["rules_level"] == "block" for r in group)
        acct_review = sum(r["score"] >= thresholds["review"] or r["rules_level"] in ("review", "block") for r in group)
        final_block = sum(r["state"] == "block" for r in group)
        final_review = sum(r["state"] in ("block", "review") for r in group)
        out[persona] = {"accounts": n, "rules_block": rules_block, "model_block": model_block,
                        "account_level_block": acct_block, "account_level_review_or_block": acct_review,
                        "final_block": final_block, "final_review_or_block": final_review,
                        "disclosed": sum(r["state"] == "disclosed" for r in group),
                        "rate_account_level_block": round(acct_block / n, 3) if n else None,
                        "rate_final_block": round(final_block / n, 3) if n else None}
    return out


def roc_pr(rows: list[dict[str, Any]]) -> dict[str, Any]:
    """ROC and precision-recall of the learned model score, with the ROC of the rules statistic for comparison."""
    y = [r["y"] for r in rows]
    s = [r["score"] for r in rows]
    fpr, tpr, _ = roc_curve(y, s)
    prec, rec, _ = precision_recall_curve(y, s)
    r_fpr, r_tpr, _ = roc_curve(y, [r["rules_value"] for r in rows])
    fig, ax = plt.subplots(1, 2, figsize=(11, 4.5))
    ax[0].plot(r_fpr, r_tpr, lw=2, color="#2a6fdb", label="rules statistic (D1)")
    ax[0].plot(fpr, tpr, lw=2.5, color="#c62828", label="learned model")
    ax[0].legend(loc="lower right")
    ax[0].plot([0, 1], [0, 1], ls="--", color="grey")
    ax[0].set(xlabel="False positive rate (normal accounts)", ylabel="Recall (attack accounts)",
              title=f"Account ROC, model AUC = {roc_auc_score(y, s):.3f}")
    ax[1].plot(rec, prec, lw=2.5, color="#2a6fdb")
    ax[1].set(xlabel="Recall", ylabel="Precision", title="Account precision-recall")
    fig.tight_layout()
    fig.savefig(REPORTS / "roc_pr.png", dpi=110)
    plt.close(fig)
    return {"auc": round(float(roc_auc_score(y, s)), 4),
            "rules_auc": round(float(roc_auc_score(y, [r["rules_value"] for r in rows])), 4),
            "accounts": len(rows), "attack_accounts": sum(y)}


def time_to_block(scen: Scenario, result: OfflineResult, worker: Worker) -> dict[str, Any]:
    """Requests and simulated minutes until each attack account is blocked. Leaked trajectories per campaign."""
    first_seen: dict[str, float] = {}
    for r in result.requests:
        first_seen.setdefault(r["account"], r["sim_time"])
    per_persona: dict[str, list[dict[str, float]]] = defaultdict(list)
    for aid, acc in result.accounts.items():
        if acc["role"] != "attack":
            continue
        blocked = acc["first_block_time"] is not None
        per_persona[acc["persona"]].append({
            "blocked": blocked, "requests_before_block": acc["requests_before_block"],
            "minutes_to_block": (acc["first_block_time"] - first_seen[aid]) / 60 if blocked and aid in first_seen else None})
    summary = {}
    for persona, items in per_persona.items():
        mins = [i["minutes_to_block"] for i in items if i["minutes_to_block"] is not None]
        reqs = [i["requests_before_block"] for i in items if i["blocked"]]
        summary[persona] = {"accounts": len(items), "blocked": sum(i["blocked"] for i in items),
                            "median_requests_to_block": float(np.median(reqs)) if reqs else None,
                            "median_sim_minutes_to_block": round(float(np.median(mins)), 1) if mins else None}
    leaked = leakage_by_campaign(result, worker)
    return {"per_persona": summary, "leakage": leaked}


def leakage_by_campaign(result: OfflineResult, worker: Worker) -> dict[str, Any]:
    """Complete trajectories (final step served) per campaign and per persona, with the total possible."""
    by_camp: dict[str, dict[str, int]] = defaultdict(lambda: {"served_trajectories": 0, "possible_trajectories": 0})
    by_persona: dict[str, dict[str, int]] = defaultdict(lambda: {"served_trajectories": 0, "possible_trajectories": 0})
    for r in result.requests:
        if not r.get("final"):
            continue
        acct = worker.accounts.get(r["account"])
        camp = (acct.campaign_id if acct else None) or "none"
        for bucket in (by_camp[camp], by_persona[r["persona"]]):
            bucket["possible_trajectories"] += 1
            bucket["served_trajectories"] += int(r["status"] == 200)
    return {"by_campaign": dict(by_camp), "by_persona": dict(by_persona),
            "note": "possible_trajectories counts final steps the sender tried; a blocked session stops earlier, so this undercounts what the attacker wanted."}


def per_request_vs_account(scen: Scenario, worker: Worker, rows: list[dict[str, Any]]) -> dict[str, Any]:
    """The key result: per-request false flags at scale versus falsely blocked normal accounts.

    The per-request threshold comes from validation. The holdout only measures it.
    """
    model = am.load(MODELS / am.REQUEST_MODEL_FILE)
    reqs = [(aid, f) for _, aid, f in worker.request_rows if aid in scen.labels]
    y = np.array([int(scen.labels[a]["role"] == "attack") for a, _ in reqs])
    s = np.array(model.predict_many([f for _, f in reqs]))
    thr = model.thresholds["request"]                 # chosen on validation request rows (train_all.py)
    flag = s >= thr
    fpr_at = float(flag[y == 0].mean())
    tpr_at = float(flag[y == 1].mean())
    per_day = CFG["eval"]["requests_per_day"]
    normal_rows = [r for r in rows if r["y"] == 0]
    false_blocks = sum(r["state"] == "block" for r in normal_rows)
    out = {"request_auc": round(float(roc_auc_score(y, s)), 4), "request_threshold_from_val": float(thr),
           "request_fpr": round(fpr_at, 4), "request_recall": round(tpr_at, 4),
           "requests_per_day": per_day, "false_request_flags_per_day": int(round(fpr_at * per_day)),
           "normal_accounts": len(normal_rows), "falsely_blocked_normal_accounts": false_blocks,
           "account_fpr": round(false_blocks / max(1, len(normal_rows)), 4),
           "attack_account_recall": round(sum(r["state"] == "block" for r in rows if r["y"]) / max(1, sum(r["y"] for r in rows)), 4)}
    out["cumulative_example"] = cumulative_plot(scen, reqs, model, s, out)
    return out


def cumulative_plot(scen: Scenario, reqs: list, model: Any, s: np.ndarray, summary: dict[str, Any]) -> dict[str, str]:
    """Cumulative per-request log-odds of one hydra and one solo account versus the number of requests."""
    prior = math.log(max(1e-6, float(np.mean(s))) / max(1e-6, 1 - float(np.mean(s))))
    series: dict[str, list[float]] = defaultdict(list)
    for (aid, _), p in zip(reqs, s):
        p = min(max(float(p), 1e-6), 1 - 1e-6)
        series[aid].append(math.log(p / (1 - p)) - prior)
    chosen = {}
    for persona in ("hydra", "solo_dev"):
        cands = [a for a in series if scen.labels[a]["persona"] == persona]
        if cands:
            chosen[persona] = max(cands, key=lambda a: len(series[a]))
    fig, ax = plt.subplots(1, 2, figsize=(12, 4.6))
    for persona, aid in chosen.items():
        ax[0].plot(np.cumsum(series[aid][:400]), lw=2.5, color=COLORS[persona], label=persona.replace("_", " "))
    ax[0].axhline(0, color="grey", lw=1)
    ax[0].set(xlabel="Requests from the account", ylabel="Cumulative log-odds (sum of per-request evidence)",
              title="One request is weak evidence; many requests are strong")
    ax[0].legend()
    vals = [summary["false_request_flags_per_day"], summary["falsely_blocked_normal_accounts"]]
    bars = ax[1].bar(["Per-request model\nfalse flags per day", "Per-account model\nfalsely blocked accounts"], vals,
                     color=["#c62828", "#1b7f3b"])
    ax[1].set_yscale("symlog")
    ax[1].bar_label(bars, labels=[f"{v:,}" for v in vals])
    ax[1].set_title(f"At {summary['requests_per_day']:,} requests per day")
    fig.tight_layout()
    fig.savefig(REPORTS / "per_request_vs_account.png", dpi=110)
    plt.close(fig)
    return {p: "acct " + a[-6:] for p, a in chosen.items()}


def _binary(probs: list[float], labels: list[int], thr: float = 0.5) -> dict[str, Any]:
    pred = [p >= thr for p in probs]
    tp = sum(p and y for p, y in zip(pred, labels))
    fp = sum(p and not y for p, y in zip(pred, labels))
    pos, neg = sum(labels), len(labels) - sum(labels)
    return {"n": len(labels), "auc": round(float(roc_auc_score(labels, probs)), 4) if 0 < pos < len(labels) else None,
            "precision": round(tp / max(1, tp + fp), 4), "recall": round(tp / max(1, pos), 4),
            "fpr": round(fp / max(1, neg), 4), "threshold": thr}


def context_holdout() -> list[dict[str, Any]]:
    """The holdout test set of the context model (bank B prompts, realism examples). Built on first use."""
    from dw.train import context_data
    if not (context_data.OUT / "holdout.jsonl").exists():
        context_data.build(holdout=True)
    return [json.loads(line) for line in open(context_data.OUT / "holdout.jsonl", encoding="utf-8")]


def _scored(rows: list[dict[str, Any]]) -> tuple[list[float], list[int], int]:
    """Context-model scores of rows. Rows without a score (service down) are left out and counted."""
    from dw.common.context import scorer
    scores = scorer().score_many(r["text"] for r in rows)
    scorer().save()
    kept = [(scores[r["text"]], r["label"]) for r in rows if scores.get(r["text"]) is not None]
    return [p for p, _ in kept], [y for _, y in kept], len(rows) - len(kept)


def elicitation_bank_b() -> dict[str, Any]:
    """D4: the context model versus the keyword baseline on bank B phrases (not seen in training) in holdout prompts."""
    import re
    pattern = re.compile((MODELS / "d4_keywords.txt").read_text())
    rows = [r for r in context_holdout() if r["task"] == "elicitation"]
    probs, labels, unscored = _scored(rows)
    keyword = [1.0 if pattern.search(r["text"]) else 0.0 for r in rows]
    return {"classifier": _binary(probs, labels) if probs else None,
            "keyword_baseline": _binary(keyword, [r["label"] for r in rows]),
            "n": len(rows), "unscored": unscored}


def fake_env_summary(rows: list[dict[str, Any]]) -> dict[str, Any]:
    """D6: realism features of fake-environment accounts against all others, and the context model on the holdout set."""
    out: dict[str, Any] = {}
    for group, pred in (("fake_env", lambda r: r["persona"] == "fake_env"), ("others", lambda r: r["persona"] != "fake_env")):
        sel = [r["features"] for r in rows if pred(r)]
        out[group] = {k: round(float(np.mean([f.get(k, 0.0) for f in sel])), 4) if sel else None
                      for k in ("d6_fake_prob_mean", "d6_fake_high_frac")}
    realism = [r for r in context_holdout() if r["task"] == "realism"]
    probs, labels, unscored = _scored(realism)
    out["realism_holdout"] = {**_binary(probs, labels), "unscored": unscored} if probs else None
    from dw.train import context_data
    check = [json.loads(line) for line in open(context_data.OUT / "holdout_swechat_check.jsonl", encoding="utf-8")]
    c_probs, _, _ = _scored(check)
    if c_probs:
        out["realism_fpr_on_real_swechat_holdout"] = round(float(np.mean([p >= 0.5 for p in c_probs])), 4)
        out["realism_real_outputs_n"] = len(c_probs)
    return out


def campaign_effect(per: dict[str, Any], worker: Worker) -> dict[str, Any]:
    st = per.get("stealth", {})
    return {"stealth_accounts": st.get("accounts"),
            "stealth_recall_account_level": st.get("rate_account_level_block"),
            "stealth_recall_after_campaigns": st.get("rate_final_block"),
            "campaigns": [{"id": c.id, "size": len(c.members), "score": round(c.score, 3), "state": c.state,
                           "top_edge_types": c.top_edge_types} for c in worker.campaigns.values()]}


def main() -> None:
    REPORTS.mkdir(parents=True, exist_ok=True)
    embed.load_cache(EMB_CACHE)
    scen, worker, result, eval_s = run_holdout()
    rows = account_table(scen, worker)
    save_features(f"holdout_{CFG['account']['holdout_seed']}", {r["id"]: r["features"] for r in rows},
                  {r["id"]: scen.labels[r["id"]] for r in rows})
    thresholds = worker.model.thresholds
    per = persona_metrics(rows, thresholds)
    thresholds = {**thresholds, "rules": worker.rules}
    compute = json.loads((REPORTS / "compute.json").read_text()) if (REPORTS / "compute.json").exists() else {}
    compute["eval"] = {"holdout_requests": len(result.requests), "wall_s": round(eval_s, 1),
                       "requests_per_s": round(len(result.requests) / max(eval_s, 1e-9), 1)}
    (REPORTS / "compute.json").write_text(json.dumps(compute, indent=2))
    metrics = {
        "scenario": {"split": "holdout", "seed": CFG["account"]["holdout_seed"], "scale": CFG["eval"]["scale"],
                     "requests": len(result.requests), "accounts": len(scen.labels), "scored_accounts": len(rows)},
        "thresholds": thresholds,
        "account_roc": roc_pr(rows),
        "per_persona": per,
        "campaign_effect": campaign_effect(per, worker),
        "time_to_block": time_to_block(scen, result, worker),
        "per_request_vs_per_account": per_request_vs_account(scen, worker, rows),
        "elicitation_bank_b": elicitation_bank_b(),
        "fake_env": fake_env_summary(rows),
        "generator_counters": result.counters,
        "compute": {"train": compute.get("train"), "eval": compute["eval"],
                    "load": _read("load.json"), "llm_cost": _read("llm_cost.json")},
    }
    (REPORTS / "metrics.json").write_text(json.dumps(metrics, indent=2, default=float))
    embed.save_cache(EMB_CACHE)
    print(json.dumps({k: metrics[k] for k in ("account_roc", "thresholds")}, indent=1))
    print(json.dumps(metrics["per_request_vs_per_account"], indent=1, default=float))


def _read(name: str) -> Any:
    p = REPORTS / name
    return json.loads(p.read_text()) if p.exists() else None


if __name__ == "__main__":
    main()
