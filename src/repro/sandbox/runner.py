"""Runs pytest inside a workspace under a hard timeout. OWNER: Engineer B."""
from __future__ import annotations

import os
import re
import signal
import subprocess
import sys
import time

from repro.contracts import SANDBOX_MAX_OUTPUT_CHARS, SANDBOX_TIMEOUT_S, ExecutionResult
from repro.sandbox.workspace import Workspace

# The only variables the sandboxed process inherits. An ALLOWLIST, not a
# denylist: a denylist has to enumerate every secret-shaped name that exists
# now and every one anyone adds later (AWS_*, ANTHROPIC_API_KEY, GH_TOKEN, ...)
# and it is wrong the first time it misses one. This also drops PYTHONPATH and
# PYTEST_ADDOPTS, so the sandbox cannot see the harness's own sys.path or flags.
ENV_ALLOWLIST = (
    "PATH",
    "HOME",
    "LANG",
    "LC_ALL",
    "LC_CTYPE",
    "TMPDIR",
    "TZ",
    "TERM",
    "USER",
    "LOGNAME",
    "SYSTEMROOT",  # Windows: CPython will not start without it
    "COMSPEC",
)

_TRUNCATION_MARKER = "[... truncated: {omitted} of {total} chars cut from the head ...]\n"

# pytest's last line, with (`= 1 failed in 0.03s =`) or without (`-q`: `1 failed
# in 0.03s`) the '=' padding, and with the optional `(0:01:05)` long-run suffix.
_SUMMARY_RE = re.compile(
    r"^=*\s*(?P<body>\S.*?)\s+in\s+[\d.]+s(?:\s+\([^)]*\))?\s*=*$",
    re.MULTILINE,
)
_COUNT_RE = re.compile(
    r"(\d+)\s+(passed|failed|errors?|skipped|xfailed|xpassed|deselected|warnings?)\b"
)
# Words that mean "this line really is the summary" rather than some test's output.
_SUMMARY_WORDS = ("passed", "failed", "error", "skipped", "xfailed", "xpassed", "no tests ran")


def run_pytest(
    ws: Workspace,
    target: str | None = None,
    timeout_s: int = SANDBOX_TIMEOUT_S,
) -> ExecutionResult:
    """Run the suite (or one node id) and parse the summary line into counts.

    MUST NOT raise on test failure — a failing test is data, not an error.
    MUST return ``timed_out=True`` rather than hanging.
    MUST truncate stdout/stderr to the tail, never return full dumps.

    ``target`` is a pytest node id relative to the workspace root, e.g.
    ``"tests/test_pricing.py"`` or ``"tests/test_pricing.py::test_total"``.
    None runs everything. It is model-chosen input, so it goes through the same
    containment check as every other path (see ``Workspace.resolve_path``) and
    may not start with ``-``; a violation raises ``ValueError``. That is the one
    thing this function raises on, and it is a security refusal, never a verdict
    about the code under test.

    Caller precondition: put the workspace ``root`` somewhere with no pytest
    config above it (a temp dir). pytest searches PARENT directories for an ini
    file, so a workspace nested inside another project inherits that project's
    ``[tool.pytest.ini_options]``.
    """
    args = _target_args(ws, target)

    cmd = [
        sys.executable,
        "-m",
        "pytest",
        "-q",
        "--tb=short",
        "-p",
        "no:cacheprovider",  # do not litter .pytest_cache into the workspace
        *args,
    ]

    started = time.monotonic()
    stdout, stderr, exit_code, timed_out = _spawn(cmd, cwd=str(ws.path), timeout_s=timeout_s)
    duration_s = time.monotonic() - started

    if timed_out:
        stderr += (
            f"\n[runner] timed out after {timeout_s}s; "
            f"the whole process group was killed. Counts are not available.\n"
        )
        counts, why_unparsed = _Counts(), None
    else:
        counts, why_unparsed = _parse_summary(stdout, stderr)
        if why_unparsed:
            # "never guess": the counts stay at zero and the reason is recorded.
            stderr += f"\n[runner] {why_unparsed}\n"

    return ExecutionResult(
        exit_code=exit_code,
        # Notes are appended BEFORE truncation on purpose: truncation keeps the
        # tail, so anything appended here survives it.
        stdout_tail=_tail(stdout, SANDBOX_MAX_OUTPUT_CHARS),
        stderr_tail=_tail(stderr, SANDBOX_MAX_OUTPUT_CHARS),
        duration_s=round(duration_s, 3),
        timed_out=timed_out,
        passed=counts.passed,
        failed=counts.failed,
        errors=counts.errors,
    )


# ---------------------------------------------------------------------------
# process handling
# ---------------------------------------------------------------------------
def _spawn(cmd: list[str], cwd: str, timeout_s: int) -> tuple[str, str, int, bool]:
    """Run ``cmd`` and always return; never raises on a non-zero exit code."""
    proc = subprocess.Popen(
        cmd,
        cwd=cwd,
        env=_clean_env(),
        stdin=subprocess.DEVNULL,  # a test that reads stdin must not block forever
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
        encoding="utf-8",
        errors="replace",
        # New session => new process group, so one killpg reaches xdist workers
        # and anything else the tests spawned. Killing just the child leaves
        # those alive, holding our pipes open, and communicate() hangs forever.
        start_new_session=True,
    )

    try:
        stdout, stderr = proc.communicate(timeout=timeout_s)
        return stdout, stderr, proc.returncode, False
    except subprocess.TimeoutExpired:
        pass

    _kill_group(proc)
    stdout, stderr = _drain(proc)
    return stdout, stderr, -1, True


def _kill_group(proc: subprocess.Popen) -> None:
    """SIGKILL the child's whole process group. Never raises.

    The guard below is not paranoia. If the child were ever spawned without
    ``start_new_session=True`` it would share OUR process group, and this
    killpg would SIGKILL the agent along with the test — turning a routine
    timeout into the whole run vanishing. Verified: removing start_new_session
    kills the test session itself.
    """
    try:
        pgid = os.getpgid(proc.pid)
        if pgid != os.getpgid(0):
            os.killpg(pgid, signal.SIGKILL)
            return
    except (OSError, AttributeError):
        pass
    try:  # no process groups (Windows), or the group was already gone
        proc.kill()
    except Exception:
        pass


def _drain(proc: subprocess.Popen, grace_s: float = 2.0) -> tuple[str, str]:
    """Collect whatever the killed process managed to write. Never raises.

    Bounded on purpose: nothing that has just been SIGKILLed legitimately needs
    seconds to close a pipe, and this grace is the only thing between the caller
    and a run that overshoots its own timeout.
    """
    for _ in range(2):
        try:
            out, err = proc.communicate(timeout=grace_s)
            return out or "", err or ""
        except subprocess.TimeoutExpired:
            _kill_group(proc)
        except Exception:
            break
    return "", "[runner] output could not be collected after the timeout kill\n"


def _clean_env() -> dict[str, str]:
    env = {name: os.environ[name] for name in ENV_ALLOWLIST if name in os.environ}
    # Keep the workspace tree byte-stable: .pyc files would otherwise show up in
    # the patcher's before/after hashes as spurious changes.
    env["PYTHONDONTWRITEBYTECODE"] = "1"
    # pytest abbreviates its short-summary lines to the terminal width, so the
    # operator's window size would otherwise decide whether the fix agent gets
    # "AssertionError: expected 4 got 5" or "AssertionError: expected 4 g...".
    # Pin it: wider lines, and identical output on every machine.
    env["COLUMNS"] = "120"
    return env


# ---------------------------------------------------------------------------
# target validation
# ---------------------------------------------------------------------------
def _target_args(ws: Workspace, target: str | None) -> list[str]:
    if target is None or not target.strip() or target.strip() == ".":
        return []

    target = target.strip()
    if target.startswith("-"):
        raise ValueError(
            f"target may not start with '-': {target!r} would be parsed as a pytest "
            f"flag, not a test to run."
        )

    # 'tests/t.py::TestClass::test_x' -> validate the file part only.
    ws.resolve_path(target.split("::", 1)[0])
    return [target]


# ---------------------------------------------------------------------------
# summary parsing
# ---------------------------------------------------------------------------
class _Counts:
    __slots__ = ("passed", "failed", "errors")

    def __init__(self, passed: int = 0, failed: int = 0, errors: int = 0) -> None:
        self.passed = passed
        self.failed = failed
        self.errors = errors


def _parse_summary(stdout: str, stderr: str) -> tuple[_Counts, str | None]:
    """Return (counts, reason_it_could_not_be_parsed).

    A reason means the counts are all zero because we did not find the summary,
    not because the run had nothing in it.
    """
    line = _find_summary_line(stdout) or _find_summary_line(stderr)
    if line is None:
        return _Counts(), (
            "could not find pytest's summary line in the output; counts are "
            "reported as 0. This usually means pytest never got as far as "
            "running tests (a usage error, a crashed interpreter, or a plugin "
            "that failed to load)."
        )

    if "no tests ran" in line:
        return _Counts(), None

    counts = _Counts()
    for number, word in _COUNT_RE.findall(line):
        n = int(number)
        if word == "passed":
            counts.passed = n
        elif word == "failed":
            counts.failed = n
        elif word.startswith("error"):
            counts.errors = n
    return counts, None


def _find_summary_line(text: str) -> str | None:
    """The LAST line that looks like pytest's final summary, or None."""
    for match in reversed(list(_SUMMARY_RE.finditer(text))):
        body = match.group("body").strip()
        if any(word in body for word in _SUMMARY_WORDS):
            return body
    return None


# ---------------------------------------------------------------------------
# output bounding
# ---------------------------------------------------------------------------
def _tail(text: str, limit: int) -> str:
    """Keep the LAST ``limit`` chars, marker included, and say what was cut.

    The tail is where the assertion is; the head is import noise. The marker is
    counted against ``limit`` because ExecutionResult caps these fields at
    SANDBOX_MAX_OUTPUT_CHARS and pydantic rejects anything longer.
    """
    total = len(text)
    if total <= limit:
        return text

    # Two passes: the marker states how much it cut, and its own length changes
    # how much that is. One re-computation settles it exactly.
    marker = _TRUNCATION_MARKER.format(omitted=total - limit, total=total)
    keep = limit - len(marker)
    if keep <= 0:
        return text[-limit:]
    marker = _TRUNCATION_MARKER.format(omitted=total - keep, total=total)
    keep = limit - len(marker)
    if keep <= 0:
        return text[-limit:]
    return marker + text[-keep:]
