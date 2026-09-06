"""Reading a repository so a person can look at it. Read-only, bounded, contained.

A demo you cannot read is a demo of nothing: a list of nine repository names
tells a judge as much about the seeded bug as the word "shopcart" does. This
module is what lets the UI show the actual code — the same code the agent is
about to search, and, after a run, the file its hypothesis named.

It is deliberately NOT `Workspace`. A workspace copies the whole tree because
the agent is going to write to it; a preview reads two or three files and must
not cost a copy, must never write, and wants the files `Workspace` skips —
tests, the README, the requirements — because those are the interesting ones.

Every path that arrives here came off an HTTP query string, so it is treated
the way `Workspace` treats a path a model chose: resolved, symlinks followed,
and refused unless the result is still inside the root. The bounds are the
other half — a preview of a 2 GB file is a dead browser tab.
"""
from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path

#: Never listed. Same set as the retrieval index skips, plus the noise a
#: checkout accumulates: none of it is what someone opened this panel to read.
SKIP_DIRS = frozenset(
    {
        ".git",
        ".venv",
        "venv",
        "__pycache__",
        "node_modules",
        ".tox",
        ".mypy_cache",
        ".pytest_cache",
        ".ruff_cache",
        "build",
        "dist",
        ".idea",
        ".vscode",
        ".eggs",
    }
)

#: What is worth opening in a code viewer. An extension list rather than a
#: content sniff: a repository is allowed to contain anything, and "is this
#: bytes or text" is a question with no cheap right answer.
TEXT_SUFFIXES = frozenset(
    {
        ".py", ".pyi", ".txt", ".md", ".rst", ".cfg", ".ini", ".toml", ".yaml",
        ".yml", ".json", ".sh", ".env", ".gitignore", ".sql", ".js", ".jsx",
        ".ts", ".tsx", ".css", ".html", ".xml", ".csv",
    }
)

#: Files with no suffix that are still worth reading.
TEXT_NAMES = frozenset({"README", "LICENSE", "Makefile", "Dockerfile", "CHANGELOG"})

MAX_FILES = 2_000
MAX_FILE_BYTES = 400_000
MAX_PREVIEW_CHARS = 200_000

_TRUNCATION_NOTE = "\n\n… truncated: {shown} of {total} characters shown …\n"


class PreviewError(ValueError):
    """The path could not be shown, and the message says why to a person."""


@dataclass(frozen=True)
class FileEntry:
    """One row in the file list."""

    path: str
    size: int
    is_test: bool
    readable: bool

    def as_dict(self) -> dict:
        return {
            "path": self.path,
            "size": self.size,
            "is_test": self.is_test,
            "readable": self.readable,
        }


def is_text(rel_path: str) -> bool:
    """True when this file is worth opening in a viewer."""
    name = Path(rel_path).name
    if name in TEXT_NAMES:
        return True
    return Path(name).suffix.lower() in TEXT_SUFFIXES


def is_test_file(rel_path: str) -> bool:
    """Mirrors `repo_facts.is_test_path`, but for LABELLING rather than ranking.

    The panel marks tests instead of hiding them: the existing suite is half the
    story of a run — it is the thing the patch may not break — so somebody
    reading the repo should be able to see it.
    """
    path = Path(rel_path)
    if any(part in {"tests", "test"} for part in path.parts[:-1]):
        return True
    stem = path.stem
    return stem.startswith("test_") or stem.endswith("_test")


def resolve_inside(root: Path, rel_path: str) -> Path:
    """Resolve `rel_path` under `root`, or raise. The security boundary.

    Same rules as `Workspace.resolve_path`, for the same reason and against a
    less trusted input: this one arrives on a query string from a browser.
    """
    raw = (rel_path or "").strip()
    if not raw:
        raise PreviewError("no file was named")
    if "\x00" in raw:
        raise PreviewError("that path contains a NUL byte")

    candidate = Path(raw)
    if candidate.is_absolute() or candidate.anchor:
        raise PreviewError(f"{raw!r} is absolute; ask for a path relative to the repository")

    root = root.resolve()
    resolved = (root / candidate).resolve()
    if resolved != root and not resolved.is_relative_to(root):
        raise PreviewError(f"{raw!r} is outside the repository")
    return resolved


def tree(root: Path | str, max_files: int = MAX_FILES) -> tuple[list[FileEntry], bool]:
    """Every readable file under `root`, sorted, capped. Returns (files, truncated).

    Sorted so that source comes before tests and directories group together,
    which is the order a person reads a small repository in. Walks with pruning
    rather than globbing everything and filtering afterwards, so a checkout with
    a huge `node_modules` costs nothing to skip.
    """
    base = Path(root).expanduser().resolve()
    if not base.is_dir():
        raise PreviewError(f"no such repository: {base}")

    found: list[FileEntry] = []
    truncated = False
    for dirpath, dirnames, filenames in os.walk(base, onerror=None):
        dirnames[:] = sorted(name for name in dirnames if name not in SKIP_DIRS)
        here = Path(dirpath)
        for name in sorted(filenames):
            rel = (here / name).relative_to(base).as_posix()
            try:
                size = (here / name).stat().st_size
            except OSError:
                continue
            if len(found) >= max_files:
                truncated = True
                break
            found.append(
                FileEntry(
                    path=rel,
                    size=size,
                    is_test=is_test_file(rel),
                    readable=is_text(rel) and size <= MAX_FILE_BYTES,
                )
            )
        if truncated:
            break

    found.sort(key=lambda entry: (entry.is_test, entry.path))
    return found, truncated


def read(root: Path | str, rel_path: str, max_chars: int = MAX_PREVIEW_CHARS) -> dict:
    """One file's text, bounded and marked when it was cut.

    Refuses what it cannot usefully show rather than returning a screen of
    mojibake: a binary file is not a preview failure to debug, it is a file
    nobody wanted to read.
    """
    base = Path(root).expanduser().resolve()
    target = resolve_inside(base, rel_path)
    rel = target.relative_to(base).as_posix() if target != base else ""

    if target.is_dir():
        raise PreviewError(f"{rel_path!r} is a directory, not a file")
    if not target.is_file():
        raise PreviewError(f"there is no {rel_path!r} in this repository")
    if not is_text(rel):
        raise PreviewError(f"{rel} is not a text file, so there is nothing to show")

    size = target.stat().st_size
    if size > MAX_FILE_BYTES:
        raise PreviewError(
            f"{rel} is {size // 1024} KB, which is larger than this panel will load"
        )

    try:
        with target.open("r", encoding="utf-8", errors="replace") as handle:
            # One character more than we can return: its presence is what says
            # the file was longer, without reading all of it.
            chunk = handle.read(max_chars + 1)
    except OSError as exc:
        raise PreviewError(f"{rel} could not be read: {exc}") from exc

    truncated = len(chunk) > max_chars
    if truncated:
        chunk = chunk[:max_chars] + _TRUNCATION_NOTE.format(shown=max_chars, total=size)

    return {
        "path": rel,
        "text": chunk,
        "size": size,
        "lines": chunk.count("\n") + 1,
        "truncated": truncated,
        "is_test": is_test_file(rel),
    }


#: Directories that hold real code about somebody else's problem. On a large
#: project these sort first alphabetically and are the worst possible thing to
#: open with -- `examples/randomuser-sqlite.py` tells you nothing about
#: `records.py` sitting beside it.
_SIDESHOW_DIRS = frozenset({"examples", "example", "docs", "doc", "scripts", "benchmarks"})


def opening_file(files: list[FileEntry]) -> str | None:
    """Which file to show first: the one most likely to hold what you came for.

    Ranked, in order: real source before tests and before examples; shallow
    before deep, because a project's own module sits near the root and its
    incidentals do not; underscore-led modules last, since `__init__.py` is
    usually empty and an empty pane reads as a broken panel. Ties break on the
    path, so the same repository always opens on the same file.
    """
    readable = [entry for entry in files if entry.readable]
    if not readable:
        return None

    def rank(entry: FileEntry) -> tuple:
        parts = Path(entry.path).parts
        return (
            entry.is_test,
            any(part in _SIDESHOW_DIRS for part in parts[:-1]),
            not entry.path.endswith(".py"),
            # Underscore-led modules are plumbing: `__init__.py` is usually
            # empty, `__main__.py` is a CLI shim, and `_compat.py` is the least
            # informative file in any project that has one. None of them says
            # what the project does, and one of them is what alphabetical order
            # would otherwise hand you.
            Path(entry.path).name.startswith("_"),
            len(parts),
            entry.path,
        )

    return min(readable, key=rank).path
