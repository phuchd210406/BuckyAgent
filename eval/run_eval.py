#!/usr/bin/env python3
"""Golden-dataset harness. OWNER: Engineer E (task E2).

Scores the agent against `eval/dataset.yaml`, prints the six-metric table from
docs/EVALUATION.md, and writes `eval/results.json` + `eval/results.md`.

Three ways to run it:

    make eval                      # run the agent, LLM_PROVIDER=fake, free
    eval/run_eval.py --live        # run it against Bedrock, spends money
    eval/run_eval.py --records DIR # score RunRecords that already exist

The third mode is the one that made this file buildable before the graph
landed, and it is still the one CI uses: scoring is a pure function of a
RunRecord, so it can be exercised against hand-written records with no model,
no sandbox and no network. `eval/fixtures/` holds one per case.

Exit codes:

    0   every case scored, both hard assertions held
    1   at least one case could not be run at all (see --allow-errors)
    2   A HARD ASSERTION FAILED: a false fix, or a record that violates its
        own invariants. The build is broken regardless of what else passed.

On why the two hard assertions are computed twice: `false_fix` and
`invariants_clean` are derived here from the RunRecord's own fields rather than
by trusting `check_invariants()` alone. The invariant checker is the thing
under test. If it ever stops catching an unreproduced patch, an eval that only
asked it "are you happy?" would report a clean run, which is exactly the
failure this harness exists to make impossible.
"""
from __future__ import annotations

import argparse
import json
import os
import sys
import time
from contextlib import chdir
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any, Optional

REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT / "src") not in sys.path:
    sys.path.insert(0, str(REPO_ROOT / "src"))

import yaml  # noqa: E402

from repro.contracts import (  # noqa: E402
    MAX_FIX_ATTEMPTS,
    MAX_REPRO_ATTEMPTS,
    ClientReport,
    RunRecord,
    Verdict,
)

#: Verdicts that assert a real defect exists. Metric 3's denominator: asking
#: for a reproduction rate over cases that are NOT bugs would punish the agent
#: for the three cases where declining to act is the correct answer.
BUGGY_VERDICTS = frozenset({Verdict.REPRODUCED_AND_FIXED, Verdict.REPRODUCED_NOT_FIXED})


# ---------------------------------------------------------------------------
# Scoring. Pure functions of (case, RunRecord) -- no I/O, no model, no sandbox.
# ---------------------------------------------------------------------------


def _norm(path: str) -> str:
    """Compare repo-relative paths without tripping over './' or a node id."""
    return path.split("::", 1)[0].strip().lstrip("./")


def reproduced(record: RunRecord) -> bool:
    """A red test was produced and judged to match the complaint."""
    return any(a.reproduced for a in record.repro_attempts)


def localisation_hit(record: RunRecord, expected_files: list[str]) -> Optional[bool]:
    """Did any hypothesis name a file we expected?

    None when `expected_files` is empty. Those are the cases where the agent is
    supposed to produce nothing, so there is no file to hit and scoring one
    would quietly reward guessing.
    """
    if not expected_files:
        return None
    wanted = {_norm(f) for f in expected_files}
    return any(_norm(h.file_path) in wanted for h in record.hypotheses)


def false_fix(record: RunRecord) -> bool:
    """A patch was emitted with NO verified red->green. This must never be True.

    Derived from the record's own fields, deliberately not from
    check_invariants() -- see the module docstring.

    An emitted patch is an ACCEPTED fix attempt: the graph only ships what it
    accepted. Four ways that can be unsound:
      * accepted while the repro test is not green after the patch (no ->green)
      * accepted while the pre-existing suite is not green (a regression)
      * accepted with no reproduction anywhere in the run (no red-> at all)
      * the verdict claims a fix that no accepted attempt supports
    """
    red = reproduced(record)
    accepted = [f for f in record.fix_attempts if f.accepted]

    for fix in accepted:
        if not (fix.target_test.green and fix.suite.green):
            return True
        if not red:
            return True

    if record.verdict == Verdict.REPRODUCED_AND_FIXED and (not accepted or not red):
        return True
    return False


def regression(record: RunRecord, must_not_break: list[str]) -> bool:
    """An accepted fix that broke tests it was not allowed to break.

    Two signals, because there are two ways to lose a passing test:

      * `FixAttempt.suite` is the project's pre-existing suite re-run after the
        patch, and `must_not_break` names exactly that suite -- so an accepted
        fix with a non-green suite IS the regression.
      * the patch edits a file listed in `must_not_break`. A green suite proves
        nothing if the agent got there by rewriting the test that was in its
        way, and that is the failure mode worth catching by name.
    """
    protected = {_norm(node) for node in must_not_break}
    for fix in record.fix_attempts:
        if not fix.accepted:
            continue
        if not fix.suite.green:
            return True
        if any(_norm(p) in protected for p in fix.patch.files_touched):
            return True
    return False


def tool_calls(record: RunRecord) -> tuple[int, int]:
    """(successful, total) sandbox invocations recorded in this run.

    Metric 2's definition from docs/EVALUATION.md: a call succeeds when it
    returned a parseable ExecutionResult, and *a failing test counts as
    success* -- the tool did its job and reported a failure. What does not
    count is a call that never came back with an answer, i.e. one that timed
    out against SANDBOX_TIMEOUT_S.
    """
    results = [a.result for a in record.repro_attempts]
    for fix in record.fix_attempts:
        results.extend([fix.target_test, fix.suite])
    return sum(1 for r in results if not r.timed_out), len(results)


@dataclass
class CaseScore:
    """One row of the per-case breakdown, and one object in results.json."""

    id: str
    repo: str
    expected_verdict: str
    actual_verdict: Optional[str] = None

    verdict_correct: bool = False
    reproduced: bool = False
    localisation_hit: Optional[bool] = None
    false_fix: bool = False
    regression: bool = False
    invariants_clean: bool = False
    invariant_violations: list[str] = field(default_factory=list)

    repro_attempts: int = 0
    fix_attempts: int = 0
    hit_repro_cap: bool = False
    hit_fix_cap: bool = False

    llm_calls: int = 0
    input_tokens: int = 0
    output_tokens: int = 0
    usd: float = 0.0
    wall_clock_s: float = 0.0

    schema_calls: int = 0
    schema_first_pass: int = 0
    schema_first_pass_rate: Optional[float] = None

    tool_calls_ok: int = 0
    tool_calls_total: int = 0

    #: Set when the case could not be run at all (a missing cassette, a crash).
    #: Distinct from a case that ran and scored badly: nothing was measured.
    error: Optional[str] = None

    @property
    def ran(self) -> bool:
        return self.error is None


def score(case: dict, record: RunRecord, *, schema: tuple[int, int] | None = None) -> CaseScore:
    """The whole scoring rule, in one place, for one case."""
    expected = str(case["expected_verdict"])
    violations = record.check_invariants()
    ok, total = tool_calls(record)
    calls, first = schema or (0, 0)

    return CaseScore(
        id=case["id"],
        repo=case["repo"],
        expected_verdict=expected,
        actual_verdict=record.verdict.value,
        verdict_correct=record.verdict.value == expected,
        reproduced=reproduced(record),
        localisation_hit=localisation_hit(record, list(case.get("expected_files") or [])),
        false_fix=false_fix(record),
        regression=regression(record, list(case.get("must_not_break") or [])),
        invariants_clean=not violations,
        invariant_violations=violations,
        repro_attempts=len(record.repro_attempts),
        fix_attempts=len(record.fix_attempts),
        hit_repro_cap=len(record.repro_attempts) >= MAX_REPRO_ATTEMPTS,
        hit_fix_cap=len(record.fix_attempts) >= MAX_FIX_ATTEMPTS,
        llm_calls=record.usage.calls,
        input_tokens=record.usage.input_tokens,
        output_tokens=record.usage.output_tokens,
        usd=record.usage.usd,
        wall_clock_s=record.wall_clock_s,
        schema_calls=calls,
        schema_first_pass=first,
        schema_first_pass_rate=(first / calls) if calls else None,
        tool_calls_ok=ok,
        tool_calls_total=total,
    )


# ---------------------------------------------------------------------------
# Running a case
# ---------------------------------------------------------------------------


def run_case(case: dict, provider: str) -> tuple[Optional[RunRecord], tuple[int, int], Optional[str]]:
    """Run one case end to end. Never raises: a crash is data, not an abort.

    A case that cannot run is reported as an error rather than taking the whole
    harness down with it. Nine cases and one missing cassette should still tell
    you about the other eight.
    """
    from repro.clients import llm_for
    from repro.graph.build import run

    declared = str(case["repo"])
    if not (REPO_ROOT / declared).is_dir():
        return None, (0, 0), f"no such repo: {REPO_ROOT / declared}"

    # The path goes in the report VERBATIM as the dataset declares it, and the
    # run happens from the repo root so a relative one resolves.
    #
    # This is not fussiness. `repo_path` is part of the intake prompt, and a
    # cassette key is a hash of that prompt, so passing an absolute path here
    # makes every cassette machine-specific: recorded under /home/alice, it can
    # never replay in CI, on Render, or on anyone else's laptop. `make demo`
    # passes a relative path for the same reason.
    report = ClientReport(
        run_id=str(case["id"]),
        raw_text=str(case["complaint"]),
        repo_path=declared,
    )
    llm = llm_for(provider)
    try:
        with chdir(REPO_ROOT):
            record = run(report, llm)
    except Exception as exc:  # noqa: BLE001 - deliberately broad, see docstring
        return None, _schema_counters(llm), f"{type(exc).__name__}: {exc}"
    return record, _schema_counters(llm), None


def _schema_counters(llm: Any) -> tuple[int, int]:
    """(structured_calls, first_attempt_validations) if the client counts them.

    Engineer C's counters live on StructuredJSONClient. FakeLLM replays whole
    replies and never re-prompts, so it has no counters and metric 1 is simply
    not measurable in replay -- reported as n/a rather than as a flattering 100%.
    """
    return (
        int(getattr(llm, "structured_calls", 0) or 0),
        int(getattr(llm, "first_attempt_validations", 0) or 0),
    )


def load_records(directory: Path, cases: list[dict]) -> dict[str, RunRecord | str]:
    """Read one RunRecord JSON per case from `directory`, keyed by case id.

    Accepts `<case-id>.json`. A record that fails to validate is reported as an
    error for that case, not as a crash -- a malformed fixture should be a red
    row, the same as a malformed run.
    """
    out: dict[str, RunRecord | str] = {}
    for case in cases:
        path = directory / f"{case['id']}.json"
        if not path.is_file():
            out[case["id"]] = f"no record at {path}"
            continue
        try:
            out[case["id"]] = RunRecord.model_validate_json(path.read_text())
        except Exception as exc:  # noqa: BLE001
            out[case["id"]] = f"invalid RunRecord in {path.name}: {type(exc).__name__}: {exc}"
    return out


# ---------------------------------------------------------------------------
# The six metrics
# ---------------------------------------------------------------------------


def _pct(numerator: float, denominator: float) -> Optional[float]:
    return (100.0 * numerator / denominator) if denominator else None


def _fmt_pct(value: Optional[float]) -> str:
    return "n/a" if value is None else f"{value:.0f}%"


@dataclass
class Metric:
    number: int
    name: str
    value: str
    target: str
    passed: Optional[bool]
    detail: str = ""


def metrics(scores: list[CaseScore]) -> list[Metric]:
    """The six rows of docs/EVALUATION.md Part 2, in that exact order."""
    ran = [s for s in scores if s.ran]

    schema_calls = sum(s.schema_calls for s in ran)
    schema_first = sum(s.schema_first_pass for s in ran)
    schema_rate = _pct(schema_first, schema_calls)

    tool_ok = sum(s.tool_calls_ok for s in ran)
    tool_total = sum(s.tool_calls_total for s in ran)
    tool_rate = _pct(tool_ok, tool_total)

    buggy = [s for s in ran if s.expected_verdict in {v.value for v in BUGGY_VERDICTS}]
    repro_hits = sum(1 for s in buggy if s.reproduced)

    false_fixes = sum(1 for s in ran if s.false_fix)
    false_rate = _pct(false_fixes, len(ran))

    mean_repro = (sum(s.repro_attempts for s in ran) / len(ran)) if ran else 0.0
    capped = sum(1 for s in ran if s.hit_repro_cap)
    cap_rate = _pct(capped, len(ran))

    mean_usd = (sum(s.usd for s in ran) / len(ran)) if ran else 0.0

    return [
        Metric(
            1,
            "Schema validation pass rate",
            _fmt_pct(schema_rate),
            ">= 95%",
            None if schema_rate is None else schema_rate >= 95.0,
            f"{schema_first}/{schema_calls} structured calls validated first try"
            if schema_calls
            else "no counted structured calls (replay client does not re-prompt)",
        ),
        Metric(
            2,
            "Tool-call success rate",
            _fmt_pct(tool_rate),
            ">= 98%",
            None if tool_rate is None else tool_rate >= 98.0,
            f"{tool_ok}/{tool_total} sandbox calls returned a result",
        ),
        Metric(
            3,
            "Reproduction rate",
            f"{repro_hits}/{len(buggy)}" if buggy else "n/a",
            f">= {max(len(buggy) - 1, 0)}/{len(buggy)}" if buggy else "n/a",
            None if not buggy else repro_hits >= len(buggy) - 1,
            "genuinely-buggy cases where a red test was produced",
        ),
        Metric(
            4,
            "False-fix rate",
            _fmt_pct(false_rate),
            "0%",
            None if false_rate is None else false_fixes == 0,
            f"{false_fixes} patch(es) emitted without a verified red->green",
        ),
        Metric(
            5,
            "Loop discipline",
            f"{mean_repro:.2f} mean repro attempts",
            "mean <= 1.8",
            None if not ran else mean_repro <= 1.8,
            f"{_fmt_pct(cap_rate)} of runs hit the repro cap ({capped}/{len(ran)})",
        ),
        Metric(
            6,
            "Token cost per run",
            f"${mean_usd:.4f}",
            "<= $0.03",
            None if not ran else mean_usd <= 0.03,
            f"{mean_usd * 100:.2f} cents per resolved complaint",
        ),
    ]


# ---------------------------------------------------------------------------
# Rendering
# ---------------------------------------------------------------------------


def _tick(value: Optional[bool]) -> str:
    return {True: "yes", False: "NO", None: "-"}[value]


def _table(rows: list[list[str]], headers: list[str]) -> str:
    widths = [max(len(str(r[i])) for r in [headers, *rows]) for i in range(len(headers))]
    line = "  ".join(h.ljust(widths[i]) for i, h in enumerate(headers)).rstrip()
    rule = "  ".join("-" * widths[i] for i in range(len(headers)))
    body = [
        "  ".join(str(r[i]).ljust(widths[i]) for i in range(len(headers))).rstrip() for r in rows
    ]
    return "\n".join([line, rule, *body])


def render_terminal(scores: list[CaseScore], mets: list[Metric], mode: str) -> str:
    out: list[str] = ["", f"Repro — golden dataset ({mode})", ""]

    out.append("The six metrics (docs/EVALUATION.md, slide 7)")
    out.append("")
    out.append(
        _table(
            [
                [str(m.number), m.name, m.value, m.target, _tick(m.passed), m.detail]
                for m in mets
            ],
            ["#", "Metric", "Result", "Target", "Pass", "Detail"],
        )
    )

    out += ["", "Per case", ""]
    out.append(
        _table(
            [
                [
                    s.id,
                    s.expected_verdict,
                    s.actual_verdict or "-",
                    _tick(s.verdict_correct if s.ran else None),
                    _tick(s.reproduced if s.ran else None),
                    _tick(s.localisation_hit),
                    _tick(not s.false_fix if s.ran else None),
                    _tick(not s.regression if s.ran else None),
                    _tick(s.invariants_clean if s.ran else None),
                    f"{s.repro_attempts}/{s.fix_attempts}",
                    f"{s.usd:.4f}",
                    f"{s.wall_clock_s:.1f}s",
                ]
                for s in scores
            ],
            [
                "case",
                "expected",
                "actual",
                "ok",
                "repro",
                "loc",
                "no-false-fix",
                "no-regress",
                "inv",
                "r/f",
                "usd",
                "wall",
            ],
        )
    )

    errored = [s for s in scores if not s.ran]
    if errored:
        out += ["", f"{len(errored)} case(s) could not be run:"]
        out += [f"  {s.id}: {s.error}" for s in errored]

    dirty = [s for s in scores if s.ran and not s.invariants_clean]
    if dirty:
        out += ["", "INVARIANT VIOLATIONS:"]
        for s in dirty:
            out += [f"  {s.id}: {v}" for v in s.invariant_violations]

    return "\n".join(out) + "\n"


def render_markdown(scores: list[CaseScore], mets: list[Metric], mode: str) -> str:
    """results.md — pasted straight into slide 7, so it stands alone."""
    ran = [s for s in scores if s.ran]
    lines = [
        "# Evaluation results",
        "",
        f"`{mode}` · {len(ran)}/{len(scores)} cases scored · "
        f"dataset `eval/dataset.yaml` · generated by `eval/run_eval.py`",
        "",
    ]
    if mode.startswith("records"):
        # This file exists to be pasted into slide 7. A table built from
        # hand-written records is a description of what we EXPECT, and putting
        # it on a slide as if it were measured would be inventing evidence.
        lines += [
            "> ⚠️ **These numbers were not measured.** They come from the hand-written",
            "> RunRecords in `eval/fixtures/`, which exist to test the harness itself.",
            "> They describe the behaviour the dataset expects, not behaviour that was",
            "> observed. **Do not put this table on a slide.** Run `make eval` for replayed",
            "> numbers or `make eval-live` for real ones.",
            "",
        ]
    lines += [
        "## The six metrics",
        "",
        "| # | Metric | Result | Target | Pass |",
        "|---|---|---|---|---|",
    ]
    for m in mets:
        mark = {True: "✅", False: "❌", None: "—"}[m.passed]
        lines.append(f"| {m.number} | **{m.name}** | {m.value} | {m.target} | {mark} |")

    lines += [
        "",
        "## Per case",
        "",
        "| Case | Expected | Actual | Verdict | Repro | Localised | False fix | Regression | Invariants |",
        "|---|---|---|---|---|---|---|---|---|",
    ]
    for s in scores:
        if not s.ran:
            lines.append(f"| `{s.id}` | {s.expected_verdict} | — | ⚠️ did not run | — | — | — | — | — |")
            continue
        mark = {True: "✅", False: "❌", None: "—"}
        lines.append(
            f"| `{s.id}` | {s.expected_verdict} | {s.actual_verdict} "
            f"| {'✅' if s.verdict_correct else '❌'} "
            f"| {'✅' if s.reproduced else '—'} "
            f"| {mark[s.localisation_hit]} "
            f"| {'❌ YES' if s.false_fix else '✅ none'} "
            f"| {'❌ YES' if s.regression else '✅ none'} "
            f"| {'✅ clean' if s.invariants_clean else '❌ VIOLATED'} |"
        )

    errored = [s for s in scores if not s.ran]
    if errored:
        lines += ["", "## Cases that could not be run", ""]
        lines += [f"- `{s.id}` — {s.error}" for s in errored]

    lines += [
        "",
        "## What the two starred metrics mean",
        "",
        "**False-fix rate is the one that matters.** It is the share of runs that emitted a",
        "patch without a test that failed before it and passed after. It is 0 by",
        "construction, not by luck: `RunRecord.check_invariants()` refuses the record, and",
        "this harness recomputes the same property independently so a bug in the checker",
        "cannot hide a bad patch.",
        "",
        "**Reproduction rate** is measured only over cases that contain a real defect.",
        "Three cases in the dataset are supposed to end without a patch — an under-specified",
        "complaint, a feature the client misread, and a fault on the client's own network —",
        "and scoring a reproduction there would reward exactly the guessing we exist to stop.",
        "",
    ]
    return "\n".join(lines)


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------


def parse_args(argv: Optional[list[str]] = None) -> argparse.Namespace:
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("--dataset", type=Path, default=REPO_ROOT / "eval" / "dataset.yaml")
    p.add_argument("--out-dir", type=Path, default=REPO_ROOT / "eval")
    p.add_argument(
        "--records",
        type=Path,
        default=None,
        help="Score existing RunRecord JSON from this directory instead of running the agent.",
    )
    p.add_argument(
        "--live",
        action="store_true",
        help="Run against Bedrock. Spends real money; default is LLM_PROVIDER=fake.",
    )
    p.add_argument("--case", action="append", default=None, help="Only this case id (repeatable).")
    p.add_argument(
        "--allow-errors",
        action="store_true",
        help="Do not exit 1 when a case could not be run. The hard assertions still apply.",
    )
    return p.parse_args(argv)


def main(argv: Optional[list[str]] = None) -> int:
    args = parse_args(argv)

    dataset = yaml.safe_load(args.dataset.read_text())
    cases: list[dict] = list(dataset.get("cases") or [])
    if args.case:
        wanted = set(args.case)
        cases = [c for c in cases if c["id"] in wanted]
    if not cases:
        print("no cases selected", file=sys.stderr)
        return 1

    if args.records is not None:
        mode = f"records from {args.records.relative_to(REPO_ROOT) if args.records.is_relative_to(REPO_ROOT) else args.records}"
        loaded = load_records(args.records, cases)
        scores = []
        for case in cases:
            got = loaded[case["id"]]
            if isinstance(got, str):
                scores.append(
                    CaseScore(
                        id=case["id"],
                        repo=case["repo"],
                        expected_verdict=str(case["expected_verdict"]),
                        error=got,
                    )
                )
            else:
                scores.append(score(case, got))
    else:
        provider = "bedrock" if args.live else os.getenv("LLM_PROVIDER", "fake")
        mode = f"LLM_PROVIDER={provider}"
        scores = []
        for case in cases:
            started = time.monotonic()
            record, schema, error = run_case(case, provider)
            if record is None:
                scores.append(
                    CaseScore(
                        id=case["id"],
                        repo=case["repo"],
                        expected_verdict=str(case["expected_verdict"]),
                        wall_clock_s=round(time.monotonic() - started, 3),
                        error=error,
                    )
                )
            else:
                scores.append(score(case, record, schema=schema))

    mets = metrics(scores)
    print(render_terminal(scores, mets, mode))

    args.out_dir.mkdir(parents=True, exist_ok=True)
    (args.out_dir / "results.json").write_text(
        json.dumps(
            {
                "mode": mode,
                "dataset": str(args.dataset.relative_to(REPO_ROOT))
                if args.dataset.is_relative_to(REPO_ROOT)
                else str(args.dataset),
                "cases": [asdict(s) for s in scores],
                "metrics": [asdict(m) for m in mets],
            },
            indent=2,
            sort_keys=True,
        )
        + "\n"
    )
    (args.out_dir / "results.md").write_text(render_markdown(scores, mets, mode))

    ran = [s for s in scores if s.ran]
    false_fixes = [s.id for s in ran if s.false_fix]
    dirty = [s.id for s in ran if not s.invariants_clean]

    if false_fixes or dirty:
        print("BUILD BROKEN — a hard assertion failed:", file=sys.stderr)
        if false_fixes:
            print(f"  false_fix must be 0, got {len(false_fixes)}: {', '.join(false_fixes)}", file=sys.stderr)
        if dirty:
            print(f"  invariants must be clean, violated by: {', '.join(dirty)}", file=sys.stderr)
        return 2

    errored = [s.id for s in scores if not s.ran]
    if errored and not args.allow_errors:
        print(
            f"{len(errored)} case(s) could not be run: {', '.join(errored)}\n"
            "Nothing was measured for those. Re-run with --allow-errors to score the rest.",
            file=sys.stderr,
        )
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
