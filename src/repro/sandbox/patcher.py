"""Applies and reverts unified diffs. OWNER: Engineer B."""
from __future__ import annotations

from repro.contracts import Patch
from repro.sandbox.workspace import Workspace


def apply_patch(ws: Workspace, patch: Patch) -> tuple[bool, str]:
    """Return (applied, message). Never leaves the workspace half-patched."""
    raise NotImplementedError("Engineer B, task B4")


def revert_patch(ws: Workspace, patch: Patch) -> None:
    raise NotImplementedError("Engineer B, task B4")
