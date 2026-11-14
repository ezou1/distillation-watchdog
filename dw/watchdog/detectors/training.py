"""Fit, evaluate, save, and load the small learned models of the detectors (D3 topics, D4 elicitation, D6 realism).

The functions take plain lists of texts and labels. `dw/train/train_all.py` calls them.
"""
from __future__ import annotations

import random
import re
from collections import Counter
from pathlib import Path
from typing import Any

import joblib
import numpy as np
from sklearn.cluster import KMeans
from sklearn.feature_extraction.text import ENGLISH_STOP_WORDS, TfidfVectorizer
from sklearn.linear_model import LogisticRegression
from sklearn.metrics import precision_recall_fscore_support, roc_auc_score
from sklearn.pipeline import FeatureUnion, Pipeline
from sklearn.preprocessing import FunctionTransformer

from dw.common.embed import embed
from dw.watchdog.detectors.common import DCFG

MODEL_FILES = {"d3": "d3_kmeans.pkl", "d4": "d4_elicit.pkl", "d6": "d6_realism.pkl"}


def fit_topics(first_prompts: list[str], k: int | None = None) -> KMeans:
    """Fit k-means (k = detectors.d3.kmeans_k) on the embeddings of first prompts."""
    n = DCFG["d3"].get("kmeans_fit_n", len(first_prompts))
    if len(first_prompts) > n:
        first_prompts = random.Random(DCFG["d3"]["seed"]).sample(first_prompts, n)
    k = min(k or DCFG["d3"]["kmeans_k"], len(first_prompts))
    return KMeans(n_clusters=k, n_init=4, random_state=DCFG["d3"]["seed"]).fit(embed(first_prompts))


def fit_elicitation(texts: list[str], labels: list[int]) -> Pipeline:
    """Fit word 1-2 gram + char 3-5 gram TF-IDF and logistic regression on user texts (1 = elicitation)."""
    features = FeatureUnion([
        ("word", TfidfVectorizer(analyzer="word", ngram_range=(1, 2), sublinear_tf=True, max_features=100_000)),
        ("char", TfidfVectorizer(analyzer="char_wb", ngram_range=(3, 5), sublinear_tf=True, max_features=200_000)),
    ])
    return _fit(features, texts, labels, DCFG["d4"]["lr_C"])


def fit_realism(texts: list[str], labels: list[int]) -> Pipeline:
    """Fit char 3-5 gram TF-IDF and logistic regression on tool outputs (1 = LLM-written, 0 = real)."""
    features = Pipeline([
        ("cut", FunctionTransformer(cut_tool_output)),
        ("tfidf", TfidfVectorizer(analyzer="char_wb", ngram_range=(3, 5), sublinear_tf=True, max_features=200_000)),
    ])
    return _fit(features, texts, labels, DCFG["d6"]["lr_C"])


def cut_tool_output(texts: list[str]) -> list[str]:
    """Keep the first detectors.d6.realism_chars characters of each tool output (training and inference)."""
    n = DCFG["d6"]["realism_chars"]
    return [t[:n] for t in texts]


def _fit(features: Any, texts: list[str], labels: list[int], c: float) -> Pipeline:
    model = Pipeline([("tfidf", features),
                      ("lr", LogisticRegression(C=c, max_iter=2000, class_weight="balanced"))])
    return model.fit(texts, labels)


def keyword_baseline(bank_a_texts: list[str], top: int | None = None) -> re.Pattern[str]:
    """Return a regex of the most frequent word n-grams (by document frequency) in the bank A texts.

    N-grams that contain only stop words, or that are part of a phrase already chosen, are skipped.
    """
    top = top or DCFG["d4"]["keyword_top"]
    lo, hi = DCFG["d4"]["keyword_ngrams"]
    df: Counter[str] = Counter()
    for text in bank_a_texts:
        words = re.findall(r"[a-z0-9']+", text.lower())
        grams = {" ".join(words[i:i + n]) for n in range(lo, hi + 1) for i in range(len(words) - n + 1)}
        df.update(g for g in grams if not set(g.split()) <= ENGLISH_STOP_WORDS)
    chosen: list[str] = []
    for gram, _ in sorted(df.items(), key=lambda kv: (-kv[1], -len(kv[0].split()), kv[0])):
        if not any(gram in c or c in gram for c in chosen):
            chosen.append(gram)
        if len(chosen) == top:
            break
    return re.compile(r"\b(?:" + "|".join(re.escape(c).replace(r"\ ", r"\s+") for c in chosen) + r")\b",
                      re.IGNORECASE)


def evaluate_classifier(model: Any, texts: list[str], labels: list[int], threshold: float = 0.5) -> dict[str, float]:
    """Return AUC, precision, recall, F1 and false positive rate of a text classifier."""
    scores = model.predict_proba(texts)[:, 1]
    return _metrics(labels, scores >= threshold) | {"auc": float(roc_auc_score(labels, scores))}


def evaluate_keyword(pattern: re.Pattern[str], texts: list[str], labels: list[int]) -> dict[str, float]:
    """Return precision, recall, F1 and false positive rate of the keyword regex."""
    return _metrics(labels, np.array([bool(pattern.search(t)) for t in texts]))


def _metrics(labels: list[int], pred: np.ndarray) -> dict[str, float]:
    y = np.asarray(labels)
    p, r, f, _ = precision_recall_fscore_support(y, pred, average="binary", zero_division=0)
    negatives = max(int((y == 0).sum()), 1)
    return {"precision": float(p), "recall": float(r), "f1": float(f),
            "fpr": float((pred & (y == 0)).sum() / negatives), "n": float(len(y))}


def save_model(model: Any, models_dir: Path, key: str) -> Path:
    """Write a model to models_dir with the file name of MODEL_FILES[key]."""
    models_dir.mkdir(parents=True, exist_ok=True)
    path = models_dir / MODEL_FILES[key]
    joblib.dump(model, path)
    return path


def load_model(models_dir: Path | None, key: str) -> Any | None:
    """Load a model, or return None if models_dir is None or the file does not exist."""
    if models_dir is None or not (models_dir / MODEL_FILES[key]).exists():
        return None
    return joblib.load(models_dir / MODEL_FILES[key])
