"""Driver protocol (in-process Python Protocol counterpart to the LDP/1 wire form).

Built at 0.1.0 build order step 5 (ROADMAP.md).
"""

from __future__ import annotations

from typing import Protocol


class Driver(Protocol):
    """What any driver must provide - the shape hapax/showrunner will need to match later.

    One implementation exists at 0.1.0 (`drivers/cli`), so this is
    documentation-weight today, not an abstraction validated by multiple
    callers yet - PLAN.md §3.2's replaceability test (three independent
    driver implementations against one protocol) is what eventually
    proves this shape, not this file alone.
    """

    def run(self) -> None: ...
