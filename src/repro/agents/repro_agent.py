"""Node: repro_agent. OWNER: Engineer A (orchestration) + Engineer C (prompt bodies)."""
from __future__ import annotations

from repro.agents._common import (
    add_usage,
    errored,
    is_reproduction,
    model_failures,
    payload,
    source_excerpts,
    why_not_reproduced,
)
from repro.contracts import (
    MAX_REPRO_ATTEMPTS,
    ExecutionResult,
    ReproAttempt,
    TestArtifact,
)
from repro.graph.sandbox_seam import Sandbox, require_sandbox, unsafe_test_path
from repro.graph.state import GraphState
from repro.llm.base import LLMClient

SYSTEM_PROMPT = """\
Write ONE pytest test that FAILS because of the reported bug.

- Import only from the project and the standard library.
- `source` is the real code: call only names in it, with their real parameters.
- No network, no sleep, no randomness.
- Never mock or patch the code under test; mocking it proves nothing.
- Assert what the CLIENT described, not what the code does now.

A test that PASSES is a failed reproduction, and so is one that fails with TypeError, NameError or ImportError. If a previous attempt is shown, read its output and change approach.

Example - "charged me postage even though the site says free postage over $50":

    from shopcart.pricing import Line, total

    def test_free_shipping_uses_pre_discount_total():
        assert total([Line("A", 55.0, 1)], promo="SAVE10") == 49.50
"""

#: A whole test file, JSON-escaped. The 1024 default truncates
#: real ones, and a truncated reply is refused outright by the client rather
#: than parsed -- so this ceiling is the difference between an attempt and an
#: error.
MAX_TOKENS = 4096


def repro_agent_node(state: GraphState, llm: LLMClient, *, sandbox: Sandbox | None = None) -> dict:
    """Write one candidate failing test, RUN IT, and judge the result in Python.

    `reproduced` is the gate for the whole product, so it is never asked of the
    model: it is computed from the ExecutionResult by `is_reproduction`.
    """
    box = require_sandbox(sandbox)
    attempts = state.get("repro_attempts", [])

    hypotheses = state.get("hypotheses", [])
    parts: dict = {
        "facts": state.get("facts"),
        "hypotheses": hypotheses,
        "previous": [_previous(a) for a in attempts[-1:]],
    }
    source = source_excerpts(box, hypotheses)
    if source:
        parts["source"] = source

    try:
        test, response = llm.structured(
            system=SYSTEM_PROMPT,
            user=payload(**parts),
            max_tokens=MAX_TOKENS,
            schema=TestArtifact,
        )
    except model_failures() as exc:
        # No test to run, but the loop is bounded and one try is now spent.
        # A cassette that is missing, a schema the model cannot hit twice, or a
        # reply the token ceiling cut off: all recoverable, none fatal.
        return _no_usable_test(state, exc)

    result, refusal = _write_and_run(box, test)
    reproduced = is_reproduction(result)
    if not reproduced and refusal is None:
        # A test that passed or broke stays out of tests/: check 2 runs the
        # whole suite, and a leftover red test there would veto every fix.
        _remove(box, test.path)

    return {
        "repro_attempts": [
            ReproAttempt(
                # repro_count is owned by the graph; this only labels the attempt.
                attempt_no=min(state.get("repro_count", 0) + 1, MAX_REPRO_ATTEMPTS),
                test=test,
                result=result,
                reproduced=reproduced,
                reasoning=refusal or why_not_reproduced(result),
            )
        ],
        "usage": add_usage(state, response),
    }


#: How much of the previous attempt's pytest output goes back to the model.
#: The end is where the assertion or the exception is.
PREVIOUS_OUTPUT_CHARS = 1500


def _previous(attempt: ReproAttempt) -> dict:
    """What the model needs to do better than its last try: what pytest said."""
    return {
        "path": attempt.test.path,
        "reasoning": attempt.reasoning,
        "stderr": attempt.result.stderr_tail,
        "output": attempt.result.stdout_tail[-PREVIOUS_OUTPUT_CHARS:],
    }


def _remove(box: Sandbox, path: str) -> None:
    """Delete a generated test, on a sandbox that can. Never raises."""
    remove = getattr(box, "remove_test", None)
    if remove is None:
        return
    try:
        remove(path)
    except (OSError, ValueError):
        pass


def _write_and_run(box: Sandbox, test: TestArtifact) -> tuple[ExecutionResult, str | None]:
    """Run the model's test, or record why we would not. Never raises.

    The path comes from the model, so it is untrusted input: it can point
    outside `tests/`, climb out of the workspace with `..`, or look like a
    pytest flag. The sandbox refuses all three -- and an uncaught refusal used
    to take the whole run down with it, which turns one bad reply into a lost
    run instead of one spent attempt.
    """
    problem = unsafe_test_path(test.path)
    if problem:
        refusal = f"Refused to write this test: {problem}. That is a broken test, not a reproduction."
        return errored(refusal), refusal
    try:
        box.write_test(test)
        return box.run_test(test.path), None
    except ValueError as exc:
        refusal = f"The sandbox refused this test: {exc}"
        return errored(refusal), refusal


def _no_usable_test(state: GraphState, exc: BaseException) -> dict:
    """Record the attempt the model failed to produce, so the retry has context."""
    attempt_no = min(state.get("repro_count", 0) + 1, MAX_REPRO_ATTEMPTS)
    reasoning = (
        f"The model did not return a usable test ({type(exc).__name__}: {exc}). "
        "Nothing was written and nothing was run, so this reproduces nothing."
    )
    return {
        "repro_attempts": [
            ReproAttempt(
                attempt_no=attempt_no,
                test=TestArtifact(path=f"tests/test_attempt_{attempt_no}_not_generated.py", source=""),
                result=errored(reasoning),
                reproduced=False,
                reasoning=reasoning,
            )
        ],
        "usage": add_usage(state),
    }
