"""Patch application tests. OWNER: Engineer B.

Every behavioural test runs TWICE -- once through `git apply`, once through the
pure-Python fallback with GIT_BINARY set to None. A fallback that is only
reached on a machine without git is a fallback nobody ever runs, and it would
rot silently until the one demo laptop that lacks git.

The atomicity tests hash the whole tree, directories included: a patch that
half-applies and leaves an empty directory behind has still changed the tree
that attempt N+1 is written against.
"""
from __future__ import annotations

import hashlib
import os
from pathlib import Path

import pytest

from repro.contracts import Patch
from repro.sandbox import patcher
from repro.sandbox.patcher import apply_patch, revert_patch
from repro.sandbox.workspace import Workspace

SHOPCART = Path(__file__).resolve().parents[1] / "fixtures" / "demo_repos" / "shopcart"


def hash_tree(root: Path) -> str:
    """A digest over every path, every byte, and the shape of the tree."""
    h = hashlib.sha256()
    for dirpath, dirnames, filenames in os.walk(root):
        dirnames.sort()
        here = Path(dirpath)
        for name in sorted(dirnames) + sorted(filenames):
            entry = here / name
            h.update(entry.relative_to(root).as_posix().encode() + b"\0")
            if entry.is_symlink():
                h.update(b"symlink\0" + os.readlink(entry).encode() + b"\0")
            elif entry.is_file():
                h.update(b"file\0" + entry.read_bytes() + b"\0")
            else:
                h.update(b"dir\0")
    return h.hexdigest()


@pytest.fixture(params=["git", "python"])
def backend(request, monkeypatch):
    """Run every test through both applier backends."""
    if request.param == "python":
        monkeypatch.setattr(patcher, "GIT_BINARY", None)
    else:
        if patcher.GIT_BINARY is None:  # pragma: no cover
            pytest.skip("git is not installed")
    return request.param


@pytest.fixture
def ws(tmp_path):
    with Workspace(SHOPCART, tmp_path / "roots") as workspace:
        yield workspace
    patcher.forget(workspace)


def patch_of(diff: str, files: list[str] | None = None) -> Patch:
    return Patch(unified_diff=diff, files_touched=files or [], rationale="test patch")


# The real shopcart fix: compare the threshold against the PRE-discount total.
GOOD_DIFF = """\
--- a/shopcart/pricing.py
+++ b/shopcart/pricing.py
@@ -41,4 +41,4 @@
 def total(lines: list[Line], promo: str | None = None) -> float:
     subtotal = sum(line.subtotal for line in lines)
     discounted = apply_promo(subtotal, promo)
-    return round(discounted + shipping_for(discounted), 2)
+    return round(discounted + shipping_for(subtotal), 2)
"""

BAD_CONTEXT_DIFF = """\
--- a/shopcart/pricing.py
+++ b/shopcart/pricing.py
@@ -41,4 +41,4 @@
 def total(lines: list[Line], promo: str | None = None) -> float:
     subtotal = sum(line.subtotal for line in lines)
-    THIS LINE IS NOT IN THE FILE
+    replacement
 something else
"""


# --- 1. clean apply ---------------------------------------------------------
def test_clean_apply(ws, backend):
    before = hash_tree(ws.path)

    applied, message = apply_patch(ws, patch_of(GOOD_DIFF))

    assert applied is True, message
    assert "shopcart/pricing.py" in message
    assert "shipping_for(subtotal)" in ws.read_file("shopcart/pricing.py")
    assert hash_tree(ws.path) != before


# --- 2. apply then revert returns to the original hash ----------------------
def test_apply_then_revert_restores_the_original_hash(ws, backend):
    before = hash_tree(ws.path)

    assert apply_patch(ws, patch_of(GOOD_DIFF))[0] is True
    assert hash_tree(ws.path) != before, "the patch did not actually change anything"

    revert_patch(ws, patch_of(GOOD_DIFF))

    assert hash_tree(ws.path) == before


# --- 3. a bad hunk leaves the tree untouched --------------------------------
def test_a_bad_hunk_leaves_the_tree_byte_identical(ws, backend):
    before = hash_tree(ws.path)

    applied, message = apply_patch(ws, patch_of(BAD_CONTEXT_DIFF))

    assert applied is False
    assert message
    assert hash_tree(ws.path) == before, "a failed patch changed the tree"


def test_a_multi_file_diff_that_half_applies_leaves_nothing_behind(ws, backend):
    """The first file patches cleanly; the second does not. Neither may land."""
    diff = GOOD_DIFF + """\
--- a/shopcart/__init__.py
+++ b/shopcart/__init__.py
@@ -1 +1 @@
-THIS IS NOT WHAT THE FILE CONTAINS
+something
"""
    before = hash_tree(ws.path)

    applied, message = apply_patch(ws, patch_of(diff))

    assert applied is False
    assert hash_tree(ws.path) == before
    assert "shipping_for(discounted)" in ws.read_file("shopcart/pricing.py")


def test_a_failed_creation_leaves_no_new_directory(ws, backend):
    """An empty directory left behind is a change to the tree like any other."""
    diff = """\
--- /dev/null
+++ b/deeply/nested/new_module.py
@@ -0,0 +1 @@
+VALUE = 1
--- a/shopcart/pricing.py
+++ b/shopcart/pricing.py
@@ -1 +1 @@
-NOT THE REAL FIRST LINE
+replacement
"""
    before = hash_tree(ws.path)

    applied, _ = apply_patch(ws, patch_of(diff))

    assert applied is False
    assert not (ws.path / "deeply").exists(), "a directory survived a failed patch"
    assert hash_tree(ws.path) == before


def test_a_backend_that_half_applies_is_rolled_back(ws, backend, monkeypatch):
    """Force the restore path that the real backends never reach.

    git apply is all-or-nothing across files, and the Python applier computes
    every file before writing any, so on a normal rejection nothing was written
    and `_restore` has nothing to undo -- which means deleting the restore call
    entirely does not fail a single test. That is the atomicity guarantee going
    untested. So inject a backend that writes one file, creates a directory, and
    THEN fails, which is exactly the state a crash mid-apply would leave.
    """
    diff = GOOD_DIFF + """\
--- /dev/null
+++ b/deeply/nested/new_module.py
@@ -0,0 +1 @@
+VALUE = 1
"""
    before = hash_tree(ws.path)

    def half_applies(*_args, **_kwargs):
        (ws.path / "shopcart" / "pricing.py").write_text("# clobbered\n")
        target = ws.path / "deeply" / "nested" / "new_module.py"
        target.parent.mkdir(parents=True)
        target.write_text("VALUE = 1\n")
        return False, "the backend gave up half way through"

    monkeypatch.setattr(patcher, "_git_apply", half_applies)
    monkeypatch.setattr(patcher, "_python_apply", half_applies)

    applied, message = apply_patch(ws, patch_of(diff))

    assert applied is False
    assert "half way" in message
    assert hash_tree(ws.path) == before, "a half-applied patch left a trace"
    assert not (ws.path / "deeply").exists(), "the directories it created survived"
    assert "shipping_for(discounted)" in ws.read_file("shopcart/pricing.py")


def test_a_half_apply_is_rolled_back_even_when_the_backend_raises(ws, backend, monkeypatch):
    """A backend that throws must not be a way to leave the tree modified."""
    before = hash_tree(ws.path)

    def explodes(*_args, **_kwargs):
        (ws.path / "shopcart" / "pricing.py").write_text("# clobbered\n")
        raise OSError("disk went away mid-write")

    monkeypatch.setattr(patcher, "_git_apply", explodes)
    monkeypatch.setattr(patcher, "_python_apply", explodes)

    applied, message = apply_patch(ws, patch_of(GOOD_DIFF))

    assert applied is False
    assert "disk went away" in message
    assert hash_tree(ws.path) == before


# --- 4. a diff referencing ../../ is rejected -------------------------------
@pytest.mark.parametrize(
    "escape",
    ["../../etc/passwd", "/etc/passwd", "shopcart/../../../etc/passwd"],
)
def test_a_traversing_diff_is_rejected(ws, backend, tmp_path, escape):
    diff = f"""\
--- a/{escape}
+++ b/{escape}
@@ -0,0 +1 @@
+pwned
"""
    before = hash_tree(ws.path)

    applied, message = apply_patch(ws, patch_of(diff))

    assert applied is False
    assert "outside the workspace" in message
    assert hash_tree(ws.path) == before
    assert not (tmp_path / "etc").exists()


def test_a_symlink_out_of_the_workspace_is_not_a_way_in(ws, backend, tmp_path):
    outside = tmp_path / "outside.txt"
    outside.write_text("secret\n")
    (ws.path / "escape").symlink_to(outside)

    diff = """\
--- a/escape
+++ b/escape
@@ -1 +1 @@
-secret
+pwned
"""
    applied, message = apply_patch(ws, patch_of(diff))

    assert applied is False
    assert "outside the workspace" in message
    assert outside.read_text() == "secret\n"


# --- 5. a diff touching a file that does not exist is rejected --------------
def test_a_diff_for_a_missing_file_is_rejected(ws, backend):
    diff = """\
--- a/shopcart/does_not_exist.py
+++ b/shopcart/does_not_exist.py
@@ -1 +1 @@
-old
+new
"""
    before = hash_tree(ws.path)

    applied, message = apply_patch(ws, patch_of(diff))

    assert applied is False
    assert "does not exist" in message
    assert hash_tree(ws.path) == before


# --- malformed input is data, never an exception ----------------------------
@pytest.mark.parametrize(
    "diff",
    [
        "",
        "   \n",
        "this is prose, not a diff\n",
        "```diff\n--- a/x\n+++ b/x\n```\n",
        "--- a/shopcart/pricing.py\n+++ b/shopcart/pricing.py\n",  # no hunks
        "--- a/shopcart/pricing.py\n+++ b/shopcart/pricing.py\n@@ nonsense @@\n",
        "@@ -1 +1 @@\n-a\n+b\n",  # hunk with no file header
    ],
)
def test_malformed_diffs_return_false_and_never_raise(ws, backend, diff):
    before = hash_tree(ws.path)

    applied, message = apply_patch(ws, patch_of(diff))

    assert applied is False
    assert message, "a rejection must say why: the model is shown this text"
    assert hash_tree(ws.path) == before


# --- creation and deletion --------------------------------------------------
def test_a_created_file_is_removed_again_by_revert(ws, backend):
    before = hash_tree(ws.path)
    diff = """\
--- /dev/null
+++ b/shopcart/helper.py
@@ -0,0 +1,2 @@
+def helper():
+    return 1
"""
    applied, message = apply_patch(ws, patch_of(diff))
    assert applied is True, message
    assert (ws.path / "shopcart" / "helper.py").is_file()

    revert_patch(ws, patch_of(diff))

    assert not (ws.path / "shopcart" / "helper.py").exists()
    assert hash_tree(ws.path) == before


def test_a_deleted_file_comes_back_on_revert(ws, backend):
    ws.write_file("notes.txt", "line one\nline two\n")
    before = hash_tree(ws.path)
    diff = """\
--- a/notes.txt
+++ /dev/null
@@ -1,2 +0,0 @@
-line one
-line two
"""
    applied, message = apply_patch(ws, patch_of(diff))
    assert applied is True, message
    assert not (ws.path / "notes.txt").exists()

    revert_patch(ws, patch_of(diff))

    assert ws.read_file("notes.txt") == "line one\nline two\n"
    assert hash_tree(ws.path) == before


def test_the_two_backends_agree(tmp_path):
    """git and the fallback must accept and reject exactly the same diffs.

    Written after they disagreed: git refused a degenerate `@@ -0,0 +0,0 @@`
    hunk and the Python applier silently deleted the file. A fallback that is
    more permissive than the primary is a fallback that ships a patch the real
    backend would have refused.
    """
    cases = {
        "good": GOOD_DIFF,
        "bad_context": BAD_CONTEXT_DIFF,
        "missing_file": "--- a/nope.py\n+++ b/nope.py\n@@ -1 +1 @@\n-a\n+b\n",
        "traversal": "--- a/../../x\n+++ b/../../x\n@@ -1 +1 @@\n-a\n+b\n",
        "empty_hunk": "--- a/shopcart/__init__.py\n+++ /dev/null\n@@ -0,0 +0,0 @@\n",
        "prose": "not a diff at all\n",
        "no_hunks": "--- a/shopcart/pricing.py\n+++ b/shopcart/pricing.py\n",
        "creation": "--- /dev/null\n+++ b/new.py\n@@ -0,0 +1 @@\n+VALUE = 1\n",
    }

    outcomes: dict[str, dict[str, tuple[bool, str]]] = {}
    for which in ("git", "python"):
        outcomes[which] = {}
        original = patcher.GIT_BINARY
        try:
            patcher.GIT_BINARY = None if which == "python" else "git"
            for name, diff in cases.items():
                with Workspace(SHOPCART, tmp_path / f"roots-{which}-{name}") as box:
                    applied, _ = apply_patch(box, patch_of(diff))
                    outcomes[which][name] = (applied, hash_tree(box.path))
                    patcher.forget(box)
        finally:
            patcher.GIT_BINARY = original

    disagreements = {
        name: (outcomes["git"][name][0], outcomes["python"][name][0])
        for name in cases
        if outcomes["git"][name][0] != outcomes["python"][name][0]
    }
    assert not disagreements, f"backends disagree on accept/reject: {disagreements}"

    differing = [
        name for name in cases if outcomes["git"][name][1] != outcomes["python"][name][1]
    ]
    assert not differing, f"backends produced different trees for: {differing}"


# --- revert semantics -------------------------------------------------------
def test_revert_with_nothing_applied_is_a_no_op(ws, backend):
    before = hash_tree(ws.path)
    revert_patch(ws, patch_of(GOOD_DIFF))  # must not raise
    assert hash_tree(ws.path) == before


def test_revert_twice_does_not_undo_more_than_was_applied(ws, backend):
    before = hash_tree(ws.path)
    assert apply_patch(ws, patch_of(GOOD_DIFF))[0] is True

    revert_patch(ws, patch_of(GOOD_DIFF))
    revert_patch(ws, patch_of(GOOD_DIFF))  # nothing left; must not raise

    assert hash_tree(ws.path) == before


def test_the_attempt_loop_leaves_a_clean_tree_between_tries(ws, backend):
    """fix._verify's real shape: apply, judge, revert, try again."""
    before = hash_tree(ws.path)

    for _ in range(3):
        applied, message = apply_patch(ws, patch_of(GOOD_DIFF))
        assert applied is True, message
        revert_patch(ws, patch_of(GOOD_DIFF))
        assert hash_tree(ws.path) == before


def test_two_workspaces_keep_separate_snapshots(tmp_path, backend):
    with Workspace(SHOPCART, tmp_path / "roots") as a, Workspace(SHOPCART, tmp_path / "roots") as b:
        before_b = hash_tree(b.path)

        assert apply_patch(a, patch_of(GOOD_DIFF))[0] is True
        revert_patch(b, patch_of(GOOD_DIFF))  # b never applied anything

        assert "shipping_for(subtotal)" in a.read_file("shopcart/pricing.py")
        assert hash_tree(b.path) == before_b
        patcher.forget(a)
        patcher.forget(b)


# --- diff dialects ----------------------------------------------------------
def test_a_git_style_diff_header_is_understood(ws, backend):
    diff = """\
diff --git a/shopcart/pricing.py b/shopcart/pricing.py
index 1234567..89abcde 100644
--- a/shopcart/pricing.py
+++ b/shopcart/pricing.py
@@ -41,4 +41,4 @@ def total(lines, promo=None):
 def total(lines: list[Line], promo: str | None = None) -> float:
     subtotal = sum(line.subtotal for line in lines)
     discounted = apply_promo(subtotal, promo)
-    return round(discounted + shipping_for(discounted), 2)
+    return round(discounted + shipping_for(subtotal), 2)
"""
    applied, message = apply_patch(ws, patch_of(diff))

    assert applied is True, message
    assert "shipping_for(subtotal)" in ws.read_file("shopcart/pricing.py")


def test_a_diff_without_a_b_prefixes_is_understood(ws, backend):
    diff = GOOD_DIFF.replace("--- a/", "--- ").replace("+++ b/", "+++ ")

    applied, message = apply_patch(ws, patch_of(diff))

    assert applied is True, message
    assert "shipping_for(subtotal)" in ws.read_file("shopcart/pricing.py")


def test_content_that_looks_like_a_file_header_is_not_one(ws, backend):
    """A removed line reads as `-...`; `--- a/x` inside a hunk is content."""
    ws.write_file("notes.txt", "keep\n--- a/fake.py\nkeep2\n")
    diff = """\
--- a/notes.txt
+++ b/notes.txt
@@ -1,3 +1,3 @@
 keep
---- a/fake.py
+--- a/real.py
 keep2
"""
    applied, message = apply_patch(ws, patch_of(diff))

    assert applied is True, message
    assert ws.read_file("notes.txt") == "keep\n--- a/real.py\nkeep2\n"
    assert not (ws.path / "fake.py").exists()


def test_a_closed_workspace_is_refused_not_crashed(tmp_path, backend):
    ws = Workspace(SHOPCART, tmp_path / "roots")
    ws.close()

    applied, message = apply_patch(ws, patch_of(GOOD_DIFF))

    assert applied is False
    assert "closed" in message
