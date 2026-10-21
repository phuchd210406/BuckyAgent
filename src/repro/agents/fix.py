"""Node: fix. OWNER: Engineer A (orchestration) + Engineer C (prompt bodies)."""
from __future__ import annotations

from repro.agents._common import add_usage, errored, model_failures, payload, source_excerpts
from repro.contracts import MAX_FIX_ATTEMPTS, ExecutionResult, FixAttempt, Patch
from repro.graph.sandbox_seam import Sandbox, require_sandbox
from repro.graph.state import GraphState
from repro.llm.base import LLMClient

SYSTEM_PROMPT = """\
Write the smallest patch that makes the failing test pass.

unified_diff must apply with `git apply` from the repo root: a/ and b/ prefixes, context and `-` lines copied exactly from `source`. If git refused your last diff, rebuild it from `source`.

Never:
- change the test file, or any test, in any way;
- weaken an assertion, delete a test, or mark one skip/xfail;
- wrap the symptom in try/except, or special-case the failing input;
- reformat, rename or tidy anything the fix does not need.

Fix the cause, in the fewest lines. If the only correct fix would change an existing test, return an empty unified_diff and name that test in rationale. That is a design decision for a human, not for you.

Example rationale: "shipping_for got the post-promo subtotal, so a $55 basket with SAVE10 fell under the $50 threshold. total() now passes the pre-discount one."
"""

#: A whole unified diff, JSON-escaped.
MAX_TOKENS = 4096


def fix_node(state: GraphState, llm: LLMClient, *, sandbox: Sandbox | None = None) -> dict:
    """Propose one patch and verify it TWICE: the repro test, then the suite."""
    box = require_sandbox(sandbox)
    attempts = state.get("repro_attempts", [])
    reproduced = [a for a in attempts if a.reproduced]
    target_path = reproduced[-1].test.path if reproduced else ""

    hypotheses = state.get("hypotheses", [])
    parts: dict = {
        "facts": state.get("facts"),
        "hypotheses": hypotheses,
        "failing_test": reproduced[-1].test if reproduced else None,
        "failure": reproduced[-1].result.stdout_tail if reproduced else "",
        "rejected": [_rejected(f) for f in state.get("fix_attempts", [])[-1:]],
    }
    source = source_excerpts(box, hypotheses)
    if source:
        parts["source"] = source

    try:
        patch, response = llm.structured(
            system=SYSTEM_PROMPT,
            user=payload(**parts),
            max_tokens=MAX_TOKENS,
            schema=Patch,
        )
    except model_failures() as exc:
        # One spent attempt, workspace untouched: nothing was applied.
        return _no_usable_patch(state, exc)

    target_test, suite, problem = _verify(box, patch, target_path)
    # The product's safety property. There is no other route to True.
    accepted = problem is None and target_test.green and suite.green

    return {
        "fix_attempts": [
            FixAttempt(
                # fix_count is owned by the graph; this only labels the attempt.
                attempt_no=min(state.get("fix_count", 0) + 1, MAX_FIX_ATTEMPTS),
                patch=patch,
                target_test=target_test,
                suite=suite,
                accepted=accepted,
                reasoning=_reasoning(target_test, suite, accepted, problem),
            )
        ],
        "usage": add_usage(state, response),
    }


#: How much of a rejected attempt's evidence goes back to the model.
REJECTED_OUTPUT_CHARS = 1500


def _rejected(attempt: FixAttempt) -> dict:
    """What the model needs to know about its last, rejected patch.

    `reasoning` alone said only "it did not work". When git refused the diff,
    git's own message (which file, which line) is what lets the next diff be
    right, so the evidence goes back with it: the failure output of the check
    that went red, or the refusal itself.
    """
    failing = attempt.target_test if not attempt.target_test.green else attempt.suite
    evidence = failing.stdout_tail or failing.stderr_tail
    return {
        "rationale": attempt.patch.rationale,
        "reasoning": attempt.reasoning,
        "evidence": evidence[-REJECTED_OUTPUT_CHARS:],
    }


def _verify(
    box: Sandbox, patch: Patch, target_path: str
) -> tuple[ExecutionResult, ExecutionResult, str | None]:
    """Apply, run both, and leave the workspace as we found it unless it worked.

    The third value names why nothing was tested at all (no target, a diff that
    would not apply, a refused path), or is None when both checks really ran.
    """
    if not target_path:
        # An empty target is not "run the repro test", it is "run the entire
        # suite" as far as pytest is concerned -- which would let a patch be
        # accepted having never been checked against the bug it claims to fix.
        problem = "There is no reproduced test to verify against, so a patch cannot be accepted."
        refused = errored(problem)
        return refused, refused, problem

    applied = False
    try:
        applied, message = box.apply_patch(patch)
        if not applied:
            # A diff that will not apply is a failed attempt, not an exception.
            problem = f"The patch was not applied ({(message or 'patch did not apply')[:500]})."
            result = errored(message or "patch did not apply")
            return result, result, problem

        target_test = box.run_test(target_path)
        suite = box.run_suite()
    except ValueError as exc:
        # A diff whose paths escape the workspace is refused by the sandbox.
        # That is one spent attempt, not a crashed run.
        if applied:
            box.revert_patch(patch)
        problem = f"The sandbox refused this patch: {exc}"
        result = errored(problem)
        return result, result, problem

    if not (target_test.green and suite.green):
        # Otherwise attempt N+1 would be written against a half-patched repo.
        box.revert_patch(patch)
    return target_test, suite, None


def _reasoning(
    target_test: ExecutionResult,
    suite: ExecutionResult,
    accepted: bool,
    problem: str | None = None,
) -> str:
    if problem:
        return f"{problem} Nothing was tested, so nothing was fixed."
    if accepted:
        return "The repro test went green and the existing suite stayed green."
    if not target_test.green and not suite.green:
        return "The patch neither fixed the repro test nor left the suite green. Reverted."
    if not target_test.green:
        return "The repro test is still red: this patch does not fix the reported bug. Reverted."
    return "The repro test went green but the patch broke the existing suite. Reverted."


def _no_usable_patch(state: GraphState, exc: BaseException) -> dict:
    """Record the attempt the model failed to produce. Nothing was applied."""
    reasoning = (
        f"The model did not return a usable patch ({type(exc).__name__}: {exc}). "
        "Nothing was applied to the workspace."
    )
    return {
        "fix_attempts": [
            FixAttempt(
                attempt_no=min(state.get("fix_count", 0) + 1, MAX_FIX_ATTEMPTS),
                patch=Patch(unified_diff="", files_touched=[], rationale=reasoning),
                target_test=errored(reasoning),
                suite=errored(reasoning),
                accepted=False,
                reasoning=reasoning,
            )
        ],
        "usage": add_usage(state),
    }
