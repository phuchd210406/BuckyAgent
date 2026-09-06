"""Node: reporter. OWNER: Engineer A (orchestration) + Engineer C (prompt bodies)."""
from __future__ import annotations

from repro.agents._common import add_usage, payload, verdict_for
from repro.contracts import Handover
from repro.graph.state import GraphState
from repro.llm.base import LLMClient

SYSTEM_PROMPT = """\
Write the two documents that end a run. The verdict is given to you: never choose it, never soften it.

dev_summary is a markdown PR body: root cause, what the patch changes and why, and the evidence trail - the test that failed, then went green, and the suite that stayed green. Exact paths.

client_reply is plain language to the reporter, echoing their own words so they know they were understood. No file names, no code, no jargon, one apology at most, no timelines. If the verdict is not reproduced_and_fixed, it must not imply anything was fixed or shipped.

Example dev_summary heading: "## Free shipping used the discounted subtotal".
Example client_reply: "You were 'charged postage even though the site says free postage over $50' - we reproduced exactly that. The discount came off before we checked the $50 limit. A fix is with our engineers to review."
"""

#: A PR body and a client email, in one object.
MAX_TOKENS = 2048


def reporter_node(state: GraphState, llm: LLMClient) -> dict:
    """Write for both audiences. The verdict is decided here, not by the model."""
    verdict = verdict_for(state)
    repro_attempts = state.get("repro_attempts", [])
    accepted = [f for f in state.get("fix_attempts", []) if f.accepted]

    handover, response = llm.structured(
        system=SYSTEM_PROMPT,
        user=payload(
            report=state["report"],
            facts=state.get("facts"),
            verdict=verdict.value,
            # Tails and rationales only: the diff itself goes in the PR, not the prompt.
            reproduced_by=[a.test.path for a in repro_attempts if a.reproduced],
            attempts={"repro": len(repro_attempts), "fix": len(state.get("fix_attempts", []))},
            fix=[{"files": f.patch.files_touched, "rationale": f.patch.rationale} for f in accepted],
        ),
        max_tokens=MAX_TOKENS,
        schema=Handover,
    )

    return {"verdict": verdict, "handover": handover, "usage": add_usage(state, response)}
