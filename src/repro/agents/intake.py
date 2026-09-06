"""Node: intake. OWNER: Engineer A (orchestration) + Engineer C (prompt bodies)."""
from __future__ import annotations

from repro.agents._common import add_usage, payload
from repro.contracts import ReportFacts
from repro.graph.state import GraphState
from repro.llm.base import LLMClient

SYSTEM_PROMPT = """\
Turn a client's bug report into structured facts. You are a stenographer, not an analyst.

Record only what the reporter SAID. If they did not say it, it is not a fact:
- they never stated what they expected -> expected_behaviour null, and "expected_behaviour" goes in missing;
- steps hold only actions they described, never the obvious intermediate step;
- confidence measures how faithful your reading is, not how real the bug is.

One invented step sends every later stage after the wrong code, and about 12% of LLM bug-report summaries contain fabricated content. Prefer null and missing to a good guess.

Example - "the app is broken, i cant sign in any more":
observed_behaviour "cannot sign in"; expected_behaviour null; steps ["tried to sign in"], NOT ["entered password", "clicked submit"]; missing ["expected_behaviour", "steps", "environment"].
"""

#: A ReportFacts is a handful of short strings.
MAX_TOKENS = 1024


def intake_node(state: GraphState, llm: LLMClient) -> dict:
    """Read state, do one job, return ONLY the keys that changed."""
    facts, response = llm.structured(
        system=SYSTEM_PROMPT,
        user=payload(report=state["report"]),
        max_tokens=MAX_TOKENS,
        schema=ReportFacts,
    )
    return {"facts": facts, "usage": add_usage(state, response)}
