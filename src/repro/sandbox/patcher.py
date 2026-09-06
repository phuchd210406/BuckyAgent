"""Applies and reverts unified diffs. OWNER: Engineer B.

Two things make this module more than a `git apply` wrapper.

ATOMICITY. `fix._verify()` applies a patch, runs the repro test and the suite,
and reverts unless BOTH went green. Attempt N+1 is written against whatever is
left behind, so a half-applied patch does not fail loudly -- it quietly makes
every later attempt reason about a tree nobody described. Every path the diff
names is snapshotted before anything is written, and restored on any failure.

CONTAINMENT. The diff is a string a language model chose, so each path in it
goes through `Workspace.resolve_path` -- the same rules as B1 -- before a byte
is written.

On the git backend: B1 strips `.git` when it copies a repo, so a Workspace is
NEVER a git repository. The task spec says to use `git apply` "if the workspace
is a git repo", which would mean never. But `git apply` does not require one --
verified: it applies happily in a plain directory, reads the diff from stdin
(so no patch file has to be written into the tree we are hashing), rejects
`../` paths on its own, and is already all-or-nothing across files. So git is
used whenever the binary exists, and the pure-Python applier is the fallback
for a machine without git rather than for a workspace without `.git`.
"""
from __future__ import annotations

import logging
import re
import shutil
import subprocess
from dataclasses import dataclass, field
from pathlib import Path

from repro.contracts import Patch
from repro.sandbox.workspace import Workspace

LOG = logging.getLogger("repro.sandbox")

#: Module-level so a test can set it to None and exercise the Python fallback.
GIT_BINARY = "git"
GIT_TIMEOUT_S = 30

_DIFF_GIT_RE = re.compile(r"^diff --git (?P<a>\S+) (?P<b>\S+)\s*$")
_HUNK_RE = re.compile(r"^@@ -(\d+)(?:,(\d+))? \+(\d+)(?:,(\d+))? @@")
_DEV_NULL = "/dev/null"


# ---------------------------------------------------------------------------
# Parsing
# ---------------------------------------------------------------------------
@dataclass
class _Hunk:
    old_start: int
    old_len: int
    new_len: int
    lines: list[str] = field(default_factory=list)

    @property
    def old_block(self) -> list[str]:
        return [ln[1:] for ln in self.lines if ln[:1] in (" ", "-")]

    @property
    def new_block(self) -> list[str]:
        return [ln[1:] for ln in self.lines if ln[:1] in (" ", "+")]


@dataclass
class _FileDiff:
    old_path: str | None  # None == /dev/null, i.e. the file is being created
    new_path: str | None  # None == /dev/null, i.e. the file is being deleted
    hunks: list[_Hunk] = field(default_factory=list)

    @property
    def target(self) -> str:
        """The path that exists on disk afterwards, or was removed."""
        return self.new_path or self.old_path or ""


class _DiffError(ValueError):
    """The diff is not something we are willing to guess about."""


def _header_path(raw: str) -> str:
    """The path out of a `--- ` / `+++ ` header, minus any timestamp column."""
    return raw.split("\t", 1)[0].strip()


def _strip(path: str, strip: int) -> str | None:
    """Apply -p<strip>. None means /dev/null."""
    if path == _DEV_NULL:
        return None
    parts = path.split("/")
    if strip and len(parts) > strip:
        parts = parts[strip:]
    return "/".join(parts)


def _detect_strip(text: str) -> int:
    """-p1 for the usual `a/foo.py` `b/foo.py` form, -p0 for bare paths."""
    for line in text.splitlines():
        if line.startswith("diff --git "):
            return 1
        if line.startswith("--- ") and line[4:].startswith(("a/", _DEV_NULL)):
            return 1
        if line.startswith("--- "):
            return 0
    return 1


def _parse_diff(text: str, strip: int) -> list[_FileDiff]:
    """Unified diff -> per-file hunks. Raises _DiffError on anything malformed.

    Hunk bodies are consumed by COUNT, taken from the `@@` header, rather than
    by looking for the next line that starts with `---`. A removed line reads as
    `-...`, so a diff whose content happens to contain `--- a/x` would otherwise
    be read as the start of a new file.
    """
    files: list[_FileDiff] = []
    current: _FileDiff | None = None
    hunk: _Hunk | None = None
    old_seen = new_seen = 0

    for raw in text.splitlines():
        if hunk is not None and (old_seen < hunk.old_len or new_seen < hunk.new_len):
            marker = raw[:1]
            if marker == "\\":  # "\ No newline at end of file"
                continue
            if marker == "" or raw == "":
                # An empty line in a diff body is a context line whose content
                # is empty; git writes it as a bare newline.
                raw, marker = " ", " "
            if marker not in (" ", "-", "+"):
                raise _DiffError(f"unexpected line inside a hunk: {raw!r}")
            hunk.lines.append(raw)
            old_seen += marker in (" ", "-")
            new_seen += marker in (" ", "+")
            continue

        hunk = None
        if raw.startswith("diff --git "):
            match = _DIFF_GIT_RE.match(raw)
            if match is None:
                raise _DiffError(f"malformed 'diff --git' line: {raw!r}")
            current = _FileDiff(
                _strip(match.group("a"), strip), _strip(match.group("b"), strip)
            )
            files.append(current)
        elif raw.startswith("--- "):
            path = _strip(_header_path(raw[4:]), strip)
            if current is None or current.hunks:
                current = _FileDiff(path, None)
                files.append(current)
            else:
                current.old_path = path
        elif raw.startswith("+++ "):
            if current is None:
                raise _DiffError("a '+++' header appeared before any '---' header")
            current.new_path = _strip(_header_path(raw[4:]), strip)
        elif raw.startswith("@@"):
            match = _HUNK_RE.match(raw)
            if match is None:
                raise _DiffError(f"malformed hunk header: {raw!r}")
            if current is None:
                raise _DiffError("a hunk appeared before any file header")
            hunk = _Hunk(
                old_start=int(match.group(1)),
                old_len=int(match.group(2) or 1),
                new_len=int(match.group(4) or 1),
            )
            current.hunks.append(hunk)
            old_seen = new_seen = 0
        # Everything else (index, mode, rename, binary markers) is metadata we
        # do not act on. A binary patch simply produces no hunks and fails the
        # emptiness check below.

    if not files:
        raise _DiffError("no file headers found; this does not look like a unified diff")
    for file_diff in files:
        if not file_diff.target:
            raise _DiffError("a file section names /dev/null on both sides")
        if not file_diff.hunks:
            raise _DiffError(f"no hunks for {file_diff.target!r}")
        for hunk in file_diff.hunks:
            if not hunk.lines:
                # `@@ -0,0 +0,0 @@` changes nothing and means nothing. git apply
                # refuses it; rejecting it here keeps both backends identical
                # instead of letting the fallback be quietly more permissive.
                raise _DiffError(f"empty hunk for {file_diff.target!r}")
    return files


# ---------------------------------------------------------------------------
# Snapshots
# ---------------------------------------------------------------------------
@dataclass
class _Snapshot:
    diff: str
    files: dict[str, bytes | None]  # rel path -> bytes, or None if it did not exist
    dirs: frozenset[str]  # directories that existed before, so new ones can be pruned


#: workspace root -> stack of snapshots, newest last. revert_patch unwinds it.
#: The seam hands revert_patch a Patch, not a handle, so the pre-patch state has
#: to be remembered here.
_SNAPSHOTS: dict[str, list[_Snapshot]] = {}


def _dirs_under(root: Path) -> frozenset[str]:
    out = set()
    for path in root.rglob("*"):
        if path.is_dir() and not path.is_symlink():
            out.add(path.relative_to(root).as_posix())
    return frozenset(out)


def _snapshot(ws: Workspace, diff: str, rel_paths: list[str]) -> _Snapshot:
    root = ws.path
    files: dict[str, bytes | None] = {}
    for rel in rel_paths:
        target = root / rel
        files[rel] = target.read_bytes() if target.is_file() else None
    return _Snapshot(diff=diff, files=files, dirs=_dirs_under(root))


def _restore(ws: Workspace, snap: _Snapshot) -> None:
    """Put the workspace back exactly as the snapshot found it. Never raises."""
    root = ws.path
    try:
        for rel, content in snap.files.items():
            target = root / rel
            if content is None:
                if target.is_symlink() or target.exists():
                    target.unlink()
            else:
                target.parent.mkdir(parents=True, exist_ok=True)
                target.write_bytes(content)

        # A directory the patch created is as much a change to the tree as a
        # file is: the atomicity tests hash directory entries too.
        for rel in sorted(_dirs_under(root) - snap.dirs, key=len, reverse=True):
            try:
                (root / rel).rmdir()  # only succeeds while empty, which is right
            except OSError:
                pass
    except OSError as exc:  # pragma: no cover - restoring is best-effort by design
        LOG.error("could not fully restore workspace %s: %s", root, exc)


# ---------------------------------------------------------------------------
# Backends
# ---------------------------------------------------------------------------
def _git_available() -> bool:
    return bool(GIT_BINARY) and shutil.which(GIT_BINARY) is not None


def _run_git(ws: Workspace, args: list[str], diff: str) -> subprocess.CompletedProcess:
    return subprocess.run(
        [GIT_BINARY, "apply", *args, "-"],
        cwd=str(ws.path),
        input=diff,
        capture_output=True,
        text=True,
        timeout=GIT_TIMEOUT_S,
        check=False,
    )


def _git_apply(ws: Workspace, diff: str, strip: int) -> tuple[bool, str]:
    """--check first so a bad diff never gets as far as touching the tree."""
    args = [f"-p{strip}", "--whitespace=nowarn"]
    check = _run_git(ws, [*args, "--check"], diff)
    if check.returncode != 0:
        # git's own message names the file and the line. That text is fed back
        # to the model as the reason attempt N was rejected, so pass it through
        # rather than replacing it with something of our own.
        return False, _git_message(check, "git apply --check rejected the diff")

    applied = _run_git(ws, args, diff)
    if applied.returncode != 0:
        return False, _git_message(applied, "git apply failed after --check passed")
    return True, "applied with git apply"


def _git_message(proc: subprocess.CompletedProcess, prefix: str) -> str:
    detail = (proc.stderr or proc.stdout or "").strip()
    return f"{prefix}: {detail}" if detail else prefix


def _python_apply(ws: Workspace, files: list[_FileDiff]) -> tuple[bool, str]:
    """Fallback for a machine with no git. Computes every file before writing any."""
    root = ws.path
    planned: list[tuple[Path, str | None]] = []  # (path, new text) - None means delete

    for file_diff in files:
        target = root / file_diff.target
        if file_diff.old_path is None:  # creation
            if target.exists():
                return False, f"{file_diff.target}: diff creates a file that already exists"
            original: list[str] = []
            trailing_newline = True
        else:
            if not target.is_file():
                return False, f"{file_diff.target}: no such file in the workspace"
            text = target.read_text(encoding="utf-8", errors="surrogateescape")
            original = text.splitlines()
            trailing_newline = text.endswith("\n") or text == ""

        merged = _apply_hunks(original, file_diff.hunks)
        if merged is None:
            return False, f"{file_diff.target}: hunk does not match the file"

        if file_diff.new_path is None:  # deletion
            planned.append((target, None))
        else:
            body = "\n".join(merged)
            if merged and trailing_newline:
                body += "\n"
            planned.append((target, body))

    for target, body in planned:
        if body is None:
            target.unlink(missing_ok=True)
        else:
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_text(body, encoding="utf-8", errors="surrogateescape")
    return True, "applied with the pure-Python applier (no git binary available)"


def _apply_hunks(lines: list[str], hunks: list[_Hunk]) -> list[str] | None:
    """Return the patched lines, or None if any hunk's context does not match."""
    out = list(lines)
    offset = 0
    for hunk in hunks:
        old_block = hunk.old_block
        start = _locate(out, old_block, hunk.old_start - 1 + offset)
        if start is None:
            return None
        new_block = hunk.new_block
        out[start : start + len(old_block)] = new_block
        offset += len(new_block) - len(old_block)
    return out


def _locate(lines: list[str], block: list[str], hint: int) -> int | None:
    """Where `block` sits in `lines`: at the hint, else the first exact match.

    The whole block -- context and removed lines together -- must match exactly.
    Searching elsewhere absorbs the wrong line numbers that generated diffs
    routinely carry; requiring an exact match is what stops that from silently
    patching the wrong place.
    """
    if not block:
        return max(0, min(hint, len(lines)))
    if 0 <= hint <= len(lines) - len(block) and lines[hint : hint + len(block)] == block:
        return hint
    for index in range(0, len(lines) - len(block) + 1):
        if lines[index : index + len(block)] == block:
            return index
    return None


# ---------------------------------------------------------------------------
# The public surface (pinned by graph/sandbox_seam.WorkspaceSandbox)
# ---------------------------------------------------------------------------
def apply_patch(ws: Workspace, patch: Patch) -> tuple[bool, str]:
    """Return (applied, message). Never leaves the workspace half-patched.

    Never raises: `fix._verify` turns a False into a failed attempt and carries
    the message back to the model, so a malformed diff is data, not an error.
    """
    diff = patch.unified_diff or ""
    if not diff.strip():
        return False, "the patch carries an empty diff, so there is nothing to apply"
    if not diff.endswith("\n"):
        # git apply rejects a diff whose last line has no newline.
        diff += "\n"

    try:
        strip = _detect_strip(diff)
        files = _parse_diff(diff, strip)
    except _DiffError as exc:
        return False, f"could not parse the diff: {exc}"
    except Exception as exc:  # pragma: no cover - parser must never escape
        return False, f"could not parse the diff: {exc!r}"

    # Containment, before a single byte is written.
    rel_paths: list[str] = []
    try:
        for file_diff in files:
            for candidate in (file_diff.old_path, file_diff.new_path):
                if candidate is not None and candidate not in rel_paths:
                    ws.resolve_path(candidate)
                    rel_paths.append(candidate)
    except ValueError as exc:
        return False, f"the diff names a path outside the workspace: {exc}"
    except RuntimeError as exc:
        return False, str(exc)

    # A path that must already exist and does not is caught here rather than by
    # a backend, so both backends refuse it identically.
    for file_diff in files:
        if file_diff.old_path is not None and not (ws.path / file_diff.old_path).is_file():
            return False, (
                f"the diff modifies {file_diff.old_path!r}, which does not exist "
                f"in the workspace"
            )

    try:
        snap = _snapshot(ws, diff, rel_paths)
    except OSError as exc:
        return False, f"could not snapshot the workspace before patching: {exc}"

    try:
        if _git_available():
            ok, message = _git_apply(ws, diff, strip)
        else:
            ok, message = _python_apply(ws, files)
    except (OSError, subprocess.SubprocessError) as exc:
        ok, message = False, f"the patch backend failed: {exc}"

    if not ok:
        _restore(ws, snap)
        return False, message

    _SNAPSHOTS.setdefault(str(ws.path), []).append(snap)
    return True, f"{message}: {', '.join(sorted(f.target for f in files))}"


def revert_patch(ws: Workspace, patch: Patch) -> None:
    """Restore the state from before the matching apply_patch. Never raises.

    A no-op when nothing was applied, because `fix._verify` reverts only after a
    successful apply and a revert that raised would take the whole run with it.
    """
    stack = _SNAPSHOTS.get(str(ws.path))
    if not stack:
        LOG.warning("revert_patch called for %s with nothing applied; ignoring", ws.path)
        return

    snap = stack.pop()
    if not stack:
        _SNAPSHOTS.pop(str(ws.path), None)

    expected = patch.unified_diff or ""
    if snap.diff.strip() != expected.strip():
        # Restore the real last-applied state anyway: leaving the workspace as
        # the patch left it would be worse than reverting the "wrong" one.
        LOG.warning(
            "revert_patch for %s was handed a different diff than the one last applied; "
            "reverting the last applied patch",
            ws.path,
        )
    _restore(ws, snap)


def forget(ws: Workspace) -> None:
    """Drop any remembered snapshots for a workspace that is being closed."""
    _SNAPSHOTS.pop(str(ws.path), None)
