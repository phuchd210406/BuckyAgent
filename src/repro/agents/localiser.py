"""Node: localiser. OWNER: Engineer A (orchestration) + Engineer C (prompt bodies)."""
from __future__ import annotations

from repro.agents._common import add_usage, payload
from repro.contracts import MAX_LOCALISE_CANDIDATES, Hypothesis, ReportFacts
from repro.graph.sandbox_seam import Sandbox, require_sandbox
from repro.graph.state import GraphState
from repro.llm.base import LLMClient

SYSTEM_PROMPT = """\
Name the one place in the code that best explains what the client observed.

You get the extracted facts and snippets found by local search. Choose the snippet whose code most directly PRODUCES the observed behaviour - not the most interesting code, not the code most in need of tidying.

Copy file_path exactly from the hit you chose. Never propose a fix, that is a later step. If nothing explains the report, take the closest hit and set confidence below 0.3 rather than manufacture certainty.

rationale is two sentences: the first quotes the client's own words, the second names the line or condition that produces them. Example:
"The client was 'charged postage even though the site says free postage over $50'. shipping_for compares that threshold against the already-discounted subtotal, so a $55 basket with a promo falls under $50 and is charged."
"""


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
