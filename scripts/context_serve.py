"""Scoring service of the context model. Runs on the GPU pod, not in the package.

It listens on 127.0.0.1 only. The watchdog machine reaches it through an SSH tunnel (scripts/context_tunnel.sh),
so the service is not open to the internet.

POST /score {"texts": [...]} -> {"probs": [...], "model": "<dir>"}   (probability of label 1 per text)
GET  /health -> {"ok": true, "model": "<dir>"}

Usage: python context_serve.py --model DIR [--port 9100] [--max-len 768]
"""
from __future__ import annotations

import argparse

import torch
import uvicorn
from fastapi import FastAPI
from pydantic import BaseModel
from transformers import AutoModelForSequenceClassification, AutoTokenizer


class ScoreRequest(BaseModel):
    texts: list[str]


def make_app(model_dir: str, max_len: int, batch: int) -> FastAPI:
    tok = AutoTokenizer.from_pretrained(model_dir)
    model = AutoModelForSequenceClassification.from_pretrained(model_dir, dtype=torch.bfloat16).cuda().eval()
    app = FastAPI()

    @app.get("/health")
    def health() -> dict:
        return {"ok": True, "model": model_dir}

    @app.post("/score")
    @torch.no_grad()
    def score(req: ScoreRequest) -> dict:
        probs: list[float] = []
        # sort by length so a batch has little padding; restore the order at the end
        order = sorted(range(len(req.texts)), key=lambda i: len(req.texts[i]))
        out = [0.0] * len(req.texts)
        for i in range(0, len(order), batch):
            idx = order[i:i + batch]
            enc = tok([req.texts[j] for j in idx], truncation=True, max_length=max_len, padding=True,
                      return_tensors="pt").to("cuda")
            p = torch.softmax(model(**enc).logits.float(), -1)[:, 1].tolist()
            for j, v in zip(idx, p):
                out[j] = v
        probs = out
        return {"probs": probs, "model": model_dir}

    return app


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--model", required=True)
    ap.add_argument("--port", type=int, default=9100)
    ap.add_argument("--max-len", type=int, default=768)
    ap.add_argument("--batch", type=int, default=64)
    args = ap.parse_args()
    uvicorn.run(make_app(args.model, args.max_len, args.batch), host="127.0.0.1", port=args.port, log_level="warning")


if __name__ == "__main__":
    main()
