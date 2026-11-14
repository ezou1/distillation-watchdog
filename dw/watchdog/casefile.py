"""Case files (section B8.8): an LLM summary of detector evidence, with a citation validator.

Only detector summaries and numbers go to the helper model, never raw user text.
If the validator rejects the text two times, the case file has status "fallback" and shows only the evidence list.
"""
from __future__ import annotations

import json
import logging
import re
from typing import Any, Optional, Protocol

from dw.common.config import CFG
from dw.common.schema import Evidence

log = logging.getLogger("dw.casefile")

_BRACKET = re.compile(r"\[([^\[\]]+)\]")
_ID = re.compile(r"^[a-z]+-[\w.\-]+$")
_LIST_ITEM = re.compile(r"^\s*(?:[-*+]|\d+[.)])\s+")

SYSTEM = (
    "You write short case files for fraud investigators at an AI model provider. "
    "You get detector findings about one account or one group of accounts. "
    "Write Markdown with these sections: ## Findings, ## Evidence, ## Confidence, ## Open questions, "
    "## Recommended action. Use short sentences. "
    "Every paragraph and every list item must cite one or more item IDs in square brackets, for example [ev-0000412]. "
    "Cite only IDs from the input. Do not invent numbers."
)


class ChatClient(Protocol):
    async def chat(self, messages: list[dict[str, Any]], **params: Any) -> dict[str, Any]: ...


def paragraphs(text: str) -> list[str]:
    """Blocks separated by blank lines. Each list item is a separate block. Headings are left out."""
    blocks: list[str] = []
    for block in re.split(r"\n\s*\n", text.strip()):
        current: list[str] = []
        for line in block.splitlines():
            if not line.strip() or line.lstrip().startswith("#"):
                continue
            if _LIST_ITEM.match(line) and current:
                blocks.append(" ".join(current))
                current = []
            current.append(line.strip())
        if current:
            blocks.append(" ".join(current))
    return blocks


def citations(text: str) -> list[str]:
    """All IDs cited in square brackets, in order, without repeats."""
    found: list[str] = []
    for group in _BRACKET.findall(text):
        for token in re.split(r"[,;\s]+", group.strip()):
            if _ID.match(token) and token not in found:
                found.append(token)
    return found


def validate(text: str, known_ids: set[str]) -> tuple[bool, list[str], str]:
    """Check a case file. Return (ok, cited IDs, reason).

    It fails if the text is empty, cites an unknown ID, or has a paragraph without a citation.
    """
    blocks = paragraphs(text)
    if not blocks:
        return False, [], "empty text"
    cited = citations(text)
    unknown = [c for c in cited if c not in known_ids]
    if unknown:
        return False, cited, f"unknown IDs: {', '.join(unknown)}"
    for block in blocks:
        if not citations(block):
            return False, cited, f"paragraph without citation: {block[:80]}"
    return True, cited, "ok"


def contribution_items(contributions: dict[str, float]) -> list[dict[str, Any]]:
    """Top feature contributions as citable items with IDs `fx-<feature>`."""
    top = sorted(contributions.items(), key=lambda p: -abs(p[1]))[:CFG["casefile"]["top_contributions"]]
    return [{"id": f"fx-{name}", "summary": f"Feature {name} adds {value:+.2f} to the log-odds."}
            for name, value in top]


def top_evidence(evidence: list[Evidence]) -> list[Evidence]:
    """The heaviest evidence items (casefile.top_evidence), newest first among equals."""
    ranked = sorted(evidence, key=lambda e: (-e.weight, -e.sim_time))
    return ranked[:CFG["casefile"]["top_evidence"]]


def build_prompt(subject: dict[str, Any], items: list[dict[str, Any]], template: Optional[str]) -> list[dict[str, str]]:
    """Chat messages for the helper model. Inputs are detector summaries and numbers only."""
    payload = {"subject": subject, "items": items}
    if template:
        payload["template_inversion"] = template
    return [{"role": "system", "content": SYSTEM},
            {"role": "user", "content": json.dumps(payload, indent=1)}]


def _text_of(resp: dict[str, Any]) -> str:
    return str(resp["choices"][0]["message"].get("content") or "")


async def write_casefile(case_id: str, subject: dict[str, Any], evidence: list[Evidence],
                         contributions: dict[str, float], template: Optional[str],
                         client: ChatClient) -> dict[str, Any]:
    """Ask the helper model for a case file. Validate it, retry once, else return the fallback."""
    ev_items = [{"id": e.id, "detector": e.detector, "summary": e.summary, "weight": e.weight}
                for e in top_evidence(evidence)]
    items = ev_items + contribution_items(contributions)
    known = {i["id"] for i in items}
    messages = build_prompt(subject, items, template)
    result = {"id": case_id, "status": "fallback", "cached": False, "text": "", "cited": [],
              "evidence": [{"id": i["id"], "summary": i["summary"]} for i in items]}
    for _attempt in range(2):
        try:
            resp = await client.chat(messages, max_tokens=CFG["casefile"]["max_tokens"],
                                     temperature=CFG["casefile"]["temperature"])
        except Exception as exc:  # offline mode, network, or provider error: show the evidence list
            log.warning("case file %s: LLM call failed: %s", case_id, exc)
            return result
        text = _text_of(resp)
        ok, cited, reason = validate(text, known)
        if ok:
            result.update(status="ok", text=text, cited=cited)
            return result
        messages = messages + [{"role": "assistant", "content": text},
                               {"role": "user", "content": f"Rejected: {reason}. Write it again. "
                                                           "Cite only the given IDs, in every paragraph."}]
    return result
