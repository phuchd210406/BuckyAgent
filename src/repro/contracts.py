"""
Repro — frozen data contracts.

THIS FILE IS OWNED BY THE TECH LEAD AND IS FROZEN AT HOUR 1.

Every workstream imports from here and from nowhere else in the package.
That is the entire reason five engineers can work in parallel without
blocking each other: the *shapes* are agreed up front, so each module can be
built and unit-tested against the shape before the module it talks to exists.

If you believe you need to change something here:
  1. Do NOT edit it yourself.
  2. Post in the team channel with the exact field you need.
  3. The lead makes the edit, bumps CONTRACT_VERSION, and tells everyone.

Session 3, "Descriptions are the interface": every ``description=`` string in
this file is read by the model when it fills the schema. They are prompt text,
not comments. Write them as instructions to a careful junior colleague.
"""

from __future__ import annotations

from datetime import datetime, timezone
from enum import Enum
from typing import Literal, Optional

from pydantic import BaseModel, ConfigDict, Field

CONTRACT_VERSION = "1.0.0"

# ---------------------------------------------------------------------------
# Loop bounds.
#
# Session 3, "Bound every loop": a refine loop that exits when the critic is
# satisfied will sometimes never be satisfied, and you find out from the bill.
# These caps live in state and IGNORE the model's judgement. Nothing in this
# codebase may loop without consulting one of these.
# ---------------------------------------------------------------------------
MAX_CLARIFY_ROUNDS = 1
MAX_LOCALISE_CANDIDATES = 5
MAX_REPRO_ATTEMPTS = 3
MAX_FIX_ATTEMPTS = 3
MAX_TOTAL_LLM_CALLS = 40
MAX_RUN_USD = 0.25  # hard stop per run; the whole team budget is USD 20

# Sandbox bounds.
SANDBOX_TIMEOUT_S = 60
SANDBOX_MAX_OUTPUT_CHARS = 4_000  # "Keep payloads small" — tails only, never dumps


class Strict(BaseModel):
    """Base model: reject unknown fields loudly instead of silently dropping them."""

    model_config = ConfigDict(extra="forbid", frozen=False)


# ---------------------------------------------------------------------------
# 1. What comes in
# ---------------------------------------------------------------------------


class Channel(str, Enum):
    EMAIL = "email"
    CHAT = "chat"
    ISSUE = "issue"
    PHONE_NOTE = "phone_note"


class ClientReport(Strict):
    """A complaint exactly as a non-technical person wrote it. Never edited."""

    run_id: str = Field(description="Opaque id for this run, e.g. a uuid4 hex.")
    raw_text: str = Field(description="The complaint verbatim. Do not clean it up.")
    channel: Channel = Channel.EMAIL
    reporter_name: Optional[str] = None
    repo_path: str = Field(description="Absolute path to a local checkout of the project.")
    received_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))


# ---------------------------------------------------------------------------
# 2. Intake — turning prose into facts
# ---------------------------------------------------------------------------


class ReportFacts(Strict):
    """
    The structured reading of a client complaint.

    Every field is what the REPORTER said or implied, never what we assume.
    If the report does not say it, leave it None and name it in `missing`.
    """

    observed_behaviour: Optional[str] = Field(
        None, description="What actually happened, in one sentence, from the report only."
    )
    expected_behaviour: Optional[str] = Field(
        None, description="What the reporter expected instead. None if they never said."
    )
    steps: list[str] = Field(
        default_factory=list,
        description="Ordered actions the reporter described taking. One action per item. "
        "Do not invent intermediate steps that the reporter did not mention.",
    )
    entrypoint_hint: Optional[str] = Field(
        None,
        description="The user-facing feature named or implied, e.g. 'checkout', "
        "'password reset'. Used to seed code search. None if unclear.",
    )
    environment: Optional[str] = Field(
        None, description="Browser, OS, device or version, only if stated."
    )
    missing: list[str] = Field(
        default_factory=list,
        description="Field names from this model that a developer would need and the "
        "report does not supply. Drives whether we ask the client a question.",
    )
    confidence: float = Field(
        ge=0.0, le=1.0, description="0.0 to 1.0. How confident you are this reading is faithful."
    )


class ClarifyingQuestion(Strict):
    """One question to send back to a NON-TECHNICAL person."""

    question: str = Field(
        description="Plain language. No jargon, no stack traces, no file names. "
        "A question the reporter can answer from memory in one line."
    )
    why_it_matters: str = Field(description="One line, for the developer's audit trail.")
    unblocks_field: str = Field(description="Which ReportFacts field this would fill.")


# ---------------------------------------------------------------------------
# 3. Localisation — where in the code
# ---------------------------------------------------------------------------


class Hypothesis(Strict):
    """One guess at where the fault lives. Small on purpose: no file contents."""

    file_path: str = Field(description="Repo-relative path, e.g. 'shopcart/pricing.py'.")
    symbol: Optional[str] = Field(None, description="Function or class name, if identified.")
    rationale: str = Field(
        description="Two sentences maximum linking the client's words to this code."
    )
    confidence: float = Field(ge=0.0, le=1.0)


# ---------------------------------------------------------------------------
# 4. Execution — the only thing that touches reality
# ---------------------------------------------------------------------------


class ExecutionResult(Strict):
    """Result of running a command in the sandbox. Tails only, never full dumps."""

    exit_code: int
    stdout_tail: str = Field(max_length=SANDBOX_MAX_OUTPUT_CHARS)
    stderr_tail: str = Field(max_length=SANDBOX_MAX_OUTPUT_CHARS)
    duration_s: float
    timed_out: bool = False
    passed: int = Field(0, description="Count of passing tests parsed from the report.")
    failed: int = Field(0, description="Count of failing tests parsed from the report.")
    errors: int = 0

    @property
    def green(self) -> bool:
        return self.exit_code == 0 and not self.timed_out and self.failed == 0 and self.errors == 0


class TestArtifact(Strict):
    """A generated test file."""

    path: str = Field(description="Repo-relative, must start with 'tests/' and end '.py'.")
    source: str = Field(description="Complete file contents. Must be importable on its own.")


class ReproAttempt(Strict):
    """
    One try at writing a test that FAILS because of the reported bug.

    `reproduced` is the gate for the whole product. It is True only when the
    test ran, did not error on import, and failed with an assertion that
    matches what the client described.
    """

    attempt_no: int = Field(ge=1, le=MAX_REPRO_ATTEMPTS)
    test: TestArtifact
    result: ExecutionResult
    reproduced: bool
    reasoning: str = Field(description="Why this counts, or does not count, as a reproduction.")


class Patch(Strict):
    """A minimal proposed change. Unified diff so a human can read it in the PR."""

    unified_diff: str = Field(description="Valid `git apply`-able unified diff.")
    files_touched: list[str]
    rationale: str = Field(description="Two sentences: root cause, and why this fixes it.")


class FixAttempt(Strict):
    """One try at a patch, with BOTH verifications recorded separately."""

    attempt_no: int = Field(ge=1, le=MAX_FIX_ATTEMPTS)
    patch: Patch
    target_test: ExecutionResult = Field(description="The repro test, re-run after the patch.")
    suite: ExecutionResult = Field(description="The project's full pre-existing test suite.")
    accepted: bool = Field(
        description="True only if target_test.green AND suite.green. No other route to True."
    )
    reasoning: str


# ---------------------------------------------------------------------------
# 5. What comes out
# ---------------------------------------------------------------------------


class Verdict(str, Enum):
    NEEDS_CLARIFICATION = "needs_clarification"
    NOT_REPRODUCED = "not_reproduced"
    REPRODUCED_NOT_FIXED = "reproduced_not_fixed"
    REPRODUCED_AND_FIXED = "reproduced_and_fixed"
    ABORTED_BUDGET = "aborted_budget"


TERMINAL_VERDICTS = frozenset(Verdict)


class TokenUsage(Strict):
    input_tokens: int = 0
    output_tokens: int = 0
    calls: int = 0
    usd: float = 0.0

    def merge(self, other: "TokenUsage") -> "TokenUsage":
        return TokenUsage(
            input_tokens=self.input_tokens + other.input_tokens,
            output_tokens=self.output_tokens + other.output_tokens,
            calls=self.calls + other.calls,
            usd=round(self.usd + other.usd, 6),
        )


class Handover(Strict):
    """
    The two audiences. Session 3: "leaves people genuinely better off" — both
    the developer who has to trust the change AND the client who reported it.
    """

    dev_summary: str = Field(description="Markdown for the pull request body.")
    client_reply: str = Field(
        description="Plain language, no jargon, addressed to the reporter. "
        "Confirms what we reproduced in their own words. Never promises a fix "
        "that the verdict does not support."
    )


class RunRecord(Strict):
    """The complete audit trail of one run. Persisted; replayable; demo-able."""

    run_id: str
    contract_version: str = CONTRACT_VERSION
    report: ClientReport
    facts: Optional[ReportFacts] = None
    questions: list[ClarifyingQuestion] = Field(default_factory=list)
    hypotheses: list[Hypothesis] = Field(default_factory=list)
    repro_attempts: list[ReproAttempt] = Field(default_factory=list)
    fix_attempts: list[FixAttempt] = Field(default_factory=list)
    verdict: Verdict = Verdict.NOT_REPRODUCED
    handover: Optional[Handover] = None
    usage: TokenUsage = Field(default_factory=TokenUsage)
    wall_clock_s: float = 0.0

    # --- invariants ---------------------------------------------------------
    def check_invariants(self) -> list[str]:
        """
        The safety property of the whole system, expressed as code.

        Returns a list of violated invariants; empty list means sound.
        `tests/test_invariants.py` asserts this is empty for every eval run,
        and the API refuses to emit a patch when it is not.
        """
        bad: list[str] = []
        reproduced = any(a.reproduced for a in self.repro_attempts)
        accepted = [f for f in self.fix_attempts if f.accepted]

        if self.verdict == Verdict.REPRODUCED_AND_FIXED:
            if not reproduced:
                bad.append("verdict=fixed but no repro attempt was marked reproduced")
            if not accepted:
                bad.append("verdict=fixed but no fix attempt was accepted")
        if accepted and not reproduced:
            bad.append("a patch was accepted without a reproduction: forbidden")
        for f in self.fix_attempts:
            if f.accepted and not (f.target_test.green and f.suite.green):
                bad.append(f"fix {f.attempt_no} accepted without red->green + green suite")
        if len(self.repro_attempts) > MAX_REPRO_ATTEMPTS:
            bad.append("repro loop exceeded its cap")
        if len(self.fix_attempts) > MAX_FIX_ATTEMPTS:
            bad.append("fix loop exceeded its cap")
        if self.usage.usd > MAX_RUN_USD:
            bad.append(f"run cost {self.usage.usd} exceeded MAX_RUN_USD")
        return bad


# ---------------------------------------------------------------------------
# 6. The LLM seam
# ---------------------------------------------------------------------------


class LLMResponse(Strict):
    text: str
    stop_reason: str = Field(
        description="'end_turn' means the model finished. 'max_tokens' means the "
        "ceiling cut it off and the text is INCOMPLETE — never parse it as JSON."
    )
    usage: TokenUsage


StreamEventType = Literal[
    "run_started",
    "node_started",
    "node_finished",
    "clarify",
    "hypothesis",
    "repro_attempt",
    "fix_attempt",
    "verdict",
    "error",
]


class StreamEvent(Strict):
    """What the web UI receives over SSE. One event per meaningful step."""

    type: StreamEventType
    run_id: str
    at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))
    label: str = Field(description="One short human-readable line for the timeline.")
    payload: dict = Field(default_factory=dict)
