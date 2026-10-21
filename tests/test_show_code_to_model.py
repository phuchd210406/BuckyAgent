"""The repro and fix nodes see the real code, and are judged on real evidence.

Three failures from real runs, each pinned here:

  1. Neither node was shown the source. The repro node guessed signatures, so
     its "reproduction" was a TypeError from calling a function with an
     argument it does not take; the fix node wrote diffs against a file it had
     never seen, so `git apply` refused every one.
  2. That TypeError was counted as a reproduction, because pytest reports it
     as "1 failed" exactly like a real assertion.
  3. The fix node was never told WHY its last diff was rejected: git's own
     message was replaced by "neither fixed ... nor left the suite green".
"""
from __future__ import annotations

import json
import textwrap
from pathlib import Path

import pytest

from repro.agents._common import (
    broken_test_reason,
    is_reproduction,
    source_excerpts,
    why_not_reproduced,
)
from repro.agents.fix import fix_node
from repro.agents.reporter import reporter_node
from repro.agents.repro_agent import repro_agent_node
from repro.contracts import (
    ClientReport,
    ExecutionResult,
    Handover,
    Hypothesis,
    Patch,
    ReportFacts,
    ReproAttempt,
    Verdict,
)
from repro.contracts import TestArtifact as GeneratedTest
from repro.graph.build import run
from repro.graph.sandbox_seam import SOURCE_MAX_CHARS, StubSandbox, WorkspaceSandbox
from repro.llm.base import SchemaValidationError
from repro.llm.fake import ScriptedLLM
from repro.sandbox.patcher import apply_patch
from repro.sandbox.workspace import Workspace


def failed(stdout: str, n: int = 1) -> ExecutionResult:
    return ExecutionResult(exit_code=1, stdout_tail=stdout, stderr_tail="", duration_s=0.1, failed=n)


SIGNATURE_MISMATCH = """\
_____________________________ test_free_shipping ______________________________
tests/test_shipping.py:5: in test_free_shipping
    assert shipping_for(subtotal=55.0, discount=5.5) == 0
           ^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^
E   TypeError: shipping_for() got an unexpected keyword argument 'discount'
=========================== short test summary info ============================
FAILED tests/test_shipping.py::test_free_shipping - TypeError: shipping_for() got...
1 failed in 0.05s
"""

ASSERTION = """\
_________________________________ test_genuine _________________________________
tests/test_fees.py:8: in test_genuine
    assert late_fee(due, returned) == 0
E   AssertionError: assert 40 == 0
1 failed in 0.05s
"""

TYPEERROR_IN_PROJECT = """\
_____________________________ test_inside_project ______________________________
tests/test_fees.py:11: in test_inside_project
    late_fee(due, None)
library/fees.py:19: in days_late
    return max(0, (returned - due).days)
E   TypeError: unsupported operand type(s) for -: 'NoneType' and 'date'
1 failed in 0.05s
"""


# --- 1. a test that fails on its own mistake is not a reproduction ------------


def test_a_signature_mismatch_in_the_test_is_a_broken_test():
    result = failed(SIGNATURE_MISMATCH)

    assert broken_test_reason(result) == (
        "TypeError: shipping_for() got an unexpected keyword argument 'discount'"
    )
    assert not is_reproduction(result)
    assert "broken test" in why_not_reproduced(result)
    assert "discount" in why_not_reproduced(result)


def test_a_real_assertion_failure_is_still_a_reproduction():
    result = failed(ASSERTION)

    assert broken_test_reason(result) is None
    assert is_reproduction(result)


def test_a_typeerror_raised_inside_the_project_is_the_bug_not_the_test():
    # The deepest frame is the project's own file: that TypeError IS the bug.
    result = failed(TYPEERROR_IN_PROJECT)

    assert broken_test_reason(result) is None
    assert is_reproduction(result)


@pytest.mark.parametrize(
    "line",
    [
        "E   NameError: name 'Basket' is not defined",
        "E   TypeError: total() missing 1 required positional argument: 'lines'",
        "E   TypeError: late_fee() takes 2 positional arguments but 3 were given",
        "E   AttributeError: module 'shopcart.pricing' has no attribute 'total_price'",
        "E   ModuleNotFoundError: No module named 'shop'",
    ],
)
def test_mistakes_only_a_wrong_test_makes_are_broken_tests(line):
    result = failed(f"tests/test_x.py:3: in test_x\n    call()\n{line}\n1 failed\n")

    assert broken_test_reason(result) is not None
    assert not is_reproduction(result)


@pytest.mark.parametrize(
    "line",
    [
        # These can be exactly what the client saw, so they still count.
        "E   AttributeError: 'NoneType' object has no attribute 'title'",
        "E   TypeError: 'NoneType' object is not subscriptable",
        "E   ZeroDivisionError: division by zero",
    ],
)
def test_errors_a_real_bug_can_cause_still_count(line):
    result = failed(f"tests/test_x.py:3: in test_x\n    call()\n{line}\n1 failed\n")

    assert broken_test_reason(result) is None
    assert is_reproduction(result)


def test_one_broken_test_in_the_file_spoils_the_reproduction():
    # A fix is verified by running the whole file again: a broken test in it
    # would stay red after any fix, so the file proves nothing.
    result = failed(ASSERTION + SIGNATURE_MISMATCH, n=2)

    assert not is_reproduction(result)


# --- 2. the sandbox can show the model a source file, safely -----------------


@pytest.fixture
def project(tmp_path: Path):
    source = tmp_path / "project"
    (source / "shop").mkdir(parents=True)
    (source / "tests").mkdir()
    (source / "shop" / "__init__.py").write_text("")
    (source / "shop" / "pricing.py").write_text(
        textwrap.dedent(
            '''\
            FREE_OVER = 50


            def shipping_for(subtotal):
                """Postage for a basket of this value."""
                if subtotal > FREE_OVER:
                    return 0
                return 5


            def total(subtotal, discount=0):
                discounted = subtotal - discount
                return discounted + shipping_for(discounted)
            '''
        )
    )
    (source / "tests" / "test_pricing.py").write_text(
        "from shop.pricing import shipping_for\n\n\ndef test_small_basket_pays_postage():\n"
        "    assert shipping_for(10) == 5\n"
    )
    (source / ".env").write_text("SECRET_KEY=hunter2\n")
    ws = Workspace(source, root=tmp_path / "ws")
    try:
        yield ws
    finally:
        ws.close()


def test_read_source_returns_the_real_file(project):
    box = WorkspaceSandbox(project)

    code = box.read_source("shop/pricing.py")

    assert code is not None and "def shipping_for(subtotal):" in code


@pytest.mark.parametrize("path", [".env", "../project/shop/pricing.py", "/etc/passwd", "shop/missing.py", ""])
def test_read_source_never_shows_secrets_escapes_or_missing_files(project, path):
    assert WorkspaceSandbox(project).read_source(path) is None


def test_read_source_is_capped(project):
    (project.path / "shop" / "big.py").write_text("x = 1\n" * 10_000)

    code = WorkspaceSandbox(project).read_source("shop/big.py")

    assert code is not None and len(code) <= SOURCE_MAX_CHARS


def test_source_excerpts_reads_distinct_suspected_files_only(project):
    hypotheses = [
        Hypothesis(file_path="shop/pricing.py", rationale="a. b.", confidence=0.9),
        Hypothesis(file_path="shop/pricing.py", rationale="a. b.", confidence=0.5),
        Hypothesis(file_path=".env", rationale="a. b.", confidence=0.4),
    ]

    excerpts = source_excerpts(WorkspaceSandbox(project), hypotheses)

    assert [e["path"] for e in excerpts] == ["shop/pricing.py"]


def test_a_sandbox_without_read_source_still_works():
    class OldSandbox:  # a test double written before read_source existed
        pass

    assert source_excerpts(OldSandbox(), [Hypothesis(file_path="a.py", rationale="a. b.", confidence=1)]) == []


def test_the_agent_may_not_overwrite_the_projects_own_tests(project):
    box = WorkspaceSandbox(project)
    original = (project.path / "tests" / "test_pricing.py").read_text()

    with pytest.raises(ValueError, match="project's own tests"):
        box.write_test(GeneratedTest(path="tests/test_pricing.py", source="def test_x():\n    assert 0\n"))

    assert (project.path / "tests" / "test_pricing.py").read_text() == original


def test_remove_test_only_removes_what_the_agent_wrote(project):
    box = WorkspaceSandbox(project)
    box.write_test(GeneratedTest(path="tests/test_new.py", source="def test_x():\n    pass\n"))
    # Rewriting its own file on a later attempt is allowed.
    box.write_test(GeneratedTest(path="tests/test_new.py", source="def test_y():\n    pass\n"))

    box.remove_test("tests/test_new.py")
    box.remove_test("tests/test_pricing.py")

    assert not (project.path / "tests" / "test_new.py").exists()
    assert (project.path / "tests" / "test_pricing.py").exists()


# --- 3. the nodes put the source, and the evidence, in front of the model ----


def facts() -> ReportFacts:
    return ReportFacts(
        observed_behaviour="charged postage on a basket over $50",
        entrypoint_hint="checkout",
        confidence=0.9,
    )


def pricing_hypothesis() -> Hypothesis:
    return Hypothesis(file_path="shop/pricing.py", symbol="total", rationale="a. b.", confidence=0.8)


def sent(llm: ScriptedLLM, index: int = -1) -> dict:
    return json.loads(llm.calls[index][1])


def test_the_repro_node_is_shown_the_suspected_source(project):
    llm = ScriptedLLM([GeneratedTest(path="tests/test_bug.py", source="def test_bug():\n    assert 0\n")])
    state = {"facts": facts(), "hypotheses": [pricing_hypothesis()], "repro_count": 0}

    repro_agent_node(state, llm, sandbox=WorkspaceSandbox(project))

    source = sent(llm)["source"]
    assert source[0]["path"] == "shop/pricing.py"
    assert "def shipping_for(subtotal):" in source[0]["code"]


def test_the_repro_node_sees_what_pytest_said_last_time(project):
    llm = ScriptedLLM([GeneratedTest(path="tests/test_bug2.py", source="def test_bug():\n    assert 0\n")])
    last = ReproAttempt(
        attempt_no=1,
        test=GeneratedTest(path="tests/test_bug.py", source=""),
        result=failed(SIGNATURE_MISMATCH),
        reproduced=False,
        reasoning="broken",
    )
    state = {"facts": facts(), "hypotheses": [], "repro_attempts": [last], "repro_count": 1}

    repro_agent_node(state, llm, sandbox=WorkspaceSandbox(project))

    assert "unexpected keyword argument 'discount'" in sent(llm)["previous"][0]["output"]


def test_a_test_that_did_not_reproduce_is_removed_from_the_workspace(project):
    box = WorkspaceSandbox(project)
    broken = GeneratedTest(
        path="tests/test_bug.py",
        source="from shop.pricing import shipping_for\n\n\ndef test_bug():\n"
        "    assert shipping_for(subtotal=55, discount=5) == 0\n",
    )

    out = repro_agent_node(
        {"facts": facts(), "hypotheses": [], "repro_count": 0}, ScriptedLLM([broken]), sandbox=box
    )

    attempt = out["repro_attempts"][0]
    assert attempt.result.failed == 1  # pytest called it a failure...
    assert attempt.reproduced is False  # ...but it is the test's own mistake
    assert "broken test" in attempt.reasoning
    assert not (project.path / "tests" / "test_bug.py").exists()


def test_a_real_reproduction_is_kept_for_the_fix_node(project):
    box = WorkspaceSandbox(project)
    real = GeneratedTest(
        path="tests/test_bug.py",
        source="from shop.pricing import total\n\n\ndef test_bug():\n    assert total(55, discount=5.5) == 49.5\n",
    )

    out = repro_agent_node(
        {"facts": facts(), "hypotheses": [], "repro_count": 0}, ScriptedLLM([real]), sandbox=box
    )

    assert out["repro_attempts"][0].reproduced is True
    assert (project.path / "tests" / "test_bug.py").exists()


class RefusingApply(StubSandbox):
    def apply_patch(self, patch):
        return False, "git apply --check rejected the diff: error: patch failed: shop/pricing.py:3"


def reproduced_state() -> dict:
    attempt = ReproAttempt(
        attempt_no=1,
        test=GeneratedTest(path="tests/test_bug.py", source="def test_bug():\n    assert 0\n"),
        result=failed(ASSERTION),
        reproduced=True,
        reasoning="ran and failed",
    )
    return {"facts": facts(), "hypotheses": [pricing_hypothesis()], "repro_attempts": [attempt], "fix_count": 0}


def bad_diff() -> Patch:
    return Patch(unified_diff="--- a/shop/pricing.py\n+++ b/shop/pricing.py\n", files_touched=[], rationale="a. b.")


def test_a_diff_that_does_not_apply_says_so_in_plain_words():
    out = fix_node(reproduced_state(), ScriptedLLM([bad_diff()]), sandbox=RefusingApply())

    attempt = out["fix_attempts"][0]
    assert attempt.accepted is False
    assert attempt.reasoning.startswith("The patch was not applied")
    assert "patch failed: shop/pricing.py:3" in attempt.reasoning
    assert "neither fixed" not in attempt.reasoning


def test_the_next_fix_attempt_is_told_what_git_said():
    state = reproduced_state()
    first = fix_node(state, ScriptedLLM([bad_diff()]), sandbox=RefusingApply())
    state = {**state, "fix_attempts": first["fix_attempts"], "fix_count": 1}
    llm = ScriptedLLM([bad_diff()])

    fix_node(state, llm, sandbox=RefusingApply())

    rejected = sent(llm)["rejected"][0]
    assert "patch failed: shop/pricing.py:3" in rejected["evidence"]
    assert rejected["reasoning"].startswith("The patch was not applied")


def test_the_fix_node_is_shown_the_suspected_source(project):
    llm = ScriptedLLM([bad_diff()])

    fix_node(reproduced_state(), llm, sandbox=WorkspaceSandbox(project))

    assert "def total(subtotal, discount=0):" in sent(llm)["source"][0]["code"]


def test_a_diff_with_miscounted_hunk_headers_still_applies(project):
    # Models get the @@ line counts wrong far more often than the context.
    diff = (
        "--- a/shop/pricing.py\n"
        "+++ b/shop/pricing.py\n"
        "@@ -11,3 +11,99 @@\n"
        " def total(subtotal, discount=0):\n"
        "     discounted = subtotal - discount\n"
        "-    return discounted + shipping_for(discounted)\n"
        "+    return discounted + shipping_for(subtotal)\n"
    )

    ok, message = apply_patch(project, Patch(unified_diff=diff, files_touched=["shop/pricing.py"], rationale="a. b."))

    assert ok, message
    assert "shipping_for(subtotal)" in (project.path / "shop" / "pricing.py").read_text()


# --- 4. end to end: a broken first test no longer blocks the fix --------------


def test_a_broken_first_test_neither_counts_nor_vetoes_the_fix(project, tmp_path):
    """The whole story, against a real workspace and real pytest.

    Attempt 1 guesses a signature and breaks. Before: it counted as the
    reproduction, and its file stayed in tests/, red forever, so no patch could
    ever leave the suite green. Now: it is discarded, attempt 2 reproduces
    the bug for real, and the right patch is accepted.
    """
    broken = GeneratedTest(
        path="tests/test_attempt_one.py",
        source="from shop.pricing import shipping_for\n\n\ndef test_bug():\n"
        "    assert shipping_for(subtotal=55, discount=5.5) == 0\n",
    )
    real = GeneratedTest(
        path="tests/test_attempt_two.py",
        source="from shop.pricing import total\n\n\ndef test_bug():\n    assert total(55, discount=5.5) == 49.5\n",
    )
    fix = Patch(
        unified_diff=(
            "--- a/shop/pricing.py\n"
            "+++ b/shop/pricing.py\n"
            "@@ -11,3 +11,3 @@\n"
            " def total(subtotal, discount=0):\n"
            "     discounted = subtotal - discount\n"
            "-    return discounted + shipping_for(discounted)\n"
            "+    return discounted + shipping_for(subtotal)\n"
        ),
        files_touched=["shop/pricing.py"],
        rationale="Postage was judged on the discounted total. It now uses the pre-discount one.",
    )
    llm = ScriptedLLM(
        [facts(), pricing_hypothesis(), broken, real, fix, Handover(dev_summary="d", client_reply="c")]
    )
    report = ClientReport(run_id="e2e", raw_text="charged postage over $50", repo_path=str(project.path))

    record = run(report, llm, sandbox=WorkspaceSandbox(project))

    first, second = record.repro_attempts
    assert first.reproduced is False and "broken test" in first.reasoning
    assert second.reproduced is True
    assert record.fix_attempts[0].accepted is True
    assert record.fix_attempts[0].suite.green
    assert record.verdict == Verdict.REPRODUCED_AND_FIXED


# --- 5. a letter the model could not write does not cost the verdict ---------


class NoLetters:
    calls: list = []

    def structured(self, **kw):
        raise SchemaValidationError("no cassette for Handover")


def test_the_verdict_survives_when_the_letters_cannot_be_written():
    state = {**reproduced_state(), "report": ClientReport(run_id="r", raw_text="x", repo_path="/tmp/x")}

    out = reporter_node(state, NoLetters())

    assert out["verdict"] == Verdict.REPRODUCED_NOT_FIXED
    assert out["handover"] is None
