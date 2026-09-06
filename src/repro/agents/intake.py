"""Node: intake. OWNER: Engineer A (orchestration) + Engineer C (prompt bodies)."""
from __future__ import annotations

from repro.agents._common import add_usage, payload
from repro.contracts import ReportFacts
from repro.graph.state import GraphState
from repro.llm.base import LLMClient

SYSTEM_PROMPT = "TODO: Engineer C owns this"


def intake_node(state: GraphState, llm: LLMClient) -> dict:
    """Read state, do one job, return ONLY the keys that changed."""
    facts, response = llm.structured(
        system=SYSTEM_PROMPT,
        user=payload(report=state["report"]),
        schema=ReportFacts,
    )
    return {"facts": facts, "usage": add_usage(state, response)}
