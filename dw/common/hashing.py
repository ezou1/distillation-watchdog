"""Canonical message serialization and the prefix hash chain (section B8.2).

h_0 = H(tools_schema_id). h_i = H(h_{i-1} || H(message_i)). The key of a request is h_n.
"""
from __future__ import annotations

import hashlib
import json
from typing import Iterable

from dw.common.schema import Message

HASH_CHARS = 16


def _h(data: str) -> str:
    return hashlib.sha256(data.encode("utf-8")).hexdigest()[:HASH_CHARS]


def serialize(msg: Message) -> str:
    """Return a deterministic string for one canonical message."""
    return json.dumps(
        {"r": msg.role, "t": msg.text, "k": msg.tool, "a": msg.args},
        sort_keys=True, separators=(",", ":"), ensure_ascii=False,
    )


def message_hash(msg: Message) -> str:
    return _h(serialize(msg))


def prefix_chain(tools_schema_id: str, messages: Iterable[Message]) -> list[str]:
    """Return [h_0, h_1, ..., h_n]. The last item is the request key."""
    chain = [_h("schema:" + tools_schema_id)]
    for msg in messages:
        chain.append(_h(chain[-1] + message_hash(msg)))
    return chain


def text_hash(text: str) -> str:
    """Hash of a normalized text (used for first-user-message task overlap)."""
    return _h(" ".join(text.split()).lower())


def salted_id(raw: str, salt: str) -> str:
    """Salted SHA-256 of an identifier (SWE-chat user IDs, repo IDs)."""
    return hashlib.sha256((salt + "|" + raw).encode("utf-8")).hexdigest()


def pseudonym(account_id: str, salt: str) -> str:
    """Display name for an account. The same scheme for every account."""
    return "acct-" + salted_id(account_id, salt)[:6]
