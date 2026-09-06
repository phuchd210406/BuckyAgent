"""Making a real project's tests importable before the graph starts.

The failure this prevents is subtle and expensive: without the project's
dependencies, every generated test errors at collection, `is_reproduction`
correctly refuses to call an error a reproduction, and the run ends
"not reproduced" for a reason that has nothing to do with the client's bug.

The tests that actually build a virtualenv are marked slow.
"""
from __future__ import annotations

import subprocess
import sys

import pytest

from repro.sandbox import deps
from repro.sandbox.deps import declared_dependencies, install_mode, prepare


@pytest.fixture(autouse=True)
def _default_mode(monkeypatch):
    monkeypatch.delenv(deps.INSTALL_ENV, raising=False)


def test_the_mode_is_read_at_call_time(monkeypatch):
    assert install_mode() == "auto"
    monkeypatch.setenv(deps.INSTALL_ENV, "0")
    assert install_mode() == "never"
    monkeypatch.setenv(deps.INSTALL_ENV, "1")
    assert install_mode() == "always"
    monkeypatch.setenv(deps.INSTALL_ENV, "nonsense")
    assert install_mode() == "auto", "an unreadable value must not disable the feature"


def test_a_stdlib_only_project_declares_nothing(tmp_path):
    (tmp_path / "pkg.py").write_text("VALUE = 1\n")
    assert declared_dependencies(tmp_path) == []


def test_declarations_are_found_in_the_order_pip_should_see_them(tmp_path):
    (tmp_path / "pyproject.toml").write_text("[project]\nname='x'\n")
    (tmp_path / "requirements.txt").write_text("\n")
    (tmp_path / "test-requirements.txt").write_text("\n")
    found = declared_dependencies(tmp_path)
    assert found[0] == "pyproject.toml"
    assert found.index("requirements.txt") < found.index("test-requirements.txt")


def test_auto_skips_a_project_with_nothing_to_install(tmp_path):
    """The seeded demo repo is stdlib-only, so the offline demo stays offline."""
    (tmp_path / "pkg.py").write_text("VALUE = 1\n")
    env = prepare(tmp_path, tmp_path / "venv")
    assert env.installed is False
    assert env.python == sys.executable
    assert not (tmp_path / "venv").exists()


def test_off_means_off_even_when_the_project_declares_dependencies(tmp_path, monkeypatch):
    monkeypatch.setenv(deps.INSTALL_ENV, "0")
    (tmp_path / "requirements.txt").write_text("requests\n")
    env = prepare(tmp_path, tmp_path / "venv")
    assert env.installed is False
    assert "off" in env.summary


def test_a_failed_install_degrades_the_run_instead_of_ending_it(tmp_path, monkeypatch):
    """A dependency problem is data for the record, never an exception."""
    (tmp_path / "requirements.txt").write_text("this-package-does-not-exist-anywhere\n")
    monkeypatch.setattr(
        deps, "_make_venv", lambda *a, **k: (_ for _ in ()).throw(deps._StepFailed("no venv"))
    )
    env = prepare(tmp_path, tmp_path / "venv")
    assert env.installed is False
    assert env.python == sys.executable
    assert "no venv" in env.summary


@pytest.mark.slow
def test_a_real_virtualenv_gets_the_project_and_pytest(tmp_path):
    """End to end, no network beyond PyPI for pytest: a project that imports itself."""
    (tmp_path / "pyproject.toml").write_text(
        "[project]\nname = 'demoproj'\nversion = '0.1.0'\n"
        "[build-system]\nrequires = ['setuptools']\nbuild-backend = 'setuptools.build_meta'\n"
    )
    (tmp_path / "demoproj").mkdir()
    (tmp_path / "demoproj" / "__init__.py").write_text("def value():\n    return 41\n")
    (tmp_path / "tests").mkdir()
    (tmp_path / "tests" / "test_value.py").write_text(
        "from demoproj import value\n\ndef test_value():\n    assert value() == 42\n"
    )

    env = prepare(tmp_path, tmp_path / "venv")
    try:
        assert env.installed, env.summary
        assert env.python != sys.executable

        # The whole point: the generated test can now IMPORT the project, so it
        # fails on the assertion rather than erroring on the import.
        proc = subprocess.run(
            [env.python, "-m", "pytest", "-q", "tests/test_value.py"],
            cwd=tmp_path, capture_output=True, text=True, timeout=180,
        )
        assert "1 failed" in proc.stdout, proc.stdout[-2000:]
        assert "ModuleNotFoundError" not in proc.stdout
    finally:
        env.close()
    assert not (tmp_path / "venv").exists(), "the virtualenv must not outlive the run"
