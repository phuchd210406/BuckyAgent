"""Node: reporter. OWNER: Engineer A (orchestration) + Engineer C (prompt bodies)."""
from __future__ import annotations

from repro.agents._common import add_usage, payload, verdict_for
from repro.contracts import Handover
from repro.graph.state import GraphState
from repro.llm.base import LLMClient

SYSTEM_PROMPT = "TODO: Engineer C owns this"


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
        schema=Handover,
    )

    return {"verdict": verdict, "handover": handover, "usage": add_usage(state, response)}
