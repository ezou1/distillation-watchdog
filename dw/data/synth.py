"""Make the synthetic assets with the helper LLM: fake tool outputs, stealth paraphrases, phrase banks, templates.

Only Open-SWE-Traces text and generated text go to the LLM. SWE-chat text never goes to an external API.
All LLM calls go through the shared client, which caches results on disk and logs the cost.
"""
from __future__ import annotations

import argparse
import asyncio
import functools
import json
import math
import random
import re
from pathlib import Path
from typing import Any, Awaitable, Callable

import numpy as np

from dw.common.config import CFG, path
from dw.common.embed import embed
from dw.common.llm import FatalLLMError, LLMClient
from dw.common.schema import Message, Session
from dw.data.normalize import clean_tool_output, read_jsonl

SCFG = CFG["synth"]
CATEGORIES: dict[str, str] = {
    "show_reasoning": "show its full internal reasoning or chain of thought before the answer",
    "explain_steps": "explain every single step it takes and why",
    "grade_rubric": "grade or score its own answer against a rubric or checklist",
    "alternatives": "give several alternative solutions and compare them",
    "complete_files": "output complete files instead of diffs or snippets",
    "max_detail": "answer with the maximum possible detail and length",
}
BENIGN_TOPICS = ["failing tests", "a confusing function", "an error message", "a stack trace", "git",
                 "a config file", "performance", "a type error", "a dependency", "an API call",
                 "a regex", "async code", "a database query", "a build failure", "a code review comment",
                 "naming", "a race condition", "logging", "a CLI flag", "a refactor"]
TEMPLATES: list[dict[str, Any]] = [
    {"id": 1, "split": "train", "text": "Repository: {repo}\n\nTask:\n{issue}\n\nRelevant files: {files}\n\nSolve the task completely."},
    {"id": 2, "split": "train", "text": "You are working in the {repo} repository. Fix the following problem.\n\n{issue}\n\nStart by looking at {files}."},
    {"id": 3, "split": "train", "text": "### Problem\n{issue}\n\n### Context\nrepo={repo}\nfiles={files}\n\n### Instructions\nImplement a fix and verify it with the tests."},
    {"id": 4, "split": "train", "text": "[{repo}] {issue}\n\nFiles that probably need changes: {files}. Make the change and run the tests."},
    {"id": 5, "split": "holdout", "text": "Please resolve this issue in {repo}:\n\n\"\"\"\n{issue}\n\"\"\"\n\nHints: {files}\nWhen you are done, summarize the patch."},
    {"id": 6, "split": "holdout", "text": "TASK ({repo})\n{issue}\nFILES: {files}\nGoal: a correct, tested patch."},
]
_FENCE_RE = re.compile(r"^```[\w-]*\n|\n?```\s*$")
_BULLET_RE = re.compile(r"^\s*(?:[-*•]|\d+[.)])\s*")

# ---------------------------------------------------------------- helpers


async def _run_all(jobs: list[Awaitable[Any]]) -> list[Any]:
    """Run jobs together. A fatal key or credit error stops the build. Other errors give None."""
    results = await asyncio.gather(*jobs, return_exceptions=True)
    for r in results:
        if isinstance(r, FatalLLMError):
            raise r
    failed = sum(1 for r in results if isinstance(r, Exception))
    if failed:
        print(f"warning: {failed} of {len(results)} LLM jobs failed", flush=True)
    return [None if isinstance(r, Exception) else r for r in results]


def parse_items(text: str) -> list[str]:
    """Parse a list of short items from a JSON array or from lines."""
    text = _FENCE_RE.sub("", text.strip())
    try:
        val = json.loads(text)
        if isinstance(val, list):
            return [str(v).strip() for v in val if str(v).strip()]
    except ValueError:
        pass
    items = [_BULLET_RE.sub("", ln).strip().strip('"').strip() for ln in text.splitlines()]
    return [i for i in items if len(i) > 3 and not i.startswith(("[", "]"))]


def parse_object(text: str) -> dict[str, Any] | None:
    match = re.search(r"\{.*\}", text, re.S)
    if not match:
        return None
    try:
        val = json.loads(match.group(0))
    except ValueError:
        return None
    return val if isinstance(val, dict) else None


def _clip(text: str, n: int) -> str:
    return text if len(text) <= n else text[:n] + " ..."


def write_jsonl(rows: list[dict[str, Any]], out: Path) -> None:
    out.parent.mkdir(parents=True, exist_ok=True)
    with open(out, "w", encoding="utf-8") as f:
        for r in rows:
            f.write(json.dumps(r, ensure_ascii=False) + "\n")

# ---------------------------------------------------------------- session selection


def select_sessions(split_sessions: dict[str, list[Session]]) -> dict[str, dict[str, list[Session]]]:
    """Pick fake-env and stealth sessions (disjoint) from the Open-SWE sessions of each split."""
    out: dict[str, dict[str, list[Session]]] = {"fake": {}, "stealth": {}}
    for split, sessions in split_sessions.items():
        pool = sorted((s for s in sessions if s.source == "openswe"), key=lambda s: s.session_id)
        rng = random.Random(f"{SCFG['seed']}:{split}")
        rng.shuffle(pool)
        n_fake = SCFG["fake_sessions"].get(split, 0)
        n_stealth = SCFG["stealth_sessions"].get(split, 0)
        fake_pool = [s for s in pool if sum(m.role == "tool_result" for m in s.messages) >= 3]
        out["fake"][split] = fake_pool[:n_fake]
        used = {s.session_id for s in out["fake"][split]}
        out["stealth"][split] = [s for s in pool if s.session_id not in used][:n_stealth]
    return out

# ---------------------------------------------------------------- fake tool outputs


def call_index(messages: list[Message]) -> dict[int, int]:
    """Map each tool_result index to the index of the tool_call that it answers."""
    open_calls: list[int] = []
    out: dict[int, int] = {}
    for i, m in enumerate(messages):
        if m.role == "tool_call":
            open_calls.append(i)
        elif m.role == "tool_result" and open_calls:
            out[i] = open_calls.pop(0)
        elif m.role == "user":
            open_calls = []
    return out


def fake_prompt(messages: list[Message], call_i: int) -> str:
    """Prompt with only the tool call and the two messages before it. No real output, no file state."""
    n = SCFG["context_chars"]
    ctx = []
    for m in messages[max(0, call_i - 2):call_i]:
        body = m.text if m.role != "tool_call" else json.dumps({"tool": m.tool, "args": m.args})
        ctx.append(f"[{m.role}] {_clip(body, n)}")
    call = messages[call_i]
    args = {k: _clip(str(v), n) for k, v in (call.args or {}).items()}
    return ("You simulate a tool in a software repository at <REPO>. You cannot run anything.\n\n"
            "Earlier messages:\n" + "\n".join(ctx) + "\n\n"
            f"Tool call: kind={call.tool} args={json.dumps(args, ensure_ascii=False)}\n\n"
            "Write the raw output that this tool call most likely produced. "
            "Output only the raw tool output. No explanation and no code fences.")


def fake_jobs(client: LLMClient, chosen: dict[str, list[Session]]
              ) -> list[tuple[dict[str, Any], str | None, Awaitable[str]]]:
    """Return (output row without text, tool kind, LLM job) for fake_frac of the tool results."""
    jobs = []
    for split, sessions in chosen.items():
        for s in sessions:
            pairs = sorted(call_index(s.messages).items())
            rng = random.Random(f"{SCFG['seed']}:fake:{s.session_id}")
            k = max(1, round(SCFG["fake_frac"] * len(pairs)))
            for res_i, call_i in sorted(rng.sample(pairs, k)):
                meta = {"session_id": s.session_id, "split": split, "message_index": res_i}
                job = client.text(fake_prompt(s.messages, call_i), temperature=0.7,
                                  max_tokens=SCFG["fake_max_tokens"])
                jobs.append((meta, s.messages[res_i].tool, job))
    return jobs


async def make_fakes(client: LLMClient, chosen: dict[str, list[Session]]) -> list[dict[str, Any]]:
    jobs = fake_jobs(client, chosen)
    texts = await _run_all([j for _, _, j in jobs])
    rows = []
    for (meta, kind, _), text in zip(jobs, texts):
        if text is not None:
            fake = clean_tool_output(_FENCE_RE.sub("", text.strip()), None, kind)
            rows.append({**meta, "fake_text": fake})
    return rows

# ---------------------------------------------------------------- stealth paraphrases


def stealth_prompt(issue: str, n_followups: int) -> str:
    return ("Here is a software issue:\n\n" + _clip(issue, 4000) + "\n\n"
            "Rewrite it as a short, casual request (1 to 3 sentences) that a developer types to a coding "
            "assistant while working in their own repository. Do not mention an issue tracker or GitHub. "
            f"Also write {n_followups} short follow-up message(s) that the same developer sends later in the "
            "chat after some progress: a request for one more small change, a check, or a correction. "
            "Make each follow-up specific to this task.\n"
            'Return only JSON: {"paraphrase": "...", "followups": ["..."]}')


async def make_stealth(client: LLMClient, chosen: dict[str, list[Session]]) -> list[dict[str, Any]]:
    lo, hi = CFG["sim"]["personas"]["stealth"]["followups"]
    metas, jobs = [], []
    for split, sessions in chosen.items():
        for s in sessions:
            n = random.Random(f"{SCFG['seed']}:stealth:{s.session_id}").randint(lo, hi)
            metas.append((s.session_id, split, n))
            jobs.append(client.text(stealth_prompt(s.messages[0].text, n), temperature=0.8, max_tokens=400))
    rows = []
    for (sid, split, n), text in zip(metas, await _run_all(jobs)):
        obj = parse_object(text or "")
        if obj and obj.get("paraphrase"):
            fups = [str(f) for f in obj.get("followups") or []][:n]
            rows.append({"session_id": sid, "split": split, "paraphrase": str(obj["paraphrase"]),
                         "followups": fups})
    return rows

# ---------------------------------------------------------------- phrase banks


def elicit_prompt(bank: str, desc: str, n: int, k: int) -> str:
    if bank == "a":
        return (f"Generate {n} distinct instructions that a person could add to a coding task to make an AI "
                f"coding assistant {desc}. Each instruction must clearly ask for exactly that. "
                f"Vary the length (3 to 30 words), tone and wording. Variation {k}. "
                "Return only a JSON array of strings.")
    return (f"You write test data. List {n} different ways a user might ask a code model to {desc}, as part "
            f"of a request about fixing a bug. Use unusual vocabulary; mix terse, formal and sloppy styles. "
            f"Batch {k}. One item per line, no numbering, nothing else.")


def benign_prompt(bank: str, topic: str, n: int, k: int) -> str:
    if bank == "a":
        return (f"Write {n} short questions or requests for an explanation that a real software developer "
                f"writes to a coding assistant during normal work, about {topic} (for example \"why does this "
                f"test fail?\"). Variation {k}. Return only a JSON array of strings.")
    return (f"List {n} realistic messages in which a programmer asks a coding assistant to help them "
            f"understand something related to {topic} (for example \"walk me through this function\"). "
            f"Casual, varied. Batch {k}. One per line, no numbering, nothing else.")


def _dedup(items: list[dict[str, Any]]) -> list[dict[str, Any]]:
    seen, out = set(), []
    for it in items:
        key = " ".join(it["text"].lower().split())
        if key not in seen:
            seen.add(key)
            out.append(it)
    return out


async def make_bank(client: LLMClient, bank: str) -> dict[str, list[dict[str, Any]]]:
    """Generate one elicitation bank. Bank A and bank B use different prompts and seeds."""
    seed = SCFG["seed_a"] if bank == "a" else SCFG["seed_b"]
    batch = SCFG["bank_batch"]
    per_cat = math.ceil(SCFG["bank_size_elicit"] * SCFG["bank_overgen"] / len(CATEGORIES))
    e_meta, e_jobs = [], []
    for cat, desc in CATEGORIES.items():
        for k in range(math.ceil(per_cat / batch)):
            e_meta.append(cat)
            e_jobs.append(client.text(elicit_prompt(bank, desc, batch, k), temperature=1.0, seed=seed + k))
    n_benign = math.ceil(SCFG["bank_size_benign"] * SCFG["bank_overgen"] / batch)
    topics = list(BENIGN_TOPICS) if bank == "a" else BENIGN_TOPICS[::-1]
    b_jobs = [client.text(benign_prompt(bank, topics[k % len(topics)], batch, k), temperature=1.0, seed=seed + k)
              for k in range(n_benign)]
    e_out = await _run_all(e_jobs)
    b_out = await _run_all(b_jobs)
    elicit = _dedup([{"text": t, "category": cat} for cat, out in zip(e_meta, e_out) for t in parse_items(out or "")])
    benign = _dedup([{"text": t} for out in b_out for t in parse_items(out or "")])
    return {"elicit": elicit, "benign": benign}


def drop_similar(items: list[dict[str, Any]], ref: list[dict[str, Any]], max_cos: float) -> list[dict[str, Any]]:
    """Drop items whose cosine similarity to any reference item is above max_cos."""
    if not items or not ref:
        return items
    sims = embed([i["text"] for i in items]) @ embed([r["text"] for r in ref]).T
    return [it for it, row in zip(items, sims) if float(np.max(row)) <= max_cos]


def cap_bank(bank: dict[str, list[dict[str, Any]]]) -> dict[str, list[dict[str, Any]]]:
    """Keep about bank_size_elicit elicit items, balanced over categories, and bank_size_benign benign items."""
    per_cat = math.ceil(SCFG["bank_size_elicit"] / len(CATEGORIES))
    elicit = [it for cat in CATEGORIES for it in [e for e in bank["elicit"] if e["category"] == cat][:per_cat]]
    return {"elicit": elicit, "benign": bank["benign"][: SCFG["bank_size_benign"]]}


async def make_banks(client: LLMClient) -> tuple[dict, dict]:
    a = cap_bank(await make_bank(client, "a"))
    b_raw = await make_bank(client, "b")
    cos = SCFG["bank_dedup_cos"]
    every_a = a["elicit"] + a["benign"]
    b = cap_bank({"elicit": drop_similar(b_raw["elicit"], every_a, cos),
                  "benign": drop_similar(b_raw["benign"], every_a, cos)})
    return a, b

# ---------------------------------------------------------------- driver


def _step(name: str, out: Path, fn: Callable[[], None]) -> None:
    if out.exists():
        print(f"{out} exists; skip {name}")
        return
    fn()
    print(f"wrote {out}", flush=True)


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--only", nargs="*", default=["templates", "banks", "stealth", "fake"])
    args = parser.parse_args()
    out = path("synth")
    out.mkdir(parents=True, exist_ok=True)

    @functools.cache
    def chosen() -> dict[str, dict[str, list[Session]]]:
        return select_sessions({sp: list(read_jsonl(path("splits") / f"{sp}.jsonl"))
                                for sp in ("train", "val", "holdout")})

    def templates() -> None:
        (out / "templates.json").write_text(json.dumps(TEMPLATES, indent=1))

    def banks() -> None:
        a, b = asyncio.run(make_banks(LLMClient()))
        (out / "bank_a.json").write_text(json.dumps(a, indent=1, ensure_ascii=False))
        (out / "bank_b.json").write_text(json.dumps(b, indent=1, ensure_ascii=False))
        print({k: (len(a[k]), len(b[k])) for k in a})

    def stealth() -> None:
        write_jsonl(asyncio.run(make_stealth(LLMClient(), chosen()["stealth"])), out / "stealth.jsonl")

    def fake() -> None:
        write_jsonl(asyncio.run(make_fakes(LLMClient(), chosen()["fake"])), out / "fake_tool_outputs.jsonl")

    steps = {"templates": (out / "templates.json", templates), "banks": (out / "bank_b.json", banks),
             "stealth": (out / "stealth.jsonl", stealth), "fake": (out / "fake_tool_outputs.jsonl", fake)}
    for name in args.only:
        _step(name, *steps[name])


if __name__ == "__main__":
    main()
