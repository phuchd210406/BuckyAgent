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

from langgraph.graph import END, START, StateGraph

from repro.agents.clarify import clarify_node
from repro.agents.fix import fix_node
from repro.agents.intake import intake_node
from repro.agents.localiser import localiser_node
from repro.agents.reporter import reporter_node
from repro.agents.repro_agent import repro_agent_node
from repro.contracts import (
    MAX_CLARIFY_ROUNDS,
    MAX_FIX_ATTEMPTS,
    MAX_REPRO_ATTEMPTS,
    MAX_RUN_USD,
    MAX_TOTAL_LLM_CALLS,
    LLMResponse,
    TokenUsage,
    Verdict,
)
from repro.graph.sandbox_seam import Sandbox, StubSandbox
from repro.graph.state import GraphState
from repro.llm.base import LLMClient

# Not a loop bound, so it does not live in contracts.py: this is the intake
# quality gate from docs/ARCHITECTURE.md ("confidence < 0.6 or missing critical").
CLARIFY_CONFIDENCE_FLOOR = 0.6

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
# Metering.
# ---------------------------------------------------------------------------


class _MeteredLLM:
    """Counts what the model actually cost, on the way through.

    The graph cannot ask a node how much it spent -- a node that under-reports
    its usage would buy itself unlimited loops. Nodes DO report their own usage
    (see agents/_common.add_usage), and the guard below throws that number away
    in favour of this one.
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

#: Nodes that touch the workspace. The other four are (state, llm) and no more.
SANDBOX_NODES = frozenset({"localise", "repro", "fix"})


def _guarded(name: str, node, llm: _MeteredLLM, sandbox: Sandbox):
    """Wrap a node: budget check on entry, counters and usage owned by us."""
    counter = NODE_COUNTERS.get(name)

    def run_node(state: GraphState) -> dict:
        if budget_exhausted(state):
            # Do not run the body, do not call the model, do not increment.
            # The routers see the same thing and steer the run to its end.
            return {"verdict": Verdict.ABORTED_BUDGET}

        llm.take()  # drop anything stray so this node is charged for its own calls
        if name in SANDBOX_NODES:
            update = dict(node(state, llm, sandbox=sandbox) or {})
        else:
            update = dict(node(state, llm) or {})
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
        ("intake", intake_node),
        ("clarify", clarify_node),
        ("localise", localiser_node),
        ("repro", repro_agent_node),
        ("fix", fix_node),
        ("report", reporter_node),
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
