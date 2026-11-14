"""Leakage controls of BUILD.md section B10: denylist, format probe, prose probe, cross-harness, permutation, ablation.

Probes and retrained models are fit on the train split (thresholds on validation). The holdout split is evaluation input only.
Run `python -m dw.eval.controls`. It writes reports/controls.json and reports/controls_ablation.png.
"""
from __future__ import annotations

import json
import math
import random
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable, Iterable, Sequence

import matplotlib
import numpy as np
from sklearn.feature_extraction.text import TfidfVectorizer
from sklearn.linear_model import LogisticRegression
from sklearn.pipeline import make_pipeline
from sklearn.preprocessing import StandardScaler

from dw.common import embed
from dw.common.config import CFG, path
from dw.data.normalize import FORBIDDEN
from dw.sim.offline import run_offline
from dw.sim.scenario import build_scenario, load_assets
from dw.train.train_all import EMB_CACHE, FEATURES, MODELS, _auc, save_features
from dw.watchdog import account_model as am
from dw.watchdog.worker import Worker

matplotlib.use("Agg")
import matplotlib.pyplot as plt  # noqa: E402

KCFG = CFG["controls"]
REPORTS = path("reports")
POSITIVE_SOURCE = "openswe"          # probe label 1; swechat is label 0
DETECTORS = ("d1", "d2", "d3", "d4", "d5", "d6", "d7")

# ---------------------------------------------------------------- feature origin (control 6)

REAL = "real_data"             # structure or content of real recorded sessions
SYNTH = "synthetic_text"       # LLM-written or hand-written text that the simulator inserts
SIM = "simulator"              # simulator parameters: request fields, best-of-n copies, timing, volume

FEATURE_ORIGIN: dict[str, tuple[str, str]] = {
    "d1_dup_rate": (SIM, "best-of-n copies are a persona parameter"),
    "d1_max_dup": (SIM, "best-of-n copies are a persona parameter"),
    "d1_hot_dup_rate": (SIM, "best-of-n copies at the persona temperature"),
    "d1_branch_factor": (REAL, "different children of one prefix come from real repeated attempts"),
    "d1_branch_frac": (REAL, "different children of one prefix come from real repeated attempts"),
    "d1_cross_account_dup_rate": (REAL, "identical prefixes of real attempts of one task on other accounts"),
    "d2_user_turns_per_session": (REAL, "user turns of the real sessions"),
    "d2_followup_rate": (REAL, "real follow-ups; stealth adds LLM-written follow-ups"),
    "d2_correction_rate": (REAL, "correction phrases in real user text"),
    "d2_return_rate": (SIM, "session start days come from the scheduler (fit on real gaps)"),
    "d2_stop_end_frac": (REAL, "real sessions that end at the model stop"),
    "d3_distinct_repos": (REAL, "repositories of the real sessions"),
    "d3_repo_entropy": (REAL, "repositories of the real sessions"),
    "d3_sessions_per_repo": (REAL, "repositories of the real sessions"),
    "d3_topic_entropy": (REAL, "topics of the real first prompts"),
    "d4_elicit_mean": (SYNTH, "elicitation phrases come from the LLM-written banks"),
    "d4_elicit_max": (SYNTH, "elicitation phrases come from the LLM-written banks"),
    "d4_elicit_frac": (SYNTH, "elicitation phrases come from the LLM-written banks"),
    "d4_max_tokens_frac": (SIM, "max_tokens is a persona parameter"),
    "d4_max_effort_frac": (SIM, "reasoning_effort is a persona parameter"),
    "d4_n_gt1_rate": (SIM, "n is a persona parameter"),
    "d4_mean_temp": (SIM, "temperature is a persona parameter"),
    "d5_template_mass": (SYNTH, "attacker templates are written assets; normal prompts are real"),
    "d5_skeletons_per_session": (SYNTH, "attacker templates are written assets; normal prompts are real"),
    "d5_top_skeleton_frac": (SYNTH, "attacker templates are written assets; normal prompts are real"),
    "d6_contradictions_per_100": (SYNTH, "fake-env tool outputs are LLM-written; others are real"),
    "d6_contradiction_session_frac": (SYNTH, "fake-env tool outputs are LLM-written; others are real"),
    "d6_fake_prob_mean": (SYNTH, "fake-env tool outputs are LLM-written; others are real"),
    "d7_req_per_active_hour": (SIM, "timing and volume come from the scheduler"),
    "d7_max_concurrent_sessions": (SIM, "timing and volume come from the scheduler"),
    "d7_mean_concurrent_sessions": (SIM, "timing and volume come from the scheduler"),
    "d7_active_hours_per_day": (SIM, "timing and volume come from the scheduler"),
    "d7_user_field_frac": (SIM, "the user field is a persona parameter"),
    "d7_distinct_users": (SIM, "the user field is a persona parameter"),
}


def feature_origin(name: str) -> str:
    """Origin family of one account feature. Unknown D7 features are simulator; other unknowns are 'unknown'."""
    if name in FEATURE_ORIGIN:
        return FEATURE_ORIGIN[name][0]
    return SIM if name.startswith("d7_") else "unknown"


def spec_real_data(name: str) -> bool:
    """BUILD.md B10 item 6(a): D1 branch features, D2, D3, D5, D6."""
    return name.split("_")[0] in ("d2", "d3", "d5", "d6") or (name.startswith("d1_") and feature_origin(name) == REAL)


def ablation_subsets(names: Sequence[str]) -> dict[str, list[str]]:
    """Feature subsets for the ablation: families, all features, and drop-one-detector."""
    subsets = {
        "all": list(names),
        "spec_real_data (B10 6a)": [n for n in names if spec_real_data(n)],
        "strict_real_data": [n for n in names if feature_origin(n) == REAL],
        "simulator_only": [n for n in names if feature_origin(n) == SIM],
    }
    for det in DETECTORS:
        subsets[f"drop_{det}"] = [n for n in names if not n.startswith(det + "_")]
    return subsets


# ---------------------------------------------------------------- control 1: denylist

def _encoded(s: str) -> set[str]:
    """The forms of a string inside a JSON line (with and without ASCII escapes)."""
    return {json.dumps(s, ensure_ascii=True)[1:-1], json.dumps(s, ensure_ascii=False)[1:-1]}


def denylist_file(file: Path, forbidden: Sequence[str] = FORBIDDEN) -> dict[str, Any]:
    """Count the records of one JSONL file that contain a forbidden string."""
    forms = {bad: _encoded(bad) for bad in forbidden}
    hits = {bad: 0 for bad in forbidden}
    records = dirty = 0
    with open(file, encoding="utf-8") as f:
        for line in f:
            records += 1
            found = [bad for bad, enc in forms.items() if any(e in line for e in enc)]
            dirty += bool(found)
            for bad in found:
                hits[bad] += 1
    return {"records": records, "dirty_records": dirty, "hits": {k: v for k, v in hits.items() if v}}


def denylist(data_dir: Path) -> dict[str, Any]:
    files = sorted((data_dir / "canonical").glob("*.jsonl")) + sorted((data_dir / "splits").glob("*.jsonl"))
    per_file = {str(f.relative_to(data_dir)): denylist_file(f) for f in files}
    total = sum(r["dirty_records"] for r in per_file.values())
    return {"forbidden_strings": len(FORBIDDEN), "files": per_file, "dirty_records_total": total,
            "pass": total == 0 and bool(files), "note": "Expected 0 dirty records."}


# ---------------------------------------------------------------- split reading (probes)

_SOURCE_RE = re.compile(r'"source":\s*"(\w+)"')


def sample_split(file: Path, per_source: int, seed: int) -> list[dict[str, Any]]:
    """Read a random sample of at most `per_source` sessions of each source. Parse only the chosen lines."""
    by_source: dict[str, list[int]] = {}
    with open(file, encoding="utf-8") as f:
        for i, line in enumerate(f):
            m = _SOURCE_RE.search(line[:400])
            if m:
                by_source.setdefault(m.group(1), []).append(i)
    rng = random.Random(seed)
    keep = {i for idx in by_source.values() for i in rng.sample(idx, min(per_source, len(idx)))}
    with open(file, encoding="utf-8") as f:
        return [json.loads(line) for i, line in enumerate(f) if i in keep]


def _labels(sessions: Sequence[dict[str, Any]]) -> list[int]:
    return [int(s["source"] == POSITIVE_SOURCE) for s in sessions]


# ---------------------------------------------------------------- control 2: format probe

_PATTERNS: dict[str, re.Pattern[str]] = {
    "lead_space_lines": re.compile(r"^ +\S", re.M),
    "lead_tab_lines": re.compile(r"^\t+", re.M),
    "trailing_ws_lines": re.compile(r"[ \t]+$", re.M),
    "blank_runs": re.compile(r"\n[ \t]*\n[ \t]*\n"),
    "md_heading": re.compile(r"^#{1,6} ", re.M),
    "md_bullet": re.compile(r"^\s*[-*+] ", re.M),
    "md_numbered": re.compile(r"^\s*\d+[.)] ", re.M),
    "md_table": re.compile(r"^\s*\|", re.M),
    "md_quote": re.compile(r"^> ", re.M),
    "json_keys": re.compile(r'"[\w-]+"\s*:'),
    "path_home": re.compile(r"/home/"),
    "path_windows": re.compile(r"[A-Za-z]:\\"),
    "path_relative": re.compile(r"(?<![\w.])\./"),
    "path_tilde": re.compile(r"~/"),
}
_CHAR_CLASSES: dict[str, re.Pattern[str]] = {
    "upper": re.compile(r"[A-Z]"), "lower": re.compile(r"[a-z]"), "digit": re.compile(r"[0-9]"),
    "punct": re.compile(r"[^\w\s]"), "non_ascii": re.compile(r"[^\x00-\x7f]"),
    "space": re.compile(r" "), "tab": re.compile(r"\t"), "newline": re.compile(r"\n"),
}


def text_format_features(text: str) -> dict[str, float]:
    """Formatting-only features of one text: whitespace, line endings, character classes, markdown, JSON, paths, length."""
    n = max(len(text), 1)
    lines = text.count("\n") + 1
    crlf = text.count("\r\n")
    out = {f"char_{k}": len(p.findall(text)) / n for k, p in _CHAR_CLASSES.items()}
    out.update({k: len(p.findall(text)) / lines for k, p in _PATTERNS.items()})
    out.update({
        "crlf_lines": crlf / lines,
        "cr_only": (text.count("\r") - crlf) / n,
        "ends_newline": float(text.endswith("\n")),
        "md_fence": text.count("```") / lines,
        "md_bold": text.count("**") / lines,
        "inline_code": text.count("`") / n,
        "json_start": float(text.lstrip()[:1] in ("{", "[")),
        "path_repo": text.count("<REPO>") / lines,
        "len_log": math.log1p(len(text)),
        "len_lt_100": float(len(text) < 100),
        "len_lt_1000": float(len(text) < 1000),
        "len_at_cap": float(len(text) >= CFG["normalize"]["max_tool_chars"]),
        "lines_log": math.log1p(lines),
    })
    return out


_FORMAT_ROLES = {"user": "user", "tool_result": "tool"}
_FORMAT_NAMES = sorted(text_format_features("x"))


def session_format_features(session: dict[str, Any]) -> dict[str, float]:
    """Mean text format features over the non-empty user and tool-result messages of one session."""
    out: dict[str, float] = {}
    for role, prefix in _FORMAT_ROLES.items():
        texts = [m.get("text") or "" for m in session["messages"] if m["role"] == role]
        rows = [text_format_features(t) for t in texts if t.strip()]
        out[f"{prefix}_empty_frac"] = (len(texts) - len(rows)) / len(texts) if texts else 0.0
        out[f"{prefix}_count_log"] = math.log1p(len(texts))
        for name in _FORMAT_NAMES:
            out[f"{prefix}_{name}"] = float(np.mean([r[name] for r in rows])) if rows else 0.0
    return out


def fit_probe(x_train: Any, y_train: Sequence[int], x_eval: Any, y_eval: Sequence[int],
              make_model: Callable[[], Any]) -> tuple[Any, float | None]:
    """Fit a source probe on the train split and return it with the AUC on the evaluation split."""
    model = make_model()
    model.fit(x_train, list(y_train))
    return model, _auc(list(y_eval), list(model.predict_proba(x_eval)[:, 1]))


def top_coefficients(names: Sequence[str], coef: np.ndarray, k: int) -> list[dict[str, Any]]:
    """The k largest coefficients by absolute value. Positive means 'looks like openswe'."""
    order = np.argsort(-np.abs(coef))[:k]
    return [{"name": names[i], "coef": round(float(coef[i]), 4)} for i in order]


def format_probe(train: Sequence[dict[str, Any]], test: Sequence[dict[str, Any]]) -> dict[str, Any]:
    names = sorted(session_format_features(train[0]))
    def matrix(sessions: Sequence[dict[str, Any]]) -> np.ndarray:
        rows = [session_format_features(s) for s in sessions]
        return np.array([[r[n] for n in names] for r in rows])
    model, auc = fit_probe(matrix(train), _labels(train), matrix(test), _labels(test), lambda: make_pipeline(
        StandardScaler(), LogisticRegression(C=KCFG["probe_C"], max_iter=3000)))
    target = KCFG["format_target_auc"]
    return {"auc_holdout": auc, "target_below": target, "pass": auc is not None and auc < target,
            "n_train": len(train), "n_holdout": len(test), "n_features": len(names),
            "top_features": top_coefficients(names, model[-1].coef_[0], KCFG["top_features"]),
            "note": "Fit on train, AUC on holdout. Label 1 = openswe. Formatting features of user and tool-result text only."}


# ---------------------------------------------------------------- control 3: assistant-prose probe

def assistant_prose(session: dict[str, Any]) -> str:
    text = "\n".join(m.get("text") or "" for m in session["messages"] if m["role"] == "assistant")
    return text[: KCFG["prose_max_chars"]]


def prose_probe(train: Sequence[dict[str, Any]], test: Sequence[dict[str, Any]]) -> dict[str, Any]:
    _, auc = fit_probe([assistant_prose(s) for s in train], _labels(train),
                       [assistant_prose(s) for s in test], _labels(test), lambda: make_pipeline(
                           TfidfVectorizer(ngram_range=(1, 2), min_df=2, sublinear_tf=True,
                                           max_features=KCFG["prose_max_features"]),
                           LogisticRegression(C=KCFG["probe_C"], max_iter=3000)))
    return {"auc_holdout": auc, "n_train": len(train), "n_holdout": len(test),
            "note": "TF-IDF word 1-2 grams on assistant prose. A high AUC shows why detectors do not read assistant prose."}


# ---------------------------------------------------------------- account tables (controls 4-6)

@dataclass
class Table:
    """Account feature rows with labels (1 = attack) and persona names."""

    ids: list[str]
    rows: list[dict[str, float]]
    y: list[int]
    personas: list[str]
    states: list[str | None]
    swechat: list[float | None]      # share of SWE-chat sessions of the account (evaluation label)

    @classmethod
    def from_json(cls, data: dict[str, Any]) -> "Table":
        ids = sorted(data["features"])
        lab = data["labels"]
        return cls(ids=ids, rows=[data["features"][a] for a in ids],
                   y=[int(lab[a]["role"] == "attack") for a in ids], personas=[lab[a]["persona"] for a in ids],
                   states=[lab[a].get("state") for a in ids], swechat=[lab[a].get("swechat_frac") for a in ids])

    def __add__(self, other: "Table") -> "Table":
        return Table(self.ids + other.ids, self.rows + other.rows, self.y + other.y,
                     self.personas + other.personas, self.states + other.states, self.swechat + other.swechat)


def load_table(name: str, features_dir: Path = FEATURES) -> Table | None:
    file = features_dir / f"{name}.json"
    return Table.from_json(json.loads(file.read_text())) if file.exists() else None


def holdout_table(xharness: bool, seed: int, models_dir: Path, data_dir: Path | None,
                  name: str | None = None, **scenario_args: Any) -> Table:
    """Run the holdout scenario offline with scoring. Cache the features (real data only) with the final state.

    `scenario_args` go to `build_scenario` (`counts`, `hydra_tasks`). `name` is the cache file name.
    """
    scen = build_scenario("holdout", seed, CFG["eval"]["scale"], xharness=xharness, data_dir=data_dir, **scenario_args)
    worker = Worker(models_dir=models_dir, score=True)
    run_offline(scen, worker)
    keep = [a for a, acct in worker.accounts.items()
            if a in scen.labels and acct.n_requests >= CFG["account"]["min_requests"]]
    data = {"features": {a: dict(worker.accounts[a].features) for a in keep},
            "labels": {a: {**scen.labels[a], "state": worker.accounts[a].state} for a in keep}}
    if data_dir is None:
        save_features(name or f"{'xharness' if xharness else 'holdout'}_{seed}", data["features"], data["labels"])
    return Table.from_json(data)


def recall_by_persona(model: am.LogitModel, table: Table) -> dict[str, dict[str, Any]]:
    """Per attack persona: share of accounts at block and at review (score bands of the saved thresholds) and in worker state block."""
    scores = model.predict_many(table.rows)
    block, review = model.thresholds["block"], model.thresholds["review"]
    out: dict[str, dict[str, Any]] = {}
    for persona in sorted({p for p, y in zip(table.personas, table.y) if y}):
        idx = [i for i, p in enumerate(table.personas) if p == persona]
        states = [table.states[i] for i in idx if table.states[i] is not None]
        out[persona] = {
            "accounts": len(idx),
            "recall_block": round(float(np.mean([scores[i] >= block for i in idx])), 4),
            "share_review_only": round(float(np.mean([review <= scores[i] < block for i in idx])), 4),
            "worker_state_block": round(float(np.mean([s == "block" for s in states])), 4) if states else None,
            "worker_state_review": round(float(np.mean([s == "review" for s in states])), 4) if states else None,
        }
    return out


def sessions_matched(seed: int, models_dir: Path, data_dir: Path | None) -> dict[str, Table]:
    """Two scenario pairs with the same sessions per hydra account (diagnosis D-37).

    A: the other harness with fewer hydra accounts, so each account gets as many sessions as in the main holdout.
    B: the main data with the hydra task pool cut to the size of the other-harness slice (200 accounts).
    """
    n_tasks = len({s.task_key for s in load_assets("holdout", data_dir, True).xharness})
    mean_sessions = float(np.mean(CFG["sim"]["personas"]["hydra"]["sessions_per_account"]))
    n_accounts = max(CFG["campaign"]["min_size"], round(n_tasks / mean_sessions))
    return {"xharness_matched": holdout_table(True, seed, models_dir, data_dir, f"xharness_matched_{seed}",
                                              counts={"hydra": n_accounts}),
            "main_small_pool": holdout_table(False, seed, models_dir, data_dir, f"main_small_pool_{seed}",
                                             hydra_tasks=n_tasks)}


def cross_harness(model: am.LogitModel, normal: Table, xharness: Table,
                  matched: dict[str, Table] | None = None) -> dict[str, Any]:
    out = _cross_harness(model, normal, xharness)
    if matched:
        out["option_a"] = {"main": out["normal"], "xharness": recall_by_persona(model, matched["xharness_matched"]),
                           "note": "Other harness with fewer hydra accounts: same sessions per account as the main holdout."}
        out["option_b"] = {"main": recall_by_persona(model, matched["main_small_pool"]), "xharness": out["xharness"],
                           "note": "Main data with the hydra task pool cut to the other-harness slice size: "
                                   "same sessions per account as the other harness."}
    return out


def _cross_harness(model: am.LogitModel, normal: Table, xharness: Table) -> dict[str, Any]:
    return {"normal": recall_by_persona(model, normal), "xharness": recall_by_persona(model, xharness),
            "holdout_auc": {"normal": _auc(normal.y, model.predict_many(normal.rows)),
                            "xharness": _auc(xharness.y, model.predict_many(xharness.rows))},
            "thresholds": model.thresholds,
            "note": "Saved model and validation thresholds. In 'xharness' the hydra persona replays the sweagent slice. "
                    "recall_block uses the account score only; worker_state_* include campaign propagation."}


# ---------------------------------------------------------------- control 7: source reliance

def source_reliance(model: am.LogitModel, table: Table) -> dict[str, Any]:
    """Does the account score follow the data source when the label is the same? (format probe protection)

    Normal accounts: Open-SWE based (benchmark runners) against SWE-chat based (solo developers, SaaS app).
    An AUC near 0.5 means that the score does not follow the source. Stealth accounts: Spearman correlation
    of the score with the share of SWE-chat sessions. A value near 0 means that the score does not follow the source.
    """
    from scipy.stats import spearmanr
    scores = model.predict_many(table.rows)
    block = model.thresholds["block"]
    normal = [i for i, y in enumerate(table.y) if not y and table.swechat[i] is not None]
    ose = [i for i in normal if table.swechat[i] < 0.5]
    swe = [i for i in normal if table.swechat[i] >= 0.5]
    group = lambda idx: {"accounts": len(idx), "mean_score": round(float(np.mean([scores[i] for i in idx])), 4) if idx else None,  # noqa: E731
                         "blocked": int(sum(scores[i] >= block for i in idx))}
    auc = _auc([1] * len(ose) + [0] * len(swe), [scores[i] for i in ose + swe]) if ose and swe else None
    stealth = [i for i, p in enumerate(table.personas) if p == "stealth" and table.swechat[i] is not None]
    rho = spearmanr([table.swechat[i] for i in stealth], [scores[i] for i in stealth]) if len(stealth) > 2 else None
    return {"normal_openswe": group(ose), "normal_swechat": group(swe),
            "normal_auc_openswe_vs_swechat": None if auc is None else round(auc, 4),
            "stealth_accounts": len(stealth),
            "stealth_spearman_swechat_share_vs_score": None if rho is None else round(float(rho.statistic), 4),
            "stealth_spearman_p": None if rho is None else round(float(rho.pvalue), 4),
            "note": "Saved model and validation thresholds on the holdout table. AUC near 0.5 and correlation near 0: "
                    "the score does not follow the data source within one label."}


# ---------------------------------------------------------------- control 5: label permutation

def permutation_test(train: Table, test: Table, runs: int, seed: int) -> dict[str, Any]:
    """Retrain the account model on shuffled train labels. The holdout AUC must be near 0.5."""
    rng = np.random.default_rng(seed)
    names = am.feature_names(train.rows)
    aucs = []
    for _ in range(runs):
        model = am.fit(train.rows, list(rng.permutation(train.y)), names=names)
        aucs.append(_auc(test.y, model.predict_many(test.rows)))
    valid = [a for a in aucs if a is not None]
    true_auc = _auc(test.y, am.fit(train.rows, train.y, names=names).predict_many(test.rows))
    return {"runs": runs, "holdout_aucs": [round(a, 4) for a in valid],
            "mean_auc": round(float(np.mean(valid)), 4) if valid else None,
            "std_auc": round(float(np.std(valid)), 4) if valid else None,
            "true_label_auc": _round(true_auc),
            "p_value": round((1 + sum(a >= true_auc for a in valid)) / (1 + len(valid)), 4) if true_auc else None,
            "note": "Account model retrained on train tables with shuffled labels; AUC on the holdout table. "
                    "One shuffle gives a random weight direction, so one run can be far from 0.5 when the "
                    "features separate the classes. With unequal classes, outlier accounts get the majority "
                    "label more often, which can move the mean away from 0.5 in either direction. "
                    "p_value = share of shuffled runs with AUC >= the true-label AUC."}


# ---------------------------------------------------------------- control 6: feature-family ablation

def evaluate_subset(names: list[str], train: Table, val: Table, test: Table) -> dict[str, Any]:
    """Retrain on a feature subset, choose thresholds on validation, evaluate on holdout."""
    if not names:
        return {"n_features": 0, "holdout_auc": None}
    model = am.fit(train.rows, train.y, names=names)
    thresholds = am.set_thresholds(model, val.rows, val.y)
    scores = model.predict_many(test.rows)
    pos = [s >= thresholds["block"] for s, y in zip(scores, test.y) if y]
    neg = [s >= thresholds["block"] for s, y in zip(scores, test.y) if not y]
    return {"n_features": len(names), "holdout_auc": _round(_auc(test.y, scores)),
            "block_threshold": round(thresholds["block"], 4),
            "holdout_recall_at_block": round(float(np.mean(pos)), 4) if pos else None,
            "holdout_fpr_at_block": round(float(np.mean(neg)), 4) if neg else None}


def ablation(train: Table, val: Table, test: Table) -> dict[str, Any]:
    names = am.feature_names(train.rows)
    return {"variants": {k: evaluate_subset(v, train, val, test) for k, v in ablation_subsets(names).items()},
            "feature_origin": {n: {"origin": feature_origin(n), "spec_real_data": spec_real_data(n),
                                   "why": FEATURE_ORIGIN.get(n, ("", "not in the mapping"))[1]} for n in names},
            "note": "Retrained on train tables, thresholds on validation, metrics on holdout. "
                    "real_data = real session structure; synthetic_text = LLM or hand-written assets; "
                    "simulator = persona parameters, timing, and volume."}


def plot_ablation(variants: dict[str, dict[str, Any]], out: Path) -> None:
    """Horizontal bars: holdout AUC per ablation variant, with the chance line at 0.5."""
    items = [(k, v["holdout_auc"]) for k, v in variants.items() if v.get("holdout_auc") is not None]
    fig, ax = plt.subplots(figsize=(7, 0.4 * len(items) + 1.2))
    labels, values = [k for k, _ in items][::-1], [v for _, v in items][::-1]
    ax.barh(labels, values, color="#2a78d6", height=0.6)
    for i, v in enumerate(values):
        ax.text(v + 0.005, i, f"{v:.3f}", va="center", fontsize=8, color="#0b0b0b")
    ax.axvline(0.5, color="#52514e", linestyle="--", linewidth=1)
    ax.set_xlim(0.0, 1.08)
    ax.set_xlabel("Holdout account AUC (retrained; dashed line = chance)")
    ax.set_title("Feature-family ablation", fontsize=11)
    for side in ("top", "right"):
        ax.spines[side].set_visible(False)
    fig.tight_layout()
    fig.savefig(out, dpi=130)
    plt.close(fig)


# ---------------------------------------------------------------- orchestration

def _round(x: float | None) -> float | None:
    return round(x, 4) if x is not None else None


def _skipped(reason: str) -> dict[str, Any]:
    return {"status": "skipped", "reason": reason}


def probes(data_dir: Path) -> dict[str, Any]:
    splits = data_dir / "splits"
    if not (splits / "train.jsonl").exists() or not (splits / "holdout.jsonl").exists():
        return {"format_probe": _skipped("splits missing"), "assistant_prose_probe": _skipped("splits missing")}
    n, seed = KCFG["probe_sessions_per_source"], KCFG["seed"]
    train, test = sample_split(splits / "train.jsonl", n, seed), sample_split(splits / "holdout.jsonl", n, seed)
    return {"format_probe": format_probe(train, test), "assistant_prose_probe": prose_probe(train, test)}


def train_tables(features_dir: Path) -> tuple[Table | None, Table | None]:
    parts = [load_table(f"train_{s}", features_dir) for s in CFG["account"]["train_seeds"]]
    train = None if any(p is None for p in parts) else sum(parts[1:], parts[0])  # type: ignore[arg-type]
    return train, load_table(f"val_{CFG['account']['val_seed']}", features_dir)


def model_controls(data_dir: Path | None, models_dir: Path, features_dir: Path) -> dict[str, Any]:
    """Controls 4 to 6. They need the saved account model and the cached train and validation tables."""
    model = am.load(models_dir / am.ACCOUNT_MODEL_FILE)
    if model is None:
        reason = "models/account_model.pkl missing: run dw.train.train_all first"
        return {"cross_harness": _skipped(reason), "label_permutation": _skipped(reason),
                "feature_ablation": _skipped(reason), "source_reliance": _skipped(reason)}
    seed = CFG["account"]["holdout_seed"]
    embed.load_cache(EMB_CACHE)
    cached = load_table(f"holdout_{seed}", features_dir) if data_dir is None else None
    normal = cached or holdout_table(False, seed, models_dir, data_dir)
    xharness = holdout_table(True, seed, models_dir, data_dir)
    matched = sessions_matched(seed, models_dir, data_dir)
    out: dict[str, Any] = {"cross_harness": cross_harness(model, normal, xharness, matched),
                           "source_reliance": source_reliance(model, normal)}
    train, val = train_tables(features_dir)
    if train is None or val is None:
        reason = f"cached train or val feature tables missing in {features_dir}"
        out.update(label_permutation=_skipped(reason), feature_ablation=_skipped(reason))
        return out
    out["label_permutation"] = permutation_test(train, normal, KCFG["permutation_runs"], KCFG["seed"])
    out["feature_ablation"] = ablation(train, val, normal)
    return out


def run_all(data_dir: Path | None = None, models_dir: Path = MODELS, features_dir: Path = FEATURES,
            reports_dir: Path = REPORTS) -> dict[str, Any]:
    """Run all six controls. Write reports/controls.json and reports/controls_ablation.png. Return the report."""
    base = data_dir or path("data")
    report: dict[str, Any] = {"scaffold_denylist": denylist(base), **probes(base),
                              **model_controls(data_dir, models_dir, features_dir)}
    reports_dir.mkdir(parents=True, exist_ok=True)
    (reports_dir / "controls.json").write_text(json.dumps(report, indent=2))
    variants = report["feature_ablation"].get("variants")
    if variants:
        plot_ablation(variants, reports_dir / "controls_ablation.png")
    return report


def _summary(report: dict[str, Any]) -> Iterable[str]:
    yield f"denylist dirty records: {report['scaffold_denylist']['dirty_records_total']}"
    for key in ("format_probe", "assistant_prose_probe"):
        yield f"{key}: AUC {report[key].get('auc_holdout', report[key].get('reason'))}"
    ch = report["cross_harness"]
    if "normal" in ch:
        yield f"hydra recall normal {ch['normal'].get('hydra')} xharness {ch['xharness'].get('hydra')}"
    yield f"permutation mean AUC: {report['label_permutation'].get('mean_auc')}"


def main() -> None:
    import argparse
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--data-dir", type=Path, default=None, help="data folder with canonical/ and splits/ (tests)")
    parser.add_argument("--reports-dir", type=Path, default=REPORTS, help="output folder")
    args = parser.parse_args()
    for line in _summary(run_all(args.data_dir, reports_dir=args.reports_dir)):
        print(f"[controls] {line}", flush=True)


if __name__ == "__main__":
    main()
