"""Getting a real project's tests to the point where they can run at all.

The seeded fixture imports nothing but the standard library, so for its whole
life this system ran pytest with its OWN interpreter and that was enough. A real
repository off GitHub is not like that: its tests import `requests`, or `numpy`,
or the package itself, and against the harness interpreter every one of them
fails at collection. That is an ERROR, and `is_reproduction` correctly refuses to
call an error a reproduction -- so without this module every real run ends
"not reproduced" for a reason that has nothing to do with the client's bug.

So: one throwaway virtualenv per run, the project's declared dependencies
installed into it, and pytest run with that interpreter.

This is a deliberate, bounded widening of ARCHITECTURE.md's "no pip install"
guardrail, and the boundaries are the point:

  * it happens ONCE, before the graph starts, from a plan derived by reading
    files -- never from anything a model said;
  * the agent still has no install capability: there is no new method on the
    `Sandbox` protocol, so no node can reach this;
  * it is bounded by a timeout and skipped entirely when the repo declares no
    dependencies (`REPRO_INSTALL_DEPS=auto`, the default), which keeps the
    offline demo offline;
  * a failed install is not a failed run. We say so, and fall back to the
    harness interpreter -- the tests may still error, and that error is now
    visible in the run record rather than mysterious.
"""
from __future__ import annotations

import logging
import os
import shutil
import subprocess
import sys
from dataclasses import dataclass, field
from pathlib import Path

LOG = logging.getLogger("repro.sandbox.deps")

INSTALL_ENV = "REPRO_INSTALL_DEPS"
INSTALL_TIMEOUT_S = int(os.getenv("REPRO_INSTALL_TIMEOUT_S", "420"))

#: Requirement files worth installing, in the order pip should see them.
REQUIREMENT_FILES = (
    "requirements.txt",
    "requirements-dev.txt",
    "requirements_dev.txt",
    "dev-requirements.txt",
    "test-requirements.txt",
    "requirements/dev.txt",
    "requirements/test.txt",
)

#: A repo with one of these declares dependencies, and `auto` will install.
PROJECT_FILES = ("pyproject.toml", "setup.py", "setup.cfg")

_TRUTHY = frozenset({"1", "true", "yes", "on", "always"})
_FALSY = frozenset({"0", "false", "no", "off", "never"})


@dataclass
class Environment:
    """The interpreter a run should use, and the story of how it got there."""

    python: str = sys.executable
    installed: bool = False
    venv_path: Path | None = None
    notes: list[str] = field(default_factory=list)

    def note(self, line: str) -> None:
        LOG.info("%s", line)
        self.notes.append(line)

    @property
    def summary(self) -> str:
        return " ".join(self.notes) if self.notes else "Using the harness interpreter."

    def close(self) -> None:
        """Remove the virtualenv. Idempotent, never raises."""
        if self.venv_path is not None:
            shutil.rmtree(self.venv_path, ignore_errors=True)
            self.venv_path = None


def install_mode() -> str:
    """`always`, `never` or `auto`. Read at call time, never cached in Settings."""
    raw = os.environ.get(INSTALL_ENV, "auto").strip().lower()
    if raw in _TRUTHY:
        return "always"
    if raw in _FALSY:
        return "never"
    return "auto"


def declared_dependencies(repo: Path) -> list[str]:
    """Repo-relative names of the dependency declarations we found. Cheap, no I/O beyond stat."""
    found = [name for name in PROJECT_FILES if (repo / name).is_file()]
    found += [name for name in REQUIREMENT_FILES if (repo / name).is_file()]
    return found


def prepare(repo: Path, venv_path: Path, *, timeout_s: int = INSTALL_TIMEOUT_S) -> Environment:
    """Build an interpreter that can import `repo`'s tests, or explain why not.

    Returns an `Environment` whose `.python` is always runnable: the new venv
    when the install worked, and this process's interpreter when it did not.
    Never raises -- a dependency problem must degrade the run, not end it.
    """
    env = Environment()
    mode = install_mode()
    declared = declared_dependencies(repo)

    if mode == "never":
        env.note("Dependency installation is off (REPRO_INSTALL_DEPS=0).")
        return env
    if mode == "auto" and not declared:
        env.note("No dependency declarations found; using the harness interpreter.")
        return env

    try:
        python = _make_venv(venv_path, timeout_s=timeout_s)
    except _StepFailed as exc:
        env.note(f"Could not create a virtualenv ({exc}); using the harness interpreter.")
        return env

    env.venv_path = venv_path
    env.python = python

    ok = True
    for name in declared:
        if name in PROJECT_FILES:
            continue  # handled once, below, after the requirement files
        try:
            _pip(python, ["install", "-r", name], cwd=repo, timeout_s=timeout_s)
            env.note(f"Installed {name}.")
        except _StepFailed as exc:
            ok = False
            env.note(f"{name} did not install ({exc}).")

    if any(name in PROJECT_FILES for name in declared):
        try:
            # Editable, so the workspace copy under test is the code that gets
            # imported -- a non-editable install would put a SECOND copy in
            # site-packages and the patch would be verified against the wrong one.
            _pip(python, ["install", "-e", "."], cwd=repo, timeout_s=timeout_s)
            env.note("Installed the project itself (editable).")
        except _StepFailed as exc:
            ok = False
            env.note(f"The project itself did not install ({exc}).")

    try:
        _pip(python, ["install", "pytest"], cwd=repo, timeout_s=180)
    except _StepFailed as exc:
        # Without pytest in the venv there is nothing to run tests WITH, so this
        # one failure does send us back to the harness interpreter.
        env.note(f"pytest could not be installed into the virtualenv ({exc}).")
        env.close()
        return Environment(notes=[*env.notes, "Fell back to the harness interpreter."])

    env.installed = True
    if ok:
        env.note("Dependencies ready.")
    else:
        env.note("Some dependencies are missing; tests that need them will error.")
    return env


# ---------------------------------------------------------------------------
# internals
# ---------------------------------------------------------------------------


class _StepFailed(RuntimeError):
    """One install step failed. Carries the tail of what the tool printed."""


def _uv() -> str | None:
    """`uv` if it is on PATH. It builds the same venv an order of magnitude faster."""
    return shutil.which("uv")


def _make_venv(venv_path: Path, *, timeout_s: int) -> str:
    """Create the virtualenv and return its interpreter path."""
    venv_path.parent.mkdir(parents=True, exist_ok=True)
    shutil.rmtree(venv_path, ignore_errors=True)

    uv = _uv()
    if uv:
        _run([uv, "venv", "--python", sys.executable, str(venv_path)], timeout_s=min(timeout_s, 120))
    else:
        _run(
            [sys.executable, "-m", "venv", str(venv_path)],
            timeout_s=min(timeout_s, 180),
        )

    python = venv_path / ("Scripts" if os.name == "nt" else "bin") / (
        "python.exe" if os.name == "nt" else "python"
    )
    if not python.exists():
        raise _StepFailed(f"no interpreter at {python}")
    return str(python)


def _pip(python: str, args: list[str], *, cwd: Path, timeout_s: int) -> None:
    """Install into `python`'s environment, through uv when we have it."""
    uv = _uv()
    if uv:
        _run([uv, "pip", "install", "--python", python, *args[1:]], cwd=cwd, timeout_s=timeout_s)
        return
    _run([python, "-m", "pip", *args, "--disable-pip-version-check"], cwd=cwd, timeout_s=timeout_s)


def _run(cmd: list[str], *, cwd: Path | None = None, timeout_s: int) -> None:
    try:
        proc = subprocess.run(
            cmd,
            cwd=str(cwd) if cwd else None,
            capture_output=True,
            text=True,
            timeout=timeout_s,
            check=False,
        )
    except subprocess.TimeoutExpired as exc:
        raise _StepFailed(f"timed out after {timeout_s}s") from exc
    except OSError as exc:
        raise _StepFailed(str(exc)) from exc
    if proc.returncode != 0:
        tail = (proc.stderr or proc.stdout or "").strip().splitlines()
        raise _StepFailed(tail[-1][:300] if tail else f"exit code {proc.returncode}")
