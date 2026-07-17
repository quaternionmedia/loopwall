"""Terminal rendering for MonitorState (PLAN.md §3.6). Rich-based, read-only.

`render()` is a pure function (state in, renderable out) so it's testable
without a live camera or terminal. `run()` is the actual live loop, used by
the `loopwall monitor` CLI command.
"""

from __future__ import annotations

import time

from rich.console import Console, Group
from rich.live import Live
from rich.panel import Panel
from rich.table import Table

from loopwall.capture.ring import RingCapture
from loopwall.monitor.state import MonitorState, capture_monitor_state


def _format_bytes(n: int) -> str:
    size = float(n)
    for unit in ("B", "KB", "MB", "GB"):
        if size < 1024 or unit == "GB":
            return f"{size:.1f} {unit}"
        size /= 1024
    return f"{size:.1f} GB"


def render(state: MonitorState) -> Group:
    parts = []

    if state.capture is not None:
        cap = state.capture
        table = Table.grid(padding=(0, 2))
        table.add_column(justify="right", style="dim")
        table.add_column()
        table.add_row("running", "[green]yes[/]" if cap.is_running else "[red]no[/]")
        table.add_row("segments", str(cap.segment_count))
        if cap.ring_start is not None and cap.ring_end is not None:
            table.add_row(
                "coverage",
                f"[{cap.ring_start:.2f}s, {cap.ring_end:.2f}s) "
                f"= {cap.ring_coverage_seconds:.1f}s / {cap.ring_capacity_seconds:.0f}s capacity",
            )
        else:
            table.add_row("coverage", "(no segments yet)")
        table.add_row("ring disk usage", _format_bytes(cap.ring_dir_bytes))
        if cap.last_extraction_latency_ms is not None:
            table.add_row("last extraction latency", f"{cap.last_extraction_latency_ms:.0f}ms")
        parts.append(Panel(table, title="capture (Tier 0)", border_style="cyan"))
    else:
        parts.append(Panel("(no capture attached)", title="capture (Tier 0)", border_style="dim"))

    if state.transport is not None or state.tracks:
        # Tier 1 — not populated until core/timeline + core/tracks land
        # (ROADMAP.md build order step 10); left here so the render layer
        # doesn't need a rewrite when that data starts arriving.
        parts.append(Panel("(Tier 1 rendering not implemented yet)", title="arrangement"))

    return Group(*parts)


def run(
    ring: RingCapture,
    refresh_hz: float = 2.0,
    console: Console | None = None,
    iterations: int | None = None,
) -> None:
    """Live-updating monitor loop. Ctrl-C exits; does not itself stop `ring` —
    callers are expected to run this inside `ring`'s own context manager so
    ring teardown happens exactly once, in exactly one place (RingCapture's
    own layered guarantee — see its docstring), not duplicated here.

    `iterations`, left unset (loop forever) for the real CLI command, exists
    so this can be tested without depending on external signal delivery to
    interrupt it — a Rich `Live` attached to a non-terminal only flushes its
    *final* frame on a graceful exit, not on a hard kill, which made this
    loop otherwise untestable via a subprocess timeout in practice.
    """
    console = console or Console()
    with Live(render(capture_monitor_state(ring)), console=console, refresh_per_second=refresh_hz) as live:
        count = 0
        while iterations is None or count < iterations:
            time.sleep(1.0 / refresh_hz)
            live.update(render(capture_monitor_state(ring)))
            count += 1
