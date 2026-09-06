"""Reading a repository for a person to look at.

Two things are under test and only one of them is the feature. The other is
containment: these paths arrive on a query string, from a browser, on an
endpoint with no authentication, and "show me a file" is one missing check away
from "show me any file on this machine".
"""
from __future__ import annotations

import pytest

from repro.sandbox import preview
from repro.sandbox.preview import PreviewError, is_test_file, opening_file, read, tree


@pytest.fixture
def repo(tmp_path):
    """A small project shaped like the seeded ones."""
    (tmp_path / "store").mkdir()
    (tmp_path / "tests").mkdir()
    (tmp_path / "__pycache__").mkdir()
    (tmp_path / "store" / "__init__.py").write_text("")
    (tmp_path / "store" / "pricing.py").write_text("def total():\n    return 1\n")
    (tmp_path / "tests" / "test_pricing.py").write_text("def test_total():\n    assert True\n")
    (tmp_path / "README.md").write_text("# store\n")
    (tmp_path / "logo.png").write_bytes(b"\x89PNG\r\n\x1a\n" + b"\x00" * 40)
    (tmp_path / "__pycache__" / "junk.pyc").write_bytes(b"\x00")
    return tmp_path


# --- listing ------------------------------------------------------------------
def test_the_listing_is_what_a_person_wants_to_read(repo):
    files, truncated = tree(repo)
    paths = [entry.path for entry in files]

    assert paths == [
        "README.md",
        "logo.png",
        "store/__init__.py",
        "store/pricing.py",
        "tests/test_pricing.py",
    ], "source before tests, and no build noise"
    assert truncated is False


def test_tests_are_labelled_rather_than_hidden(repo):
    """The existing suite is the thing a patch may not break, so it is worth seeing."""
    files, _ = tree(repo)
    by_path = {entry.path: entry for entry in files}

    assert by_path["tests/test_pricing.py"].is_test is True
    assert by_path["store/pricing.py"].is_test is False


def test_a_file_the_viewer_cannot_show_is_listed_but_not_readable(repo):
    """Listing it and refusing to open it beats pretending it is not there."""
    files, _ = tree(repo)
    by_path = {entry.path: entry for entry in files}

    assert by_path["logo.png"].readable is False
    assert by_path["store/pricing.py"].readable is True


def test_the_listing_is_capped_and_says_when_it_was(tmp_path):
    for n in range(30):
        (tmp_path / f"mod_{n:02d}.py").write_text("x = 1\n")

    files, truncated = tree(tmp_path, max_files=10)

    assert len(files) == 10
    assert truncated is True


def test_a_missing_repository_is_a_message_not_a_traceback(tmp_path):
    with pytest.raises(PreviewError) as raised:
        tree(tmp_path / "nowhere")
    assert "no such repository" in str(raised.value)


# --- which file opens first ---------------------------------------------------
def test_the_first_file_shown_is_the_one_you_came_to_read(repo):
    files, _ = tree(repo)
    assert opening_file(files) == "store/pricing.py"


def test_examples_and_plumbing_modules_never_win(tmp_path):
    """All three of these sort before `engine.py` and say nothing about the project."""
    (tmp_path / "examples").mkdir()
    (tmp_path / "src").mkdir()
    (tmp_path / "examples" / "aaa_demo.py").write_text("x = 1\n")
    (tmp_path / "src" / "__main__.py").write_text("x = 1\n")
    (tmp_path / "src" / "_compat.py").write_text("x = 1\n")
    (tmp_path / "src" / "engine.py").write_text("x = 1\n")

    files, _ = tree(tmp_path)

    assert opening_file(files) == "src/engine.py"


def test_a_repository_with_nothing_readable_opens_nothing(tmp_path):
    (tmp_path / "blob.bin").write_bytes(b"\x00\x01")
    files, _ = tree(tmp_path)
    assert opening_file(files) is None


# --- reading ------------------------------------------------------------------
def test_reading_a_file_returns_its_text_and_its_shape(repo):
    result = read(repo, "store/pricing.py")

    assert result["path"] == "store/pricing.py"
    assert "def total():" in result["text"]
    assert result["lines"] == 3
    assert result["truncated"] is False


def test_a_long_file_is_cut_and_says_so(repo):
    (repo / "store" / "big.py").write_text("# padding\n" * 500)

    result = read(repo, "store/big.py", max_chars=200)

    assert result["truncated"] is True
    assert "truncated" in result["text"]
    assert len(result["text"]) < 400


def test_a_binary_file_is_refused_rather_than_shown_as_mojibake(repo):
    with pytest.raises(PreviewError) as raised:
        read(repo, "logo.png")
    assert "not a text file" in str(raised.value)


def test_a_file_larger_than_the_panel_will_load_is_refused(repo, monkeypatch):
    monkeypatch.setattr(preview, "MAX_FILE_BYTES", 10)
    with pytest.raises(PreviewError) as raised:
        read(repo, "store/pricing.py")
    assert "larger than" in str(raised.value)


# --- containment: the part that is not about convenience ----------------------
@pytest.mark.parametrize(
    "path",
    [
        "../../../etc/passwd",
        "/etc/passwd",
        "store/../../secrets.txt",
        "",
        "   ",
    ],
)
def test_a_path_that_leaves_the_repository_is_refused(repo, path):
    with pytest.raises(PreviewError):
        read(repo, path)


def test_a_symlink_out_of_the_repository_is_not_a_door(repo, tmp_path):
    """resolve() follows the link, and the containment check is what stops it."""
    secret = tmp_path.parent / "secret.txt"
    secret.write_text("AWS_SECRET_ACCESS_KEY=hunter2\n")
    (repo / "store" / "escape.py").symlink_to(secret)

    with pytest.raises(PreviewError) as raised:
        read(repo, "store/escape.py")
    assert "outside the repository" in str(raised.value)


def test_a_directory_is_not_a_file(repo):
    with pytest.raises(PreviewError) as raised:
        read(repo, "store")
    assert "directory" in str(raised.value)


def test_is_test_file_agrees_with_the_rest_of_the_system():
    """Drifting from repo_facts would label the suite differently in two places."""
    from repro.sandbox.repo_facts import is_test_path

    for path in ("tests/test_a.py", "a/test/b.py", "pkg/thing_test.py", "pkg/core.py", "t.py"):
        assert is_test_file(path) == is_test_path(path), path
