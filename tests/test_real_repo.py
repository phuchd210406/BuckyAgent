"""One run against a REAL repository, from clone to accepted patch.

Everything here is real except the model: a git repository is cloned over a
file:// remote (so the suite needs no network), a virtualenv is built, the
project is installed into it, the generated test is written into a throwaway
copy, pytest runs it for real, the patcher applies a real unified diff, and the
suite runs again. The model's four replies are scripted, because what is under
test is the machinery around it -- and because a test whose result depends on a
language model is not a test.

This is the case the whole change exists for. Before it, every part of the
system downstream of intake had only ever seen a fixture we planted ourselves.
"""
from __future__ import annotations

import subprocess

import pytest

from repro.contracts import (
    ClientReport,
    Handover,
    Hypothesis,
    Patch,
    ReportFacts,
    Verdict,
)

# Aliased: pytest tries to COLLECT anything named Test*, and warns that it
# cannot because the pydantic model has an __init__.
from repro.contracts import TestArtifact as GeneratedTest
from repro.graph.build import run
from repro.llm.fake import ScriptedLLM
from repro.sandbox.github import RepoRef, fetch_repo

pytestmark = pytest.mark.slow

SOURCE = '''\
"""Prices a basket. Free postage over $50."""

FREE_POSTAGE_OVER = 50.0
POSTAGE = 4.99


def total(subtotal, promo_percent=0):
    discounted = subtotal * (1 - promo_percent / 100)
    postage = 0.0 if discounted >= FREE_POSTAGE_OVER else POSTAGE
    return round(discounted + postage, 2)
'''

EXISTING_TEST = '''\
from store.pricing import total


def test_no_promo_over_the_threshold_is_free():
    assert total(60.0) == 60.0


def test_a_small_basket_pays_postage():
    assert total(10.0) == 14.99
'''

# The bug the client is describing: the promo comes off BEFORE the threshold is
# checked, so a $55 basket with 10% off drops under $50 and is charged postage.
REPRO_TEST = '''\
from store.pricing import total


def test_free_postage_uses_the_pre_discount_subtotal():
    assert total(55.0, promo_percent=10) == 49.5
'''

PATCH = """\
diff --git a/store/pricing.py b/store/pricing.py
--- a/store/pricing.py
+++ b/store/pricing.py
@@ -6,5 +6,5 @@ POSTAGE = 4.99
 
 def total(subtotal, promo_percent=0):
     discounted = subtotal * (1 - promo_percent / 100)
-    postage = 0.0 if discounted >= FREE_POSTAGE_OVER else POSTAGE
+    postage = 0.0 if subtotal >= FREE_POSTAGE_OVER else POSTAGE
     return round(discounted + postage, 2)
"""


@pytest.fixture
def remote(tmp_path):
    """A git repository that looks like a small client project on GitHub."""
    repo = tmp_path / "remote"
    (repo / "store").mkdir(parents=True)
    (repo / "tests").mkdir()
    (repo / "store" / "__init__.py").write_text("")
    (repo / "store" / "pricing.py").write_text(SOURCE)
    (repo / "tests" / "test_pricing.py").write_text(EXISTING_TEST)
    # A real project declares its dependencies and expects to be importable by
    # name, which is exactly what a bare interpreter cannot do with it.
    (repo / "pyproject.toml").write_text(
        "[project]\nname = 'store'\nversion = '0.1.0'\n"
        "[build-system]\nrequires = ['setuptools']\n"
        "build-backend = 'setuptools.build_meta'\n"
    )
    subprocess.run(["git", "init", "-q", "-b", "main", str(repo)], check=True)
    subprocess.run(["git", "-C", str(repo), "add", "-A"], check=True)
    subprocess.run(
        ["git", "-C", str(repo), "-c", "user.email=t@t", "-c", "user.name=t",
         "commit", "-qm", "initial"],
        check=True,
    )
    return repo


def scripted_model() -> ScriptedLLM:
    """The four objects a run needs, in the order the graph asks for them."""
    return ScriptedLLM(
        [
            ReportFacts(
                observed_behaviour="charged postage on a basket over $50",
                expected_behaviour="no postage over $50",
                steps=["bought items totalling more than $50 with a promo code"],
                entrypoint_hint="checkout",
                confidence=0.9,
            ),
            Hypothesis(
                file_path="store/pricing.py",
                symbol="total",
                rationale=(
                    "The client was 'charged postage even though the site says free postage "
                    "over $50'. total() compares the threshold against the discounted "
                    "subtotal, so a promo can drop a $55 basket under $50."
                ),
                confidence=0.8,
            ),
            GeneratedTest(path="tests/test_repro_postage.py", source=REPRO_TEST),
            Patch(
                unified_diff=PATCH,
                files_touched=["store/pricing.py"],
                rationale=(
                    "The threshold was compared against the post-promo subtotal. It now "
                    "uses the pre-discount subtotal, leaving the discount on goods only."
                ),
            ),
            Handover(
                dev_summary="## Free postage used the discounted subtotal",
                client_reply="You were charged postage on a basket over $50. We reproduced it.",
            ),
        ]
    )


def test_a_cloned_repository_goes_from_complaint_to_verified_patch(
    tmp_path, remote, monkeypatch
):
    monkeypatch.setattr(RepoRef, "clone_url", property(lambda self: f"file://{remote}"))
    monkeypatch.setenv("REPRO_WORKSPACE", str(tmp_path / "workspaces"))
    monkeypatch.setenv("REPRO_SANDBOX_TIMEOUT_S", "180")

    checkout = fetch_repo(RepoRef("acme", "store"), cache_root=tmp_path / "cache")

    record = run(
        ClientReport(
            run_id="realrepo1",
            raw_text=(
                "hi, i tried to buy stuff this morning and it charged me postage even "
                "though the site says free postage over $50"
            ),
            repo_path=str(checkout),
        ),
        scripted_model(),
    )

    assert record.verdict is Verdict.REPRODUCED_AND_FIXED, record.model_dump_json(indent=2)
    assert record.check_invariants() == []

    # The evidence, not the verdict: red first, then green twice.
    repro = record.repro_attempts[-1]
    assert repro.reproduced and repro.result.failed == 1, repro.result.stdout_tail[-1500:]
    fix = record.fix_attempts[-1]
    assert fix.accepted
    assert fix.target_test.green, fix.target_test.stdout_tail[-1500:]
    assert fix.suite.green and fix.suite.passed >= 3, fix.suite.stdout_tail[-1500:]

    # The original checkout is untouched: the agent only ever wrote to a copy.
    assert (checkout / "store" / "pricing.py").read_text() == SOURCE
    assert not (checkout / "tests" / "test_repro_postage.py").exists()


def test_the_workspace_and_its_virtualenv_do_not_outlive_the_run(
    tmp_path, remote, monkeypatch
):
    """A leaked workspace is a copy of a client's repo; a leaked venv is 200 MB."""
    monkeypatch.setattr(RepoRef, "clone_url", property(lambda self: f"file://{remote}"))
    workspaces = tmp_path / "workspaces"
    monkeypatch.setenv("REPRO_WORKSPACE", str(workspaces))

    checkout = fetch_repo(RepoRef("acme", "store"), cache_root=tmp_path / "cache")
    run(
        ClientReport(run_id="realrepo2", raw_text="postage charged", repo_path=str(checkout)),
        scripted_model(),
    )

    leftovers = [path for path in workspaces.rglob("*") if path.is_file()]
    assert leftovers == [], f"{len(leftovers)} files left behind, e.g. {leftovers[:3]}"
