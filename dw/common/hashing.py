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


_MEMO: dict[tuple, str] = {}
_MEMO_MAX = 200_000


_BY_ID: dict[int, tuple[Message, str]] = {}


def message_hash(msg: Message, memo: bool = False) -> str:
    """Hash of serialize(msg). A bounded memo skips the JSON encoding of repeated messages (same result).

    With `memo=True` (offline replay only), a first memo uses the object identity: `from_openai` gives the
    same Message object again. It keeps a reference, so the identity cannot be used again by a new object.
    """
    hit = _BY_ID.get(id(msg)) if memo else None
    if hit is not None and hit[0] is msg:
        return hit[1]
    key = (msg.role, msg.tool, msg.text, json.dumps(msg.args, sort_keys=True) if msg.args else None)
    h = _MEMO.get(key)
    if h is None:
        if len(_MEMO) >= _MEMO_MAX:
            _MEMO.clear()
        h = _MEMO[key] = _h(serialize(msg))
    if memo:
        if len(_BY_ID) >= _MEMO_MAX:
            _BY_ID.clear()
        _BY_ID[id(msg)] = (msg, h)
    return h


def prefix_chain(tools_schema_id: str, messages: Iterable[Message], memo: bool = False) -> list[str]:
    """Return [h_0, h_1, ..., h_n]. The last item is the request key. `memo`: see `message_hash`."""
    chain = [_h("schema:" + tools_schema_id)]
    for msg in messages:
        chain.append(_h(chain[-1] + message_hash(msg, memo)))
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
