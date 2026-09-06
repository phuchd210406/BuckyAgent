"""The seam between the nodes and everything that touches reality. OWNER: Engineer A.

Engineer B owns `repro.sandbox.*`; Engineer C owns `repro.retrieval.*`. Both are
NotImplementedError today and land at hour 16. The nodes therefore depend on the
narrow surface below rather than on those modules directly, which is what lets
the whole graph be driven by a canned stub, in CI, for zero tokens.

The surface is ARCHITECTURE.md's allow-list and nothing else: read (search) a
file, write a file under tests/, run pytest, apply a unified diff. There is no
shell, no install, no network -- if it is not a method here, the agent cannot
do it.
"""
from __future__ import annotations

import os
from pathlib import Path
from typing import Protocol

from repro.contracts import (
    SANDBOX_TIMEOUT_S,
    ExecutionResult,
    Hypothesis,
    Patch,
    TestArtifact,
)
from repro.retrieval.index import rank_candidates as _rank_candidates
from repro.retrieval.index import search as _search
from repro.sandbox.patcher import apply_patch as _apply_patch
from repro.sandbox.patcher import revert_patch as _revert_patch
from repro.sandbox.runner import run_pytest as _run_pytest
from repro.sandbox.workspace import Workspace

Hit = tuple[str, str, float]

#: The agent may write here and nowhere else (ARCHITECTURE.md, "Allow-list").
TEST_DIR = "tests/"


def sandbox_timeout_s() -> int:
    """How long one pytest invocation may take. Read at call time, never cached.

    `SANDBOX_TIMEOUT_S` (60s) is right for the seeded fixture and far too short
    for a real project's whole suite, which `fix` must run green before any patch
    is accepted. A suite that times out is indistinguishable from a broken patch,
    so this is raised for real repositories rather than left to bite.
    """
    raw = os.environ.get("REPRO_SANDBOX_TIMEOUT_S", "").strip()
    if not raw:
        return SANDBOX_TIMEOUT_S
    try:
        value = int(float(raw))
    except ValueError:
        return SANDBOX_TIMEOUT_S
    return value if value > 0 else SANDBOX_TIMEOUT_S


def unsafe_test_path(path: str) -> str | None:
    """Why this test path may not be written, or None if it is fine.

    `TestArtifact.path` says "must start with 'tests/' and end '.py'", but that
    is a *description* -- prompt text for the model, not a pydantic constraint.
    Nothing enforced it, so a model that answered with 'shopcart/pricing.py'
    had its "test" written straight over the source file it was supposed to be
    testing, inside the workspace the fix is then verified in.
    """
    raw = (path or "").strip()
    if not raw:
        return "the path is empty"
    if "\x00" in raw:
        return "the path contains a NUL byte"
    if raw.startswith("-"):
        return f"{raw!r} starts with '-', which pytest reads as a flag rather than a file"
    candidate = Path(raw)
    if candidate.is_absolute() or candidate.anchor:
        return f"{raw!r} is absolute; test paths are relative to the workspace root"
    if ".." in candidate.parts:
        return f"{raw!r} contains '..' and could escape the workspace"
    if not raw.startswith(TEST_DIR):
        return f"{raw!r} is outside {TEST_DIR!r}: the agent may only write tests"
    if not raw.endswith(".py"):
        return f"{raw!r} does not end in '.py' and pytest would never collect it"
    return None


class Sandbox(Protocol):
    def search(self, query: str, k: int = 8) -> list[Hit]: ...

    def rank_candidates(self, hits: list[Hit], k: int) -> list[Hypothesis]: ...

    def write_test(self, test: TestArtifact) -> None: ...

    def run_test(self, test_path: str) -> ExecutionResult: ...

    def run_suite(self) -> ExecutionResult: ...

    def apply_patch(self, patch: Patch) -> tuple[bool, str]: ...

    def revert_patch(self, patch: Patch) -> None: ...


class WorkspaceSandbox:
    """The real thing: delegates to Engineer B's and Engineer C's functions.

    Every call below is written against the signature as published; the bodies
    raise NotImplementedError until hour 16, which is expected and is exactly
    why nothing else in the graph imports them.
    """

    def __init__(
        self,
        ws: Workspace,
        timeout_s: int | None = None,
        python_exe: str | None = None,
    ) -> None:
        self.ws = ws
        self.timeout_s = sandbox_timeout_s() if timeout_s is None else timeout_s
        #: The interpreter pytest runs under: the virtualenv `sandbox.deps`
        #: built for this repo, or None for the harness's own.
        self.python_exe = python_exe

    def search(self, query: str, k: int = 8) -> list[Hit]:
        return _search(self.ws, query, k=k)

    def rank_candidates(self, hits: list[Hit], k: int) -> list[Hypothesis]:
        return _rank_candidates(hits, k)

    def write_test(self, test: TestArtifact) -> None:
        # The security boundary, not a lint: Workspace.write_file is generic on
        # purpose (the patcher needs it), so "only under tests/" is enforced
        # here, at the one place a model-chosen path becomes a write.
        problem = unsafe_test_path(test.path)
        if problem:
            raise ValueError(f"refusing to write {test.path!r}: {problem}")
        self.ws.write_file(test.path, test.source)

    def run_test(self, test_path: str) -> ExecutionResult:
        return _run_pytest(
            self.ws, target=test_path, timeout_s=self.timeout_s, python_exe=self.python_exe
        )

    def run_suite(self) -> ExecutionResult:
        return _run_pytest(
            self.ws, target=None, timeout_s=self.timeout_s, python_exe=self.python_exe
        )

    def apply_patch(self, patch: Patch) -> tuple[bool, str]:
        return _apply_patch(self.ws, patch)

    def revert_patch(self, patch: Patch) -> None:
        _revert_patch(self.ws, patch)


class StubSandbox:
    """Canned green results, no filesystem. WIRING AND TESTS ONLY -- it runs nothing.

    build_graph() falls back to this so the graph can be compiled and driven
    before hour 16. run() (task A4) passes a WorkspaceSandbox instead.
    """

    _GREEN = ExecutionResult(exit_code=0, stdout_tail="", stderr_tail="", duration_s=0.0)

    def search(self, query: str, k: int = 8) -> list[Hit]:
        return []

    def rank_candidates(self, hits: list[Hit], k: int) -> list[Hypothesis]:
        return []

    def write_test(self, test: TestArtifact) -> None:
        return None

    def run_test(self, test_path: str) -> ExecutionResult:
        return self._GREEN

    def run_suite(self) -> ExecutionResult:
        return self._GREEN

    def apply_patch(self, patch: Patch) -> tuple[bool, str]:
        return True, "stub sandbox applied nothing"

    def revert_patch(self, patch: Patch) -> None:
        return None


def require_sandbox(sandbox: Sandbox | None) -> Sandbox:
    """A node that touches reality must be handed the thing it touches it with."""
    if sandbox is None:
        raise RuntimeError(
            "no sandbox was injected: build_graph(llm, sandbox=...) supplies one to every "
            "node that needs it. Calling this node directly? Pass sandbox=StubSandbox()."
        )
    return sandbox
