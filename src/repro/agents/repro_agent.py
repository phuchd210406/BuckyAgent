"""Node: repro_agent. OWNER: Engineer A (orchestration) + Engineer C (prompt bodies)."""
from __future__ import annotations

from repro.graph.state import GraphState
from repro.llm.base import LLMClient


def repro_agent_node(state: GraphState, llm: LLMClient) -> dict:
    """Read state, do one job, return ONLY the keys that changed."""
    raise NotImplementedError
