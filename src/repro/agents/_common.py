"""Plumbing shared by the nodes. OWNER: Engineer A.

Nothing in here is prompt text and nothing in here calls a model. The two
predicates are the product's safety property expressed once, in one place, so
no node can quietly disagree with another about what counts as a reproduction.
"""
from __future__ import annotations

import json
from typing import Any

from pydantic import BaseModel

from repro.contracts import (
    SANDBOX_MAX_OUTPUT_CHARS,
    ExecutionResult,
    LLMResponse,
    TokenUsage,
    Verdict,
)
from repro.graph.state import GraphState


def payload(**parts: Any) -> str:
    """Render the state slices a node needs as JSON for the user turn.

    Engineer C owns how a prompt READS; this owns only what data it carries.
    Keep the parts small -- ARCHITECTURE.md rule 4: the model never sees a
    whole file, and anything put in here is re-read on every turn of a loop.
    """
    return json.dumps({key: _plain(value) for key, value in parts.items()}, default=str)


def _plain(value: Any) -> Any:
    if isinstance(value, BaseModel):
        return value.model_dump(mode="json")
    if isinstance(value, (list, tuple)):
        return [_plain(item) for item in value]
    return value


def add_usage(state: GraphState, *responses: LLMResponse) -> TokenUsage:
    """This node's spend, added to the run's. The graph re-checks it either way."""
    total = state.get("usage") or TokenUsage()
    for response in responses:
        total = total.merge(response.usage)
    return total


def is_reproduction(result: ExecutionResult) -> bool:
    """True only when the test RAN and FAILED.

    The three ways this is False are all different, and conflating them is how a
    patch gets accepted for a bug nobody ever saw:
      * the test passed        -> the reported behaviour did not happen;
      * the test errored       -> it never ran (import or collection error);
      * the test timed out     -> we know nothing at all.
    """
    return not result.timed_out and result.errors == 0 and result.failed > 0


def why_not_reproduced(result: ExecutionResult) -> str:
    """One audit-trail line naming which of the three cases this was."""
    if result.timed_out:
        return "The test did not finish inside the sandbox timeout, so it proves nothing."
    if result.errors:
        return (
            "The test errored before it could run (import or collection error). "
            "That is a broken test, not a reproduction of the client's bug."
        )
    if result.failed:
        return f"The test ran and failed ({result.failed} failed): the reported behaviour occurred."
    return "The test ran and passed, so the behaviour the client described did not occur."


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


def errored(message: str) -> ExecutionResult:
    """An ExecutionResult for something that never got as far as running."""
    return ExecutionResult(
        exit_code=1,
        stdout_tail="",
        stderr_tail=message[:SANDBOX_MAX_OUTPUT_CHARS],
        duration_s=0.0,
        errors=1,
    )
