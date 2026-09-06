"""StreamEvents, built in ONE place. OWNER: Engineer A.

Both the real graph and the API's mock replay emit through here, so the web UI
cannot be built against a payload shape the real run does not produce. If a
field is not in this file, the browser never sees it.

`StreamEvent.payload` is the UI's channel, not the model's. ARCHITECTURE.md's
"keep payloads small" rule is about what goes into a PROMPT and is re-read on
every turn of a loop; a browser reads an event once. So the test source and the
diff go over the wire here, and never into a prompt.
"""
from __future__ import annotations

from repro.contracts import (
    ClarifyingQuestion,
    ClientReport,
    FixAttempt,
    Hypothesis,
    ReportFacts,
    ReproAttempt,
    RunRecord,
    StreamEvent,
)

#: Human labels for the node names the graph uses.
NODE_LABELS = {
    "intake": "Reading the complaint",
    "clarify": "Writing a question for the client",
    "localise": "Searching the code",
    "repro": "Writing a failing test",
    "fix": "Proposing a patch",
    "report": "Writing the handover",
}


def run_started(report: ClientReport) -> StreamEvent:
    return StreamEvent(
        type="run_started",
        run_id=report.run_id,
        label="Run started",
        payload={"repo_path": report.repo_path},
    )


def node_started(run_id: str, node: str, attempt_no: int | None = None, of: int | None = None) -> StreamEvent:
    payload: dict = {"node": node}
    if attempt_no is not None:
        payload["attempt_no"] = attempt_no
        payload["of"] = of
    return StreamEvent(
        type="node_started",
        run_id=run_id,
        label=NODE_LABELS.get(node, node),
        payload=payload,
    )


def intake_finished(run_id: str, facts: ReportFacts) -> StreamEvent:
    return StreamEvent(
        type="node_finished",
        run_id=run_id,
        label="Report understood",
        payload={"node": "intake", "facts": facts.model_dump(mode="json")},
    )


def hypothesis_found(run_id: str, hypothesis: Hypothesis) -> StreamEvent:
    where = hypothesis.file_path + (f"::{hypothesis.symbol}" if hypothesis.symbol else "")
    return StreamEvent(
        type="hypothesis",
        run_id=run_id,
        label=where,
        payload=hypothesis.model_dump(mode="json"),
    )


def localise_finished(run_id: str, candidates: int) -> StreamEvent:
    return StreamEvent(
        type="node_finished",
        run_id=run_id,
        label=f"{candidates} candidate{'' if candidates == 1 else 's'} ranked",
        payload={"node": "localise", "candidates": candidates},
    )


def clarify_asked(run_id: str, question: ClarifyingQuestion) -> StreamEvent:
    return StreamEvent(
        type="clarify",
        run_id=run_id,
        label="A question for the client",
        payload=question.model_dump(mode="json"),
    )


def repro_attempted(run_id: str, attempt: ReproAttempt) -> StreamEvent:
    result = attempt.result
    return StreamEvent(
        type="repro_attempt",
        run_id=run_id,
        label=(
            f"Attempt {attempt.attempt_no} "
            + ("went red - reproduced" if attempt.reproduced else "did not reproduce")
        ),
        payload={
            "attempt_no": attempt.attempt_no,
            "reproduced": attempt.reproduced,
            "test_path": attempt.test.path,
            "test_source": attempt.test.source,
            "exit_code": result.exit_code,
            "passed": result.passed,
            "failed": result.failed,
            "errors": result.errors,
            "duration_s": result.duration_s,
            "stdout_tail": result.stdout_tail,
            "reasoning": attempt.reasoning,
        },
    )


def fix_attempted(run_id: str, attempt: FixAttempt) -> StreamEvent:
    return StreamEvent(
        type="fix_attempt",
        run_id=run_id,
        label="Patch verified twice" if attempt.accepted else "Patch rejected",
        payload={
            "attempt_no": attempt.attempt_no,
            "accepted": attempt.accepted,
            "files_touched": attempt.patch.files_touched,
            "unified_diff": attempt.patch.unified_diff,
            "rationale": attempt.patch.rationale,
            "target_test_green": attempt.target_test.green,
            "target_test_passed": attempt.target_test.passed,
            "suite_green": attempt.suite.green,
            "suite_passed": attempt.suite.passed,
            "reasoning": attempt.reasoning,
        },
    )


def report_finished(run_id: str) -> StreamEvent:
    return StreamEvent(
        type="node_finished",
        run_id=run_id,
        label="Handover written",
        payload={"node": "report"},
    )


def verdict_reached(record: RunRecord, reporter_name: str | None = None) -> StreamEvent:
    return StreamEvent(
        type="verdict",
        run_id=record.run_id,
        label=record.verdict.value.replace("_", " ").capitalize(),
        payload={
            "verdict": record.verdict.value,
            "usd": record.usage.usd,
            "calls": record.usage.calls,
            "wall_clock_s": record.wall_clock_s,
            "handover": record.handover.model_dump(mode="json") if record.handover else None,
            "reporter_name": reporter_name,
        },
    )


def verification_failed(run_id: str, violations: list[str]) -> StreamEvent:
    """The record contradicted itself, so nothing it produced may be trusted."""
    return StreamEvent(
        type="error",
        run_id=run_id,
        label="Verification failed — the patch was withheld",
        payload={
            "error": "verification failed",
            "terminal": False,  # the verdict still follows; the patch is just gone
            "violations": violations,
            "consequence": (
                "This run's record violated its own invariants, so every patch was "
                "stripped before it left the API. Nothing here may be applied."
            ),
        },
    )


def run_failed(run_id: str, exc: BaseException) -> StreamEvent:
    return StreamEvent(
        type="error",
        run_id=run_id,
        label=f"The run stopped: {type(exc).__name__}",
        payload={"error": f"{type(exc).__name__}: {exc}", "terminal": True},
    )
