"""Termination tests. OWNER: Engineer A.

The expensive failure mode in this project is not a wrong answer, it is a loop
that never stops. So these tests do not script a model that behaves; they
script one that behaves as badly as it possibly can, and assert on the EXACT
number of times each node ran. A test that only asserted "it finished" would
still pass with the cap set to 300.
"""
from __future__ import annotations

import logging
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

import pytest

from repro.agents import fix as fix_module
from repro.agents import intake, localiser, reporter, repro_agent
from repro.agents.fix import fix_node
from repro.contracts import (
    MAX_FIX_ATTEMPTS,
    MAX_REPRO_ATTEMPTS,
    MAX_TOTAL_LLM_CALLS,
    ClientReport,
    ExecutionResult,
    FixAttempt,
    Handover,
    Hypothesis,
    LLMResponse,
    Patch,
    ReportFacts,
    ReproAttempt,
    RunRecord,
    TokenUsage,
    Verdict,
)
from repro.contracts import TestArtifact as GeneratedTest
from repro.graph import build as build_mod
from repro.graph.build import assemble_run_record, budget_exhausted, build_graph, run
from repro.graph.sandbox_seam import WorkspaceSandbox, unsafe_test_path
from repro.llm.base import SchemaValidationError
from repro.llm.fake import ScriptedLLM, cassette_key
from repro.sandbox.workspace import Workspace
from repro.settings import Settings

GREEN = ExecutionResult(exit_code=0, stdout_tail="", stderr_tail="", duration_s=0.1)
RED = ExecutionResult(exit_code=1, stdout_tail="1 failed", stderr_tail="", duration_s=0.1, failed=1)
IMPORT_ERROR = ExecutionResult(
    exit_code=1, stdout_tail="1 error", stderr_tail="ImportError", duration_s=0.1, errors=1
)


# --- doubles -----------------------------------------------------------------


class StubSandbox:
    """Canned results, and a tally of what the graph asked it to do.

    `test_result` may be a list: one result per run_test call, the last one
    repeating. That is how the happy path goes red (repro) then green (fix).
    """

    def __init__(self, test_result=GREEN, suite_result=GREEN, applies=True, candidates=()):
        self.test_results = list(test_result) if isinstance(test_result, list) else [test_result]
        self.suite_result = suite_result
        self.applies = applies
        self.candidates = list(candidates)
        self.writes: list[str] = []
        self.searches: list[str] = []
        self.test_runs = 0
        self.suite_runs = 0
        self.patches: list[Patch] = []
        self.reverts: list[Patch] = []

    def search(self, query: str, k: int = 8):
        self.searches.append(query)
        return []

    def rank_candidates(self, hits, k: int):
        return self.candidates[:k]

    def write_test(self, test) -> None:
        self.writes.append(test.path)

    def run_test(self, test_path: str) -> ExecutionResult:
        self.test_runs += 1
        return self.test_results.pop(0) if len(self.test_results) > 1 else self.test_results[0]

    def apply_patch(self, patch: Patch) -> tuple[bool, str]:
        self.patches.append(patch)
        return self.applies, "" if self.applies else "diff did not apply"

    def revert_patch(self, patch: Patch) -> None:
        self.reverts.append(patch)

    def run_suite(self) -> ExecutionResult:
        self.suite_runs += 1
        return self.suite_result


class GreedyLLM:
    """A ScriptedLLM that reports a ruinous bill for every single call."""

    def __init__(self, inner: ScriptedLLM, calls_per_response: int):
        self.inner = inner
        self.calls_per_response = calls_per_response

    @property
    def calls(self):
        return self.inner.calls

    def _inflate(self, response: LLMResponse) -> LLMResponse:
        return LLMResponse(
            text=response.text,
            stop_reason=response.stop_reason,
            usage=TokenUsage(calls=self.calls_per_response, usd=0.0),
        )

    def complete(self, **kw) -> LLMResponse:
        return self._inflate(self.inner.complete(**kw))

    def structured(self, **kw):
        obj, response = self.inner.structured(**kw)
        return obj, self._inflate(response)


def facts(confidence: float = 1.0) -> ReportFacts:
    return ReportFacts(
        observed_behaviour="checkout charged twice",
        entrypoint_hint="checkout",
        confidence=confidence,
    )


def hypothesis() -> Hypothesis:
    return Hypothesis(file_path="shopcart/pricing.py", rationale="Two sentences. Here.", confidence=0.7)


def generated_test(n: int = 0) -> GeneratedTest:
    return GeneratedTest(path=f"tests/test_repro_{n}.py", source="def test_repro():\n    assert 0\n")


def patch() -> Patch:
    return Patch(unified_diff="--- a\n+++ b\n", files_touched=["shopcart/pricing.py"], rationale="a. b.")


def handover() -> Handover:
    return Handover(dev_summary="stub", client_reply="stub")


def report() -> ClientReport:
    return ClientReport(run_id="r1", raw_text="charged me twice", repo_path="/tmp/shopcart")


def invoke(llm, sandbox, **state):
    return build_graph(llm, sandbox=sandbox).invoke({"report": report(), **state})


# --- 1. the repro loop never reproduces --------------------------------------


def test_repro_loop_stops_at_its_cap_when_nothing_ever_reproduces():
    # The sandbox is green every time: the generated test passes, so the bug
    # was never reproduced, so the model will want another go. Forever.
    sandbox = StubSandbox(test_result=GREEN)
    llm = ScriptedLLM(
        [facts(), hypothesis()]
        + [generated_test(i) for i in range(MAX_REPRO_ATTEMPTS)]
        + [handover()]
    )

    out = invoke(llm, sandbox)

    assert out["repro_count"] == MAX_REPRO_ATTEMPTS
    assert len(out["repro_attempts"]) == MAX_REPRO_ATTEMPTS
    assert sandbox.test_runs == MAX_REPRO_ATTEMPTS
    assert not any(a.reproduced for a in out["repro_attempts"])
    assert out.get("fix_count", 0) == 0
    assert out.get("fix_attempts", []) == []
    assert out["verdict"] == Verdict.NOT_REPRODUCED
    # intake + localise + 3 repro + report, and not one call more.
    assert len(llm.calls) == 2 + MAX_REPRO_ATTEMPTS + 1
    assert llm.replies == []


def test_a_test_that_errors_on_import_is_not_a_reproduction():
    # The most dangerous near-miss: something went red, but for the wrong
    # reason. If this counted, a patch could be accepted for a bug nobody saw.
    sandbox = StubSandbox(test_result=IMPORT_ERROR)
    llm = ScriptedLLM(
        [facts(), hypothesis()]
        + [generated_test(i) for i in range(MAX_REPRO_ATTEMPTS)]
        + [handover()]
    )

    out = invoke(llm, sandbox)

    assert out["repro_count"] == MAX_REPRO_ATTEMPTS
    assert out["verdict"] == Verdict.NOT_REPRODUCED
    assert sandbox.patches == []


# --- 2. the fix loop never gets both greens ----------------------------------


def test_fix_loop_stops_at_its_cap_when_the_patch_never_works():
    # run_test is red throughout: red on the first call means we reproduced,
    # red on every call after the patch means the patch never fixed it.
    sandbox = StubSandbox(test_result=RED, suite_result=GREEN)
    llm = ScriptedLLM(
        [facts(), hypothesis(), generated_test()]
        + [patch() for _ in range(MAX_FIX_ATTEMPTS)]
        + [handover()]
    )

    out = invoke(llm, sandbox)

    assert out["repro_count"] == 1
    assert out["fix_count"] == MAX_FIX_ATTEMPTS
    assert len(out["fix_attempts"]) == MAX_FIX_ATTEMPTS
    assert len(sandbox.patches) == MAX_FIX_ATTEMPTS
    assert not any(f.accepted for f in out["fix_attempts"])
    assert out["verdict"] == Verdict.REPRODUCED_NOT_FIXED
    # intake + localise + 1 repro + 3 fix + report.
    assert len(llm.calls) == 3 + MAX_FIX_ATTEMPTS + 1
    assert llm.replies == []


def test_a_green_target_test_with_a_broken_suite_is_not_accepted():
    # The other half of the safety property: the patch fixes the bug and
    # breaks the project. MAX_FIX_ATTEMPTS tries, then we hand it over unfixed.
    sandbox = StubSandbox(test_result=RED, suite_result=RED)
    llm = ScriptedLLM(
        [facts(), hypothesis(), generated_test()]
        + [patch() for _ in range(MAX_FIX_ATTEMPTS)]
        + [handover()]
    )

    out = invoke(llm, sandbox)

    assert out["fix_count"] == MAX_FIX_ATTEMPTS
    assert not any(f.accepted for f in out["fix_attempts"])
    assert out["verdict"] == Verdict.REPRODUCED_NOT_FIXED


# --- 3. a node that lies about the counters ----------------------------------


def _counter_resetting_repro_node(state, llm, *, sandbox=None):
    """The adversary: every attempt, it reports that it has made none."""
    return {
        "repro_count": 0,
        "repro_attempts": [
            ReproAttempt(
                attempt_no=1,
                test=generated_test(),
                result=GREEN,
                reproduced=False,
                reasoning="rogue node",
            )
        ],
        "usage": TokenUsage(calls=0, usd=0.0),  # and it claims to be free
    }


def test_a_node_cannot_reset_a_counter_to_buy_itself_more_loops(monkeypatch):
    monkeypatch.setattr(build_mod, "repro_agent_node", _counter_resetting_repro_node)
    sandbox = StubSandbox(test_result=GREEN)
    llm = ScriptedLLM([facts(), hypothesis(), handover()])

    out = invoke(llm, sandbox)

    # The counter the graph kept, not the zero the node kept returning.
    assert out["repro_count"] == MAX_REPRO_ATTEMPTS
    assert len(out["repro_attempts"]) == MAX_REPRO_ATTEMPTS
    assert out["verdict"] == Verdict.NOT_REPRODUCED
    assert len(llm.calls) == 3


def test_a_node_cannot_under_report_its_usage(monkeypatch):
    monkeypatch.setattr(build_mod, "repro_agent_node", _counter_resetting_repro_node)
    llm = ScriptedLLM([facts(), hypothesis(), handover()])

    out = invoke(llm, StubSandbox(test_result=GREEN))

    # The rogue node claimed calls=0; the graph charged it for what was spent.
    assert out["usage"].calls == len(llm.calls) == 3


# --- 4. the bill ---------------------------------------------------------------


def test_budget_exhausted_at_the_cap_and_either_side():
    under = {"usage": TokenUsage(calls=MAX_TOTAL_LLM_CALLS - 1)}
    at = {"usage": TokenUsage(calls=MAX_TOTAL_LLM_CALLS)}
    over = {"usage": TokenUsage(calls=MAX_TOTAL_LLM_CALLS + 1)}
    assert not budget_exhausted(under)
    assert budget_exhausted(at)
    assert budget_exhausted(over)
    assert not budget_exhausted({})


def test_the_run_aborts_when_the_model_burns_through_the_call_budget():
    # Two calls is all it takes at this rate, so the abort must land during
    # localise -- before repro, before fix, before anything else is bought.
    per_call = MAX_TOTAL_LLM_CALLS // 2 + 1
    llm = GreedyLLM(ScriptedLLM([facts(), hypothesis(), generated_test()]), per_call)
    sandbox = StubSandbox(test_result=RED)

    out = invoke(llm, sandbox)

    assert out["verdict"] == Verdict.ABORTED_BUDGET
    assert out["usage"].calls == 2 * per_call > MAX_TOTAL_LLM_CALLS
    assert len(llm.calls) == 2  # intake, localise, and then nothing
    assert out.get("repro_attempts", []) == []
    assert out.get("repro_count", 0) == 0
    assert sandbox.test_runs == 0
    assert out.get("handover") is None


def test_a_resumed_run_that_is_already_over_budget_never_calls_the_model():
    llm = ScriptedLLM([])  # any call at all is an "out of replies" AssertionError
    sandbox = StubSandbox()

    out = invoke(llm, sandbox, usage=TokenUsage(calls=MAX_TOTAL_LLM_CALLS))

    assert out["verdict"] == Verdict.ABORTED_BUDGET
    assert llm.calls == []
    assert sandbox.test_runs == 0


# --- the shape itself ----------------------------------------------------------


def test_clarify_ends_the_run_rather_than_looping():
    llm = ScriptedLLM([facts(confidence=0.1), _question()])
    out = invoke(llm, StubSandbox())

    assert out["clarify_rounds"] == 1
    assert out["verdict"] == Verdict.NEEDS_CLARIFICATION
    assert len(out["questions"]) == 1
    assert out.get("hypotheses", []) == []
    assert len(llm.calls) == 2


def _question():
    from repro.contracts import ClarifyingQuestion

    return ClarifyingQuestion(
        question="Did the second charge show up straight away?",
        why_it_matters="Tells us if it is a retry.",
        unblocks_field="steps",
    )


def test_a_patch_that_will_not_apply_is_a_failed_attempt_not_a_crash():
    sandbox = StubSandbox(test_result=RED, applies=False)
    llm = ScriptedLLM(
        [facts(), hypothesis(), generated_test()]
        + [patch() for _ in range(MAX_FIX_ATTEMPTS)]
        + [handover()]
    )

    out = invoke(llm, sandbox)

    assert out["fix_count"] == MAX_FIX_ATTEMPTS
    assert not any(f.accepted for f in out["fix_attempts"])
    assert all(f.target_test.errors == 1 for f in out["fix_attempts"])
    assert sandbox.test_runs == 1  # the repro run only; no patch ever landed
    assert out["verdict"] == Verdict.REPRODUCED_NOT_FIXED


# --- the whole thing, once, working -------------------------------------------


def _record(out) -> RunRecord:
    """Terminal state -> RunRecord. Task A4 replaces this with the real one."""
    return RunRecord(
        run_id=out["report"].run_id,
        report=out["report"],
        facts=out.get("facts"),
        questions=out.get("questions", []),
        hypotheses=out.get("hypotheses", []),
        repro_attempts=out.get("repro_attempts", []),
        fix_attempts=out.get("fix_attempts", []),
        verdict=out["verdict"],
        handover=out.get("handover"),
        usage=out.get("usage") or TokenUsage(),
    )


def test_end_to_end_happy_path_produces_a_sound_record():
    # Red once (that is the reproduction), green when re-run after the patch.
    sandbox = StubSandbox(test_result=[RED, GREEN], suite_result=GREEN)
    llm = ScriptedLLM([facts(), hypothesis(), generated_test(), patch(), handover()])

    out = invoke(llm, sandbox)

    assert [a.reproduced for a in out["repro_attempts"]] == [True]
    assert [f.accepted for f in out["fix_attempts"]] == [True]
    assert out["verdict"] == Verdict.REPRODUCED_AND_FIXED
    assert out["handover"].client_reply
    assert len(out["hypotheses"]) == 1
    assert sandbox.writes == ["tests/test_repro_0.py"]
    assert sandbox.test_runs == 2  # once to reproduce, once to verify the patch
    assert sandbox.suite_runs == 1
    assert sandbox.reverts == []  # nothing to undo: the patch was accepted
    assert out["repro_count"] == 1 and out["fix_count"] == 1
    # intake, localise, repro, fix, report. Five, on the happy path.
    assert len(llm.calls) == 5
    assert llm.replies == []

    record = _record(out)
    assert record.check_invariants() == []
    assert record.usage.calls == 5


# --- run(): the single public entry point -------------------------------------


class FakeWorkspace:
    """Stands in for Engineer B's Workspace, which does not exist until hour 16."""

    def __init__(self, source_repo="/tmp/shopcart", root="/tmp/repro-workspaces/ws"):
        self.source_repo = source_repo
        self.root = Path(root)
        self.closes = 0

    @property
    def path(self) -> Path:
        return self.root

    def close(self) -> None:
        self.closes += 1


def happy_path_llm() -> ScriptedLLM:
    return ScriptedLLM([facts(), hypothesis(), generated_test(), patch(), handover()])


def test_run_returns_the_assembled_record_on_the_happy_path():
    record = run(report(), happy_path_llm(), sandbox=StubSandbox(test_result=[RED, GREEN]))

    assert record.verdict == Verdict.REPRODUCED_AND_FIXED
    assert record.check_invariants() == []
    assert record.run_id == "r1"
    assert record.contract_version == "1.0.0"
    assert len(record.repro_attempts) == 1 and len(record.fix_attempts) == 1
    assert record.fix_attempts[0].patch.unified_diff  # a real patch, on a sound run
    assert record.handover is not None
    assert record.usage.calls == 5
    assert record.wall_clock_s >= 0.0


def _lying_reporter_node(state, llm):
    """Claims a verified fix on a run that never reproduced anything."""
    return {
        "verdict": Verdict.REPRODUCED_AND_FIXED,
        "fix_attempts": [
            FixAttempt(
                attempt_no=1,
                patch=patch(),
                target_test=GREEN,
                suite=GREEN,
                accepted=True,
                reasoning="fabricated",
            )
        ],
        "handover": handover(),
    }


def test_a_run_with_violated_invariants_comes_back_with_no_patch(monkeypatch, caplog):
    monkeypatch.setattr(build_mod, "reporter_node", _lying_reporter_node)
    # Green sandbox: nothing ever reproduced, so an accepted patch is forbidden.
    llm = ScriptedLLM(
        [facts(), hypothesis()] + [generated_test(i) for i in range(MAX_REPRO_ATTEMPTS)]
    )

    with caplog.at_level(logging.ERROR, logger="repro.graph"):
        record = run(report(), llm, sandbox=StubSandbox(test_result=GREEN))

    assert record.verdict == Verdict.ABORTED_BUDGET
    assert not any(f.accepted for f in record.fix_attempts)
    for attempt in record.fix_attempts:
        assert attempt.patch.unified_diff == ""
        assert attempt.patch.files_touched == []
    assert record.handover is None
    # And what comes back is itself sound -- we did not just relabel the problem.
    assert record.check_invariants() == []
    # Every violation named, at ERROR, with the run id.
    logged = caplog.text
    assert "verdict=fixed but no repro attempt was marked reproduced" in logged
    assert "a patch was accepted without a reproduction: forbidden" in logged
    assert logged.count("r1") >= 3
    # The evidence of what went wrong is kept.
    assert len(record.repro_attempts) == MAX_REPRO_ATTEMPTS


def test_the_workspace_is_closed_even_when_the_graph_raises(monkeypatch):
    workspace = FakeWorkspace()
    monkeypatch.setattr(build_mod, "open_workspace", lambda report: workspace)

    with pytest.raises(AssertionError, match="ran out of replies"):
        run(report(), ScriptedLLM([]))  # the first node call blows up

    assert workspace.closes == 1


def test_the_workspace_is_closed_on_the_way_out_of_a_good_run(monkeypatch):
    workspace = FakeWorkspace()
    monkeypatch.setattr(build_mod, "open_workspace", lambda report: workspace)
    # The graph gets a WorkspaceSandbox over the fake, so the first sandbox call
    # is what fails here -- after the workspace has been handed over.
    monkeypatch.setattr(build_mod, "WorkspaceSandbox", lambda ws: StubSandbox(test_result=[RED, GREEN]))

    record = run(report(), happy_path_llm())

    assert record.verdict == Verdict.REPRODUCED_AND_FIXED
    assert workspace.closes == 1


def test_two_runs_never_share_a_workspace(tmp_path):
    # The real Workspace, the real filesystem: same run id twice, on purpose.
    source = tmp_path / "shopcart"
    source.mkdir()
    (source / "pricing.py").write_text("def total():\n    return 0\n")
    settings = Settings(workspace_root=str(tmp_path / "workspaces"))
    client_report = ClientReport(run_id="r1", raw_text="x", repo_path=str(source))

    first = build_mod.open_workspace(client_report, settings)
    second = build_mod.open_workspace(client_report, settings)
    try:
        assert first.path != second.path
        assert (first.path / "pricing.py").exists()
        assert (second.path / "pricing.py").exists()
        # One run finishing must not pull the tree out from under the other.
        first.close()
        assert not first.path.exists()
        assert (second.path / "pricing.py").exists()
    finally:
        first.close()  # idempotent
        second.close()

    assert not second.path.exists()
    assert (source / "pricing.py").exists()  # the original is never touched


def test_two_concurrent_runs_do_not_mix_up_their_records():
    reports = [
        ClientReport(run_id=f"run-{i}", raw_text="charged me twice", repo_path="/tmp/shopcart")
        for i in range(2)
    ]

    def one(client_report):
        return run(
            client_report, happy_path_llm(), sandbox=StubSandbox(test_result=[RED, GREEN])
        )

    with ThreadPoolExecutor(max_workers=2) as pool:
        records = list(pool.map(one, reports))

    assert [r.run_id for r in records] == ["run-0", "run-1"]
    for record in records:
        assert record.verdict == Verdict.REPRODUCED_AND_FIXED
        assert record.check_invariants() == []
        # Each run was billed for its own five calls and nobody else's.
        assert record.usage.calls == 5


def test_assemble_run_record_survives_a_run_that_stopped_early():
    # The clarify path: no hypotheses, no attempts, no handover.
    record = assemble_run_record(
        {"report": report(), "facts": facts(), "verdict": Verdict.NEEDS_CLARIFICATION}
    )

    assert record.verdict == Verdict.NEEDS_CLARIFICATION
    assert record.hypotheses == [] and record.repro_attempts == [] and record.fix_attempts == []
    assert record.handover is None
    assert record.usage.calls == 0
    assert record.check_invariants() == []


# --- the model's test path is untrusted input ---------------------------------


def bad_test(path: str) -> GeneratedTest:
    return GeneratedTest(path=path, source="x = 1\n")


@pytest.mark.parametrize(
    "path",
    [
        "shopcart/pricing.py",  # would overwrite the source it is meant to test
        "../escape.py",
        "/etc/passwd",
        "-x",  # pytest would read this as a flag
        "tests/notes.txt",
        "",
        "tests/",
    ],
)
def test_unsafe_test_paths_are_named_and_refused(path):
    assert unsafe_test_path(path) is not None


@pytest.mark.parametrize("path", ["tests/test_bug.py", "tests/repro/test_bug.py"])
def test_a_test_under_tests_is_allowed(path):
    assert unsafe_test_path(path) is None


class RefusingSandbox(StubSandbox):
    """A sandbox that refuses the write, the way the real one refuses a bad path."""

    def write_test(self, test) -> None:
        raise ValueError(f"refusing to write {test.path!r}: nope")


def test_a_test_written_outside_tests_is_never_written_and_never_crashes():
    sandbox = StubSandbox(test_result=RED)  # red, so a write WOULD look like a repro
    llm = ScriptedLLM(
        [facts(), hypothesis()]
        + [bad_test("shopcart/pricing.py") for _ in range(MAX_REPRO_ATTEMPTS)]
        + [handover()]
    )

    out = invoke(llm, sandbox)

    assert sandbox.writes == []  # the source file was never touched
    assert sandbox.test_runs == 0  # and nothing was run against it
    assert not any(a.reproduced for a in out["repro_attempts"])
    assert "outside 'tests/'" in out["repro_attempts"][0].reasoning
    # The run is still bounded and still ends properly.
    assert out["repro_count"] == MAX_REPRO_ATTEMPTS
    assert out["verdict"] == Verdict.NOT_REPRODUCED


def test_a_sandbox_refusal_is_a_spent_attempt_not_a_dead_run():
    llm = ScriptedLLM(
        [facts(), hypothesis()]
        + [generated_test(i) for i in range(MAX_REPRO_ATTEMPTS)]
        + [handover()]
    )

    out = invoke(llm, RefusingSandbox(test_result=RED))

    assert out["repro_count"] == MAX_REPRO_ATTEMPTS
    assert out["verdict"] == Verdict.NOT_REPRODUCED
    assert all("refused" in a.reasoning.lower() for a in out["repro_attempts"])
    assert all(a.result.errors == 1 for a in out["repro_attempts"])


def test_the_real_sandbox_refuses_to_write_outside_tests(tmp_path):
    source = tmp_path / "shopcart"
    source.mkdir()
    (source / "pricing.py").write_text("def total():\n    return 0\n")
    ws = Workspace(source, root=tmp_path / "ws")
    box = WorkspaceSandbox(ws)
    try:
        with pytest.raises(ValueError, match="outside 'tests/'"):
            box.write_test(bad_test("pricing.py"))
        assert (ws.path / "pricing.py").read_text() == "def total():\n    return 0\n"

        box.write_test(GeneratedTest(path="tests/test_ok.py", source="def test_ok():\n    pass\n"))
        assert (ws.path / "tests" / "test_ok.py").exists()
    finally:
        ws.close()


def test_a_patch_is_never_verified_against_the_whole_suite():
    # No reproduced attempt means no target test. An empty target is "run
    # everything" to pytest, which would accept a patch on the suite alone.
    sandbox = StubSandbox(test_result=GREEN, suite_result=GREEN)

    out = fix_node({"report": report(), "fix_count": 0}, ScriptedLLM([patch()]), sandbox=sandbox)

    attempt = out["fix_attempts"][0]
    assert attempt.accepted is False
    assert sandbox.test_runs == 0
    assert sandbox.suite_runs == 0
    assert "no reproduced test" in attempt.target_test.stderr_tail


# --- living with the real LLM client ------------------------------------------
#
# Engineer C's BedrockLLM raises rather than returning a half-filled object, and
# FakeLLM raises when a cassette is missing. Both land in these nodes.


class BrokenLLM:
    """A model that cannot produce one particular schema, ever."""

    def __init__(self, inner: ScriptedLLM, breaks_on: type):
        self.inner = inner
        self.breaks_on = breaks_on
        self.attempts = 0

    @property
    def calls(self):
        return self.inner.calls

    def complete(self, **kw):
        return self.inner.complete(**kw)

    def structured(self, **kw):
        if kw["schema"] is self.breaks_on:
            self.attempts += 1
            self.inner.calls.append((kw["system"], kw["user"]))
            raise SchemaValidationError(f"{self.breaks_on.__name__} did not validate twice")
        return self.inner.structured(**kw)


def test_a_model_that_cannot_write_a_test_spends_attempts_instead_of_killing_the_run():
    llm = BrokenLLM(ScriptedLLM([facts(), hypothesis(), handover()]), GeneratedTest)
    sandbox = StubSandbox(test_result=RED)

    out = invoke(llm, sandbox)

    assert llm.attempts == MAX_REPRO_ATTEMPTS  # tried, bounded, gave up
    assert out["repro_count"] == MAX_REPRO_ATTEMPTS
    assert not any(a.reproduced for a in out["repro_attempts"])
    assert sandbox.writes == [] and sandbox.test_runs == 0
    assert "SchemaValidationError" in out["repro_attempts"][0].reasoning
    assert out["verdict"] == Verdict.NOT_REPRODUCED


def test_a_model_that_cannot_write_a_patch_spends_attempts_instead_of_killing_the_run():
    llm = BrokenLLM(ScriptedLLM([facts(), hypothesis(), generated_test(), handover()]), Patch)
    sandbox = StubSandbox(test_result=RED)

    out = invoke(llm, sandbox)

    assert llm.attempts == MAX_FIX_ATTEMPTS
    assert out["fix_count"] == MAX_FIX_ATTEMPTS
    assert not any(f.accepted for f in out["fix_attempts"])
    assert sandbox.patches == []  # nothing was ever applied
    assert out["verdict"] == Verdict.REPRODUCED_NOT_FIXED


def test_a_failed_model_call_is_still_billed():
    # Otherwise a node that retries on failure retries for free, and
    # MAX_TOTAL_LLM_CALLS stops bounding anything.
    llm = BrokenLLM(ScriptedLLM([facts(), hypothesis(), handover()]), GeneratedTest)

    out = invoke(llm, StubSandbox(test_result=RED))

    # intake + localise + 3 failed repro calls + report.
    assert out["usage"].calls == 2 + MAX_REPRO_ATTEMPTS + 1


def test_the_code_generating_nodes_raise_the_token_ceiling():
    # A TestArtifact carries a whole test file and a Patch a whole diff; the
    # client's 1024 default truncates them, and a truncated reply is refused
    # rather than parsed, which would cost an attempt for nothing.
    assert repro_agent.MAX_TOKENS >= 4096
    assert fix_module.MAX_TOKENS >= 4096
    assert reporter.MAX_TOKENS > 1024  # a PR body plus a client email


def test_every_node_states_its_own_ceiling():
    spy = SpyLLM(ScriptedLLM([facts(), hypothesis(), generated_test(), patch(), handover()]))

    invoke(spy, StubSandbox(test_result=[RED, GREEN]))

    assert spy.ceilings == [
        intake.MAX_TOKENS,
        localiser.MAX_TOKENS,
        repro_agent.MAX_TOKENS,
        fix_module.MAX_TOKENS,
        reporter.MAX_TOKENS,
    ]


class SpyLLM:
    def __init__(self, inner):
        self.inner = inner
        self.ceilings: list[int] = []

    @property
    def calls(self):
        return self.inner.calls

    def complete(self, **kw):
        self.ceilings.append(kw.get("max_tokens"))
        return self.inner.complete(**kw)

    def structured(self, **kw):
        self.ceilings.append(kw.get("max_tokens"))
        return self.inner.structured(**kw)


def test_two_runs_of_the_same_complaint_hit_the_same_cassette():
    """The offline demo and CI both depend on this.

    FakeLLM finds a reply by hashing the exact prompt. The report carries a
    fresh uuid and a fresh timestamp on every run, so leaving those in the
    prompt made every recorded cassette unfindable the moment it was written --
    a silently broken `LLM_PROVIDER=fake` that falls back to real spend.
    """
    first = ClientReport(run_id="run-a", raw_text="Charged twice.", repo_path="/tmp/shopcart")
    second = ClientReport(run_id="run-b", raw_text="Charged twice.", repo_path="/tmp/shopcart")

    spy_a, spy_b = SpyLLM(ScriptedLLM([facts()])), SpyLLM(ScriptedLLM([facts()]))
    intake.intake_node({"report": first}, spy_a)
    intake.intake_node({"report": second}, spy_b)

    (system_a, user_a), (system_b, user_b) = spy_a.calls[0], spy_b.calls[0]
    assert user_a == user_b, "the prompt still carries something run-specific"
    assert cassette_key(system_a, user_a, "ReportFacts") == cassette_key(
        system_b, user_b, "ReportFacts"
    )
    assert "run-a" not in user_a and "received_at" not in user_a
    assert "Charged twice." in user_a  # the complaint itself is still there


def test_a_recorded_run_replays_as_a_different_run(tmp_path, monkeypatch):
    """The offline path, end to end: RecordingLLM -> cassettes -> FakeLLM.

    This is `make demo` and the wifi-died fallback. It only works if the prompt
    text is identical across runs, so it is the real regression test for
    VOLATILE_REPORT_FIELDS.
    """
    from repro.agents import _common
    from repro.llm.bedrock import RecordingLLM
    from repro.llm.fake import FakeLLM

    def script():
        return [facts(), hypothesis(), generated_test(), patch(), handover()]

    def a_report(run_id: str) -> ClientReport:
        return ClientReport(run_id=run_id, raw_text="Charged twice.", repo_path="/tmp/shopcart")

    recorder = RecordingLLM(ScriptedLLM(script()), cassette_dir=str(tmp_path), enabled=True)
    recorded = run(a_report("live"), recorder, sandbox=StubSandbox(test_result=[RED, GREEN]))
    assert len(recorder.written) == 5

    # A new run id, a new received_at, no script: everything comes off disk.
    replayed = run(a_report("replay"), FakeLLM(tmp_path), sandbox=StubSandbox(test_result=[RED, GREEN]))

    assert replayed.verdict == recorded.verdict == Verdict.REPRODUCED_AND_FIXED
    assert replayed.handover == recorded.handover
    assert replayed.check_invariants() == []

    # And prove the stripping is what makes it work: put the volatile fields
    # back in the prompt and the very first cassette lookup misses.
    monkeypatch.setattr(_common, "VOLATILE_REPORT_FIELDS", frozenset())
    with pytest.raises(SchemaValidationError, match="No cassette"):
        run(a_report("third"), FakeLLM(tmp_path), sandbox=StubSandbox(test_result=[RED, GREEN]))
