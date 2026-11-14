"""Scrub secrets and personal data from text: e-mail addresses, API keys, and home paths."""
from __future__ import annotations

import re

_PATTERNS: list[tuple[re.Pattern[str], str]] = [
    (re.compile(r"[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}"), "<EMAIL>"),
    (re.compile(r"\bsk-[A-Za-z0-9_\-]{8,}"), "<KEY>"),
    (re.compile(r"\bghp_[A-Za-z0-9]{8,}"), "<KEY>"),
    (re.compile(r"\bgithub_pat_[A-Za-z0-9_]{8,}"), "<KEY>"),
    (re.compile(r"\bAKIA[A-Z0-9]{12,}"), "<KEY>"),
    (re.compile(r"\bhf_[A-Za-z0-9]{8,}"), "<KEY>"),
    (re.compile(r"\bxox[abposr]-[A-Za-z0-9\-]{8,}"), "<KEY>"),
    (re.compile(r"/Users/[^/\s\"'`]+"), "/home/<user>"),
    (re.compile(r"/home/(?!<user>)[^/\s\"'`]+"), "/home/<user>"),
    (re.compile(r"[A-Za-z]:\\\\?Users\\\\?[^\\\s\"'`]+"), "/home/<user>"),
]


def scrub(text: str) -> str:
    """Return text with secrets and personal paths replaced by placeholders."""
    if not text:
        return text
    for pattern, repl in _PATTERNS:
        text = pattern.sub(repl, text)
    return text
