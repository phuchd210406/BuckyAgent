"""Assembles the graph. OWNER: Engineer A."""
from __future__ import annotations

from functools import partial

from langgraph.graph import END, START, StateGraph

from repro.contracts import (
    MAX_CLARIFY_ROUNDS,
    MAX_FIX_ATTEMPTS,
    MAX_REPRO_ATTEMPTS,
    ClarifyingQuestion,
    ExecutionResult,
    FixAttempt,
    Handover,
    Hypothesis,
    Patch,
    ReportFacts,
    ReproAttempt,
    TestArtifact,
    Verdict,
)
from repro.graph.state import GraphState
from repro.llm.base import LLMClient

# Not a loop bound, so it does not live in contracts.py: this is the intake
# quality gate from docs/ARCHITECTURE.md ("confidence < 0.6 or missing critical").
CLARIFY_CONFIDENCE_FLOOR = 0.6


# ---------------------------------------------------------------------------
# Routers.
#
# Plain Python, no model, no I/O. Each one reads a counter out of state and
# compares it to a cap from contracts.py. They are module-level and take a
# plain mapping so tests/test_routers.py can drive them without a graph.
# ---------------------------------------------------------------------------


def route_after_intake(state: GraphState) -> str:
    """'clarify' when the facts are too thin to search on, else 'localise'.

    The clarify branch is bounded by MAX_CLARIFY_ROUNDS. A resumed run comes
    back with clarify_rounds already spent, and must not ask a second time.
    """
    if state.get("clarify_rounds", 0) >= MAX_CLARIFY_ROUNDS:
        return "localise"
    facts = state.get("facts")
    if facts is None:
        return "clarify"
    if facts.confidence < CLARIFY_CONFIDENCE_FLOOR:
        return "clarify"
    if facts.missing:
        return "clarify"
    return "localise"


def route_after_repro(state: GraphState) -> str:
    """'fix' once reproduced, 'repro' while attempts remain, else 'report'.

    Reproduction is read off the attempts themselves, never off the model's
    opinion that it is nearly there.
    """
    if any(attempt.reproduced for attempt in state.get("repro_attempts", [])):
        return "fix"
    if state.get("repro_count", 0) < MAX_REPRO_ATTEMPTS:
        return "repro"
    return "report"


def route_after_fix(state: GraphState) -> str:
    """'report' once a patch is accepted, 'fix' while attempts remain, else 'report'."""
    if any(attempt.accepted for attempt in state.get("fix_attempts", [])):
        return "report"
    if state.get("fix_count", 0) < MAX_FIX_ATTEMPTS:
        return "fix"
    return "report"


# ---------------------------------------------------------------------------
# Stub nodes (task A1).
#
# Every one returns a hardcoded partial update and nothing else: no model call,
# no filesystem. The real bodies land in src/repro/agents/*.py at task A3; this
# file only proves the wiring. Counters are incremented here because the
# routers read them -- without that the loop edges would spin forever.
# ---------------------------------------------------------------------------

_STUB_GREEN = ExecutionResult(exit_code=0, stdout_tail="", stderr_tail="", duration_s=0.0)
_STUB_RED = ExecutionResult(
    exit_code=1, stdout_tail="1 failed", stderr_tail="", duration_s=0.0, failed=1
)


def _intake_stub(state: GraphState, llm: LLMClient) -> dict:
    return {
        "facts": ReportFacts(
            observed_behaviour="stub: the thing did the wrong thing",
            expected_behaviour="stub: the thing does the right thing",
            steps=["stub step"],
            entrypoint_hint="stub",
            confidence=1.0,
        )
    }


def _clarify_stub(state: GraphState, llm: LLMClient) -> dict:
    return {
        "questions": [
            ClarifyingQuestion(
                question="stub question",
                why_it_matters="stub",
                unblocks_field="observed_behaviour",
            )
        ],
        "clarify_rounds": state.get("clarify_rounds", 0) + 1,
        "verdict": Verdict.NEEDS_CLARIFICATION,
    }


def _localise_stub(state: GraphState, llm: LLMClient) -> dict:
    return {
        "hypotheses": [
            Hypothesis(file_path="stub/module.py", symbol="stub", rationale="stub.", confidence=0.5)
        ]
    }


def _repro_stub(state: GraphState, llm: LLMClient) -> dict:
    attempt_no = state.get("repro_count", 0) + 1
    return {
        "repro_attempts": [
            ReproAttempt(
                attempt_no=attempt_no,
                test=TestArtifact(path="tests/test_stub.py", source="def test_stub():\n    pass\n"),
                result=_STUB_RED,
                reproduced=True,
                reasoning="stub: wiring only",
            )
        ],
        "repro_count": attempt_no,
    }


def _fix_stub(state: GraphState, llm: LLMClient) -> dict:
    attempt_no = state.get("fix_count", 0) + 1
    return {
        "fix_attempts": [
            FixAttempt(
                attempt_no=attempt_no,
                patch=Patch(unified_diff="", files_touched=[], rationale="stub. stub."),
                target_test=_STUB_GREEN,
                suite=_STUB_GREEN,
                accepted=True,
                reasoning="stub: wiring only",
            )
        ],
        "fix_count": attempt_no,
    }


def _report_stub(state: GraphState, llm: LLMClient) -> dict:
    return {
        "verdict": _verdict_for(state),
        "handover": Handover(dev_summary="stub", client_reply="stub"),
    }


def _verdict_for(state: GraphState) -> Verdict:
    """The verdict the evidence in state supports. Read-only, no model."""
    reproduced = any(a.reproduced for a in state.get("repro_attempts", []))
    accepted = any(f.accepted for f in state.get("fix_attempts", []))
    if reproduced and accepted:
        return Verdict.REPRODUCED_AND_FIXED
    if reproduced:
        return Verdict.REPRODUCED_NOT_FIXED
    return Verdict.NOT_REPRODUCED


def build_graph(llm: LLMClient):
    """Compile and return the Repro graph.

    Shape (see docs/ARCHITECTURE.md):
        START -> intake -> (clarify | localise)
        localise -> repro -> {reproduced? fix : repro (<=3) : report}
        fix -> {accepted? report : fix (<=3) : report}
        report -> END
    """
    graph = StateGraph(GraphState)

    graph.add_node("intake", partial(_intake_stub, llm=llm))
    graph.add_node("clarify", partial(_clarify_stub, llm=llm))
    graph.add_node("localise", partial(_localise_stub, llm=llm))
    graph.add_node("repro", partial(_repro_stub, llm=llm))
    graph.add_node("fix", partial(_fix_stub, llm=llm))
    graph.add_node("report", partial(_report_stub, llm=llm))

    graph.add_edge(START, "intake")
    graph.add_conditional_edges(
        "intake", route_after_intake, {"clarify": "clarify", "localise": "localise"}
    )
    # Not a loop: the run stops here and a human answers the questions.
    graph.add_edge("clarify", END)
    graph.add_edge("localise", "repro")
    graph.add_conditional_edges(
        "repro", route_after_repro, {"fix": "fix", "repro": "repro", "report": "report"}
    )
    graph.add_conditional_edges(
        "fix", route_after_fix, {"fix": "fix", "report": "report"}
    )
    graph.add_edge("report", END)

    return graph.compile()
