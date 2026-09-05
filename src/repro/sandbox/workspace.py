"""Isolated, disposable copies of a project. OWNER: Engineer B."""
from __future__ import annotations

from pathlib import Path


class Workspace:
    """A throwaway copy of the target repo that the agent is allowed to mutate.

    Guarantees Engineer B must uphold (asserted in tests/test_sandbox.py):
      * the original repo directory is never written to;
      * every path written is inside the workspace root (no ``..`` escapes);
      * ``close()`` is safe to call twice and removes everything.
    """

    def __init__(self, source_repo: str | Path, root: str | Path) -> None:
        raise NotImplementedError("Engineer B, task B2")

    @property
    def path(self) -> Path:
        raise NotImplementedError

    def write_file(self, rel_path: str, contents: str) -> Path:
        raise NotImplementedError

    def read_file(self, rel_path: str, max_chars: int = 8000) -> str:
        raise NotImplementedError

    def close(self) -> None:
        raise NotImplementedError
