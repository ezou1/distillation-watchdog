"""Shared data contracts: canonical messages and sessions, request events, evidence, account state.

Every component reads and writes these types. Change them only with a note in BUILD_LOG.md.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Literal, Optional

from pydantic import BaseModel, Field

Role = Literal["user", "assistant", "tool_call", "tool_result"]
ToolKind = Literal["read", "write", "edit", "shell", "search", "other"]
Source = Literal["swechat", "openswe", "openswe_xharness"]
AccountStateName = Literal["insufficient", "normal", "review", "block", "disclosed"]


class Message(BaseModel):
    """One canonical turn. Tool results carry the kind of the call they answer."""

    role: Role
    text: str = ""
    tool: Optional[ToolKind] = None
    args: Optional[dict[str, Any]] = None


class Session(BaseModel):
    """One canonical session from either dataset. `source` is for evaluation only."""

    session_id: str
    source: Source
    group_id: str
    repo_key: str
    task_key: Optional[str] = None
    messages: list[Message]
    resolved: Optional[int] = None
    real_times: Optional[list[float]] = None


class RequestEvent(BaseModel):
    """What the lab tells the watchdog about one request. It never holds `x_replay` content."""

    request_id: str
    account_id: str
    sim_time: float
    model: str = ""
    tools_schema_id: str = "none"
    temperature: float = 1.0
    n: int = 1
    max_tokens: Optional[int] = None
    reasoning_effort: Optional[str] = None
    user: Optional[str] = None
    messages: list[Message] = Field(default_factory=list)
    prefix_hashes: list[str] = Field(default_factory=list)   # h_0 .. h_n (section B8.2)
    key: str = ""                                             # h_n
    dup_count: int = 1           # times this account sent this key, this one included
    global_dup_count: int = 1    # times any account sent this key, this one included
    inline_flags: list[str] = Field(default_factory=list)
    status: int = 200
    finish_reason: Optional[str] = None   # "stop" | "tool_calls" | None (blocked)
    backend: Literal["replay", "live", "none"] = "none"
    latency_ms: float = 0.0
    declared_use: Optional[str] = None
    quota: Optional[int] = None


class Evidence(BaseModel):
    """One weak signal with a pointer to the exact request and message."""

    id: str
    detector: str
    account_id: str
    request_ids: list[str]
    message_index: Optional[int] = None
    summary: str
    weight: float = 1.0
    sim_time: float = 0.0


@dataclass
class ViewMessage:
    """A message as detectors see it. Assistant prose is always empty."""

    index: int
    role: Role
    text: str
    tool: Optional[str]
    args: Optional[dict[str, Any]]


@dataclass
class DetectorView:
    """The only view of a request that detectors get.

    It has no assistant prose, no `x_replay`, no `source`, and no label.
    `session_id`, `step` and `new_session` come from the prefix hash chain in the worker.
    """

    request_id: str
    account_id: str
    sim_time: float
    tools_schema_id: str
    temperature: float
    n: int
    max_tokens: Optional[int]
    reasoning_effort: Optional[str]
    user: Optional[str]
    messages: list[ViewMessage]
    prefix_hashes: list[str]
    key: str
    dup_count: int
    global_dup_count: int
    finish_reason: Optional[str]
    session_id: str
    step: int
    new_session: bool


@dataclass
class AccountState:
    """Live state of one account in the watchdog. Detectors keep private state in `scratch`."""

    account_id: str
    declared_use: Optional[str] = None
    quota: Optional[int] = None
    n_requests: int = 0
    n_blocked: int = 0
    first_seen: float = 0.0
    last_seen: float = 0.0
    keys: dict[str, str] = field(default_factory=dict)          # full key -> session_id
    sessions: dict[str, dict[str, Any]] = field(default_factory=dict)  # session_id -> stats
    scratch: dict[str, dict[str, Any]] = field(default_factory=dict)   # detector name -> state
    evidence: list[Evidence] = field(default_factory=list)
    features: dict[str, float] = field(default_factory=dict)
    contributions: dict[str, float] = field(default_factory=dict)
    score: Optional[float] = None                                 # learned model score
    rules_level: Optional[str] = None                             # rules score level: normal, review, block
    state: AccountStateName = "insufficient"
    campaign_id: Optional[str] = None
    score_history: list[tuple[float, float]] = field(default_factory=list)  # (sim_time, score)
    dirty: bool = True
