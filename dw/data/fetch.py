"""Fetch the raw data. Stream Open-SWE-Traces rows to parquet and read SWE-chat Claude Code turns with duckdb.

SWE-chat is already on disk. This module reads only the columns and sessions that we need.
"""
from __future__ import annotations

import argparse
import json
import os
from pathlib import Path
from typing import Any, Iterator

import pyarrow as pa
import pyarrow.parquet as pq

from dw.common.config import CFG, path

OPENSWE_DATASET = "nvidia/Open-SWE-Traces"
SWECHAT_TURN_TYPES = ("user_prompt", "assistant_response", "tool_use", "tool_result")
SWECHAT_COLUMNS = ("session_id", "user_id", "repo_id", "timestamp", "turn_number",
                   "content_block_index", "role", "turn_type", "content", "tool_name",
                   "tool_call_id", "tool_input_json")
_BATCH = 200
OPENSWE_SCHEMA = pa.schema([
    ("instance_id", pa.string()), ("repo", pa.string()), ("trajectory_id", pa.string()),
    ("messages", pa.string()), ("resolved", pa.int64()), ("teacher_model", pa.string()),
    ("enable_thinking", pa.bool_()), ("reasoning_effort", pa.string()),
])


def openswe_row(row: dict[str, Any]) -> dict[str, Any]:
    """Keep only the fields that we use. Store messages as one JSON string."""
    teacher = (row.get("metadata") or {}).get("teacher_model") or {}
    return {
        "instance_id": row.get("instance_id"),
        "repo": row.get("repo"),
        "trajectory_id": row.get("trajectory_id"),
        "messages": json.dumps(row.get("messages") or [], ensure_ascii=False),
        "resolved": None if row.get("resolved") is None else int(row["resolved"]),
        "teacher_model": teacher.get("name"),
        "enable_thinking": teacher.get("enable_thinking"),
        "reasoning_effort": teacher.get("reasoning_effort"),
    }


def _stream(split: str) -> Iterator[dict[str, Any]]:
    from datasets import load_dataset
    ds = load_dataset(OPENSWE_DATASET, CFG["fetch"]["openswe_config"], split=split,
                      streaming=True, token=os.environ.get("HF_TOKEN"))
    yield from ds


def fetch_openswe(split: str, max_rows: int, out: Path) -> int:
    """Stream at most max_rows rows of one split to a parquet file. Return the row count."""
    out.parent.mkdir(parents=True, exist_ok=True)
    tmp = out.with_suffix(".part")
    writer: pq.ParquetWriter | None = None
    batch: list[dict[str, Any]] = []
    n = 0
    for row in _stream(split):
        batch.append(openswe_row(row))
        n += 1
        if len(batch) >= _BATCH or n >= max_rows:
            writer = writer or pq.ParquetWriter(tmp, OPENSWE_SCHEMA)
            writer.write_table(pa.Table.from_pylist(batch, schema=OPENSWE_SCHEMA))
            batch = []
            print(f"{split}: {n} rows", flush=True)
        if n >= max_rows:
            break
    if batch:
        writer = writer or pq.ParquetWriter(tmp, OPENSWE_SCHEMA)
        writer.write_table(pa.Table.from_pylist(batch, schema=OPENSWE_SCHEMA))
    if writer:
        writer.close()
        tmp.replace(out)
    return n


def swechat_sessions() -> list[dict[str, Any]]:
    """Return the Claude Code session rows of SWE-chat (session_id, user_id, repo_id)."""
    import duckdb
    src = path("raw") / "swechat" / "sessions.parquet"
    con = duckdb.connect()
    rows = con.execute(
        f"SELECT session_id, user_id, repo_id FROM read_parquet('{src}') WHERE agent = ?",
        [CFG["fetch"]["swechat_agent"]]).fetchall()
    return [{"session_id": s, "user_id": u, "repo_id": r} for s, u, r in rows]


def swechat_turns(max_sessions: int | None = None) -> Iterator[tuple[str, list[dict[str, Any]]]]:
    """Yield (session_id, ordered turn rows) for Claude Code sessions. Only the kept turn types."""
    import duckdb
    sessions = swechat_sessions()
    ids = sorted(s["session_id"] for s in sessions)[: max_sessions or None]
    src = path("raw") / "swechat" / "conversations.parquet"
    con = duckdb.connect()
    con.execute(f"SET memory_limit='{CFG['fetch']['duckdb_memory']}'")
    con.execute("CREATE TEMP TABLE keep(session_id VARCHAR)")
    con.executemany("INSERT INTO keep VALUES (?)", [(i,) for i in ids])
    types = ", ".join(f"'{t}'" for t in SWECHAT_TURN_TYPES)
    cols = ", ".join(f"c.{c}" for c in SWECHAT_COLUMNS)
    cur = con.execute(
        f"SELECT {cols} FROM read_parquet('{src}') c JOIN keep k USING (session_id) "
        f"WHERE c.turn_type IN ({types}) "
        f"ORDER BY c.session_id, c.turn_number, c.content_block_index")
    current: str | None = None
    turns: list[dict[str, Any]] = []
    while True:
        chunk = cur.fetchmany(5000)
        if not chunk:
            break
        for values in chunk:
            row = dict(zip(SWECHAT_COLUMNS, values))
            if row["session_id"] != current and turns:
                yield current, turns  # type: ignore[misc]
                turns = []
            current = row["session_id"]
            turns.append(row)
    if turns:
        yield current, turns  # type: ignore[misc]


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("which", choices=["openswe", "xharness"])
    args = parser.parse_args()
    fcfg = CFG["fetch"]
    if args.which == "openswe":
        out, split, rows = path("raw") / "openswe.parquet", fcfg["openswe_split"], fcfg["openswe_rows"]
    else:
        out, split, rows = (path("raw") / "openswe_xharness.parquet", fcfg["openswe_xharness_split"],
                            fcfg["openswe_xharness_rows"])
    if out.exists():
        print(f"{out} exists; skip")
        return
    print(f"wrote {fetch_openswe(split, rows, out)} rows to {out}")


if __name__ == "__main__":
    main()
