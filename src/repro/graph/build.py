"""Assembles the graph. OWNER: Engineer A."""
from __future__ import annotations

from repro.llm.base import LLMClient


def build_graph(llm: LLMClient):
    """Compile and return the Repro graph.

    Shape (see docs/ARCHITECTURE.md):
        START -> intake -> (clarify | localise)
        localise -> repro -> {reproduced? fix : repro (<=3) : report}
        fix -> {accepted? report : fix (<=3) : report}
        report -> END
    """
    raise NotImplementedError("Engineer A, task A4")
