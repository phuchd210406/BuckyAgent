"""Repo facts for the localiser. OWNER: Engineer B.

`retrieval/index.py` prefers this module the moment it imports, so the
agreement tests at the bottom are not decoration: if the two disagree about
what a test file is, Engineer C's ranking changes the day this lands and
nothing else in the suite would notice.
"""
from __future__ import annotations

import subprocess
from pathlib import Path

import pytest

from repro.retrieval import index
from repro.sandbox import repo_facts
from repro.sandbox.fake import FakeWorkspace
from repro.sandbox.repo_facts import is_test_path, list_source_files, recent_changes
from repro.sandbox.workspace import Workspace

REPO_ROOT = Path(__file__).resolve().parents[1]
SHOPCART = REPO_ROOT / "fixtures" / "demo_repos" / "shopcart"


def make_repo(tmp_path: Path, files: dict[str, str], name: str = "src_repo") -> Path:
    source = tmp_path / name
    source.mkdir(parents=True, exist_ok=True)
    for rel, text in files.items():
        target = source / rel
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(text)
    return source


def git_repo(tmp_path: Path, commits: list[dict[str, str]], name: str = "git_repo") -> Path:
    """A real repo with real history, one commit per dict."""
    source = tmp_path / name
    source.mkdir(parents=True, exist_ok=True)
    env = {
        "GIT_AUTHOR_NAME": "T", "GIT_AUTHOR_EMAIL": "t@e",
        "GIT_COMMITTER_NAME": "T", "GIT_COMMITTER_EMAIL": "t@e",
        "PATH": "/usr/bin:/bin", "HOME": str(tmp_path),
    }
    subprocess.run(["git", "init", "-q"], cwd=source, env=env, check=True)
    for number, files in enumerate(commits, start=1):
        for rel, text in files.items():
            target = source / rel
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_text(text)
        subprocess.run(["git", "add", "-A"], cwd=source, env=env, check=True)
        subprocess.run(
            ["git", "commit", "-q", "-m", f"commit {number}"], cwd=source, env=env, check=True
        )
    return source


def _fixture_has_history() -> bool:
    try:
        done = subprocess.run(
            ["git", "-C", str(SHOPCART), "rev-parse", "--is-inside-work-tree"],
            capture_output=True, text=True, timeout=10, check=False,
        )
    except (OSError, subprocess.SubprocessError):
        return False
    return done.returncode == 0


@pytest.fixture
def shopcart_ws(tmp_path):
    with Workspace(SHOPCART, tmp_path / "roots") as ws:
        yield ws


# ---------------------------------------------------------------------------
# list_source_files
# ---------------------------------------------------------------------------
def test_source_files_on_the_demo_repo(shopcart_ws):
    assert list_source_files(shopcart_ws) == ["shopcart/__init__.py", "shopcart/pricing.py"]


def test_tests_and_junk_are_excluded(tmp_path):
    source = make_repo(
        tmp_path,
        {
            "app/pricing.py": "x = 1\n",
            "app/test_pricing.py": "x = 1\n",  # test_ prefix
            "app/pricing_test.py": "x = 1\n",  # _test suffix
            "tests/test_app.py": "x = 1\n",  # tests/ directory
            "test/helpers.py": "x = 1\n",  # test/ directory
            "app/sub/tests/deep.py": "x = 1\n",  # nested tests/
            "build/generated.py": "x = 1\n",
            "dist/wheel.py": "x = 1\n",
            ".tox/py311/lib.py": "x = 1\n",
            "node_modules/pkg/index.py": "x = 1\n",
            ".venv/lib/site.py": "x = 1\n",
            "venv/lib/site.py": "x = 1\n",
            "app/README.md": "not python",
        },
    )
    with Workspace(source, tmp_path / "roots") as ws:
        assert list_source_files(ws) == ["app/pricing.py"]


def test_results_are_sorted_and_capped(tmp_path):
    source = make_repo(tmp_path, {f"app/m{i:03d}.py": "x = 1\n" for i in range(20)})
    with Workspace(source, tmp_path / "roots") as ws:
        assert list_source_files(ws, max_files=5) == [f"app/m{i:03d}.py" for i in range(5)]
        assert list_source_files(ws) == sorted(list_source_files(ws))
        assert list_source_files(ws, max_files=0) == []
        assert list_source_files(ws, max_files=-1) == []


def test_an_empty_repo_returns_nothing(tmp_path):
    source = make_repo(tmp_path, {}, name="empty")
    with Workspace(source, tmp_path / "roots") as ws:
        assert list_source_files(ws) == []


def test_it_does_not_walk_into_a_symlinked_directory(tmp_path):
    """A symlinked directory could otherwise walk straight out of the workspace."""
    outside = tmp_path / "outside"
    outside.mkdir()
    (outside / "secret.py").write_text("SECRET = 1\n")
    source = make_repo(tmp_path, {"app/real.py": "x = 1\n"})

    with Workspace(source, tmp_path / "roots") as ws:
        (ws.path / "app" / "link").symlink_to(outside, target_is_directory=True)
        assert list_source_files(ws) == ["app/real.py"]


def test_a_missing_directory_is_not_a_crash(tmp_path):
    """A FakeWorkspace has no tree at all. Fewer candidates, not an exception."""
    assert list_source_files(FakeWorkspace()) == []


# ---------------------------------------------------------------------------
# recent_changes
# ---------------------------------------------------------------------------
def test_recent_changes_reads_the_sources_history(shopcart_ws):
    """The workspace has no .git -- B1 strips it -- so this MUST use ws.source.

    If it ever returns [] here, the recency prior has silently stopped existing.

    Skipped when the fixture is not inside a checkout (a source tarball, a CI
    job that fetched without history). That is an absent environment, not a
    broken module -- the hermetic tests below build their own repos in tmp_path
    and cover the same behaviour unconditionally.
    """
    if not _fixture_has_history():
        pytest.skip("fixtures/demo_repos/shopcart is not inside a git checkout")

    changes = recent_changes(shopcart_ws)

    assert changes, "no history found: the recency prior is dead"
    paths = [path for _, _, path in changes]
    assert "shopcart/pricing.py" in paths
    for sha, date, path in changes:
        assert len(sha) == 40 and all(c in "0123456789abcdef" for c in sha)
        assert date.startswith("20") and "T" in date  # strict ISO 8601
        assert not path.startswith("fixtures/"), "paths must be workspace-relative"


def test_newest_first_and_one_entry_per_path(tmp_path):
    source = git_repo(
        tmp_path,
        [
            {"old.py": "1\n"},
            {"middle.py": "1\n"},
            {"old.py": "2\n", "new.py": "1\n"},  # old.py touched AGAIN, most recently
        ],
    )
    with Workspace(source, tmp_path / "roots") as ws:
        changes = recent_changes(ws)
        paths = [path for _, _, path in changes]

        assert paths.count("old.py") == 1, "one entry per path, not one per commit"
        # The last commit touched new.py and old.py; middle.py is older.
        assert set(paths[:2]) == {"new.py", "old.py"}
        assert paths[-1] == "middle.py"
        # And the sha recorded for old.py is the LATEST one that touched it.
        by_path = {path: sha for sha, _, path in changes}
        assert by_path["old.py"] == by_path["new.py"]


def test_recent_changes_is_capped(tmp_path):
    source = git_repo(tmp_path, [{f"m{i}.py": "1\n" for i in range(30)}])
    with Workspace(source, tmp_path / "roots") as ws:
        assert len(recent_changes(ws, limit=5)) == 5
        assert recent_changes(ws, limit=0) == []
        assert recent_changes(ws, limit=-1) == []


def test_a_non_git_directory_returns_empty_and_does_not_raise(tmp_path):
    source = make_repo(tmp_path, {"app/x.py": "1\n"}, name="plain")
    # tmp_path is under /tmp, with no repository above it.
    with Workspace(source, tmp_path / "roots") as ws:
        assert recent_changes(ws) == []


def test_no_git_binary_returns_empty(shopcart_ws, monkeypatch):
    monkeypatch.setattr(repo_facts, "GIT_BINARY", None)
    assert recent_changes(shopcart_ws) == []


def test_a_git_that_fails_returns_empty(shopcart_ws, monkeypatch):
    def explode(*_args, **_kwargs):
        raise OSError("git went away")

    monkeypatch.setattr(repo_facts.subprocess, "run", explode)
    assert recent_changes(shopcart_ws) == []


def test_a_file_deleted_from_the_tree_is_not_reported(tmp_path):
    """History remembers it; the workspace does not. It cannot hold today's bug."""
    source = git_repo(tmp_path, [{"gone.py": "1\n", "stays.py": "1\n"}])
    subprocess.run(["git", "rm", "-q", "gone.py"], cwd=source, check=True, env={
        "GIT_AUTHOR_NAME": "T", "GIT_AUTHOR_EMAIL": "t@e", "GIT_COMMITTER_NAME": "T",
        "GIT_COMMITTER_EMAIL": "t@e", "PATH": "/usr/bin:/bin", "HOME": str(tmp_path)})

    with Workspace(source, tmp_path / "roots") as ws:
        paths = [path for _, _, path in recent_changes(ws)]
        assert "stays.py" in paths
        assert "gone.py" not in paths


def test_a_traversing_path_from_history_is_refused(shopcart_ws, monkeypatch):
    """Paths come out of someone else's commit history: same rules as everything else."""
    monkeypatch.setattr(
        repo_facts,
        "_read_history",
        lambda ws: "\x1f" + "a" * 40 + "\x1f2026-01-01T00:00:00+00:00\n../../etc/passwd\n",
    )
    assert recent_changes(shopcart_ws) == []


def test_results_are_deterministic(shopcart_ws):
    """These go into a prompt, and a prompt is a cassette key."""
    assert recent_changes(shopcart_ws) == recent_changes(shopcart_ws)
    assert list_source_files(shopcart_ws) == list_source_files(shopcart_ws)


def test_paths_within_one_commit_are_sorted(tmp_path):
    source = git_repo(tmp_path, [{"z.py": "1\n", "a.py": "1\n", "m.py": "1\n"}])
    with Workspace(source, tmp_path / "roots") as ws:
        paths = [path for _, _, path in recent_changes(ws)]
        assert paths == sorted(paths), "git's order is not guaranteed; ours must be"


def test_the_source_repo_is_not_written_to(shopcart_ws):
    """`git log` is read-only; B1's promise about the source still holds."""
    import hashlib
    import os

    def digest(root: Path) -> str:
        h = hashlib.sha256()
        for dirpath, dirnames, filenames in os.walk(root):
            dirnames.sort()
            for name in sorted(dirnames) + sorted(filenames):
                entry = Path(dirpath) / name
                h.update(entry.relative_to(root).as_posix().encode())
                if entry.is_file():
                    h.update(entry.read_bytes())
        return h.hexdigest()

    before = digest(SHOPCART)
    recent_changes(shopcart_ws)
    assert digest(SHOPCART) == before


# ---------------------------------------------------------------------------
# Agreement with retrieval/index.py -- the drift alarm
# ---------------------------------------------------------------------------
CLASSIFICATION_CORPUS = [
    "app/pricing.py",
    "app/test_pricing.py",
    "app/pricing_test.py",
    "tests/test_app.py",
    "tests/__init__.py",
    "test/helpers.py",
    "app/sub/tests/deep.py",
    "app/tests/conftest.py",
    "shopcart/pricing.py",
    "testing/util.py",
    "app/contest.py",
    "app/latest.py",
]


@pytest.mark.parametrize("rel", CLASSIFICATION_CORPUS)
def test_test_path_classification_agrees_with_retrieval(rel):
    """C2 falls back to its own copy of this rule. The two must not diverge."""
    assert is_test_path(rel) == index._is_test_path(rel), (
        f"{rel!r}: repo_facts and retrieval/index disagree about whether this is a "
        f"test file. C2's ranking depends on them being the same rule."
    )


def test_skip_dirs_cover_everything_retrieval_skips():
    missing = index._SKIP_DIRS - repo_facts.SKIP_DIRS
    assert not missing, (
        f"retrieval/index skips {sorted(missing)} and this module does not, so those "
        f"files would enter the corpus the day repo_facts lands."
    )


def test_retrieval_gets_the_same_answer_through_either_path(tmp_path):
    """index.list_source_files prefers this module; the result must not change."""
    source = make_repo(
        tmp_path,
        {
            "app/pricing.py": "x = 1\n",
            "app/test_pricing.py": "x = 1\n",
            "tests/test_app.py": "x = 1\n",
            "build/generated.py": "x = 1\n",
            "pkg/mod.py": "x = 1\n",
        },
    )
    with Workspace(source, tmp_path / "roots") as ws:
        through_b5 = index.list_source_files(ws)

        # Force the fallback that C2 uses when repo_facts is absent.
        import builtins

        real_import = builtins.__import__

        def no_repo_facts(name, *args, **kwargs):
            if name == "repro.sandbox.repo_facts":
                raise ImportError("simulated: B5 has not landed")
            return real_import(name, *args, **kwargs)

        builtins.__import__ = no_repo_facts
        try:
            through_fallback = index.list_source_files(ws)
        finally:
            builtins.__import__ = real_import

    assert through_b5 == through_fallback == ["app/pricing.py", "pkg/mod.py"]
