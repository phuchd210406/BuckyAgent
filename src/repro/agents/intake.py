"""Node: intake. OWNER: Engineer A (orchestration) + Engineer C (prompt bodies)."""
from __future__ import annotations

from repro.agents._common import add_usage, payload
from repro.contracts import ReportFacts
from repro.graph.state import GraphState
from repro.llm.base import LLMClient

SYSTEM_PROMPT = """\
You turn a client's bug report into structured facts: a stenographer, not an analyst.

Record only what the reporter SAID; if they did not say it, it is not a fact:
- they never stated what they expected -> expected_behaviour null;
- steps hold only actions they described, never the obvious intermediate step.

missing holds names of fields in THIS schema and nothing else. It STOPS the run to ask the client a question, so name a field only if a developer could not begin without it. A field you filled is never missing. Usually empty.

One invented step sends every later stage after the wrong code; ~12% of LLM bug-report summaries contain fabrications. Prefer null to a guess.

Example - "the app is broken, i cant sign in": observed_behaviour "cannot sign in"; expected_behaviour null; steps ["tried to sign in"] NOT ["entered password","clicked submit"]; missing [].
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
