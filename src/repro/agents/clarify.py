"""Node: clarify. OWNER: Engineer A (orchestration) + Engineer C (prompt bodies).

Terminal on purpose: the run stops here and a human answers. See build.py --
clarify has an edge to END, not back to intake, and MAX_CLARIFY_ROUNDS bounds
how often a resumed run may arrive here at all.

NOTE FOR THE LEAD: ARCHITECTURE.md says three questions; ClarifyingQuestion is
a single question and contracts.py is frozen, so this asks for one. Two ways to
get three, both yours to pick: a `ClarifyingQuestions` list wrapper in
contracts.py (one model call), or a MAX_CLARIFY_QUESTIONS cap to bound a loop
of three calls. I did not invent either.
"""
from __future__ import annotations

from repro.agents._common import add_usage, payload
from repro.contracts import ClarifyingQuestion, Verdict
from repro.graph.state import GraphState
from repro.llm.base import LLMClient

SYSTEM_PROMPT = """\
Write ONE question back to the person who reported the problem. They are not a developer and cannot read code.

You get their report, the facts we extracted, and the fields still missing. Ask the single question that unblocks the most important one.

Never mention file names, stack traces, error codes, endpoints, "console", "network tab", "browser", or "reproduce". If someone's grandparent could not answer it, rewrite it. Ask what they saw, what they expected, or when it started - something answerable from memory in one line.

BAD: "What HTTP status did the checkout endpoint return?"
GOOD: "When you tried to pay, did you see an error message, or did the page just not do anything?"

One question only. They may not reply twice.
"""


def clarify_node(state: GraphState, llm: LLMClient) -> dict:
    """Read state, do one job, return ONLY the keys that changed."""
    facts = state.get("facts")
    question, response = llm.structured(
        system=SYSTEM_PROMPT,
        user=payload(
            report=state["report"],
            facts=facts,
            missing=list(facts.missing) if facts else [],
        ),
        schema=ClarifyingQuestion,
    )
    # clarify_rounds is incremented by the graph, never here.
    return {
        "questions": [question],
        "verdict": Verdict.NEEDS_CLARIFICATION,
        "usage": add_usage(state, response),
    }
