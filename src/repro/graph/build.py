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

import logging
import time
from pathlib import Path
from typing import Callable

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
    ClientReport,
    LLMResponse,
    Patch,
    RunRecord,
    StreamEvent,
    TokenUsage,
    Verdict,
)
from repro.contracts import (
    MAX_FIX_ATTEMPTS as _MAX_FIX,
)
from repro.graph import events
from repro.graph.sandbox_seam import Sandbox, StubSandbox, WorkspaceSandbox
from repro.graph.state import GraphState
from repro.llm.base import LLMClient
from repro.sandbox.workspace import Workspace
from repro.settings import Settings

LOG = logging.getLogger("repro.graph")

# Not a loop bound, so it does not live in contracts.py: this is the intake
# quality gate from docs/ARCHITECTURE.md ("confidence < 0.6 or missing critical").
CLARIFY_CONFIDENCE_FLOOR = 0.6

#: Somewhere to send StreamEvents as they happen. Called from the graph thread.
EventSink = Callable[[StreamEvent], None]


def _sink(on_event: EventSink | None) -> EventSink:
    """Never let a watcher break the thing it is watching."""
    if on_event is None:
        return lambda event: None

    def safe(event: StreamEvent) -> None:
        try:
            on_event(event)
        except Exception:  # noqa: BLE001 - a dropped event is not a failed run
            LOG.exception("run %s: an event sink raised; the run continues", event.run_id)

    return safe


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
        try:
            response = self._inner.complete(system=system, user=user, max_tokens=max_tokens)
        except Exception:
            self._charge_for_a_failed_call()
            raise
        self._delta = self._delta.merge(response.usage)
        return response

    def structured(self, *, system: str, user: str, schema, max_tokens: int = 1024):
        try:
            obj, response = self._inner.structured(
                system=system, user=user, schema=schema, max_tokens=max_tokens
            )
        except Exception:
            self._charge_for_a_failed_call()
            raise
        self._delta = self._delta.merge(response.usage)
        return obj, response

    def _charge_for_a_failed_call(self) -> None:
        """A call that raised was still a call, and Bedrock still billed it.

        The nodes that loop now survive a schema failure and try again, so an
        uncounted failure would be a free retry -- exactly the hole
        MAX_TOTAL_LLM_CALLS exists to close. The tokens are not recoverable
        through the protocol (the exception carries no usage), so the count is
        charged and the dollars are not: the call cap still binds.
        """
        self._delta = self._delta.merge(TokenUsage(calls=1))


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


def _guarded(name: str, node, llm: _MeteredLLM, sandbox: Sandbox, emit):
    """Wrap a node: budget check on entry, counters and usage owned by us.

    This is also where the run becomes watchable. The guard already brackets
    every node, so it is the one place that knows a node started, what it
    returned, and that nothing else ran in between -- which is exactly what a
    transition is. No LangGraph streaming, no second source of truth.
    """
    counter = NODE_COUNTERS.get(name)

    def run_node(state: GraphState) -> dict:
        if budget_exhausted(state):
            # Do not run the body, do not call the model, do not increment.
            # The routers see the same thing and steer the run to its end.
            return {"verdict": Verdict.ABORTED_BUDGET}

        run_id = state["report"].run_id
        attempt_no = (state.get(counter, 0) + 1) if counter in ("repro_count", "fix_count") else None
        emit(events.node_started(run_id, name, attempt_no, _CAP_FOR.get(name)))

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
        _emit_for(emit, run_id, name, update)
        return update

    run_node.__name__ = f"guarded_{name}"
    return run_node


#: How many attempts each looping node is allowed, for the "(2 of 3)" in the UI.
_CAP_FOR = {"repro": MAX_REPRO_ATTEMPTS, "fix": _MAX_FIX}


def _emit_for(emit, run_id: str, name: str, update: dict) -> None:
    """Turn what a node returned into events. Driven by the update, not a guess."""
    if update.get("facts") is not None:
        emit(events.intake_finished(run_id, update["facts"]))
    for question in update.get("questions", []):
        emit(events.clarify_asked(run_id, question))
    hypotheses = update.get("hypotheses", [])
    for hypothesis in hypotheses:
        emit(events.hypothesis_found(run_id, hypothesis))
    if hypotheses:
        emit(events.localise_finished(run_id, len(hypotheses)))
    for attempt in update.get("repro_attempts", []):
        emit(events.repro_attempted(run_id, attempt))
    for attempt in update.get("fix_attempts", []):
        emit(events.fix_attempted(run_id, attempt))
    if name == "report":
        emit(events.report_finished(run_id))


def build_graph(llm: LLMClient, sandbox: Sandbox | None = None, on_event: EventSink | None = None):
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
    emit = _sink(on_event)

    graph = StateGraph(GraphState)
    for name, node in (
        ("intake", intake_node),
        ("clarify", clarify_node),
        ("localise", localiser_node),
        ("repro", repro_agent_node),
        ("fix", fix_node),
        ("report", reporter_node),
    ):
        graph.add_node(name, _guarded(name, node, metered, sandbox, emit))

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


# ---------------------------------------------------------------------------
# The public entry point.
# ---------------------------------------------------------------------------


def assemble_run_record(final_state: GraphState) -> RunRecord:
    """Terminal GraphState -> the audit trail contract. No I/O, no model."""
    report = final_state["report"]
    return RunRecord(
        run_id=report.run_id,
        report=report,
        facts=final_state.get("facts"),
        questions=list(final_state.get("questions", [])),
        hypotheses=list(final_state.get("hypotheses", [])),
        repro_attempts=list(final_state.get("repro_attempts", [])),
        fix_attempts=list(final_state.get("fix_attempts", [])),
        verdict=final_state.get("verdict") or Verdict.NOT_REPRODUCED,
        handover=final_state.get("handover"),
        usage=final_state.get("usage") or TokenUsage(),
    )


def open_workspace(report: ClientReport, settings: Settings | None = None) -> Workspace:
    """A disposable copy of the target repo, private to this run.

    `/tmp/repro-workspaces/<run_id>` per ARCHITECTURE.md, and that is only the
    parent: Workspace mkdtemps a fresh directory inside it, so even two runs
    that share a run id (a retry, a replayed cassette) get separate trees and
    neither can delete the other's in `close()`.
    """
    settings = settings or Settings()
    return Workspace(report.repo_path, root=Path(settings.workspace_root) / report.run_id)


def run(
    report: ClientReport,
    llm: LLMClient,
    *,
    sandbox: Sandbox | None = None,
    on_event: EventSink | None = None,
) -> RunRecord:
    """Run one report end to end. This is the function everyone else calls.

        from repro.graph.build import run
        record: RunRecord = run(client_report, llm)

    Safe to call concurrently: every run gets its own workspace, its own
    compiled graph and its own meter, and shares nothing but the LLM client.

    Pass `sandbox` to supply your own (tests, or a caller that owns the
    workspace already); then closing it is the caller's job.

    Pass `on_event` to watch it happen: one StreamEvent per transition, emitted
    from whatever thread is running the graph. A sink that raises is ignored --
    a broken watcher must never take down the run it is watching.
    """
    started = time.monotonic()
    emit = _sink(on_event)
    emit(events.run_started(report))
    workspace: Workspace | None = None
    try:
        if sandbox is None:
            workspace = open_workspace(report)
            sandbox = WorkspaceSandbox(workspace)
        initial: GraphState = {"report": report}
        if workspace is not None:
            initial["workspace_path"] = str(workspace.path)
        final_state = build_graph(llm, sandbox=sandbox, on_event=emit).invoke(initial)
    finally:
        # Even when the graph raised. A leaked workspace is a leaked copy of a
        # client's repository sitting in /tmp.
        if workspace is not None:
            workspace.close()

    record = assemble_run_record(final_state)
    record.wall_clock_s = round(time.monotonic() - started, 3)
    record = enforce_invariants(record, emit)
    emit(events.verdict_reached(record, report.reporter_name))
    return record


def enforce_invariants(record: RunRecord, on_event: EventSink | None = None) -> RunRecord:
    """The last gate before anything leaves this process.

    A record that violates its own invariants is a record we cannot explain, so
    it does not get to ship a patch. Everything else is kept: the attempts, the
    counts and the cost are the evidence of what went wrong.
    """
    violations = record.check_invariants()
    if not violations:
        return record

    LOG.error(
        "run %s VIOLATED %d INVARIANT(S); verdict forced to %s and every patch withheld",
        record.run_id,
        len(violations),
        Verdict.ABORTED_BUDGET.value,
    )
    for violation in violations:
        LOG.error("run %s invariant violated: %s", record.run_id, violation)
    _sink(on_event)(events.verification_failed(record.run_id, violations))

    return record.model_copy(
        update={
            "verdict": Verdict.ABORTED_BUDGET,
            "fix_attempts": [
                attempt.model_copy(
                    update={
                        "patch": _WITHHELD_PATCH,
                        "accepted": False,
                        "reasoning": f"Patch withheld, run unsound. {attempt.reasoning}",
                    }
                )
                for attempt in record.fix_attempts
            ],
            # The handover was written from evidence we have just declared
            # unsound, and a client_reply must never promise a fix the verdict
            # does not support.
            "handover": None,
        }
    )


#: What replaces a patch that may not ship. Empty diff, and it says why.
_WITHHELD_PATCH = Patch(
    unified_diff="",
    files_touched=[],
    rationale=(
        "Withheld. This run violated its own invariants, so no patch from it may be "
        "shown to a human as if it were verified."
    ),
)
