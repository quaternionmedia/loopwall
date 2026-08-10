"""Unit test for _run_interactive's uvicorn.run() call itself - not a live-process
Ctrl-C test (see HANDOFF.md's dated entry on why that's not reliably testable here),
just a guard against silently dropping the fix that makes Ctrl-C actually work.
"""

from __future__ import annotations

from pathlib import Path
from unittest.mock import patch

from loopwall.__main__ import _run_interactive


def test_run_interactive_bounds_uvicorns_graceful_shutdown_wait(tmp_path: Path) -> None:
    """uvicorn's `timeout_graceful_shutdown` defaults to None - an unbounded wait for
    every open connection to finish naturally, including /wall/preview.ts, which is a
    deliberately-infinite MPEG-TS stream as long as a browser tab has it open. Without
    an explicit bound, Ctrl-C sets uvicorn's shutdown flag but then hangs forever
    waiting for that connection - confirmed by reading uvicorn/server.py's own
    `shutdown()` (`asyncio.wait_for(..., timeout=self.config.timeout_graceful_shutdown)`)
    and reproduced against a real uvicorn server with an active streaming connection.
    This just guards the fix (an explicit bound) staying in place.
    """
    with patch("loopwall.__main__.uvicorn.run") as mock_run, patch("loopwall.__main__.webbrowser.open"):
        _run_interactive(
            camera_type="lavfi",
            device=None,
            lavfi_source=None,
            width=320,
            height=240,
            fps=15,
            ring_dir=tmp_path / "ring",
            ring_seconds=20.0,
            segment_seconds=2.0,
            session_root=tmp_path / "sessions",
            preview_port=0,
            wall_port=0,
            midi=False,
            midi_port=None,
            midi_beats_per_bar=4,
            midi_bindings=None,
        )

    mock_run.assert_called_once()
    assert mock_run.call_args.kwargs.get("timeout_graceful_shutdown") is not None
    assert mock_run.call_args.kwargs["timeout_graceful_shutdown"] > 0
