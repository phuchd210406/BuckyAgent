"""Contract tests. These run in CI on every push and guard the shared seam.

If one of these fails, someone edited contracts.py without telling the team.
"""
from __future__ import annotations

import pytest
from pydantic import ValidationError

from repro import contracts as C


def test_contract_version_pinned():
    assert C.CONTRACT_VERSION == "1.0.0"


def test_unknown_fields_are_rejected_not_dropped():
    with pytest.raises(ValidationError):
        C.Hypothesis(file_path="a.py", rationale="x", confidence=0.5, typo_field=1)


def test_confidence_is_bounded():
    with pytest.raises(ValidationError):
        C.Hypothesis(file_path="a.py", rationale="x", confidence=1.4)


def test_execution_result_green_requires_everything():
    ok = dict(exit_code=0, stdout_tail="", stderr_tail="", duration_s=0.1)
    assert C.ExecutionResult(**ok).green
    assert not C.ExecutionResult(**{**ok, "exit_code": 1}).green
    assert not C.ExecutionResult(**{**ok, "timed_out": True}).green
    assert not C.ExecutionResult(**{**ok, "failed": 1}).green


def _run(**kw) -> C.RunRecord:
    base = dict(
        run_id="r1",
        report=C.ClientReport(run_id="r1", raw_text="broken", repo_path="/tmp/x"),
    )
    return C.RunRecord(**{**base, **kw})


def _exec(green: bool) -> C.ExecutionResult:
    return C.ExecutionResult(
        exit_code=0 if green else 1,
        stdout_tail="",
        stderr_tail="",
        duration_s=0.1,
        failed=0 if green else 1,
    )


def _patch() -> C.Patch:
    return C.Patch(unified_diff="--- a\n+++ b\n", files_touched=["a.py"], rationale="r")


def test_clean_run_has_no_invariant_violations():
    assert _run().check_invariants() == []


def test_cannot_claim_fixed_without_reproduction():
    rec = _run(verdict=C.Verdict.REPRODUCED_AND_FIXED)
    assert "no repro attempt" in " ".join(rec.check_invariants())


def test_cannot_accept_a_patch_that_leaves_the_suite_red():
    rec = _run(
        fix_attempts=[
            C.FixAttempt(
                attempt_no=1,
                patch=_patch(),
                target_test=_exec(True),
                suite=_exec(False),   # regression!
                accepted=True,
                reasoning="",
            )
        ]
    )
    violations = " ".join(rec.check_invariants())
    assert "red->green" in violations


def test_budget_overrun_is_an_invariant_violation():
    rec = _run(usage=C.TokenUsage(usd=C.MAX_RUN_USD + 0.01))
    assert any("MAX_RUN_USD" in v for v in rec.check_invariants())
