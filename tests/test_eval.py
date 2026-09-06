"""The eval harness scores what it claims to score. OWNER: Engineer E (task E2).

The harness is the instrument the slides are read off, so the thing worth
testing is not that it prints a table -- it is that the two hard assertions
FIRE. A green eval that cannot go red measures nothing.
"""
from __future__ import annotations

import importlib.util
import json
import sys
from datetime import datetime, timezone
from pathlib import Path

import yaml

from repro.contracts import (
    ClientReport,
    ExecutionResult,
    FixAttempt,
    Hypothesis,
    Patch,
    ReproAttempt,
    RunRecord,
    TokenUsage,
    Verdict,
)

# Aliased on import: pytest tries to collect any class named Test*, and the
# contract's TestArtifact has an __init__. Same trick as test_replay_fidelity.
from repro.contracts import TestArtifact as GeneratedTest

REPO_ROOT = Path(__file__).resolve().parents[1]


def _load_harness():
    """eval/ is a directory of scripts, not an importable package."""
    spec = importlib.util.spec_from_file_location("run_eval", REPO_ROOT / "eval" / "run_eval.py")
    module = importlib.util.module_from_spec(spec)
    # Register before exec: @dataclass resolves its own module out of
    # sys.modules, and blows up with a bare AttributeError if it is not there.
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


ev = _load_harness()

AT = datetime(2026, 1, 1, tzinfo=timezone.utc)


def a_report(run_id: str = "case") -> ClientReport:
    return ClientReport(run_id=run_id, raw_text="it charged me postage", repo_path="repo", received_at=AT)


def red(failed: int = 1, passed: int = 0) -> ExecutionResult:
    return ExecutionResult(exit_code=1, stdout_tail="", stderr_tail="", duration_s=0.1,
                           passed=passed, failed=failed)


def green(passed: int = 1) -> ExecutionResult:
    return ExecutionResult(exit_code=0, stdout_tail="", stderr_tail="", duration_s=0.1, passed=passed)


def a_patch(files: list[str] | None = None) -> Patch:
    return Patch(unified_diff="--- a\n+++ b\n", files_touched=files or ["shopcart/pricing.py"],
                 rationale="root cause; minimal change")


def a_repro(reproduced: bool = True, attempt_no: int = 1) -> ReproAttempt:
    return ReproAttempt(attempt_no=attempt_no,
                        test=GeneratedTest(path="tests/test_repro.py", source="def test():\n    pass\n"),
                        result=red() if reproduced else green(), reproduced=reproduced,
                        reasoning="because")


def a_fix(*, accepted: bool = True, target=None, suite=None, files=None, attempt_no: int = 1) -> FixAttempt:
    return FixAttempt(attempt_no=attempt_no, patch=a_patch(files), target_test=target or green(),
                      suite=suite or green(6), accepted=accepted, reasoning="because")


def a_record(**kw) -> RunRecord:
    kw.setdefault("run_id", "case")
    kw.setdefault("report", a_report(kw["run_id"]))
    kw.setdefault("usage", TokenUsage(input_tokens=100, output_tokens=10, calls=3, usd=0.01))
    return RunRecord(**kw)


CASE = {
    "id": "case",
    "repo": "fixtures/demo_repos/shopcart",
    "complaint": "it charged me postage",
    "expected_verdict": "reproduced_and_fixed",
    "expected_files": ["shopcart/pricing.py"],
    "must_not_break": ["tests/test_pricing.py"],
}


# --- the happy path -------------------------------------------------------


def test_a_clean_fixed_run_scores_clean():
    record = a_record(verdict=Verdict.REPRODUCED_AND_FIXED,
                      hypotheses=[Hypothesis(file_path="shopcart/pricing.py", rationale="r", confidence=0.7)],
                      repro_attempts=[a_repro()], fix_attempts=[a_fix()])
    s = ev.score(CASE, record)
    assert s.verdict_correct and s.reproduced and s.localisation_hit
    assert not s.false_fix and not s.regression and s.invariants_clean


# --- false fix: the assertion the product exists to make -------------------


def test_a_patch_accepted_without_a_reproduction_is_a_false_fix():
    record = a_record(verdict=Verdict.REPRODUCED_AND_FIXED,
                      repro_attempts=[a_repro(reproduced=False)], fix_attempts=[a_fix()])
    assert ev.false_fix(record) is True


def test_a_patch_accepted_while_the_suite_is_red_is_a_false_fix():
    record = a_record(verdict=Verdict.REPRODUCED_AND_FIXED, repro_attempts=[a_repro()],
                      fix_attempts=[a_fix(suite=red(failed=1, passed=5))])
    assert ev.false_fix(record) is True


def test_a_fixed_verdict_with_no_accepted_patch_is_a_false_fix():
    record = a_record(verdict=Verdict.REPRODUCED_AND_FIXED, repro_attempts=[a_repro()],
                      fix_attempts=[a_fix(accepted=False)])
    assert ev.false_fix(record) is True


def test_a_rejected_patch_alone_is_not_a_false_fix():
    """Trying and being refused by the gate is the system working, not a failure."""
    record = a_record(verdict=Verdict.REPRODUCED_NOT_FIXED, repro_attempts=[a_repro()],
                      fix_attempts=[a_fix(accepted=False, suite=red())])
    assert ev.false_fix(record) is False


def test_false_fix_is_caught_even_if_the_invariant_checker_goes_blind(monkeypatch):
    """The harness must not delegate its own hard assertion to the thing under test.

    If check_invariants() ever stops catching an unreproduced patch, an eval
    that only asked it "are you happy?" would report a clean run. This is that
    regression, simulated.
    """
    record = a_record(verdict=Verdict.REPRODUCED_AND_FIXED,
                      repro_attempts=[a_repro(reproduced=False)], fix_attempts=[a_fix()])
    monkeypatch.setattr(RunRecord, "check_invariants", lambda self: [])
    s = ev.score(CASE, record)
    assert s.invariants_clean, "the checker was silenced for this test"
    assert s.false_fix is True, "the harness must catch it independently"


# --- regression -----------------------------------------------------------


def test_an_accepted_fix_with_a_red_suite_is_a_regression():
    record = a_record(verdict=Verdict.REPRODUCED_AND_FIXED, repro_attempts=[a_repro()],
                      fix_attempts=[a_fix(suite=red(failed=1, passed=5))])
    assert ev.regression(record, ["tests/test_pricing.py"]) is True


def test_editing_a_protected_test_file_is_a_regression_even_with_a_green_suite():
    """A green suite proves nothing if the agent rewrote the test in its way."""
    record = a_record(verdict=Verdict.REPRODUCED_AND_FIXED, repro_attempts=[a_repro()],
                      fix_attempts=[a_fix(files=["tests/test_pricing.py"])])
    assert ev.regression(record, ["tests/test_pricing.py"]) is True


def test_a_protected_node_id_still_matches_its_file():
    record = a_record(repro_attempts=[a_repro()], fix_attempts=[a_fix(files=["tests/test_summary.py"])])
    assert ev.regression(record, ["tests/test_summary.py::test_words_are_never_cut_in_half"]) is True


# --- localisation ---------------------------------------------------------


def test_localisation_is_not_scored_when_no_patch_is_expected():
    """Cases 5, 6 and 8 have no file to hit; scoring one would reward guessing."""
    record = a_record(hypotheses=[Hypothesis(file_path="anything.py", rationale="r", confidence=0.5)])
    assert ev.localisation_hit(record, []) is None


def test_localisation_misses_when_no_hypothesis_names_an_expected_file():
    record = a_record(hypotheses=[Hypothesis(file_path="shopcart/cart.py", rationale="r", confidence=0.5)])
    assert ev.localisation_hit(record, ["shopcart/pricing.py"]) is False


# --- tool calls -----------------------------------------------------------


def test_a_failing_test_counts_as_a_successful_tool_call():
    record = a_record(repro_attempts=[a_repro()], fix_attempts=[a_fix()])
    assert ev.tool_calls(record) == (3, 3)


def test_a_timed_out_call_does_not_count_as_success():
    timed_out = ExecutionResult(exit_code=1, stdout_tail="", stderr_tail="", duration_s=60.0, timed_out=True)
    record = a_record(repro_attempts=[ReproAttempt(attempt_no=1,
        test=GeneratedTest(path="tests/test_repro.py", source="x"), result=timed_out,
        reproduced=False, reasoning="timed out")])
    assert ev.tool_calls(record) == (0, 1)


# --- the six metrics ------------------------------------------------------


def test_reproduction_rate_ignores_cases_that_are_not_bugs():
    """Three cases are supposed to end without a patch. They are not misses."""
    scores = [
        ev.CaseScore(id="bug", repo="r", expected_verdict="reproduced_and_fixed", reproduced=True),
        ev.CaseScore(id="not-a-bug", repo="r", expected_verdict="not_reproduced", reproduced=False),
        ev.CaseScore(id="vague", repo="r", expected_verdict="needs_clarification", reproduced=False),
    ]
    row = ev.metrics(scores)[2]
    assert row.name == "Reproduction rate"
    assert row.value == "1/1"


def test_the_table_has_exactly_the_six_rows_from_the_doc():
    names = [m.name for m in ev.metrics([])]
    assert names == [
        "Schema validation pass rate",
        "Tool-call success rate",
        "Reproduction rate",
        "False-fix rate",
        "Loop discipline",
        "Token cost per run",
    ]


# --- end to end through main() --------------------------------------------


def _write_dataset(tmp_path: Path, cases: list[dict]) -> Path:
    path = tmp_path / "dataset.yaml"
    path.write_text(yaml.safe_dump({"cases": cases}))
    return path


def _write_record(directory: Path, case_id: str, record: RunRecord) -> None:
    directory.mkdir(parents=True, exist_ok=True)
    (directory / f"{case_id}.json").write_text(record.model_dump_json())


def test_main_exits_zero_on_a_clean_record(tmp_path):
    records = tmp_path / "records"
    _write_record(records, "case", a_record(verdict=Verdict.REPRODUCED_AND_FIXED,
        hypotheses=[Hypothesis(file_path="shopcart/pricing.py", rationale="r", confidence=0.7)],
        repro_attempts=[a_repro()], fix_attempts=[a_fix()]))
    code = ev.main(["--dataset", str(_write_dataset(tmp_path, [CASE])), "--records", str(records),
                    "--out-dir", str(tmp_path / "out")])
    assert code == 0
    assert (tmp_path / "out" / "results.json").is_file()
    assert (tmp_path / "out" / "results.md").is_file()


def test_main_exits_two_on_a_false_fix(tmp_path):
    records = tmp_path / "records"
    _write_record(records, "case", a_record(verdict=Verdict.REPRODUCED_AND_FIXED,
        repro_attempts=[a_repro(reproduced=False)], fix_attempts=[a_fix()]))
    code = ev.main(["--dataset", str(_write_dataset(tmp_path, [CASE])), "--records", str(records),
                    "--out-dir", str(tmp_path / "out")])
    assert code == 2


def test_main_exits_two_when_a_record_violates_its_own_invariants(tmp_path):
    """Cost over MAX_RUN_USD: an invariant violation that is NOT a false fix."""
    records = tmp_path / "records"
    record = a_record(verdict=Verdict.REPRODUCED_AND_FIXED, repro_attempts=[a_repro()],
                      fix_attempts=[a_fix()],
                      usage=TokenUsage(input_tokens=1, output_tokens=1, calls=1, usd=9.99))
    _write_record(records, "case", record)
    code = ev.main(["--dataset", str(_write_dataset(tmp_path, [CASE])), "--records", str(records),
                    "--out-dir", str(tmp_path / "out")])
    assert code == 2
    scored = json.loads((tmp_path / "out" / "results.json").read_text())["cases"][0]
    assert scored["false_fix"] is False
    assert scored["invariants_clean"] is False


def test_a_missing_record_is_an_error_not_a_crash(tmp_path):
    code = ev.main(["--dataset", str(_write_dataset(tmp_path, [CASE])),
                    "--records", str(tmp_path / "empty"), "--out-dir", str(tmp_path / "out")])
    assert code == 1
    scored = json.loads((tmp_path / "out" / "results.json").read_text())["cases"][0]
    assert scored["error"] and "no record" in scored["error"]


def test_allow_errors_scores_the_rest(tmp_path):
    records = tmp_path / "records"
    _write_record(records, "case", a_record(verdict=Verdict.REPRODUCED_AND_FIXED,
        hypotheses=[Hypothesis(file_path="shopcart/pricing.py", rationale="r", confidence=0.7)],
        repro_attempts=[a_repro()], fix_attempts=[a_fix()]))
    two = [CASE, {**CASE, "id": "absent"}]
    code = ev.main(["--dataset", str(_write_dataset(tmp_path, two)), "--records", str(records),
                    "--out-dir", str(tmp_path / "out"), "--allow-errors"])
    assert code == 0


def test_a_malformed_record_is_reported_against_its_case(tmp_path):
    records = tmp_path / "records"
    records.mkdir()
    (records / "case.json").write_text('{"nope": true}')
    code = ev.main(["--dataset", str(_write_dataset(tmp_path, [CASE])), "--records", str(records),
                    "--out-dir", str(tmp_path / "out")])
    assert code == 1
    scored = json.loads((tmp_path / "out" / "results.json").read_text())["cases"][0]
    assert "invalid RunRecord" in scored["error"]


# --- the committed fixtures ------------------------------------------------


def test_every_dataset_case_has_a_committed_fixture():
    cases = yaml.safe_load((REPO_ROOT / "eval" / "dataset.yaml").read_text())["cases"]
    for case in cases:
        assert (REPO_ROOT / "eval" / "fixtures" / f"{case['id']}.json").is_file(), case["id"]


def test_the_committed_fixtures_score_clean(tmp_path):
    """`make eval-fixtures` must stay green: it is what CI runs."""
    code = ev.main(["--records", str(REPO_ROOT / "eval" / "fixtures"), "--out-dir", str(tmp_path)])
    assert code == 0
