"""LangGraph state. OWNER: Engineer A."""
from __future__ import annotations

import operator
from typing import Annotated, Optional

from typing_extensions import TypedDict

from repro.contracts import (
    ClarifyingQuestion,
    ClientReport,
    FixAttempt,
    Handover,
    Hypothesis,
    ReportFacts,
    ReproAttempt,
    TokenUsage,
    Verdict,
)


class GraphState(TypedDict, total=False):
    """Shared memory for one run.

    Reducers matter: any key written by more than one node needs one, or a
    concurrent write silently wins. Counters are plain ints because only the
    owning node increments them.
    """

    report: ClientReport
    facts: Optional[ReportFacts]
    questions: Annotated[list[ClarifyingQuestion], operator.add]
    hypotheses: Annotated[list[Hypothesis], operator.add]
    repro_attempts: Annotated[list[ReproAttempt], operator.add]
    fix_attempts: Annotated[list[FixAttempt], operator.add]
    handover: Optional[Handover]
    verdict: Verdict
    # bounds — read by the routers, never by the model
    clarify_rounds: int
    repro_count: int
    fix_count: int
    usage: TokenUsage
    workspace_path: str
