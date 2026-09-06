"""Node: localiser. OWNER: Engineer A (orchestration) + Engineer C (prompt bodies)."""
from __future__ import annotations

from repro.agents._common import add_usage, payload
from repro.contracts import MAX_LOCALISE_CANDIDATES, Hypothesis, ReportFacts
from repro.graph.sandbox_seam import Sandbox, require_sandbox
from repro.graph.state import GraphState
from repro.llm.base import LLMClient

SYSTEM_PROMPT = "TODO: Engineer C owns this"


def localiser_node(state: GraphState, llm: LLMClient, *, sandbox: Sandbox | None = None) -> dict:
    """Read state, do one job, return ONLY the keys that changed.

    One model call, not five: local search is free and `rank_candidates` turns
    its hits into typed candidates without a token, so the model is spent on
    the one hypothesis that has to link the client's words to a file.
    """
    box = require_sandbox(sandbox)
    facts = state.get("facts")
    hits = box.search(_query(facts), k=MAX_LOCALISE_CANDIDATES)

    best, response = llm.structured(
        system=SYSTEM_PROMPT,
        user=payload(facts=facts, hits=[{"path": p, "snippet": s} for p, s, _ in hits]),
        schema=Hypothesis,
    )

    hypotheses = [best]
    seen = {(best.file_path, best.symbol)}
    for candidate in box.rank_candidates(hits, MAX_LOCALISE_CANDIDATES):
        if len(hypotheses) >= MAX_LOCALISE_CANDIDATES:
            break
        key = (candidate.file_path, candidate.symbol)
        if key in seen:
            continue
        seen.add(key)
        hypotheses.append(candidate)

    return {"hypotheses": hypotheses, "usage": add_usage(state, response)}


def _query(facts: ReportFacts | None) -> str:
    """A search query, not a prompt: the client's own words, keywords only."""
    if facts is None:
        return ""
    parts = [facts.entrypoint_hint, facts.observed_behaviour, *facts.steps]
    return " ".join(part for part in parts if part)
