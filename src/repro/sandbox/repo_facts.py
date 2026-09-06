"""Cheap, pure facts about a repo. OWNER: Engineer B.

Two priors for Engineer C's localiser. Neither runs a model, neither writes
anything, and both are bounded: their output goes into a prompt, so an
unbounded return value is a cost bug as much as a correctness one.

WHERE THE HISTORY COMES FROM. B1 strips `.git` when it copies a repo, so a
Workspace is never a git repository -- `recent_changes` against the workspace
alone would return [] every single time and the recency prior would silently
never exist. So the history is read from `ws.source`, the untouched original,
which is the only place it lives. `git log` is read-only, so B1's promise that
the source repo is never written to still holds.

Two properties this module is careful about, both because the output lands in a
prompt and a prompt is a cassette key:

  * DETERMINISM. Same repo, same answer, byte for byte -- paths within a commit
    are sorted rather than left in git's order.
  * AGREEMENT WITH RETRIEVAL. `retrieval/index.py` prefers this module's
    `list_source_files` the moment it exists, and falls back to its own copy
    otherwise. If the two disagree about what a test file is, C2's ranking
    changes the day this lands, silently. `tests/test_repo_facts.py` fails the
    build if they ever drift apart.
"""
from __future__ import annotations

import logging
import os
import shutil
import subprocess
from pathlib import Path

from repro.sandbox.workspace import Workspace

LOG = logging.getLogger("repro.sandbox")

MAX_FILES = 300
MAX_CHANGES = 20
#: How much history to read. One commit can touch hundreds of files, so this is
#: the bound on work; `limit` is the bound on what comes back.
MAX_COMMITS_SCANNED = 200
GIT_BINARY = "git"
GIT_TIMEOUT_S = 15

#: Never worth indexing, and huge. Mirrors retrieval/index.py's _SKIP_DIRS --
#: broader than Workspace.IGNORED_NAMES, which only governs what gets COPIED.
SKIP_DIRS = frozenset(
    {".git", ".venv", "venv", "__pycache__", "node_modules", ".tox", "build", "dist"}
)

#: A directory called `tests` or `test` anywhere above the file.
TEST_DIR_NAMES = frozenset({"tests", "test"})


def is_test_path(rel_path: str) -> bool:
    """True for a test file, by directory or by filename convention.

    The fault lives in the source. A test file that exercises the buggy function
    shares all of its vocabulary and would crowd out the thing we are looking
    for, so retrieval excludes these.
    """
    path = Path(rel_path)
    if any(part in TEST_DIR_NAMES for part in path.parts[:-1]):
        return True
    stem = path.stem
    return stem.startswith("test_") or stem.endswith("_test")


def list_source_files(ws: Workspace, max_files: int = MAX_FILES) -> list[str]:
    """Repo-relative .py paths, tests excluded, sorted, capped. Never raises.

    Walks with pruning rather than globbing everything and filtering after, so a
    repo with a 40,000-file node_modules costs nothing to skip.
    """
    if max_files <= 0:
        return []

    root = Path(ws.path)
    found: list[str] = []
    try:
        # followlinks=False (the default) matters: a symlinked directory could
        # otherwise walk out of the workspace, or loop forever.
        for dirpath, dirnames, filenames in os.walk(root, onerror=None):
            dirnames[:] = sorted(name for name in dirnames if name not in SKIP_DIRS)
            here = Path(dirpath)
            for name in filenames:
                if not name.endswith(".py"):
                    continue
                rel = (here / name).relative_to(root).as_posix()
                if not is_test_path(rel):
                    found.append(rel)
    except OSError as exc:
        # A repo we cannot fully walk is fewer candidates, not a failed run.
        LOG.warning("could not fully list %s: %s", root, exc)

    return sorted(found)[:max_files]


def recent_changes(ws: Workspace, limit: int = MAX_CHANGES) -> list[tuple[str, str, str]]:
    """(sha, iso_date, path) for recently changed files, newest first. Never raises.

    A bug reported today is far likelier to live in a file touched last week than
    in one untouched for two years, so this is a strong prior for almost no cost.

    Returns [] when there is no history to read: no git binary, no repository,
    or a repository that cannot be queried. One entry per PATH -- the most recent
    commit that touched it -- because the second-most-recent change to the same
    file tells the ranking nothing and costs the same tokens.

    Only paths that still exist inside the workspace come back. A file deleted
    three commits ago cannot hold the bug being reported now, and a path that
    failed containment has no business in a prompt.
    """
    if limit <= 0:
        return []

    raw = _read_history(ws)
    if raw is None:
        return []

    newest: dict[str, tuple[str, str]] = {}
    order: list[str] = []
    sha = date = ""
    for line in raw.splitlines():
        if line.startswith("\x1f"):
            parts = line.split("\x1f")
            if len(parts) >= 3:
                sha, date = parts[1], parts[2]
            continue
        rel = line.strip()
        if not rel or not sha:
            continue
        if rel not in newest:  # git log is newest-first, so the first win stands
            newest[rel] = (sha, date)
            order.append(rel)

    changes: list[tuple[str, str, str]] = []
    for rel in order:
        if not _inside_workspace(ws, rel):
            continue
        found_sha, found_date = newest[rel]
        changes.append((found_sha, found_date, rel))
        if len(changes) >= limit:
            break
    return changes


# ---------------------------------------------------------------------------
# internals
# ---------------------------------------------------------------------------
def _read_history(ws: Workspace) -> str | None:
    """The first of workspace-then-source that actually HAS history, else None.

    Being a directory is not the test -- the workspace always is one, and never
    has a `.git`, so choosing on directory-ness alone would mean the source is
    never consulted and this function always returned []. Ask git, in order,
    and take the first that answers.
    """
    for candidate in (Path(ws.path), Path(ws.source)):
        try:
            if not candidate.is_dir():
                continue
        except OSError:  # pragma: no cover - a path we cannot even stat
            continue
        raw = _git_log(candidate)
        if raw:
            return raw
    return None


def _git_log(repo: Path) -> str | None:
    """`git log --name-only` for one directory subtree, or None. Never raises.

    `--relative -- .` is doing the real work. A client's repo_path is its own
    repo root, but fixtures/demo_repos/shopcart is a SUBDIRECTORY of this
    project -- without those flags git would answer with this project's commits
    and paths like `fixtures/demo_repos/shopcart/shopcart/pricing.py`. With
    them, both cases give the commits that touched this subtree and paths
    relative to it, which is exactly what the workspace uses.
    """
    if not GIT_BINARY or shutil.which(GIT_BINARY) is None:
        return None
    try:
        proc = subprocess.run(
            [
                GIT_BINARY,
                "-C",
                str(repo),
                "log",
                f"-n{MAX_COMMITS_SCANNED}",
                "--name-only",
                "--no-renames",
                "--relative",
                # %cI is when the change LANDED, and strict ISO 8601.
                "--pretty=format:\x1f%H\x1f%cI",
                "--",
                ".",
            ],
            capture_output=True,
            text=True,
            timeout=GIT_TIMEOUT_S,
            check=False,
        )
    except (OSError, subprocess.SubprocessError) as exc:
        LOG.debug("git log failed for %s: %s", repo, exc)
        return None

    if proc.returncode != 0:
        # "not a git repository" lands here, and is not an error: a client may
        # hand us a plain directory, and the spec says return [] rather than raise.
        return None
    return proc.stdout


def _inside_workspace(ws: Workspace, rel: str) -> bool:
    """The path still exists in the workspace, and is genuinely inside it.

    Paths come out of someone else's commit history, so they get the same
    containment check as every other path this package touches.
    """
    try:
        return ws.resolve_path(rel).exists()
    except (ValueError, RuntimeError, OSError):
        return False
