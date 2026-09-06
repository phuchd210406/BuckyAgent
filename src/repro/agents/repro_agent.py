"""Node: repro_agent. OWNER: Engineer A (orchestration) + Engineer C (prompt bodies)."""
from __future__ import annotations

from repro.agents._common import add_usage, is_reproduction, payload, why_not_reproduced
from repro.contracts import MAX_REPRO_ATTEMPTS, ReproAttempt, TestArtifact
from repro.graph.sandbox_seam import Sandbox, require_sandbox
from repro.graph.state import GraphState
from repro.llm.base import LLMClient

SYSTEM_PROMPT = """\
Write ONE pytest test that FAILS because of the reported bug.

- Import only from the project and the standard library.
- No network, no sleep, no randomness.
- Never mock or patch the code under test; mocking it proves nothing.
- Assert what the CLIENT described, not what the code does now.

A test that PASSES is a failed reproduction: you have not shown the bug exists. If a previous attempt is shown, read its error and change approach.

Example - "charged me postage even though the site says free postage over $50":

    from shopcart.pricing import Line, total

    def test_free_shipping_uses_pre_discount_total():
        # $55 of goods, 10% promo. Free postage was promised over $50.
        assert total([Line("A", 55.0, 1)], promo="SAVE10") == 49.50

It fails today because postage is added back: that failure IS the reproduction.
"""


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
