"""Router tests. OWNER: Engineer A.

The routers are the only thing standing between a bad model day and the team's
USD 20, so they are tested away from the graph: plain dicts in, edge name out.
Each router is pinned at its cap and one step either side of it.
"""
from __future__ import annotations

import pytest

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
    CRITICAL_FACTS,
    missing_critical,
    route_after_fix,
    route_after_intake,
    route_after_repro,
)

GREEN = ExecutionResult(exit_code=0, stdout_tail="", stderr_tail="", duration_s=0.1)
RED = ExecutionResult(exit_code=1, stdout_tail="1 failed", stderr_tail="", duration_s=0.1, failed=1)


def facts(confidence: float = 1.0, missing: list[str] | None = None, **absent) -> ReportFacts:
    """Complete, confident facts. Pass `observed_behaviour=None` to take one away."""
    return ReportFacts(
        **{
            "observed_behaviour": "checkout charged twice",
            "expected_behaviour": "charged once",
            "entrypoint_hint": "checkout",
            "confidence": confidence,
            "missing": missing or [],
            **absent,
        }
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
#
# Going to clarify ENDS the run: the client is asked a question and a human
# resumes it later. So the bar is "the localiser has nothing to work with", not
# "the report could have been more complete" -- see CRITICAL_FACTS. The cap on
# top of that is MAX_CLARIFY_ROUNDS: one round of questions, ever.

UNSEARCHABLE = {"observed_behaviour": None, "entrypoint_hint": None}


def test_intake_asks_below_the_clarify_cap():
    state = {
        "facts": facts(missing=["observed_behaviour"], **UNSEARCHABLE),
        "clarify_rounds": MAX_CLARIFY_ROUNDS - 1,
    }
    assert route_after_intake(state) == "clarify"


def test_intake_stops_asking_at_the_clarify_cap():
    state = {
        "facts": facts(missing=["observed_behaviour"], **UNSEARCHABLE),
        "clarify_rounds": MAX_CLARIFY_ROUNDS,
    }
    assert route_after_intake(state) == "localise"


def test_intake_stops_asking_past_the_clarify_cap():
    state = {
        "facts": facts(missing=["observed_behaviour"], **UNSEARCHABLE),
        "clarify_rounds": MAX_CLARIFY_ROUNDS + 1,
    }
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


# --- which missing fields actually stop a run -------------------------------


@pytest.mark.parametrize("field", sorted(CRITICAL_FACTS))
def test_a_critical_field_that_is_really_absent_stops_the_run(field):
    thin = facts(missing=[field], **{field: None})
    assert route_after_intake({"facts": thin, "clarify_rounds": 0}) == "clarify"
    assert missing_critical(thin) == {field}


@pytest.mark.parametrize("field", ["environment", "steps", "expected_behaviour"])
def test_a_non_critical_missing_field_does_not_stop_the_run(field):
    """The reporter not spelling something out is not a reason to end the run.

    Most client complaints never state what they expected instead; they say
    what went wrong. The localiser can search from that.
    """
    incomplete = facts(missing=[field], **{field: None} if field != "steps" else {})
    assert route_after_intake({"facts": incomplete, "clarify_rounds": 0}) == "localise"
    assert missing_critical(incomplete) == set()


def test_a_field_name_that_is_not_in_reportfacts_is_ignored():
    """One recorded run in three named fields that do not exist in the schema.

    A router that trusted the list verbatim would end the run over a field it
    could not have filled in the first place.
    """
    invented = facts(missing=["order_id", "basket_total", "postage_amount_charged"])
    assert route_after_intake({"facts": invented, "clarify_rounds": 0}) == "localise"
    assert missing_critical(invented) == set()


def test_a_critical_field_the_model_filled_is_not_missing_however_it_is_labelled():
    """`missing` is the model's opinion about its own output, so it is checked.

    In the committed recording the model put "expected_behaviour" in `missing`
    having just filled it. The same slip on a critical field must not end a run
    whose facts are right there.
    """
    contradictory = facts(missing=["observed_behaviour", "entrypoint_hint"])

    assert contradictory.observed_behaviour and contradictory.entrypoint_hint
    assert missing_critical(contradictory) == set()
    assert route_after_intake({"facts": contradictory, "clarify_rounds": 0}) == "localise"


def test_the_recorded_shopcart_facts_reach_the_localiser():
    """The regression, in the model's own words.

    Exactly what Haiku 4.5 returned for the flagship demo case
    (src/repro/llm/cassettes/2d8376a00755e01d.json). Before CRITICAL_FACTS this
    ended the run at the first node, every time.
    """
    recorded = ReportFacts(
        observed_behaviour=(
            "charged postage fee despite basket total exceeding $50 and site "
            "stating free postage over $50"
        ),
        expected_behaviour="no postage charge should apply",
        steps=["attempted to purchase items", "basket total was over $50"],
        entrypoint_hint="checkout",
        environment=None,
        missing=["expected_behaviour"],
        confidence=0.72,
    )

    assert route_after_intake({"facts": recorded, "clarify_rounds": 0}) == "localise"


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
