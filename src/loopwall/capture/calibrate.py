"""calibrate — registered stub. See PLAN.md §3.4. Target: 0.2.0.

Until this lands, per-camera offset_ms comes from config (default 0).
"""

from typing import Any


def calibrate(*args: Any, **kwargs: Any) -> Any:
    raise NotImplementedError(
        "capture.calibrate: lands at 0.2.0; offset_ms comes from config (default 0) until then "
        "(PLAN.md §3.4; STUBS.md)"
    )
