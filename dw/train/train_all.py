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


def train_elicitation(train: list[Session], val: list[Session]) -> dict[str, Any]:
    bank_a = load_bank("bank_a")
    rng = random.Random(CFG["split"]["seed"])
    tr_prompts = rng.sample([first_user(s) for s in train if first_user(s)], k=min(TCFG["elicit_prompts"], len(train)))
    va_prompts = [first_user(s) for s in val if first_user(s)][: TCFG["elicit_prompts"] // 3]
    x_tr, y_tr = elicitation_set(tr_prompts, bank_a, seed=1)
    x_va, y_va = elicitation_set(va_prompts, bank_a, seed=2)
    model = tr.fit_elicitation(x_tr, y_tr)
    tr.save_model(model, MODELS, "d4")
    pattern = tr.keyword_baseline(bank_a["elicit"])
    (MODELS / "d4_keywords.txt").write_text(pattern.pattern)
    return {"train_n": len(x_tr), "val": tr.evaluate_classifier(model, x_va, y_va),
            "val_keyword": tr.evaluate_keyword(pattern, x_va, y_va)}


def real_tool_outputs(sessions: list[Session], limit: int, seed: int) -> list[str]:
    outs = [m.text for s in sessions for m in s.messages if m.role == "tool_result" and m.text.strip()]
    random.Random(seed).shuffle(outs)
    return outs[:limit]


def fake_outputs(split: str) -> list[str]:
    rows = [json.loads(x) for x in open(path("synth") / "fake_tool_outputs.jsonl", encoding="utf-8")]
    cap = CFG["normalize"]["max_tool_chars"]
    return [r["fake_text"][:cap] for r in rows if r["split"] == split and r["fake_text"].strip()]


def train_realism(train: list[Session], val: list[Session]) -> dict[str, Any]:
    """Positives: LLM-written outputs. Negatives: real outputs from both sources (same split)."""
    fake_tr, fake_va = fake_outputs("train"), fake_outputs("val")
    real_tr = real_tool_outputs(train, TCFG["realism_neg_ratio"] * len(fake_tr), seed=3)
    real_va = real_tool_outputs(val, TCFG["realism_neg_ratio"] * len(fake_va), seed=4)
    model = tr.fit_realism(fake_tr + real_tr, [1] * len(fake_tr) + [0] * len(real_tr))
    tr.save_model(model, MODELS, "d6")
    return {"train_pos": len(fake_tr), "train_neg": len(real_tr),
            "val": tr.evaluate_classifier(model, fake_va + real_va, [1] * len(fake_va) + [0] * len(real_va))}


def scenario_features(split: str, seed: int, training: bool, record: bool) -> tuple[dict, dict, list]:
    """Run one scenario offline with score=False. Return features, labels, and per-request rows."""
    scen = build_scenario(split, seed, TCFG["scenario_scale"], training=training)
    worker = Worker(models_dir=MODELS, score=False, record_requests=record)
    run_offline(scen, worker)
    worker.rescore()
    table = {a: f for a, f in worker.feature_table().items()
             if worker.accounts[a].n_requests >= CFG["account"]["min_requests"] and a in scen.labels}
    labels = {a: int(scen.labels[a]["role"] == "attack") for a in table}
    rows = [(f, int(scen.labels[a]["role"] == "attack")) for _, a, f in worker.request_rows if a in scen.labels] if record else []
    save_features(f"{split}_{seed}", table, {a: scen.labels[a] for a in table})
    return table, labels, rows


def save_features(name: str, table: dict[str, dict[str, float]], labels: dict[str, dict[str, str]]) -> None:
    """Cache an account feature table with its labels for the leakage controls."""
    FEATURES.mkdir(parents=True, exist_ok=True)
    (FEATURES / f"{name}.json").write_text(json.dumps({"features": table, "labels": labels}))


def train_account_models() -> dict[str, Any]:
    rows: list[dict[str, float]] = []
    labels: list[int] = []
    req_rows: list[tuple[dict[str, float], int]] = []
    for seed in CFG["account"]["train_seeds"]:
        table, lab, rr = scenario_features("train", seed, training=True, record=True)
        rows += list(table.values())
        labels += [lab[a] for a in table]
        req_rows += rr
    model = am.fit(rows, labels)
    va_table, va_lab, _ = scenario_features("val", CFG["account"]["val_seed"], training=False, record=False)
    thresholds = am.set_thresholds(model, list(va_table.values()), [va_lab[a] for a in va_table])
    am.save(model, MODELS / am.ACCOUNT_MODEL_FILE)

    rng = random.Random(5)
    rng.shuffle(req_rows)
    req_rows = req_rows[: TCFG["request_rows_max"]]
    req_model = am.fit([r for r, _ in req_rows], [y for _, y in req_rows])
    am.save(req_model, MODELS / am.REQUEST_MODEL_FILE)
    val_scores = model.predict_many(list(va_table.values()))
    return {"train_accounts": len(rows), "train_attack": sum(labels), "val_accounts": len(va_table),
            "thresholds": thresholds, "coefficients": am.coefficients(model),
            "val_auc": _auc([va_lab[a] for a in va_table], val_scores),
            "request_rows": len(req_rows), "request_coefficients": am.coefficients(req_model)}


def _auc(labels: list[int], scores: list[float]) -> float | None:
    from sklearn.metrics import roc_auc_score
    return float(roc_auc_score(labels, scores)) if len(set(labels)) == 2 else None


def timed(name: str, fn: Callable[[], Any], compute: dict[str, Any]) -> Any:
    """Run one step. Record wall time and peak memory (MB)."""
    t0 = time.time()
    out = fn()
    compute[name] = {"wall_s": round(time.time() - t0, 2),
                     "peak_rss_mb": round(resource.getrusage(resource.RUSAGE_SELF).ru_maxrss / 1024, 1)}
    print(f"[train] {name}: {compute[name]}", flush=True)
    return out


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
    metrics["d4_elicitation"] = timed("d4_elicitation", lambda: train_elicitation(train, val), compute)
    metrics["d6_realism"] = timed("d6_realism", lambda: train_realism(train, val), compute)
    metrics["account_model"] = timed("account_model", train_account_models, compute)
    embed.save_cache(EMB_CACHE)
    compute["train_total_s"] = round(time.time() - t0, 1)
    (REPORTS / "train_metrics.json").write_text(json.dumps(metrics, indent=2))
    old = json.loads((REPORTS / "compute.json").read_text()) if (REPORTS / "compute.json").exists() else {}
    old["train"] = compute
    (REPORTS / "compute.json").write_text(json.dumps(old, indent=2))
    print(json.dumps({"thresholds": metrics["account_model"]["thresholds"],
                      "val_auc": metrics["account_model"]["val_auc"]}, indent=1))


if __name__ == "__main__":
    main()
