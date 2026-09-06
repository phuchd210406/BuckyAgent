"""In-memory stand-ins for the sandbox. OWNER: Engineer B.

Engineer A needs to drive the graph through repro and fix loops without waiting
on real subprocesses. `StubSandbox` in graph/sandbox_seam.py already returns a
canned green result; what it cannot do is return a DIFFERENT result each call,
which is exactly what a loop test needs -- red, red, green -- and what this adds.

The danger with a fake is that it is more permissive than the real thing: a test
that passes against a fake which accepts `../../etc/passwd`, or which returns an
ExecutionResult the real predicates would classify differently, is a test that
lies about the system it claims to cover. So:

  * `FakeWorkspace` enforces the SAME containment rules as `Workspace` and
    truncates with the SAME function (`workspace.truncate_head`);
  * `tests/test_fake_parity.py` fails the build if the two ever drift;
  * the result constructors below are asserted against the real
    `agents._common.is_reproduction` predicate, so a `red_result()` is one the
    product would actually count as a reproduction.
"""
from __future__ import annotations

import itertools
import os
from pathlib import Path, PurePosixPath
from types import TracebackType
from typing import Iterable, Sequence

from repro.contracts import SANDBOX_TIMEOUT_S, ExecutionResult
from repro.sandbox.workspace import Workspace, truncate_head

#: Every FakeWorkspace gets its own root, mirroring the real invariant that two
#: workspaces over one source never share a path.
_COUNTER = itertools.count(1)
FAKE_ROOT = PurePosixPath("/fake/workspaces")


# ---------------------------------------------------------------------------
# Prepared results
#
# The stdout shapes mirror what runner.run_pytest actually emits, so a test that
# asserts on `stdout_tail` is asserting against something the real runner would
# have produced.
# ---------------------------------------------------------------------------
def green_result(passed: int = 1, duration_s: float = 0.01) -> ExecutionResult:
    """A suite that ran and passed. The ONLY shape for which `.green` is True."""
    return ExecutionResult(
        exit_code=0,
        stdout_tail=f"{passed} passed in {duration_s:.2f}s\n",
        stderr_tail="",
        duration_s=duration_s,
        passed=passed,
        failed=0,
        errors=0,
    )


def red_result(failed: int = 1, passed: int = 0, duration_s: float = 0.02) -> ExecutionResult:
    """A test that RAN and FAILED -- the only shape that counts as a reproduction."""
    summary = f"{failed} failed" + (f", {passed} passed" if passed else "")
    return ExecutionResult(
        exit_code=1,
        stdout_tail=(
            "=========================== short test summary info ============================\n"
            "FAILED tests/test_repro.py::test_it - AssertionError: assert 49.5 == 54.4\n"
            f"{summary} in {duration_s:.2f}s\n"
        ),
        stderr_tail="",
        duration_s=duration_s,
        passed=passed,
        failed=failed,
        errors=0,
    )


def timeout_result(timeout_s: int = SANDBOX_TIMEOUT_S) -> ExecutionResult:
    """The sandbox killed it. Counts are ZERO because we know nothing at all."""
    return ExecutionResult(
        exit_code=-1,
        stdout_tail="",
        stderr_tail=(
            f"\n[runner] timed out after {timeout_s}s; the whole process group was "
            f"killed. Counts are not available.\n"
        ),
        duration_s=float(timeout_s),
        timed_out=True,
        passed=0,
        failed=0,
        errors=0,
    )


def import_error_result(module: str = "definitely_not_a_real_module") -> ExecutionResult:
    """A test that never RAN. Not a reproduction: it is a broken test."""
    return ExecutionResult(
        exit_code=1,
        stdout_tail=(
            "=========================== short test summary info ============================\n"
            f"ERROR tests/test_repro.py - ModuleNotFoundError: No module named '{module}'\n"
            "1 error in 0.12s\n"
        ),
        stderr_tail="",
        duration_s=0.12,
        passed=0,
        failed=0,
        errors=1,
    )


# ---------------------------------------------------------------------------
# FakeWorkspace
# ---------------------------------------------------------------------------
class FakeWorkspace:
    """An in-memory Workspace. Same public surface, no filesystem.

    Signature-compatible with `Workspace` (enforced by tests/test_fake_parity.py)
    except that the constructor's arguments are optional, because there is no
    tree to copy and requiring callers to invent two paths would be noise.
    """

    def __init__(
        self,
        source_repo: str | Path = "/fake/source-repo",
        root: str | Path = FAKE_ROOT,
    ) -> None:
        self._source = Path(source_repo)
        self._root = Path(root) / f"repro-ws-fake{next(_COUNTER)}"
        self._files: dict[str, str] = {}
        self._closed = False

    # --- identity -----------------------------------------------------------
    @property
    def path(self) -> Path:
        return self._root

    @property
    def source(self) -> Path:
        return self._source

    @property
    def closed(self) -> bool:
        return self._closed

    # --- the security boundary ----------------------------------------------
    def resolve_path(self, rel_path: str | Path) -> Path:
        """The same rules as Workspace.resolve_path, minus the filesystem.

        `..` and absolute paths are rejected identically. Symlink escapes cannot
        be simulated -- there is no filesystem to plant one on -- so a test that
        cares about those has to use the real Workspace. Everything the fake CAN
        check, it checks, so no test passes here that would fail there.
        """
        if self._closed:
            raise RuntimeError("workspace is closed; it no longer exists on disk")

        raw = str(rel_path)
        if not raw.strip():
            raise ValueError("path is empty; expected a repo-relative path such as 'tests/t.py'")
        if "\x00" in raw:
            raise ValueError(f"path contains a NUL byte: {raw!r}")

        candidate = Path(raw)
        if candidate.is_absolute() or candidate.anchor:
            raise ValueError(
                f"absolute paths are not allowed: {raw!r}. "
                f"Pass a path relative to the workspace root ({self._root})."
            )

        # normpath collapses '..' lexically, which is all of resolve()'s job that
        # is meaningful without a real tree.
        resolved = Path(os.path.normpath(self._root / candidate))
        if resolved == self._root:
            raise ValueError(f"path {raw!r} names the workspace root itself, not a file inside it")
        if not resolved.is_relative_to(self._root):
            raise ValueError(
                f"path escapes the workspace: {raw!r} resolves to {resolved}, "
                f"which is outside {self._root}"
            )
        return resolved

    # --- I/O ----------------------------------------------------------------
    def write_file(self, rel_path: str, contents: str) -> Path:
        target = self.resolve_path(rel_path)
        self._files[target.relative_to(self._root).as_posix()] = contents
        return target

    def read_file(self, rel_path: str, max_chars: int = 8000) -> str:
        if max_chars <= 0:
            raise ValueError(f"max_chars must be positive, got {max_chars}")

        target = self.resolve_path(rel_path)
        key = target.relative_to(self._root).as_posix()
        if key not in self._files:
            raise FileNotFoundError(f"no such file in the fake workspace: {rel_path!r}")

        contents = self._files[key]
        # The real one reads max_chars+1 off disk; the same bound, in memory.
        return truncate_head(contents[: max_chars + 1], max_chars, len(contents.encode()))

    # --- teardown -----------------------------------------------------------
    def close(self) -> None:
        self._closed = True

    def __enter__(self) -> "FakeWorkspace":
        return self

    def __exit__(
        self,
        exc_type: type[BaseException] | None,
        exc: BaseException | None,
        tb: TracebackType | None,
    ) -> None:
        self.close()

    def __repr__(self) -> str:
        state = "closed" if self._closed else "open"
        return f"<FakeWorkspace {state} root={self._root} files={len(self._files)}>"

    # --- fake-only helpers (deliberately not on the real Workspace) ----------
    #: Anything added here must also be listed in FAKE_ONLY in the parity test,
    #: so a helper cannot be mistaken for part of the real API by accident.
    def written_files(self) -> dict[str, str]:
        """Everything write_file has stored, for assertions."""
        return dict(self._files)


# ---------------------------------------------------------------------------
# ScriptedRunner
# ---------------------------------------------------------------------------
class ScriptedRunner:
    """Hands back prepared ExecutionResults, in order, in place of run_pytest.

        runner = ScriptedRunner([red_result(), red_result(), green_result()])
        monkeypatch.setattr(runner_module, "run_pytest", runner)

    Running out of results is an AssertionError, not a silent green: the whole
    point of a bounded loop is that a test notices when the graph went round
    more times than it expected. Same idiom as `llm.fake.ScriptedLLM`.
    """

    def __init__(
        self,
        results: Sequence[ExecutionResult] | None = None,
        default: ExecutionResult | None = None,
    ) -> None:
        self.results: list[ExecutionResult] = list(results or ())
        self.default = default
        #: (target, timeout_s) for every call, in order.
        self.calls: list[tuple[str | None, int]] = []

    def queue(self, *results: ExecutionResult) -> "ScriptedRunner":
        """Append more results. Returns self so it can be chained."""
        self.results.extend(results)
        return self

    def extend(self, results: Iterable[ExecutionResult]) -> "ScriptedRunner":
        self.results.extend(results)
        return self

    def __call__(
        self,
        ws: Workspace,
        target: str | None = None,
        timeout_s: int = SANDBOX_TIMEOUT_S,
    ) -> ExecutionResult:
        self.calls.append((target, timeout_s))
        if self.results:
            return self.results.pop(0)
        if self.default is not None:
            return self.default
        raise AssertionError(
            "ScriptedRunner ran out of results: the graph ran pytest more times than "
            f"the test prepared for (call {len(self.calls)}, target={target!r}). "
            "Queue another result, or pass default= if the count is not the point."
        )

    #: The real thing is a module-level function; expose the same name so a
    #: caller can swap either form in.
    run_pytest = __call__

    def __repr__(self) -> str:
        return f"<ScriptedRunner queued={len(self.results)} calls={len(self.calls)}>"
