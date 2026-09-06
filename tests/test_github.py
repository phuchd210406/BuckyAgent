"""Fetching a real repository.

The parsing half is exercised exhaustively because it is the first thing a
person touches: what they paste is a URL from a browser bar, and every shape a
browser bar produces has to land on the same repository. The fetching half is
driven through a LOCAL git repository over a file:// remote, so the suite still
runs with no network.
"""
from __future__ import annotations

import subprocess

import pytest

from repro.sandbox import github
from repro.sandbox.github import RepoFetchError, RepoRef, fetch_repo, parse_repo_ref


# --- parsing ------------------------------------------------------------------
@pytest.mark.parametrize(
    "text, owner, repo, ref",
    [
        ("https://github.com/psf/requests", "psf", "requests", None),
        ("https://github.com/psf/requests/", "psf", "requests", None),
        ("https://github.com/psf/requests.git", "psf", "requests", None),
        ("http://github.com/psf/requests", "psf", "requests", None),
        ("https://www.github.com/psf/requests", "psf", "requests", None),
        ("github.com/psf/requests", "psf", "requests", None),
        ("git@github.com:psf/requests.git", "psf", "requests", None),
        ("psf/requests", "psf", "requests", None),
        ("  psf/requests  ", "psf", "requests", None),
        # A branch link is what you get from GitHub's own branch switcher.
        ("https://github.com/pallets/flask/tree/3.0.x", "pallets", "flask", "3.0.x"),
        ("psf/requests@v2.31.0", "psf", "requests", "v2.31.0"),
        # A deep link to a file still names the repository.
        ("https://github.com/psf/requests/blob/main/setup.py", "psf", "requests", None),
        ("https://github.com/my-org/my.repo_name", "my-org", "my.repo_name", None),
    ],
)
def test_every_shape_a_person_might_paste(text, owner, repo, ref):
    parsed = parse_repo_ref(text)
    assert (parsed.owner, parsed.repo, parsed.ref) == (owner, repo, ref)


@pytest.mark.parametrize(
    "text",
    [
        "",
        "   ",
        "not a repo",
        "https://gitlab.com/owner/repo",
        "https://example.com/psf/requests",
        "/etc/passwd",
        "../../secrets",
    ],
)
def test_refuses_anything_that_is_not_a_github_repository(text):
    with pytest.raises(RepoFetchError):
        parse_repo_ref(text)


def test_the_cache_name_separates_refs_and_is_filesystem_safe():
    plain = RepoRef("psf", "requests")
    branch = RepoRef("psf", "requests", "feature/nested-name")
    assert plain.cache_name != branch.cache_name
    for name in (plain.cache_name, branch.cache_name):
        assert "/" not in name and ".." not in name


def test_the_clone_url_is_built_from_the_parts_not_from_the_input():
    """A pasted URL is never handed to git verbatim; it is rebuilt from owner/repo."""
    assert parse_repo_ref(
        "https://github.com/psf/requests/blob/main/setup.py"
    ).clone_url == "https://github.com/psf/requests.git"


# --- fetching -----------------------------------------------------------------
@pytest.fixture
def origin(tmp_path):
    """A real git repository on disk, to be cloned from over file://."""
    repo = tmp_path / "origin"
    (repo / "pkg").mkdir(parents=True)
    (repo / "pkg" / "__init__.py").write_text("VALUE = 1\n")
    subprocess.run(["git", "init", "-q", "-b", "main", str(repo)], check=True)
    subprocess.run(["git", "-C", str(repo), "add", "-A"], check=True)
    subprocess.run(
        ["git", "-C", str(repo), "-c", "user.email=t@t", "-c", "user.name=t",
         "commit", "-qm", "first"],
        check=True,
    )
    return repo


def test_a_clone_lands_on_disk_and_a_second_ask_reuses_it(tmp_path, origin, monkeypatch):
    """The second fetch must not re-clone: it is the same repository, refreshed."""
    ref = RepoRef("local", "origin")
    monkeypatch.setattr(RepoRef, "clone_url", property(lambda self: f"file://{origin}"))

    first = fetch_repo(ref, cache_root=tmp_path / "cache")
    assert (first / "pkg" / "__init__.py").read_text() == "VALUE = 1\n"

    (origin / "pkg" / "__init__.py").write_text("VALUE = 2\n")
    subprocess.run(["git", "-C", str(origin), "add", "-A"], check=True)
    subprocess.run(
        ["git", "-C", str(origin), "-c", "user.email=t@t", "-c", "user.name=t",
         "commit", "-qm", "second"],
        check=True,
    )

    second = fetch_repo(ref, cache_root=tmp_path / "cache")
    assert second == first, "the cached checkout should be reused, not duplicated"
    assert (second / "pkg" / "__init__.py").read_text() == "VALUE = 2\n", "and refreshed"


def test_the_checkout_keeps_its_history_so_the_recency_prior_works(tmp_path, origin, monkeypatch):
    """`repo_facts.recent_changes` reads git log off the SOURCE, so it must be a repo."""
    monkeypatch.setattr(RepoRef, "clone_url", property(lambda self: f"file://{origin}"))
    path = fetch_repo(RepoRef("local", "origin"), cache_root=tmp_path / "cache")
    assert (path / ".git").is_dir()


def test_a_repository_that_does_not_exist_explains_itself(tmp_path, monkeypatch):
    monkeypatch.setattr(
        RepoRef, "clone_url", property(lambda self: f"file://{tmp_path / 'nowhere'}")
    )
    with pytest.raises(RepoFetchError) as raised:
        fetch_repo(RepoRef("local", "nowhere"), cache_root=tmp_path / "cache", timeout_s=30)
    assert "local/nowhere" in str(raised.value)


def test_an_oversized_checkout_is_refused_rather_than_copied(tmp_path, origin, monkeypatch):
    """A run copies, indexes and tests the tree; an enormous one is a hang, not a demo."""
    monkeypatch.setattr(RepoRef, "clone_url", property(lambda self: f"file://{origin}"))
    monkeypatch.setattr(github, "MAX_CHECKOUT_MB", 0)
    with pytest.raises(RepoFetchError) as raised:
        fetch_repo(RepoRef("local", "origin"), cache_root=tmp_path / "cache")
    assert "larger than" in str(raised.value)


def test_a_token_is_used_for_the_transfer_and_only_for_the_transfer(monkeypatch):
    """It goes in the URL git fetches with, and `origin` is reset to the plain one."""
    monkeypatch.setenv("GITHUB_TOKEN", "ghp_notarealtoken")
    ref = RepoRef("acme", "private-thing")
    assert github._authed_url(ref) == (
        "https://x-access-token:ghp_notarealtoken@github.com/acme/private-thing.git"
    )
    assert "ghp_notarealtoken" not in ref.clone_url


def test_no_token_means_the_url_is_untouched(monkeypatch):
    monkeypatch.delenv("GITHUB_TOKEN", raising=False)
    monkeypatch.delenv("GH_TOKEN", raising=False)
    ref = RepoRef("psf", "requests")
    assert github._authed_url(ref) == ref.clone_url


def test_the_token_does_not_end_up_in_the_checkouts_git_config(tmp_path, origin, monkeypatch):
    """The clone outlives the run, so `origin` must be the plain URL afterwards."""
    monkeypatch.setattr(RepoRef, "clone_url", property(lambda self: f"file://{origin}"))
    monkeypatch.setenv("GITHUB_TOKEN", "ghp_notarealtoken")
    path = fetch_repo(RepoRef("local", "origin"), cache_root=tmp_path / "cache")
    assert "ghp_notarealtoken" not in (path / ".git" / "config").read_text()
