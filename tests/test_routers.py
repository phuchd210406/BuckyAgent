"""Router tests. OWNER: Engineer A.

The routers are the only thing standing between a bad model day and the team's
USD 20, so they are tested away from the graph: plain dicts in, edge name out.
Each router is pinned at its cap and one step either side of it.
"""
from __future__ import annotations

from repro.contracts import (
    MAX_CLARIFY_ROUNDS,
    MAX_FIX_ATTEMPTS,
    MAX_REPRO_ATTEMPTS,
    ExecutionResult,
    FixAttempt,
    Patch,
    ReportFacts,
    ReproAttempt,
)
from repro.contracts import (
    TestArtifact as GeneratedTest,
)
from repro.graph.build import (
    CLARIFY_CONFIDENCE_FLOOR,
    route_after_fix,
    route_after_intake,
    route_after_repro,
)

GREEN = ExecutionResult(exit_code=0, stdout_tail="", stderr_tail="", duration_s=0.1)
RED = ExecutionResult(exit_code=1, stdout_tail="1 failed", stderr_tail="", duration_s=0.1, failed=1)


def facts(confidence: float = 1.0, missing: list[str] | None = None) -> ReportFacts:
    return ReportFacts(
        observed_behaviour="checkout charged twice",
        entrypoint_hint="checkout",
        confidence=confidence,
        missing=missing or [],
    )


def repro_attempt(reproduced: bool, attempt_no: int = 1) -> ReproAttempt:
    return ReproAttempt(
        attempt_no=attempt_no,
        test=GeneratedTest(path="tests/test_x.py", source="def test_x():\n    pass\n"),
        result=RED if reproduced else GREEN,
        reproduced=reproduced,
        reasoning="fixture",
    )


def fix_attempt(accepted: bool, attempt_no: int = 1) -> FixAttempt:
    return FixAttempt(
        attempt_no=attempt_no,
        patch=Patch(unified_diff="", files_touched=["a.py"], rationale="root cause. fix."),
        target_test=GREEN if accepted else RED,
        suite=GREEN,
        accepted=accepted,
        reasoning="fixture",
    )


# --- intake -> clarify | localise -------------------------------------------
# The cap here is MAX_CLARIFY_ROUNDS: one round of questions, ever.


def test_intake_asks_below_the_clarify_cap():
    state = {"facts": facts(missing=["steps"]), "clarify_rounds": MAX_CLARIFY_ROUNDS - 1}
    assert route_after_intake(state) == "clarify"


def test_intake_stops_asking_at_the_clarify_cap():
    state = {"facts": facts(missing=["steps"]), "clarify_rounds": MAX_CLARIFY_ROUNDS}
    assert route_after_intake(state) == "localise"


def test_intake_stops_asking_past_the_clarify_cap():
    state = {"facts": facts(missing=["steps"]), "clarify_rounds": MAX_CLARIFY_ROUNDS + 1}
    assert route_after_intake(state) == "localise"


def test_intake_proceeds_on_confident_complete_facts():
    assert route_after_intake({"facts": facts(), "clarify_rounds": 0}) == "localise"


def test_intake_asks_when_confidence_is_below_the_floor():
    thin = facts(confidence=CLARIFY_CONFIDENCE_FLOOR - 0.01)
    assert route_after_intake({"facts": thin, "clarify_rounds": 0}) == "clarify"
    at_floor = facts(confidence=CLARIFY_CONFIDENCE_FLOOR)
    assert route_after_intake({"facts": at_floor, "clarify_rounds": 0}) == "localise"


def test_intake_asks_when_there_are_no_facts_at_all():
    assert route_after_intake({}) == "clarify"


# --- repro -> fix | repro | report ------------------------------------------


def test_repro_loops_below_the_cap():
    state = {
        "repro_count": MAX_REPRO_ATTEMPTS - 1,
        "repro_attempts": [repro_attempt(False, i + 1) for i in range(MAX_REPRO_ATTEMPTS - 1)],
    }
    assert route_after_repro(state) == "repro"


def test_repro_gives_up_at_the_cap():
    state = {
        "repro_count": MAX_REPRO_ATTEMPTS,
        "repro_attempts": [repro_attempt(False, i + 1) for i in range(MAX_REPRO_ATTEMPTS)],
    }
    assert route_after_repro(state) == "report"


def test_repro_gives_up_past_the_cap():
    state = {"repro_count": MAX_REPRO_ATTEMPTS + 1, "repro_attempts": [repro_attempt(False)]}
    assert route_after_repro(state) == "report"


def test_repro_advances_to_fix_once_reproduced():
    state = {"repro_count": 1, "repro_attempts": [repro_attempt(True)]}
    assert route_after_repro(state) == "fix"


def test_repro_advances_to_fix_even_at_the_cap():
    state = {
        "repro_count": MAX_REPRO_ATTEMPTS,
        "repro_attempts": [repro_attempt(False, 1), repro_attempt(True, 2)],
    }
    assert route_after_repro(state) == "fix"


def test_repro_first_pass_has_no_attempts_yet():
    assert route_after_repro({"repro_count": 0, "repro_attempts": []}) == "repro"


# --- fix -> fix | report -----------------------------------------------------


def test_fix_loops_below_the_cap():
    state = {
        "fix_count": MAX_FIX_ATTEMPTS - 1,
        "fix_attempts": [fix_attempt(False, i + 1) for i in range(MAX_FIX_ATTEMPTS - 1)],
    }
    assert route_after_fix(state) == "fix"


def test_fix_gives_up_at_the_cap():
    state = {
        "fix_count": MAX_FIX_ATTEMPTS,
        "fix_attempts": [fix_attempt(False, i + 1) for i in range(MAX_FIX_ATTEMPTS)],
    }
    assert route_after_fix(state) == "report"


def test_fix_gives_up_past_the_cap():
    state = {"fix_count": MAX_FIX_ATTEMPTS + 1, "fix_attempts": [fix_attempt(False)]}
    assert route_after_fix(state) == "report"


def test_fix_reports_once_accepted():
    state = {"fix_count": 1, "fix_attempts": [fix_attempt(True)]}
    assert route_after_fix(state) == "report"


def test_fix_reports_on_an_accepted_patch_even_below_the_cap():
    state = {
        "fix_count": MAX_FIX_ATTEMPTS - 1,
        "fix_attempts": [fix_attempt(False, 1), fix_attempt(True, 2)],
    }
    assert route_after_fix(state) == "report"


def test_no_router_touches_the_model():
    """A router that took an llm argument could ask the model to keep looping."""
    for router in (route_after_intake, route_after_repro, route_after_fix):
        assert router.__code__.co_varnames[: router.__code__.co_argcount] == ("state",)
