"""Isolated, disposable copies of a project. OWNER: Engineer B."""
from __future__ import annotations

import shutil
import tempfile
from pathlib import Path
from types import TracebackType

# Directories that are never copied into a workspace. `.git` because the agent
# must not be able to rewrite the user's history, the other three because they
# are large, machine-specific and regenerable.
IGNORED_NAMES = (".git", ".venv", "__pycache__", "node_modules")

_TRUNCATION_MARKER = "\n\n[... truncated: file is {size} bytes, showing the first {kept} chars ...]"


class Workspace:
    """A throwaway copy of the target repo that the agent is allowed to mutate.

    Guarantees Engineer B must uphold (asserted in tests/test_sandbox.py):
      * the original repo directory is never written to;
      * every path written is inside the workspace root (no ``..`` escapes);
      * ``close()`` is safe to call twice and removes everything.

    Every ``rel_path`` reaching this class is treated as hostile input: it is a
    string a language model chose. Paths are resolved (which follows symlinks)
    and then checked to be under the workspace root, and all I/O happens on the
    *resolved* path, so a symlink planted inside the workspace cannot be used as
    a door out of it.
    """

    def __init__(self, source_repo: str | Path, root: str | Path) -> None:
        source = Path(source_repo).expanduser().resolve()
        if not source.is_dir():
            raise ValueError(f"source_repo is not a directory: {source}")

        root_dir = Path(root).expanduser()
        root_dir.mkdir(parents=True, exist_ok=True)

        # A fresh directory per workspace: two Workspaces over the same source
        # must never see each other's writes.
        self._root = Path(tempfile.mkdtemp(prefix="repro-ws-", dir=root_dir)).resolve()
        self._source = source
        self._closed = False

        try:
            # symlinks=True copies links AS links: we neither slurp in whatever
            # they point at nor follow them off-tree at copy time. Reading or
            # writing *through* one is refused later by resolve_path().
            shutil.copytree(
                source,
                self._root,
                symlinks=True,
                ignore=shutil.ignore_patterns(*IGNORED_NAMES),
                dirs_exist_ok=True,
            )
        except Exception:
            self.close()
            raise

    # --- identity -----------------------------------------------------------
    @property
    def path(self) -> Path:
        """Absolute path to the workspace root (the copied repo's top level)."""
        return self._root

    @property
    def source(self) -> Path:
        """The original repo this workspace was copied from. Never written to."""
        return self._source

    @property
    def closed(self) -> bool:
        return self._closed

    # --- the security boundary ----------------------------------------------
    def resolve_path(self, rel_path: str | Path) -> Path:
        """Resolve ``rel_path`` inside the workspace, or raise ``ValueError``.

        Public because the patcher must apply the same rules to every path it
        finds in a diff. Rejects absolute paths, ``..`` escapes and any path
        that resolves — through symlinks — outside the workspace root.
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

        # resolve() normalises '..' and follows every symlink in the chain, so
        # both attacks collapse into the same containment check.
        resolved = (self._root / candidate).resolve()

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
        """Write ``contents``, creating parent directories. Returns the abs path."""
        target = self.resolve_path(rel_path)
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(contents, encoding="utf-8")
        return target

    def read_file(self, rel_path: str, max_chars: int = 8000) -> str:
        """Return at most ``max_chars`` characters of a file inside the workspace.

        Never returns an unbounded file: at most ``max_chars`` characters are
        read off disk, and if the file was longer the returned string keeps the
        head and ends with a marker saying so. The marker is counted against
        ``max_chars``, so ``len(result) <= max_chars`` always holds.
        """
        if max_chars <= 0:
            raise ValueError(f"max_chars must be positive, got {max_chars}")

        target = self.resolve_path(rel_path)
        with target.open("r", encoding="utf-8", errors="replace") as fh:
            # One char more than we can return: its presence is what tells us
            # the file was too long, without reading the whole thing.
            chunk = fh.read(max_chars + 1)

        if len(chunk) <= max_chars:
            return chunk

        size = target.stat().st_size
        marker = _TRUNCATION_MARKER.format(size=size, kept=max_chars)
        kept = max_chars - len(marker)
        if kept <= 0:
            # Degenerate max_chars: still bounded, still marked.
            return marker[:max_chars]
        marker = _TRUNCATION_MARKER.format(size=size, kept=kept)
        kept = max(0, max_chars - len(marker))
        return (chunk[:kept] + marker)[:max_chars]

    # --- teardown -----------------------------------------------------------
    def close(self) -> None:
        """Remove the workspace. Idempotent, and never raises."""
        self._closed = True
        root = getattr(self, "_root", None)
        if root is None:
            return
        try:
            shutil.rmtree(root, ignore_errors=True)
        except Exception:  # pragma: no cover - rmtree already swallows errors
            pass

    def __enter__(self) -> "Workspace":
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
        return f"<Workspace {state} root={self._root} source={self._source}>"
