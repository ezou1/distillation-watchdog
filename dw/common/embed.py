"""One text embedding function (fastembed, BAAI/bge-small-en-v1.5, CPU) with an in-memory and disk cache."""
from __future__ import annotations

import hashlib
import threading
from functools import lru_cache
from pathlib import Path

import numpy as np

from dw.common.config import CFG, ROOT

MODEL_NAME = "BAAI/bge-small-en-v1.5"
DIM = 384
_lock = threading.Lock()
_mem: dict[str, np.ndarray] = {}


@lru_cache(maxsize=1)
def _model():  # type: ignore[no-untyped-def]
    from fastembed import TextEmbedding
    return TextEmbedding(MODEL_NAME, cache_dir=str(ROOT / CFG["paths"]["cache"] / "fastembed"),
                         threads=CFG["detectors"].get("embed_threads"))


def _key(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()[:24]


def embed(texts: list[str], max_chars: int | None = None) -> np.ndarray:
    """Return L2-normalized embeddings, shape (len(texts), 384). Repeated texts use the cache."""
    max_chars = max_chars or CFG["detectors"]["embed_max_chars"]
    texts = [t[:max_chars] for t in texts]
    keys = [_key(t) for t in texts]
    with _lock:
        missing = [(k, t) for k, t in zip(keys, texts) if k not in _mem]
        if missing:
            uniq = dict(missing)
            vecs = np.array(list(_model().embed(list(uniq.values()), batch_size=CFG["detectors"]["embed_batch"])), dtype=np.float32)
            vecs /= np.linalg.norm(vecs, axis=1, keepdims=True) + 1e-9
            for k, v in zip(uniq.keys(), vecs):
                _mem[k] = v
        return np.stack([_mem[k] for k in keys]) if keys else np.zeros((0, DIM), np.float32)


def save_cache(path: Path) -> None:
    """Write the in-memory cache to disk (npz)."""
    path.parent.mkdir(parents=True, exist_ok=True)
    with _lock:
        np.savez_compressed(path, keys=np.array(list(_mem.keys())), vecs=np.stack(list(_mem.values())) if _mem else np.zeros((0, DIM)))


def load_cache(path: Path) -> None:
    """Load a disk cache written by save_cache, if it exists."""
    if not path.exists():
        return
    data = np.load(path)
    with _lock:
        for k, v in zip(data["keys"], data["vecs"]):
            _mem[str(k)] = v
