"""Fine-tune the context model (the text parts of D4 and D6) on a GPU. Runs on the GPU pod, not in the package.

One sequence classifier with two labels for two tasks; the first token of the text names the task
("[tool]" = realism, "[prompt]" = elicitation). Input: data/context/{train,val}.jsonl from dw/train/context_data.py.
Output: <out>/best (model and tokenizer) and <out>/metrics.json. The epoch with the best mean validation AUC is kept.

Usage: python context_train.py --data DIR --out DIR [--model Qwen/Qwen3-0.6B-Base] [--epochs 2]
"""
from __future__ import annotations

import argparse
import json
import math
import random
import time
from pathlib import Path

import numpy as np
import torch
from sklearn.metrics import roc_auc_score
from transformers import AutoModelForSequenceClassification, AutoTokenizer, get_linear_schedule_with_warmup


def load(path: Path) -> list[dict]:
    return [json.loads(line) for line in open(path, encoding="utf-8")]


def batches(rows: list[dict], size: int, shuffle: bool, seed: int = 0):
    idx = list(range(len(rows)))
    if shuffle:
        random.Random(seed).shuffle(idx)
    for i in range(0, len(idx), size):
        yield [rows[j] for j in idx[i:i + size]]


@torch.no_grad()
def predict(model, tok, rows: list[dict], max_len: int, size: int = 128) -> np.ndarray:
    model.eval()
    out = []
    for b in batches(rows, size, shuffle=False):
        enc = tok([r["text"] for r in b], truncation=True, max_length=max_len, padding=True, return_tensors="pt").to("cuda")
        with torch.autocast("cuda", dtype=torch.bfloat16):
            logits = model(**enc).logits.float()
        out.append(torch.softmax(logits, -1)[:, 1].cpu().numpy())
    model.train()
    return np.concatenate(out) if out else np.zeros(0)


def threshold_at_fpr(neg: np.ndarray, fpr: float) -> float:
    """Lowest threshold with at most `fpr` of the negatives at or above it."""
    s = np.sort(neg)[::-1]
    k = int(math.floor(fpr * len(s)))
    return float(np.nextafter(s[k], 2.0)) if k < len(s) else 0.0


def evaluate(model, tok, val: list[dict], check: list[dict], max_len: int) -> dict:
    p = predict(model, tok, val, max_len)
    out: dict = {}
    for task in ("realism", "elicitation"):
        idx = [i for i, r in enumerate(val) if r["task"] == task]
        y = np.array([val[i]["label"] for i in idx])
        s = p[idx]
        t1 = threshold_at_fpr(s[y == 0], 0.01)
        t5 = threshold_at_fpr(s[y == 0], 0.05)
        out[task] = {"n": len(idx), "auc": float(roc_auc_score(y, s)),
                     "recall_at_1pct_fpr": float((s[y == 1] >= t1).mean()), "threshold_1pct": t1,
                     "recall_at_5pct_fpr": float((s[y == 1] >= t5).mean()), "threshold_5pct": t5}
    if check:
        c = predict(model, tok, check, max_len)
        r = out["realism"]
        out["realism"]["swechat_flagged_at_1pct"] = float((c >= r["threshold_1pct"]).mean())
        out["realism"]["swechat_flagged_at_5pct"] = float((c >= r["threshold_5pct"]).mean())
        out["realism"]["swechat_mean_prob"] = float(c.mean())
    out["mean_auc"] = (out["realism"]["auc"] + out["elicitation"]["auc"]) / 2
    return out


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--data", type=Path, required=True)
    ap.add_argument("--out", type=Path, required=True)
    ap.add_argument("--model", default="Qwen/Qwen3-0.6B-Base")
    ap.add_argument("--epochs", type=int, default=2)
    ap.add_argument("--lr", type=float, default=2e-5)
    ap.add_argument("--batch", type=int, default=32)
    ap.add_argument("--max-len", type=int, default=512)
    ap.add_argument("--evals-per-epoch", type=int, default=2)
    ap.add_argument("--seed", type=int, default=13)
    args = ap.parse_args()
    torch.manual_seed(args.seed)
    train, val = load(args.data / "train.jsonl"), load(args.data / "val.jsonl")
    check_file = args.data / "val_swechat_check.jsonl"
    check = load(check_file) if check_file.exists() else []
    tok = AutoTokenizer.from_pretrained(args.model)
    if tok.pad_token is None:
        tok.pad_token = tok.eos_token
    model = AutoModelForSequenceClassification.from_pretrained(args.model, num_labels=2, torch_dtype=torch.float32)
    model.config.pad_token_id = tok.pad_token_id
    model.cuda().train()
    steps_per_epoch = math.ceil(len(train) / args.batch)
    total = steps_per_epoch * args.epochs
    opt = torch.optim.AdamW(model.parameters(), lr=args.lr, weight_decay=0.01)
    sched = get_linear_schedule_with_warmup(opt, int(0.05 * total), total)
    eval_every = max(1, steps_per_epoch // args.evals_per_epoch)
    history, best, step, t0 = [], -1.0, 0, time.time()
    args.out.mkdir(parents=True, exist_ok=True)
    for epoch in range(args.epochs):
        for b in batches(train, args.batch, shuffle=True, seed=args.seed + epoch):
            enc = tok([r["text"] for r in b], truncation=True, max_length=args.max_len, padding=True,
                      return_tensors="pt").to("cuda")
            labels = torch.tensor([r["label"] for r in b], device="cuda")
            with torch.autocast("cuda", dtype=torch.bfloat16):
                loss = torch.nn.functional.cross_entropy(model(**enc).logits.float(), labels)
            loss.backward()
            torch.nn.utils.clip_grad_norm_(model.parameters(), 1.0)
            opt.step()
            sched.step()
            opt.zero_grad(set_to_none=True)
            step += 1
            if step % 50 == 0:
                print(f"step {step}/{total} loss {loss.item():.4f} {time.time() - t0:.0f}s", flush=True)
            if step % eval_every == 0 or step == total:
                m = evaluate(model, tok, val, check, args.max_len)
                m.update(step=step, epoch=epoch + step / steps_per_epoch % 1, seconds=round(time.time() - t0))
                history.append(m)
                print(json.dumps(m), flush=True)
                if m["mean_auc"] > best:
                    best = m["mean_auc"]
                    model.save_pretrained(args.out / "best", safe_serialization=True)
                    tok.save_pretrained(args.out / "best")
    result = {"model": args.model, "args": {k: str(v) for k, v in vars(args).items()}, "train_n": len(train),
              "val_n": len(val), "best_mean_auc": best,
              "best": max(history, key=lambda h: h["mean_auc"]), "history": history}
    (args.out / "metrics.json").write_text(json.dumps(result, indent=1))
    print("done", json.dumps(result["best"]), flush=True)


if __name__ == "__main__":
    main()
