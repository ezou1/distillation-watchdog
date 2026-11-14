"""Train every learned model from the train split. Select thresholds on the validation split.

The holdout split is never read here. Outputs: models/*.pkl, reports/train_metrics.json, reports/compute.json.
"""
from __future__ import annotations

import json
import random
import resource
import time
from pathlib import Path
from typing import Any, Callable, Iterator

from dw.common import embed
from dw.common.config import CFG, ROOT, path
from dw.common.schema import Session
from dw.sim.offline import run_offline
from dw.sim.scenario import build_scenario
from dw.watchdog import account_model as am
from dw.watchdog.detectors import training as tr
from dw.watchdog.worker import Worker

MODELS = path("models")
REPORTS = path("reports")
FEATURES = ROOT / CFG["paths"]["cache"] / "features"
EMB_CACHE = ROOT / CFG["paths"]["cache"] / "emb" / "emb.npz"
TCFG = CFG["train"]


def iter_sessions(split: str) -> Iterator[Session]:
    """Yield canonical sessions of one split (train or val only)."""
    assert split in ("train", "val"), "training must not read the holdout split"
    with open(path("splits") / f"{split}.jsonl", encoding="utf-8") as f:
        for line in f:
            yield Session.model_validate_json(line)


def first_user(s: Session) -> str:
    return next((m.text for m in s.messages if m.role == "user" and m.text.strip()), "")


def load_bank(name: str) -> dict[str, list[str]]:
    data = json.loads((path("synth") / f"{name}.json").read_text())
    return {"elicit": [x["text"] for x in data["elicit"]], "benign": [x["text"] for x in data["benign"]]}


def place(prompt: str, phrase: str, rng: random.Random) -> str:
    """Put a phrase before or after a prompt, as the simulator does in user turns."""
    return f"{phrase}\n\n{prompt}" if rng.random() < 0.3 else f"{prompt}\n\n{phrase}"


def elicitation_set(prompts: list[str], bank: dict[str, list[str]], seed: int) -> tuple[list[str], list[int]]:
    """Label 1: prompt with an elicitation phrase. Label 0: prompt alone or with a benign request."""
    rng = random.Random(seed)
    texts, labels = [], []
    for p in prompts:
        p = p[: TCFG["prompt_chars"]]
        texts.append(place(p, rng.choice(bank["elicit"]), rng))
        labels.append(1)
        texts.append(place(p, rng.choice(bank["benign"]), rng) if rng.random() < 0.5 else p)
        labels.append(0)
    return texts, labels


def train_topics(train: list[Session]) -> dict[str, Any]:
    prompts = [first_user(s)[:2000] for s in train if first_user(s)]
    model = tr.fit_topics(prompts)
    tr.save_model(model, MODELS, "d3")
    return {"prompts": len(prompts), "k": int(model.n_clusters)}


def keyword_baseline(val: list[Session]) -> dict[str, Any]:
    """D4 keyword baseline (a regex of the most frequent bank A phrases). The D4 text model is the context model."""
    bank_a = load_bank("bank_a")
    pattern = tr.keyword_baseline(bank_a["elicit"])
    (MODELS / "d4_keywords.txt").write_text(pattern.pattern)
    va_prompts = [first_user(s) for s in val if first_user(s)][: TCFG["elicit_prompts"] // 3]
    x_va, y_va = elicitation_set(va_prompts, bank_a, seed=2)
    return {"val_keyword": tr.evaluate_keyword(pattern, x_va, y_va)}


def context_model_metrics() -> dict[str, Any] | None:
    """Validation metrics of the context model (written by scripts/context_train.py on the GPU machine)."""
    p = MODELS / "context_metrics.json"
    if not p.exists():
        return None
    m = json.loads(p.read_text())
    return {"model": m.get("model"), "train_n": m.get("train_n"), "val_n": m.get("val_n"), "best": m.get("best")}


Snapshot = tuple[str, int, dict[str, float]]          # (account_id, request count, features)


def scenario_features(split: str, seed: int, training: bool, record: bool) -> tuple[dict, dict, list, list]:
    """Run one scenario offline with score=False.

    Return the final feature table, the labels (full label dicts), the per-request rows, and the snapshot rows.
    Snapshot rows are the features at the account.snapshot_requests counts and at the end, so the model also
    learns from partial account histories (it decides on partial histories when it runs).
    """
    scen = build_scenario(split, seed, TCFG["scenario_scale"], training=training)
    worker = Worker(models_dir=MODELS, score=False, record_requests=record, snapshots=True)
    run_offline(scen, worker)
    worker.rescore()
    table = {a: f for a, f in worker.feature_table().items()
             if worker.accounts[a].n_requests >= CFG["account"]["min_requests"] and a in scen.labels}
    labels = {a: scen.labels[a] for a in table}
    rows = [(f, int(scen.labels[a]["role"] == "attack")) for _, a, f in worker.request_rows if a in scen.labels] if record else []
    snaps: list[Snapshot] = [(a, n, f) for a, n, f in worker.snapshots if a in table and n >= CFG["account"]["min_requests"]]
    last = {a: n for a, n, _ in snaps}
    snaps += [(a, worker.accounts[a].n_requests, table[a]) for a in table if last.get(a) != worker.accounts[a].n_requests]
    save_features(f"{split}_{seed}", table, labels, snaps)
    return table, labels, rows, snaps


def save_features(name: str, table: dict[str, dict[str, float]], labels: dict[str, dict[str, Any]],
                  snapshots: list[Snapshot] | None = None) -> None:
    """Cache an account feature table with its labels (and snapshot rows) for the leakage controls."""
    FEATURES.mkdir(parents=True, exist_ok=True)
    data: dict[str, Any] = {"features": table, "labels": labels}
    if snapshots is not None:
        data["snapshots"] = [[a, n, f] for a, n, f in snapshots]
    (FEATURES / f"{name}.json").write_text(json.dumps(data))


def _scenario_job(args: tuple[str, int, bool, bool]) -> tuple[dict, dict, list, list]:
    embed.load_cache(EMB_CACHE)
    out = scenario_features(*args)
    split, seed = args[0], args[1]
    embed.save_cache(EMB_CACHE.with_name(f"emb_{split}_{seed}.npz"))
    return out


def run_scenarios(jobs: list[tuple[str, int, bool, bool]]) -> list[tuple[dict, dict, list, list]]:
    """Run the scenarios in parallel processes (spawn: no forked ONNX threads). Merge their embedding caches."""
    import multiprocessing as mp
    with mp.get_context("spawn").Pool(min(len(jobs), TCFG.get("processes", 4))) as pool:
        results = pool.map(_scenario_job, jobs)
    for split, seed, _, _ in jobs:
        part = EMB_CACHE.with_name(f"emb_{split}_{seed}.npz")
        embed.load_cache(part)
        part.unlink(missing_ok=True)
    return results


def snapshot_rows(snaps: list[Snapshot], labels: dict[str, dict[str, Any]]) -> tuple[list, list, list, list]:
    """Rows, labels (1 = attack), weights (each account counts once), and account IDs of snapshot rows."""
    count: dict[str, int] = {}
    for a, _, _ in snaps:
        count[a] = count.get(a, 0) + 1
    rows = [f for _, _, f in snaps]
    ys = [int(labels[a]["role"] == "attack") for a, _, _ in snaps]
    weights = [1.0 / count[a] for a, _, _ in snaps]
    return rows, ys, weights, [a for a, _, _ in snaps]


def account_max(values: list[float], ids: list[str]) -> dict[str, float]:
    """The largest value per account over its snapshot rows (the state is decided again at every snapshot)."""
    out: dict[str, float] = {}
    for a, v in zip(ids, values):
        out[a] = max(out.get(a, v), v)
    return out


def group_gate(train: tuple[list, list, list, list], val: tuple[list, list, list, list],
               val_labels: dict[str, dict[str, Any]], rules_thr: dict[str, float]) -> dict[str, Any]:
    """Validation check of the detector groups in the learned model (is D2 or D3 worth keeping?).

    For each variant: validation AUC and recall at the block threshold of the model alone and of the
    decision (model or rules), and the source AUC of normal accounts (Open-SWE against SWE-chat, near 0.5 is good).
    """
    from dw.watchdog import rules
    groups = list(CFG["account"]["model_groups"])
    variants = {"all": groups, **{f"without_{g}": [x for x in groups if x != g] for g in ("d2", "d3") if g in groups}}
    if "d2" in groups and "d3" in groups:
        variants["without_d2_d3"] = [x for x in groups if x not in ("d2", "d3")]
    t_rows, t_y, t_w, _ = train
    v_rows, _, _, v_ids = val
    accts = sorted(set(v_ids))
    y = [int(val_labels[a]["role"] == "attack") for a in accts]
    rules_max = account_max([rules.value(r) for r in v_rows], v_ids)
    rules_block = {a: rules_max[a] >= rules_thr["block"] for a in accts}
    out: dict[str, Any] = {}
    for name, keep in variants.items():
        names = [n for n in am.feature_names(t_rows) if n.split("_")[0] in keep]
        model = am.fit(t_rows, t_y, names=names, weights=t_w)
        best = account_max(model.predict_many(v_rows), v_ids)
        scores = [best[a] for a in accts]
        thr = am.choose_thresholds(scores, y)["block"]
        model_block = {a: best[a] >= thr for a in accts}
        pos = [a for a, yy in zip(accts, y) if yy]
        neg = [a for a, yy in zip(accts, y) if not yy]
        ose = [best[a] for a in neg if val_labels[a].get("swechat_frac", 1.0) < 0.5]
        swe = [best[a] for a in neg if val_labels[a].get("swechat_frac", 1.0) >= 0.5]
        out[name] = {
            "groups": keep, "features": len(model.names), "val_auc": _auc(y, scores),
            "model_recall_at_block": round(sum(model_block[a] for a in pos) / len(pos), 4) if pos else None,
            "decision_recall": round(sum(model_block[a] or rules_block[a] for a in pos) / len(pos), 4) if pos else None,
            "decision_false_blocks": int(sum(model_block[a] or rules_block[a] for a in neg)),
            "normal_accounts": len(neg),
            "source_auc_normal": _auc([1] * len(ose) + [0] * len(swe), ose + swe) if ose and swe else None}
    return out


def train_account_models() -> dict[str, Any]:
    from dw.watchdog import rules
    jobs = [("train", seed, True, True) for seed in CFG["account"]["train_seeds"]]
    jobs.append(("val", CFG["account"]["val_seed"], False, True))
    *train_results, (va_table, va_lab, va_req, va_snaps) = run_scenarios(jobs)
    train_snaps: list[Snapshot] = []
    train_labels: dict[str, dict[str, Any]] = {}
    req_rows: list[tuple[dict[str, float], int]] = []
    for _table, lab, rr, snaps in train_results:
        train_snaps += snaps
        train_labels.update(lab)
        req_rows += rr
    train = snapshot_rows(train_snaps, train_labels)
    val = snapshot_rows(va_snaps, va_lab)
    accts = sorted(set(val[3]))
    y_val = [int(va_lab[a]["role"] == "attack") for a in accts]

    # rules score: thresholds on the largest rules value of each validation account
    rules_max = account_max([rules.value(r) for r in val[0]], val[3])
    rules_thr = rules.fit_thresholds([rules_max[a] for a in accts], y_val)
    rules.save(rules_thr, MODELS)

    # learned model: snapshot rows of the train scenarios, only the detector groups in account.model_groups
    model = am.fit(train[0], train[1], names=am.model_names(train[0]), weights=train[2])
    val_best = account_max(model.predict_many(val[0]), val[3])
    model.thresholds = am.choose_thresholds([val_best[a] for a in accts], y_val)
    am.save(model, MODELS / am.ACCOUNT_MODEL_FILE)

    rng = random.Random(5)
    rng.shuffle(req_rows)
    req_rows = req_rows[: TCFG["request_rows_max"]]
    req_model = am.fit([r for r, _ in req_rows], [y for _, y in req_rows])
    req_model.thresholds = {"request": request_threshold(req_model, va_req)}
    am.save(req_model, MODELS / am.REQUEST_MODEL_FILE)
    return {"train_accounts": len(set(train[3])), "train_snapshot_rows": len(train[0]),
            "train_attack": sum(int(train_labels[a]["role"] == "attack") for a in set(train[3])),
            "val_accounts": len(accts), "thresholds": model.thresholds, "rules_thresholds": rules_thr,
            "rules_feature": CFG["rules"]["feature"], "coefficients": am.coefficients(model),
            "val_auc": _auc(y_val, [val_best[a] for a in accts]),
            "group_gate": group_gate(train, val, va_lab, rules_thr),
            "request_rows": len(req_rows), "request_coefficients": am.coefficients(req_model)}


def request_threshold(model: am.LogitModel, rows: list[tuple[dict[str, float], int]]) -> float:
    """The per-request threshold with the best recall minus false positive rate on validation rows (Youden)."""
    from sklearn.metrics import roc_curve
    fpr, tpr, thr = roc_curve([y for _, y in rows], model.predict_many([r for r, _ in rows]))
    return float(min(thr[int((tpr - fpr).argmax())], 1.0))


def _auc(labels: list[int], scores: list[float]) -> float | None:
    from sklearn.metrics import roc_auc_score
    return float(roc_auc_score(labels, scores)) if len(set(labels)) == 2 else None


def timed(name: str, fn: Callable[[], Any], compute: dict[str, Any]) -> Any:
    """Run one step. Record wall time, CPU time (this process and its child processes), and peak memory (MB)."""
    t0, c0 = time.time(), _cpu()
    out = fn()
    compute[name] = {"wall_s": round(time.time() - t0, 2), "cpu_s": round(_cpu() - c0, 1),
                     "peak_rss_mb": round(resource.getrusage(resource.RUSAGE_SELF).ru_maxrss / 1024, 1)}
    print(f"[train] {name}: {compute[name]}", flush=True)
    return out


def _cpu() -> float:
    own, kids = resource.getrusage(resource.RUSAGE_SELF), resource.getrusage(resource.RUSAGE_CHILDREN)
    return own.ru_utime + own.ru_stime + kids.ru_utime + kids.ru_stime


def main() -> None:
    MODELS.mkdir(parents=True, exist_ok=True)
    REPORTS.mkdir(parents=True, exist_ok=True)
    embed.load_cache(EMB_CACHE)
    compute: dict[str, Any] = {}
    t0 = time.time()
    train = timed("load_splits", lambda: list(iter_sessions("train")), compute)
    val = list(iter_sessions("val"))
    metrics: dict[str, Any] = {}
    metrics["d3_topics"] = timed("d3_topics", lambda: train_topics(train), compute)
    embed.save_cache(EMB_CACHE)
    metrics["d4_keyword_baseline"] = timed("d4_keyword_baseline", lambda: keyword_baseline(val), compute)
    metrics["context_model"] = context_model_metrics()
    metrics["account_model"] = timed("account_model", train_account_models, compute)
    embed.save_cache(EMB_CACHE)
    compute["train_total_s"] = round(time.time() - t0, 1)
    compute["train_total_cpu_s"] = round(_cpu(), 1)
    (REPORTS / "train_metrics.json").write_text(json.dumps(metrics, indent=2))
    old = json.loads((REPORTS / "compute.json").read_text()) if (REPORTS / "compute.json").exists() else {}
    old["train"] = compute
    (REPORTS / "compute.json").write_text(json.dumps(old, indent=2))
    print(json.dumps({"thresholds": metrics["account_model"]["thresholds"],
                      "val_auc": metrics["account_model"]["val_auc"]}, indent=1))


if __name__ == "__main__":
    main()
