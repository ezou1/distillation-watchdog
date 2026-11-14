"""Shared terminal status view for all five processes, built on rich Live."""
from __future__ import annotations

import asyncio
import sys
from typing import Callable, Iterable, Sequence

from rich.console import Console, Group, RenderableType
from rich.live import Live
from rich.panel import Panel
from rich.table import Table

console = Console()


def kv_table(title: str, rows: Iterable[tuple[str, object]]) -> Table:
    """A two-column table of names and values."""
    table = Table(title=title, show_header=False, expand=True)
    table.add_column("name", style="bold")
    table.add_column("value", justify="right")
    for name, value in rows:
        table.add_row(name, str(value))
    return table


def rows_table(title: str, columns: Sequence[str], rows: Iterable[Sequence[object]]) -> Table:
    """A table with a header row."""
    table = Table(title=title, expand=True)
    for col in columns:
        table.add_column(col)
    for row in rows:
        table.add_row(*[str(c) for c in row])
    return table


def panel(title: str, *parts: RenderableType) -> Panel:
    return Panel(Group(*parts), title=title)


async def run_live(render: Callable[[], RenderableType], refresh_s: float = 1.0) -> None:
    """Redraw `render()` until cancelled. Without a terminal, print it every 10 s instead."""
    if not sys.stdout.isatty():
        while True:
            console.print(render())
            await asyncio.sleep(10.0)
    with Live(render(), console=console, refresh_per_second=4, screen=False) as live:
        while True:
            await asyncio.sleep(refresh_s)
            live.update(render())
