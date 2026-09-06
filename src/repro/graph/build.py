"""Assembles the graph. OWNER: Engineer A.

WORST CASE MODEL CALLS FOR ONE RUN: 9.
    intake 1 + localise 1 + repro 3 (MAX_REPRO_ATTEMPTS) + fix 3
    (MAX_FIX_ATTEMPTS) + report 1 = 9. The clarify branch cannot be on that
    path: clarify ends the run (2 calls: intake + clarify), and a resumed run
    is a new run with its own budget.
    Each of those is one *node* call. `LLMClient.structured` is allowed one
    re-prompt on a schema failure, so the ceiling in provider round-trips is
    18. MAX_TOTAL_LLM_CALLS = 40 is the backstop for both numbers, enforced
    between nodes here rather than trusted from any node's return value.
"""
from __future__ import annotations

from typing import Protocol

from langgraph.graph import END, START, StateGraph

from repro.contracts import (
    MAX_CLARIFY_ROUNDS,
    MAX_FIX_ATTEMPTS,
    MAX_REPRO_ATTEMPTS,
    MAX_RUN_USD,
    MAX_TOTAL_LLM_CALLS,
    SANDBOX_MAX_OUTPUT_CHARS,
    ClarifyingQuestion,
    ExecutionResult,
    FixAttempt,
    Handover,
    Hypothesis,
    LLMResponse,
    Patch,
    ReportFacts,
    ReproAttempt,
    TestArtifact,
    TokenUsage,
    Verdict,
)
from repro.graph.state import GraphState
from repro.llm.base import LLMClient

# Not a loop bound, so it does not live in contracts.py: this is the intake
# quality gate from docs/ARCHITECTURE.md ("confidence < 0.6 or missing critical").
CLARIFY_CONFIDENCE_FLOOR = 0.6

# Engineer C owns every prompt in this codebase. These nodes are wiring.
SYSTEM_PROMPT = "TODO: Engineer C owns this"


# ---------------------------------------------------------------------------
# Budget.
# ---------------------------------------------------------------------------


def budget_exhausted(state: GraphState) -> bool:
    """True when this run has spent its allowance and may not call the model again.

    Read off `usage`, which the GRAPH maintains from what the client actually
    reported spending -- never from a number a node put in its return value.
    At exactly MAX_TOTAL_LLM_CALLS the allowance is spent, not nearly spent, so
    this is `>=`.
    """
    usage = state.get("usage") or TokenUsage()
    return usage.calls >= MAX_TOTAL_LLM_CALLS or usage.usd > MAX_RUN_USD


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
    if budget_exhausted(state):
        # Out of money is not a reason to bother the client with a question we
        # cannot afford to act on. Fall through; the repro router ends the run.
        return "localise"
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
    if budget_exhausted(state):
        return "report"
    if any(attempt.reproduced for attempt in state.get("repro_attempts", [])):
        return "fix"
    if state.get("repro_count", 0) < MAX_REPRO_ATTEMPTS:
        return "repro"
    return "report"


def route_after_fix(state: GraphState) -> str:
    """'report' once a patch is accepted, 'fix' while attempts remain, else 'report'."""
    if budget_exhausted(state):
        return "report"
    if any(attempt.accepted for attempt in state.get("fix_attempts", [])):
        return "report"
    if state.get("fix_count", 0) < MAX_FIX_ATTEMPTS:
        return "fix"
    return "report"


# ---------------------------------------------------------------------------
# The sandbox seam.
#
# Engineer B owns the bodies (workspace.py / runner.py / patcher.py). The nodes
# depend on THIS four-method surface -- the allow-list from ARCHITECTURE.md --
# so the graph is testable with a canned stub and no filesystem at all.
# ---------------------------------------------------------------------------


class Sandbox(Protocol):
    def write_test(self, test: TestArtifact) -> None: ...

    def run_test(self, test_path: str) -> ExecutionResult: ...

    def apply_patch(self, patch: Patch) -> tuple[bool, str]: ...

    def run_suite(self) -> ExecutionResult: ...


class StubSandbox:
    """Canned green results. TEST AND WIRING ONLY -- it runs nothing.

    build_graph() falls back to this so the graph can be compiled and driven
    before Engineer B's real sandbox lands. run() (task A4) passes a real one.
    """

    _GREEN = ExecutionResult(exit_code=0, stdout_tail="", stderr_tail="", duration_s=0.0)

    def write_test(self, test: TestArtifact) -> None:
        return None

    def run_test(self, test_path: str) -> ExecutionResult:
        return self._GREEN

    def apply_patch(self, patch: Patch) -> tuple[bool, str]:
        return True, "stub sandbox applied nothing"

    def run_suite(self) -> ExecutionResult:
        return self._GREEN


# ---------------------------------------------------------------------------
# Metering.
# ---------------------------------------------------------------------------


class _MeteredLLM:
    """Counts what the model actually cost, on the way through.

    The graph cannot ask a node how much it spent -- a node that under-reports
    its usage would buy itself unlimited loops. So every call goes through here
    and the guard below folds the delta into state itself.
    """

    def __init__(self, inner: LLMClient) -> None:
        self._inner = inner
        self._delta = TokenUsage()

    def take(self) -> TokenUsage:
        """Return the usage since the last take() and reset."""
        delta, self._delta = self._delta, TokenUsage()
        return delta

    def complete(self, *, system: str, user: str, max_tokens: int = 1024) -> LLMResponse:
        response = self._inner.complete(system=system, user=user, max_tokens=max_tokens)
        self._delta = self._delta.merge(response.usage)
        return response

    def structured(self, *, system: str, user: str, schema, max_tokens: int = 1024):
        obj, response = self._inner.structured(
            system=system, user=user, schema=schema, max_tokens=max_tokens
        )
        self._delta = self._delta.merge(response.usage)
        return obj, response


# ---------------------------------------------------------------------------
# Nodes (still stubs: prompts are placeholders, the sandbox may be canned).
#
# Real bodies move to src/repro/agents/*.py at task A3. What is real already is
# the control flow: every gate below (`reproduced`, `accepted`, `verdict`) is
# computed in Python from evidence, never asked of the model.
# ---------------------------------------------------------------------------


def _intake_node(state: GraphState, llm: LLMClient, sandbox: Sandbox) -> dict:
    facts, _ = llm.structured(system=SYSTEM_PROMPT, user=SYSTEM_PROMPT, schema=ReportFacts)
    return {"facts": facts}


def _clarify_node(state: GraphState, llm: LLMClient, sandbox: Sandbox) -> dict:
    question, _ = llm.structured(
        system=SYSTEM_PROMPT, user=SYSTEM_PROMPT, schema=ClarifyingQuestion
    )
    return {"questions": [question], "verdict": Verdict.NEEDS_CLARIFICATION}


def _localise_node(state: GraphState, llm: LLMClient, sandbox: Sandbox) -> dict:
    hypothesis, _ = llm.structured(system=SYSTEM_PROMPT, user=SYSTEM_PROMPT, schema=Hypothesis)
    return {"hypotheses": [hypothesis]}


def _repro_node(state: GraphState, llm: LLMClient, sandbox: Sandbox) -> dict:
    test, _ = llm.structured(system=SYSTEM_PROMPT, user=SYSTEM_PROMPT, schema=TestArtifact)
    sandbox.write_test(test)
    result = sandbox.run_test(test.path)
    return {
        "repro_attempts": [
            ReproAttempt(
                attempt_no=min(state.get("repro_count", 0) + 1, MAX_REPRO_ATTEMPTS),
                test=test,
                result=result,
                reproduced=is_reproduction(result),
                reasoning="TODO: Engineer C owns this",
            )
        ]
    }


def _fix_node(state: GraphState, llm: LLMClient, sandbox: Sandbox) -> dict:
    patch, _ = llm.structured(system=SYSTEM_PROMPT, user=SYSTEM_PROMPT, schema=Patch)
    attempts = state.get("repro_attempts", [])
    target_path = attempts[-1].test.path if attempts else ""
    applied, message = sandbox.apply_patch(patch)
    if applied:
        target_test = sandbox.run_test(target_path)
        suite = sandbox.run_suite()
    else:
        # A diff that will not apply is a failed attempt, not an exception.
        target_test = suite = _errored(message)
    return {
        "fix_attempts": [
            FixAttempt(
                attempt_no=min(state.get("fix_count", 0) + 1, MAX_FIX_ATTEMPTS),
                patch=patch,
                target_test=target_test,
                suite=suite,
                accepted=target_test.green and suite.green,
                reasoning="TODO: Engineer C owns this",
            )
        ]
    }


def _report_node(state: GraphState, llm: LLMClient, sandbox: Sandbox) -> dict:
    handover, _ = llm.structured(system=SYSTEM_PROMPT, user=SYSTEM_PROMPT, schema=Handover)
    return {"verdict": verdict_for(state), "handover": handover}


def is_reproduction(result: ExecutionResult) -> bool:
    """True only when the test RAN and FAILED.

    A test that errors on import did not reproduce anything -- it is a broken
    test, and treating it as a reproduction is how a patch gets accepted for a
    bug nobody ever saw.
    """
    return not result.timed_out and result.errors == 0 and result.failed > 0


def verdict_for(state: GraphState) -> Verdict:
    """The verdict the evidence in state supports. Read-only, no model."""
    if state.get("verdict") == Verdict.ABORTED_BUDGET:
        return Verdict.ABORTED_BUDGET
    reproduced = any(a.reproduced for a in state.get("repro_attempts", []))
    accepted = any(f.accepted for f in state.get("fix_attempts", []))
    if reproduced and accepted:
        return Verdict.REPRODUCED_AND_FIXED
    if reproduced:
        return Verdict.REPRODUCED_NOT_FIXED
    return Verdict.NOT_REPRODUCED


def _errored(message: str) -> ExecutionResult:
    return ExecutionResult(
        exit_code=1,
        stdout_tail="",
        stderr_tail=message[:SANDBOX_MAX_OUTPUT_CHARS],
        duration_s=0.0,
        errors=1,
    )


# ---------------------------------------------------------------------------
# Enforcement.
#
# Task A2: the counters that bound every loop, and the usage that bounds the
# bill, are owned by the graph. A node's return value cannot touch them --
# whether it resets one out of malice, a bad merge, or a model that decided it
# deserved another go.
# ---------------------------------------------------------------------------

#: Written by the guard below, stripped from every node's return value.
GRAPH_OWNED_KEYS = frozenset({"clarify_rounds", "repro_count", "fix_count", "usage"})

#: Which node increments which bound. Nodes not listed have no counter.
NODE_COUNTERS = {"clarify": "clarify_rounds", "repro": "repro_count", "fix": "fix_count"}


def _guarded(name: str, node, llm: _MeteredLLM, sandbox: Sandbox):
    """Wrap a node: budget check on entry, counters and usage owned by us."""
    counter = NODE_COUNTERS.get(name)

    def run_node(state: GraphState) -> dict:
        if budget_exhausted(state):
            # Do not run the body, do not call the model, do not increment.
            # The routers see the same thing and steer the run to its end.
            return {"verdict": Verdict.ABORTED_BUDGET}

        llm.take()  # drop anything stray so this node is charged for its own calls
        update = dict(node(state, llm, sandbox) or {})
        spent = llm.take()

        for key in GRAPH_OWNED_KEYS:
            update.pop(key, None)
        if counter is not None:
            update[counter] = state.get(counter, 0) + 1
        update["usage"] = (state.get("usage") or TokenUsage()).merge(spent)

        if budget_exhausted(update):
            update["verdict"] = Verdict.ABORTED_BUDGET
        return update

    run_node.__name__ = f"guarded_{name}"
    return run_node


def build_graph(llm: LLMClient, sandbox: Sandbox | None = None):
    """Compile and return the Repro graph.

    Shape (see docs/ARCHITECTURE.md):
        START -> intake -> (clarify | localise)
        localise -> repro -> {reproduced? fix : repro (<=3) : report}
        fix -> {accepted? report : fix (<=3) : report}
        report -> END

    Build one graph per run: the meter is per-graph, so sharing a compiled
    graph between two concurrent runs would mix up their bills.
    """
    metered = _MeteredLLM(llm)
    sandbox = StubSandbox() if sandbox is None else sandbox

    graph = StateGraph(GraphState)
    for name, node in (
        ("intake", _intake_node),
        ("clarify", _clarify_node),
        ("localise", _localise_node),
        ("repro", _repro_node),
        ("fix", _fix_node),
        ("report", _report_node),
    ):
        graph.add_node(name, _guarded(name, node, metered, sandbox))

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
    graph.add_conditional_edges("fix", route_after_fix, {"fix": "fix", "report": "report"})
    graph.add_edge("report", END)

    return graph.compile()
