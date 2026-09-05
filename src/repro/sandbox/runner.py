"""Runs pytest inside a workspace under a hard timeout. OWNER: Engineer B."""
from __future__ import annotations

from repro.contracts import ExecutionResult
from repro.sandbox.workspace import Workspace


def run_pytest(ws: Workspace, target: str | None = None, timeout_s: int = 60) -> ExecutionResult:
    """Run the suite (or one node id) and parse the summary line into counts.

    MUST NOT raise on test failure — a failing test is data, not an error.
    MUST return ``timed_out=True`` rather than hanging.
    MUST truncate stdout/stderr to the tail, never return full dumps.
    """
    raise NotImplementedError("Engineer B, task B3")
