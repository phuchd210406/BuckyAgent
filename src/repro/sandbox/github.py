"""Fetching a real repository to investigate.

The demo used to run on one seeded fixture, which meant every part of the system
downstream of it was only ever exercised against a bug we planted ourselves. A
client's repository is on GitHub, so this module turns what a person pastes into
a form -- a URL, an `owner/repo`, a `git@` remote, a link to a branch or a
commit -- into a directory on disk that `Workspace` can copy.

Three rules, and each one is a thing that goes wrong with somebody else's repo:

  * **Shallow and single-branch.** `--depth 1` on one branch. A full clone of a
    large project is minutes of network for history nobody reads; the one prior
    that DOES read history (`repo_facts.recent_changes`) needs the tip commits,
    which a depth-1 clone has.
  * **Bounded.** A clone that hangs is a run that never ends, so there is a hard
    timeout, and the checkout is refused past a size ceiling rather than filling
    the disk of whoever is demoing.
  * **Cached, and re-used carefully.** The same repo asked for twice does not
    clone twice; it fetches. The cache is never handed to the agent -- it is the
    thing `Workspace` copies FROM, so the agent still only ever writes to a
    throwaway copy.

A token is read from `GITHUB_TOKEN`/`GH_TOKEN` if one is set, which is what
makes a private repo work. It is used in the credential position of the remote
URL for the fetch and then never stored: `origin` is rewritten to the plain URL
straight afterwards, so the token does not end up in `.git/config` on disk.
"""
from __future__ import annotations

import logging
import os
import re
import shutil
import subprocess
from dataclasses import dataclass
from pathlib import Path

LOG = logging.getLogger("repro.sandbox.github")

#: Where clones live. Not under /tmp by default: a clone is expensive to redo
#: and survives a reboot happily, but it is still a cache and is safe to delete.
DEFAULT_CACHE_ROOT = Path(
    os.getenv("REPRO_CLONE_CACHE", str(Path.home() / ".cache" / "repro" / "repos"))
)

CLONE_TIMEOUT_S = int(os.getenv("REPRO_CLONE_TIMEOUT_S", "180"))

#: Refuse a checkout bigger than this. A run copies the tree, indexes it and
#: runs its tests; a 4 GB monorepo is not a demo, it is a hang.
MAX_CHECKOUT_MB = int(os.getenv("REPRO_MAX_REPO_MB", "500"))

#: GitHub's own rules: 1-39 chars of alphanumerics and hyphens for an owner;
#: repos additionally allow `.` and `_`. Anything else is not a repo name, and
#: this string ends up in a shell-free subprocess argument either way.
_OWNER = r"[A-Za-z0-9](?:[A-Za-z0-9-]{0,38})"
_REPO = r"[A-Za-z0-9_.-]{1,100}?"

_PATTERNS = (
    # https://github.com/owner/repo(.git)(/tree/ref)(/anything)
    re.compile(
        rf"^(?:https?://)?(?:www\.)?github\.com/(?P<owner>{_OWNER})/(?P<repo>{_REPO})"
        rf"(?:\.git)?(?:/tree/(?P<ref>[^/\s]+))?(?:/.*)?/?$"
    ),
    # git@github.com:owner/repo(.git)
    re.compile(rf"^git@github\.com:(?P<owner>{_OWNER})/(?P<repo>{_REPO})(?:\.git)?/?$"),
    # owner/repo, optionally owner/repo@ref
    re.compile(rf"^(?P<owner>{_OWNER})/(?P<repo>{_REPO})(?:@(?P<ref>[^/\s]+))?$"),
)


class RepoFetchError(RuntimeError):
    """Anything that stopped us getting the repository. The message is for a human."""


@dataclass(frozen=True)
class RepoRef:
    """A GitHub repository, and optionally the branch or commit that was asked for."""

    owner: str
    repo: str
    ref: str | None = None

    @property
    def slug(self) -> str:
        return f"{self.owner}/{self.repo}"

    @property
    def clone_url(self) -> str:
        return f"https://github.com/{self.owner}/{self.repo}.git"

    @property
    def cache_name(self) -> str:
        """A filesystem-safe directory name. Distinct refs never share a checkout."""
        suffix = f"@{re.sub(r'[^A-Za-z0-9._-]', '_', self.ref)}" if self.ref else ""
        return f"{self.owner}__{self.repo}{suffix}"

    def __str__(self) -> str:  # pragma: no cover - display only
        return self.slug + (f"@{self.ref}" if self.ref else "")


def parse_repo_ref(text: str) -> RepoRef:
    """Parse what a person pasted, or raise `RepoFetchError` saying what is accepted."""
    raw = (text or "").strip()
    if not raw:
        raise RepoFetchError("no repository given")
    raw = raw.rstrip("/")
    for pattern in _PATTERNS:
        match = pattern.match(raw)
        if match:
            parts = match.groupdict()
            repo = parts["repo"]
            if repo.endswith(".git"):
                repo = repo[: -len(".git")]
            return RepoRef(owner=parts["owner"], repo=repo, ref=parts.get("ref") or None)
    raise RepoFetchError(
        f"{raw!r} is not a GitHub repository. Use https://github.com/owner/repo, "
        "owner/repo, or a link to a branch (…/tree/main)."
    )


def fetch_repo(
    ref: str | RepoRef,
    *,
    cache_root: Path | str | None = None,
    timeout_s: int = CLONE_TIMEOUT_S,
) -> Path:
    """Return a local checkout of `ref`, cloning or refreshing it as needed.

    The returned path is a real git repository, so `recent_changes` can read the
    history prior off it -- which is also why the clone is not `--bare` and why
    `.git` is not stripped here. `Workspace` strips it when it copies.
    """
    repo = ref if isinstance(ref, RepoRef) else parse_repo_ref(ref)
    root = Path(cache_root or DEFAULT_CACHE_ROOT).expanduser()
    root.mkdir(parents=True, exist_ok=True)
    target = root / repo.cache_name

    if _is_git_repo(target):
        try:
            _refresh(repo, target, timeout_s)
            _check_size(target)
            return target
        except RepoFetchError:
            # A half-written or wedged cache entry is not worth diagnosing:
            # throw it away and take the clone path, which is known to work.
            LOG.warning("cache entry %s could not be refreshed; recloning", target)
            shutil.rmtree(target, ignore_errors=True)

    _clone(repo, target, timeout_s)
    _check_size(target)
    return target


# ---------------------------------------------------------------------------
# git
# ---------------------------------------------------------------------------


def _token() -> str:
    for name in ("GITHUB_TOKEN", "GH_TOKEN"):
        value = os.environ.get(name, "").strip()
        if value:
            return value
    return ""


#: Only an https GitHub remote takes a token. Derived from `clone_url` rather
#: than rebuilt, so there is one definition of where we fetch from.
_HTTPS_PREFIX = "https://github.com/"


def _authed_url(repo: RepoRef) -> str:
    """The clone URL, with a token in the credential position when we have one."""
    url = repo.clone_url
    token = _token()
    if not token or not url.startswith(_HTTPS_PREFIX):
        return url
    return url.replace("https://", f"https://x-access-token:{token}@", 1)


def _git(args: list[str], *, cwd: Path | None = None, timeout_s: int) -> subprocess.CompletedProcess:
    if shutil.which("git") is None:
        raise RepoFetchError("git is not installed, so no repository can be fetched")
    try:
        return subprocess.run(
            ["git", *args],
            cwd=str(cwd) if cwd else None,
            capture_output=True,
            text=True,
            timeout=timeout_s,
            check=False,
            # Never let git stop and ask: a prompt in a web request is a hang.
            # A private repo without a token fails fast and says so instead.
            env={**os.environ, "GIT_TERMINAL_PROMPT": "0", "GIT_ASKPASS": "true"},
        )
    except subprocess.TimeoutExpired as exc:
        raise RepoFetchError(
            f"git took longer than {timeout_s}s and was stopped. The repository may be "
            "very large; try a smaller one, or raise REPRO_CLONE_TIMEOUT_S."
        ) from exc
    except OSError as exc:  # pragma: no cover - git present but unrunnable
        raise RepoFetchError(f"could not run git: {exc}") from exc


def _clone(repo: RepoRef, target: Path, timeout_s: int) -> None:
    args = ["clone", "--depth", "1", "--single-branch"]
    if repo.ref:
        args += ["--branch", repo.ref]
    args += [_authed_url(repo), str(target)]

    proc = _git(args, timeout_s=timeout_s)
    if proc.returncode != 0:
        shutil.rmtree(target, ignore_errors=True)
        raise RepoFetchError(_explain(repo, proc.stderr))
    # Drop the token from .git/config immediately: the checkout outlives the run.
    _git(["remote", "set-url", "origin", repo.clone_url], cwd=target, timeout_s=30)


def _refresh(repo: RepoRef, target: Path, timeout_s: int) -> None:
    """Bring a cached checkout up to date, or raise so the caller re-clones."""
    fetch = ["fetch", "--depth", "1", _authed_url(repo)]
    fetch += [repo.ref] if repo.ref else ["HEAD"]
    proc = _git(fetch, cwd=target, timeout_s=timeout_s)
    if proc.returncode != 0:
        raise RepoFetchError(_explain(repo, proc.stderr))

    reset = _git(["reset", "--hard", "FETCH_HEAD"], cwd=target, timeout_s=60)
    if reset.returncode != 0:
        raise RepoFetchError(_explain(repo, reset.stderr))
    # Anything a previous run left behind (it should not, but the workspace is
    # a copy and this is the original) must not become part of the next one.
    _git(["clean", "-fdx"], cwd=target, timeout_s=60)
    _git(["remote", "set-url", "origin", repo.clone_url], cwd=target, timeout_s=30)


def _is_git_repo(path: Path) -> bool:
    return (path / ".git").is_dir()


def _explain(repo: RepoRef, stderr: str) -> str:
    """Turn git's stderr into something a person reading a web form can act on."""
    text = (stderr or "").strip()
    low = text.lower()
    if "could not find remote branch" in low or "remote branch" in low and "not found" in low:
        return f"{repo.slug} has no branch called {repo.ref!r}."
    if "repository not found" in low or "404" in low:
        detail = (
            "It may be private — set GITHUB_TOKEN to a token that can read it."
            if not _token()
            else "The token in GITHUB_TOKEN cannot read it."
        )
        return f"GitHub has no repository {repo.slug}, or it is not visible to us. {detail}"
    if "authentication failed" in low or "invalid username or token" in low:
        return f"GitHub rejected the credentials in GITHUB_TOKEN for {repo.slug}."
    if "could not resolve host" in low or "network is unreachable" in low:
        return "No network route to github.com from this machine."
    return f"git could not fetch {repo.slug}: {text[:500] or 'no output'}"


def _check_size(path: Path) -> None:
    """Refuse an enormous checkout, and say what the limit is."""
    limit_bytes = MAX_CHECKOUT_MB * 1024 * 1024
    total = 0
    for dirpath, dirnames, filenames in os.walk(path):
        if ".git" in dirnames:
            dirnames.remove(".git")  # history is not what gets copied or indexed
        for name in filenames:
            try:
                total += (Path(dirpath) / name).stat().st_size
            except OSError:
                continue
        if total > limit_bytes:
            raise RepoFetchError(
                f"the checkout is larger than {MAX_CHECKOUT_MB} MB, which is more than one "
                "run can copy, index and test. Point this at a smaller project, or raise "
                "REPRO_MAX_REPO_MB."
            )
