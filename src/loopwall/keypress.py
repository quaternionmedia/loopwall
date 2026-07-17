"""Non-blocking single-keypress polling, stdlib only, no new dependency.

Used by `loopwall demo` (PLAN.md §3.7) so a Rich `Live` display and a
space-to-capture control can run in the same loop - unlike the earlier
Enter-confirmed `input()` design, polling doesn't block, so there's no
threading needed to keep the monitor display refreshing while waiting.
"""

from __future__ import annotations

import sys

if sys.platform == "win32":
    import msvcrt

    class RawMode:
        """No-op on Windows - msvcrt reads the console directly; no mode change needed."""

        def __enter__(self) -> RawMode:
            return self

        def __exit__(self, *exc_info: object) -> None:
            pass

    def poll_key() -> str | None:
        if msvcrt.kbhit():
            return msvcrt.getwch()
        return None

else:
    import select
    import termios
    import tty

    class RawMode:
        """cbreak mode: keys are readable one at a time, without waiting for Enter."""

        def __init__(self) -> None:
            self._fd = sys.stdin.fileno()
            self._old_settings: list | None = None

        def __enter__(self) -> RawMode:
            self._old_settings = termios.tcgetattr(self._fd)
            tty.setcbreak(self._fd)
            return self

        def __exit__(self, *exc_info: object) -> None:
            if self._old_settings is not None:
                termios.tcsetattr(self._fd, termios.TCSADRAIN, self._old_settings)

    def poll_key() -> str | None:
        if select.select([sys.stdin], [], [], 0)[0]:
            return sys.stdin.read(1)
        return None
