"""Node: fix. OWNER: Engineer A (orchestration) + Engineer C (prompt bodies)."""
from __future__ import annotations

from repro.agents._common import add_usage, errored, payload
from repro.contracts import MAX_FIX_ATTEMPTS, ExecutionResult, FixAttempt, Patch
from repro.graph.sandbox_seam import Sandbox, require_sandbox
from repro.graph.state import GraphState
from repro.llm.base import LLMClient

SYSTEM_PROMPT = """\
Write the smallest patch that makes the failing test pass.

unified_diff must apply with `git apply` from the repo root: a/ and b/ prefixes, real context lines.

Never:
- change the test file, or any test, in any way;
- weaken an assertion, delete a test, or mark one skip/xfail;
- wrap the symptom in try/except, or special-case the failing input;
- reformat, rename or tidy anything the fix does not require.

Fix the cause, in the fewest lines that do it. If the only correct fix would require changing an existing test, return an empty unified_diff and use rationale to name that test and say why. That is a design decision for a human, not for you.

Example rationale: "shipping_for was called with the post-promo subtotal, so a $55 basket with SAVE10 dropped under the $50 threshold. total() now passes the pre-discount subtotal, leaving the discount on goods only."
"""


def fix_node(state: GraphState, llm: LLMClient, *, sandbox: Sandbox | None = None) -> dict:
    """Propose one patch and verify it TWICE: the repro test, then the suite."""
    box = require_sandbox(sandbox)
    attempts = state.get("repro_attempts", [])
    reproduced = [a for a in attempts if a.reproduced]
    target_path = reproduced[-1].test.path if reproduced else ""

    patch, response = llm.structured(
        system=SYSTEM_PROMPT,
        user=payload(
            facts=state.get("facts"),
            hypotheses=state.get("hypotheses", []),
            failing_test=reproduced[-1].test if reproduced else None,
            failure=reproduced[-1].result.stdout_tail if reproduced else "",
            rejected=[
                {"rationale": f.patch.rationale, "reasoning": f.reasoning}
                for f in state.get("fix_attempts", [])[-1:]
            ],
        ),
        schema=Patch,
    )

    target_test, suite = _verify(box, patch, target_path)
    # The product's safety property. There is no other route to True.
    accepted = target_test.green and suite.green

    return {
        "fix_attempts": [
            FixAttempt(
                # fix_count is owned by the graph; this only labels the attempt.
                attempt_no=min(state.get("fix_count", 0) + 1, MAX_FIX_ATTEMPTS),
                patch=patch,
                target_test=target_test,
                suite=suite,
                accepted=accepted,
                reasoning=_reasoning(target_test, suite, accepted),
            )
        ],
        "usage": add_usage(state, response),
    }


def _verify(box: Sandbox, patch: Patch, target_path: str) -> tuple[ExecutionResult, ExecutionResult]:
    """Apply, run both, and leave the workspace as we found it unless it worked."""
    applied, message = box.apply_patch(patch)
    if not applied:
        # A diff that will not apply is a failed attempt, not an exception.
        result = errored(message or "patch did not apply")
        return result, result

    target_test = box.run_test(target_path)
    suite = box.run_suite()
    if not (target_test.green and suite.green):
        # Otherwise attempt N+1 would be written against a half-patched repo.
        box.revert_patch(patch)
    return target_test, suite


def _reasoning(target_test: ExecutionResult, suite: ExecutionResult, accepted: bool) -> str:
    if accepted:
        return "The repro test went green and the existing suite stayed green."
    if not target_test.green and not suite.green:
        return "The patch neither fixed the repro test nor left the suite green. Reverted."
    if not target_test.green:
        return "The repro test is still red: this patch does not fix the reported bug. Reverted."
    return "The repro test went green but the patch broke the existing suite. Reverted."
