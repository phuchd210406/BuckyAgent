"""Workspace isolation tests. OWNER: Engineer B.

The one that matters is ``test_source_repo_is_byte_identical_after_mutation``:
if it fails, the agent is editing the user's real repository.
"""
from __future__ import annotations

import hashlib
import logging
import os
from pathlib import Path

import pytest

from repro.sandbox.workspace import KEEP_ENV_VAR, Workspace

SHOPCART = Path(__file__).resolve().parents[1] / "fixtures" / "demo_repos" / "shopcart"


def hash_tree(root: Path) -> str:
    """A single digest over every path and byte under ``root``.

    Covers content, names, and the shape of the tree, so a deletion, an addition
    and an in-place edit all change the digest.
    """
    h = hashlib.sha256()
    for dirpath, dirnames, filenames in os.walk(root):
        dirnames.sort()
        here = Path(dirpath)
        for name in sorted(dirnames) + sorted(filenames):
            entry = here / name
            rel = entry.relative_to(root).as_posix()
            h.update(rel.encode() + b"\0")
            if entry.is_symlink():
                h.update(b"symlink\0" + os.readlink(entry).encode() + b"\0")
            elif entry.is_file():
                h.update(b"file\0" + entry.read_bytes() + b"\0")
            else:
                h.update(b"dir\0")
    return h.hexdigest()


@pytest.fixture
def ws(tmp_path: Path):
    with Workspace(SHOPCART, tmp_path / "roots") as workspace:
        yield workspace


# --- 1. the one that matters ------------------------------------------------
def test_source_repo_is_byte_identical_after_mutation(tmp_path: Path):
    before = hash_tree(SHOPCART)

    with Workspace(SHOPCART, tmp_path / "roots") as ws:
        # wrote
        ws.write_file("tests/test_repro_generated.py", "def test_new():\n    assert True\n")
        ws.write_file("deeply/nested/new.py", "x = 1\n")
        # modified
        ws.write_file("shopcart/pricing.py", "# clobbered by the agent\n")
        # deleted
        (ws.path / "tests" / "test_pricing.py").unlink()

        # The workspace really did diverge, so the assertion below is not vacuous.
        assert hash_tree(ws.path) != before
        assert (SHOPCART / "tests" / "test_pricing.py").exists()

    assert hash_tree(SHOPCART) == before


# --- 2. traversal attacks ---------------------------------------------------
def test_dotdot_traversal_is_rejected(ws: Workspace):
    with pytest.raises(ValueError, match="escapes the workspace"):
        ws.write_file("../../etc/passwd", "pwned")
    with pytest.raises(ValueError, match="escapes the workspace"):
        ws.read_file("../../etc/passwd")


def test_absolute_path_is_rejected(ws: Workspace):
    with pytest.raises(ValueError, match="absolute paths are not allowed"):
        ws.write_file("/etc/passwd", "pwned")
    with pytest.raises(ValueError, match="absolute paths are not allowed"):
        ws.read_file("/etc/passwd")


def test_symlink_pointing_outside_the_workspace_is_rejected(ws: Workspace, tmp_path: Path):
    outside = tmp_path / "outside.txt"
    outside.write_text("secret\n")

    # Planted directly on disk: this is what a malicious repo, or an earlier
    # agent step running a shell, could leave behind.
    (ws.path / "escape").symlink_to(outside)
    (ws.path / "escape_dir").symlink_to(tmp_path, target_is_directory=True)

    with pytest.raises(ValueError, match="escapes the workspace"):
        ws.read_file("escape")
    with pytest.raises(ValueError, match="escapes the workspace"):
        ws.write_file("escape", "pwned")
    with pytest.raises(ValueError, match="escapes the workspace"):
        ws.write_file("escape_dir/outside.txt", "pwned")

    assert outside.read_text() == "secret\n"


# --- 3. teardown ------------------------------------------------------------
def test_close_is_idempotent_and_removes_the_tree(tmp_path: Path):
    ws = Workspace(SHOPCART, tmp_path / "roots")
    root = ws.path
    assert root.is_dir()

    ws.close()
    assert not root.exists()
    ws.close()  # must not raise
    assert not root.exists()


# --- 4. no shared state -----------------------------------------------------
def test_two_workspaces_from_one_source_are_independent(tmp_path: Path):
    with Workspace(SHOPCART, tmp_path / "roots") as a, Workspace(SHOPCART, tmp_path / "roots") as b:
        assert a.path != b.path

        a.write_file("shopcart/pricing.py", "# only in a\n")
        a.write_file("only_in_a.py", "x = 1\n")
        (a.path / "tests" / "test_pricing.py").unlink()

        assert b.read_file("shopcart/pricing.py") != "# only in a\n"
        assert not (b.path / "only_in_a.py").exists()
        assert (b.path / "tests" / "test_pricing.py").exists()
        assert hash_tree(b.path) == hash_tree(SHOPCART)

        # Closing one must not disturb the other.
        a.close()
        assert b.read_file("shopcart/__init__.py") == ""


# --- 5. bounded reads -------------------------------------------------------
def test_read_file_truncates_and_marks(ws: Workspace):
    ws.write_file("big.txt", "a" * 50_000)

    out = ws.read_file("big.txt", max_chars=1000)
    assert len(out) <= 1000
    assert "truncated" in out
    assert out.startswith("a" * 100)

    # Exactly-at-the-limit files come back whole and unmarked.
    ws.write_file("small.txt", "b" * 200)
    assert ws.read_file("small.txt", max_chars=200) == "b" * 200
    assert ws.read_file("small.txt", max_chars=8000) == "b" * 200

    # Even an absurd limit stays bounded and still says it truncated.
    tiny = ws.read_file("big.txt", max_chars=20)
    assert len(tiny) <= 20
    assert "truncat" in tiny


# --- guarantees the five tests above lean on --------------------------------
def test_ignored_directories_are_not_copied(tmp_path: Path):
    source = tmp_path / "src_repo"
    (source / "pkg").mkdir(parents=True)
    (source / "pkg" / "mod.py").write_text("x = 1\n")
    for junk in (".git", ".venv", "__pycache__", "node_modules"):
        (source / junk).mkdir()
        (source / junk / "junk.txt").write_text("no\n")
    (source / "pkg" / "__pycache__").mkdir()
    (source / "pkg" / "__pycache__" / "mod.pyc").write_text("no\n")

    with Workspace(source, tmp_path / "roots") as ws:
        assert (ws.path / "pkg" / "mod.py").read_text() == "x = 1\n"
        for junk in (".git", ".venv", "__pycache__", "node_modules"):
            assert not (ws.path / junk).exists(), junk
        assert not (ws.path / "pkg" / "__pycache__").exists()


def test_context_manager_cleans_up_even_on_error(tmp_path: Path):
    with pytest.raises(RuntimeError, match="boom"):
        with Workspace(SHOPCART, tmp_path / "roots") as ws:
            root = ws.path
            raise RuntimeError("boom")
    assert not root.exists()


def test_write_file_returns_absolute_path_inside_the_workspace(ws: Workspace):
    written = ws.write_file("a/b/c.py", "x = 1\n")
    assert written.is_absolute()
    assert written.is_relative_to(ws.path)
    assert written.read_text() == "x = 1\n"
    assert ws.read_file("a/b/c.py") == "x = 1\n"


def test_root_itself_and_empty_paths_are_rejected(ws: Workspace):
    for bad in ("", "   ", ".", "shopcart/.."):
        with pytest.raises(ValueError):
            ws.write_file(bad, "pwned")


# --- keeping the evidence of a failed run -----------------------------------
@pytest.mark.parametrize("value", ["1", "true", "TRUE", "  yes  ", "on"])
def test_keep_env_var_preserves_the_tree(tmp_path: Path, monkeypatch, value: str):
    monkeypatch.setenv(KEEP_ENV_VAR, value)
    ws = Workspace(SHOPCART, tmp_path / "roots")
    root = ws.path
    ws.write_file("tests/test_generated.py", "def test_x():\n    assert 0\n")

    ws.close()

    assert root.is_dir(), f"{KEEP_ENV_VAR}={value!r} should have kept the workspace"
    assert (root / "tests" / "test_generated.py").exists(), "the evidence must survive"
    assert ws.closed is True, "kept is not the same as open: the agent is done with it"

    ws.close()  # still idempotent
    assert root.is_dir()


@pytest.mark.parametrize("value", ["", "0", "false", "no", "off", "please"])
def test_falsey_keep_values_still_delete(tmp_path: Path, monkeypatch, value: str):
    monkeypatch.setenv(KEEP_ENV_VAR, value)
    ws = Workspace(SHOPCART, tmp_path / "roots")
    root = ws.path

    ws.close()

    assert not root.exists(), f"{KEEP_ENV_VAR}={value!r} is not a request to keep"


def test_keep_is_read_at_close_not_at_import(tmp_path: Path, monkeypatch):
    """Settings evaluates its env defaults once at import; this must not."""
    ws = Workspace(SHOPCART, tmp_path / "roots")  # flag unset at construction
    root = ws.path

    monkeypatch.setenv(KEEP_ENV_VAR, "1")
    ws.close()

    assert root.is_dir()


def test_context_manager_honours_keep_even_on_error(tmp_path: Path, monkeypatch):
    """The exception path is the one where you most need the workspace back."""
    monkeypatch.setenv(KEEP_ENV_VAR, "1")

    with pytest.raises(RuntimeError, match="boom"):
        with Workspace(SHOPCART, tmp_path / "roots") as ws:
            root = ws.path
            ws.write_file("tests/test_generated.py", "def test_x():\n    assert 0\n")
            raise RuntimeError("boom")

    assert root.is_dir()
    assert (root / "tests" / "test_generated.py").exists()


def test_kept_workspace_is_logged_with_its_path(tmp_path: Path, monkeypatch, caplog):
    """A kept workspace nobody is told about is a disk leak, not a debug aid."""
    monkeypatch.setenv(KEEP_ENV_VAR, "1")
    ws = Workspace(SHOPCART, tmp_path / "roots")
    root = ws.path

    with caplog.at_level(logging.WARNING, logger="repro.sandbox"):
        ws.close()
        ws.close()  # second close must not log again

    records = [r for r in caplog.records if KEEP_ENV_VAR in r.getMessage()]
    assert len(records) == 1
    assert str(root) in records[0].getMessage()
