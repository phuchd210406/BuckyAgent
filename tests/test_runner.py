"""pytest-runner tests. OWNER: Engineer B.

``ExecutionResult.green`` is the ground truth the whole safety property rests
on, so every case below asserts on it explicitly. It must be True in exactly
one of them.
"""
from __future__ import annotations

import os
import sys
import time
from pathlib import Path

import pytest

from repro.contracts import SANDBOX_MAX_OUTPUT_CHARS
from repro.sandbox.runner import run_pytest
from repro.sandbox.workspace import Workspace


@pytest.fixture
def make_ws(tmp_path: Path):
    """Build a throwaway repo from a {relpath: contents} dict and wrap it."""
    opened: list[Workspace] = []

    def _make(name: str, files: dict[str, str], dirs: tuple[str, ...] = ()) -> Workspace:
        source = tmp_path / "sources" / name
        source.mkdir(parents=True, exist_ok=True)
        for rel in dirs:
            (source / rel).mkdir(parents=True, exist_ok=True)
        for rel, contents in files.items():
            path = source / rel
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(contents)
        # roots/ must stay free of any pytest config above it, or the sandbox
        # would inherit this project's [tool.pytest.ini_options]. tmp_path is.
        ws = Workspace(source, tmp_path / "roots")
        opened.append(ws)
        return ws

    yield _make
    for ws in opened:
        ws.close()


# --- 1. all pass — the ONLY green case --------------------------------------
def test_all_pass_is_green(make_ws):
    ws = make_ws("ok", {"tests/test_ok.py": "def test_a():\n    assert 1\n" * 1})
    ws.write_file("tests/test_more.py", "def test_b():\n    assert 1\ndef test_c():\n    assert 1\n")

    r = run_pytest(ws, timeout_s=60)

    assert r.green is True
    assert r.exit_code == 0
    assert r.timed_out is False
    assert (r.passed, r.failed, r.errors) == (3, 0, 0)
    assert r.duration_s > 0


# --- 2. one failure ---------------------------------------------------------
def test_one_failure_is_not_an_error(make_ws):
    ws = make_ws(
        "fail",
        {
            "tests/test_bad.py": "def test_a():\n    assert 2 + 2 == 5\n",
            "tests/test_good.py": "def test_b():\n    assert 1\ndef test_c():\n    assert 1\n",
        },
    )

    r = run_pytest(ws, timeout_s=60)  # must return, not raise

    assert r.green is False
    assert r.exit_code != 0
    assert (r.passed, r.failed, r.errors) == (2, 1, 0)
    assert "test_a" in r.stdout_tail


# --- 3. import error --------------------------------------------------------
def test_import_error_is_counted_as_an_error(make_ws):
    ws = make_ws(
        "imports",
        {"tests/test_imp.py": "import definitely_not_a_real_module\n\ndef test_a():\n    assert 1\n"},
    )

    r = run_pytest(ws, timeout_s=60)

    assert r.green is False
    assert r.exit_code != 0
    assert (r.passed, r.failed, r.errors) == (0, 0, 1)


# --- 4. syntax error --------------------------------------------------------
def test_syntax_error_is_counted_as_an_error(make_ws):
    ws = make_ws("syntax", {"tests/test_syn.py": "def test_a(:\n    assert 1\n"})

    r = run_pytest(ws, timeout_s=60)

    assert r.green is False
    assert r.exit_code != 0
    assert (r.passed, r.failed, r.errors) == (0, 0, 1)


def test_plural_errors_are_parsed(make_ws):
    """'2 errors' (plural) is a different summary word from '1 error'."""
    ws = make_ws(
        "errors",
        {
            "tests/test_fx.py": (
                "import pytest\n"
                "@pytest.fixture\n"
                "def broken():\n"
                "    raise RuntimeError('fixture blew up')\n"
                "def test_a(broken):\n    pass\n"
                "def test_b(broken):\n    pass\n"
                "def test_c():\n    assert 1\n"
            )
        },
    )

    r = run_pytest(ws, timeout_s=60)

    assert r.green is False
    assert (r.passed, r.failed, r.errors) == (1, 0, 2)


# --- 5. infinite loop -------------------------------------------------------
def test_infinite_loop_times_out_and_returns_promptly(make_ws):
    ws = make_ws("hang", {"tests/test_hang.py": "def test_a():\n    while True:\n        pass\n"})

    started = time.monotonic()
    r = run_pytest(ws, timeout_s=3)
    elapsed = time.monotonic() - started

    assert r.timed_out is True
    assert r.exit_code == -1
    assert r.green is False
    assert (r.passed, r.failed, r.errors) == (0, 0, 0)
    assert elapsed < 3 + 2, f"took {elapsed:.1f}s, must return within timeout+2s"
    assert "timed out" in r.stderr_tail


def test_timeout_kills_the_whole_process_group(make_ws, tmp_path: Path):
    """A grandchild the test spawned must not survive the kill.

    Killing only the direct child leaves pytest's workers (and anything the
    tests spawned) running, holding our pipes open and burning the budget.
    """
    survivor = tmp_path / "survivor.txt"
    ws = make_ws(
        "group",
        {
            "tests/test_spawn.py": (
                "import subprocess, sys\n"
                "def test_a():\n"
                "    subprocess.Popen([sys.executable, '-c',\n"
                "        \"import time; time.sleep(4); open(%r,'w').write('survived')\"])\n"
                "    while True:\n"
                "        pass\n" % str(survivor)
            )
        },
    )

    r = run_pytest(ws, timeout_s=2)
    assert r.timed_out is True

    time.sleep(5)  # longer than the grandchild's sleep: if alive, it writes
    assert not survivor.exists(), "a grandchild outlived the timeout kill"


# --- 6. empty test directory ------------------------------------------------
def test_empty_test_directory_reports_no_tests_ran(make_ws):
    ws = make_ws("empty", {}, dirs=("tests",))

    r = run_pytest(ws, timeout_s=60)

    assert r.green is False, "collecting nothing is not a passing suite"
    assert r.exit_code != 0
    assert (r.passed, r.failed, r.errors) == (0, 0, 0)
    assert "no tests ran" in r.stdout_tail


# --- 7. a test that floods stdout -------------------------------------------
def test_huge_stdout_is_capped(make_ws):
    ws = make_ws(
        "flood",
        {
            # It must FAIL: pytest only replays captured stdout for failures,
            # which is exactly the case where the payload gets dangerous.
            "tests/test_loud.py": (
                "def test_a():\n"
                "    print('x' * 200_000)\n"
                "    assert False, 'the assertion is at the very end'\n"
            )
        },
    )

    r = run_pytest(ws, timeout_s=60)

    assert r.green is False
    assert (r.passed, r.failed, r.errors) == (0, 1, 0)
    assert len(r.stdout_tail) <= SANDBOX_MAX_OUTPUT_CHARS
    assert len(r.stderr_tail) <= SANDBOX_MAX_OUTPUT_CHARS
    assert r.stdout_tail.startswith("[... truncated:")
    # The tail is kept because that is where the assertion is.
    assert "the assertion is at the very end" in r.stdout_tail
    assert "1 failed" in r.stdout_tail
    assert r.stdout_tail.count("x") < 200_000


# --- the sandbox must not be able to read the team's credentials ------------
def test_sandbox_environment_is_clean(make_ws, monkeypatch):
    monkeypatch.setenv("AWS_SECRET_ACCESS_KEY", "leaked-secret")
    monkeypatch.setenv("AWS_PROFILE", "team")
    monkeypatch.setenv("ANTHROPIC_API_KEY", "leaked-key")
    monkeypatch.setenv("PYTHONPATH", "/somewhere/else")
    monkeypatch.setenv("PYTEST_ADDOPTS", "--maxfail=1")

    ws = make_ws(
        "env",
        {
            "tests/test_env.py": (
                "import os\n"
                "def test_no_credentials():\n"
                "    leaked = [k for k in os.environ if k.startswith('AWS_')]\n"
                "    assert leaked == [], leaked\n"
                "    assert 'ANTHROPIC_API_KEY' not in os.environ\n"
                "def test_no_pythonpath():\n"
                "    assert 'PYTHONPATH' not in os.environ\n"
                "    assert 'PYTEST_ADDOPTS' not in os.environ\n"
            )
        },
    )

    r = run_pytest(ws, timeout_s=60)

    assert r.green is True, r.stdout_tail
    assert r.passed == 2


# --- targets are model-chosen input -----------------------------------------
def test_target_runs_only_that_node(make_ws):
    ws = make_ws(
        "target",
        {
            "tests/test_one.py": "def test_a():\n    assert 1\ndef test_b():\n    assert 1\n",
            "tests/test_two.py": "def test_c():\n    assert 0\n",
        },
    )

    whole = run_pytest(ws, timeout_s=60)
    assert whole.green is False

    r = run_pytest(ws, "tests/test_one.py::test_a", timeout_s=60)
    assert r.green is True
    assert (r.passed, r.failed, r.errors) == (1, 0, 0)


def test_target_traversal_is_rejected(make_ws):
    ws = make_ws("t2", {"tests/test_a.py": "def test_a():\n    assert 1\n"})

    with pytest.raises(ValueError, match="escapes the workspace"):
        run_pytest(ws, "../../etc/passwd")
    with pytest.raises(ValueError, match="absolute paths are not allowed"):
        run_pytest(ws, "/etc/passwd")
    with pytest.raises(ValueError, match="escapes the workspace"):
        run_pytest(ws, "../outside/test_x.py::test_y")


def test_target_may_not_smuggle_a_flag(make_ws):
    ws = make_ws("t3", {"tests/test_a.py": "def test_a():\n    assert 1\n"})

    for flag in ("--collect-only", "-p", "--co"):
        with pytest.raises(ValueError, match="may not start with"):
            run_pytest(ws, flag)


def test_a_decoy_summary_line_in_test_output_does_not_win(make_ws):
    """The LAST summary line is pytest's; earlier ones are the tests' own output.

    Not hypothetical for this project: the repro agent runs pytest inside
    pytest, so real pytest summaries show up in captured output all the time.
    """
    ws = make_ws(
        "decoy",
        {
            "tests/test_decoy.py": (
                "def test_a():\n"
                "    print('99 passed in 1.23s')\n"
                "    print('======== 42 failed, 7 errors in 9.99s ========')\n"
                "    assert False\n"
            )
        },
    )

    r = run_pytest(ws, timeout_s=60)

    assert r.green is False
    assert (r.passed, r.failed, r.errors) == (0, 1, 0), "a decoy line was parsed as the summary"


def test_kill_group_never_kills_the_caller():
    """A direct unit test of the guard: our own process must survive."""
    import subprocess as sp

    from repro.sandbox.runner import _kill_group

    # Deliberately NOT start_new_session: this child shares our process group,
    # which is exactly the arrangement where a naive killpg is suicide.
    child = sp.Popen([sys.executable, "-c", "import time; time.sleep(30)"])
    try:
        _kill_group(child)
        assert child.wait(timeout=10) != 0, "the child should have been killed"
    finally:
        if child.poll() is None:  # pragma: no cover - only if the guard failed
            child.kill()
    assert os.getpid() > 0  # we are still here


# --- when the summary line is absent, never guess ---------------------------
def test_unparseable_output_reports_zero_and_says_why(make_ws):
    ws = make_ws(
        "usage",
        {
            "pytest.ini": "[pytest]\naddopts = --this-flag-does-not-exist\n",
            "tests/test_a.py": "def test_a():\n    assert 1\n",
        },
    )

    r = run_pytest(ws, timeout_s=60)

    assert r.green is False
    assert (r.passed, r.failed, r.errors) == (0, 0, 0)
    assert "could not find pytest's summary line" in r.stderr_tail


def test_failing_run_does_not_raise_for_any_shape(make_ws):
    """The contract that matters most: this function returns, always."""
    shapes = {
        "tests/test_a.py": "def test_a():\n    raise SystemExit(3)\n",
        "tests/test_b.py": "import os\ndef test_b():\n    os._exit(1)\n",
        "tests/test_c.py": "def test_c():\n    assert 1\n",
    }
    for name, body in shapes.items():
        ws = make_ws(f"shape_{name.replace('/', '_')}", {name: body})
        r = run_pytest(ws, timeout_s=30)
        assert isinstance(r.exit_code, int)
        assert len(r.stdout_tail) <= SANDBOX_MAX_OUTPUT_CHARS


def test_counts_ignore_skips_and_xfails(make_ws):
    ws = make_ws(
        "marks",
        {
            "tests/test_m.py": (
                "import pytest\n"
                "def test_a():\n    assert 1\n"
                "@pytest.mark.skip\ndef test_b():\n    pass\n"
                "@pytest.mark.xfail\ndef test_c():\n    assert 0\n"
            )
        },
    )

    r = run_pytest(ws, timeout_s=60)

    assert r.green is True, r.stdout_tail
    assert (r.passed, r.failed, r.errors) == (1, 0, 0)


def test_pyc_files_are_not_written_into_the_workspace(make_ws):
    ws = make_ws(
        "nopyc",
        {
            "pkg/__init__.py": "",
            "pkg/mod.py": "VALUE = 1\n",
            "tests/test_p.py": "from pkg.mod import VALUE\ndef test_a():\n    assert VALUE == 1\n",
        },
    )

    r = run_pytest(ws, timeout_s=60)

    assert r.green is True, r.stdout_tail + r.stderr_tail
    assert list(ws.path.rglob("__pycache__")) == []
    assert list(ws.path.rglob(".pytest_cache")) == []


def test_workspace_is_the_cwd(make_ws):
    ws = make_ws(
        "cwd",
        {
            "tests/test_cwd.py": (
                "import os\n"
                "def test_a():\n"
                "    open('cwd.txt', 'w').write(os.getcwd())\n"
            )
        },
    )

    r = run_pytest(ws, timeout_s=60)

    assert r.green is True, r.stdout_tail + r.stderr_tail
    # Written relative to the process's cwd, and it landed in the workspace.
    assert (ws.path / "cwd.txt").read_text() == str(ws.path)
