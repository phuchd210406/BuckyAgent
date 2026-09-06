"""Termination tests. OWNER: Engineer A.

The expensive failure mode in this project is not a wrong answer, it is a loop
that never stops. So these tests do not script a model that behaves; they
script one that behaves as badly as it possibly can, and assert on the EXACT
number of times each node ran. A test that only asserted "it finished" would
still pass with the cap set to 300.
"""
from __future__ import annotations

from repro.contracts import (
    MAX_FIX_ATTEMPTS,
    MAX_REPRO_ATTEMPTS,
    MAX_TOTAL_LLM_CALLS,
    ClientReport,
    ExecutionResult,
    Handover,
    Hypothesis,
    LLMResponse,
    Patch,
    ReportFacts,
    ReproAttempt,
    TokenUsage,
    Verdict,
)
from repro.contracts import TestArtifact as GeneratedTest
from repro.graph import build as build_mod
from repro.graph.build import budget_exhausted, build_graph
from repro.llm.fake import ScriptedLLM

GREEN = ExecutionResult(exit_code=0, stdout_tail="", stderr_tail="", duration_s=0.1)
RED = ExecutionResult(exit_code=1, stdout_tail="1 failed", stderr_tail="", duration_s=0.1, failed=1)
IMPORT_ERROR = ExecutionResult(
    exit_code=1, stdout_tail="1 error", stderr_tail="ImportError", duration_s=0.1, errors=1
)


# --- doubles -----------------------------------------------------------------


class StubSandbox:
    """Canned results, and a tally of what the graph asked it to do."""

    def __init__(self, test_result=GREEN, suite_result=GREEN, applies=True):
        self.test_result = test_result
        self.suite_result = suite_result
        self.applies = applies
        self.writes: list[str] = []
        self.test_runs = 0
        self.suite_runs = 0
        self.patches: list[Patch] = []

    def write_test(self, test) -> None:
        self.writes.append(test.path)

    def run_test(self, test_path: str) -> ExecutionResult:
        self.test_runs += 1
        return self.test_result

    def apply_patch(self, patch: Patch) -> tuple[bool, str]:
        self.patches.append(patch)
        return self.applies, "" if self.applies else "diff did not apply"

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


def _counter_resetting_repro_node(state, llm, sandbox):
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
    monkeypatch.setattr(build_mod, "_repro_node", _counter_resetting_repro_node)
    sandbox = StubSandbox(test_result=GREEN)
    llm = ScriptedLLM([facts(), hypothesis(), handover()])

    out = invoke(llm, sandbox)

    # The counter the graph kept, not the zero the node kept returning.
    assert out["repro_count"] == MAX_REPRO_ATTEMPTS
    assert len(out["repro_attempts"]) == MAX_REPRO_ATTEMPTS
    assert out["verdict"] == Verdict.NOT_REPRODUCED
    assert len(llm.calls) == 3


def test_a_node_cannot_under_report_its_usage(monkeypatch):
    monkeypatch.setattr(build_mod, "_repro_node", _counter_resetting_repro_node)
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
