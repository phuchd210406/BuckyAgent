"""Node: repro_agent. OWNER: Engineer A (orchestration) + Engineer C (prompt bodies)."""
from __future__ import annotations

from repro.agents._common import add_usage, is_reproduction, payload, why_not_reproduced
from repro.contracts import MAX_REPRO_ATTEMPTS, ReproAttempt, TestArtifact
from repro.graph.sandbox_seam import Sandbox, require_sandbox
from repro.graph.state import GraphState
from repro.llm.base import LLMClient

SYSTEM_PROMPT = "TODO: Engineer C owns this"


def repro_agent_node(state: GraphState, llm: LLMClient, *, sandbox: Sandbox | None = None) -> dict:
    """Write one candidate failing test, RUN IT, and judge the result in Python.

    `reproduced` is the gate for the whole product, so it is never asked of the
    model: it is computed from the ExecutionResult by `is_reproduction`.
    """
    box = require_sandbox(sandbox)
    attempts = state.get("repro_attempts", [])

    test, response = llm.structured(
        system=SYSTEM_PROMPT,
        user=payload(
            facts=state.get("facts"),
            hypotheses=state.get("hypotheses", []),
            previous=[
                {"path": a.test.path, "reasoning": a.reasoning, "stderr": a.result.stderr_tail}
                for a in attempts[-1:]
            ],
        ),
        schema=TestArtifact,
    )

    box.write_test(test)
    result = box.run_test(test.path)

    return {
        "repro_attempts": [
            ReproAttempt(
                # repro_count is owned by the graph; this only labels the attempt.
                attempt_no=min(state.get("repro_count", 0) + 1, MAX_REPRO_ATTEMPTS),
                test=test,
                result=result,
                reproduced=is_reproduction(result),
                reasoning=why_not_reproduced(result),
            )
        ],
        "usage": add_usage(state, response),
    }
