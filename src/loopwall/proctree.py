"""Kill a subprocess and its whole descendant tree, not just the tracked PID.

Factored out of capture/ring.py's RingCapture.stop() - the same shim risk
applies to any ffmpeg-family binary launched via a package-manager shim
(confirmed for both ffmpeg.exe and ffplay.exe on this project's own dev
setup: identical-size Chocolatey launcher stubs that spawn the real binary
as a child). A single-PID kill is not sufficient regardless of platform.
"""

from __future__ import annotations

import os
import platform
import signal
import subprocess

_WINDOWS = platform.system() == "Windows"


def kill_tree(proc: subprocess.Popen, timeout: float = 5.0) -> None:
    if proc.poll() is not None:
        return
    if _WINDOWS:
        # taskkill /T kills the whole process tree, not just proc.pid.
        subprocess.run(
            ["taskkill", "/F", "/T", "/PID", str(proc.pid)],
            capture_output=True,
            timeout=timeout,
        )
        proc.wait(timeout=timeout)
        return
    try:
        os.killpg(os.getpgid(proc.pid), signal.SIGTERM)
        proc.wait(timeout=timeout)
    except subprocess.TimeoutExpired:
        os.killpg(os.getpgid(proc.pid), signal.SIGKILL)
        proc.wait(timeout=timeout)
    except ProcessLookupError:
        pass
