"""
The insurance policy, under test. OWNER: Engineer C (task C4).

`LLM_PROVIDER=fake` has to reproduce a recorded run EXACTLY, offline, with the
wifi off. That claim is what lets CI run the whole pipeline on every push and
what saves the demo if the venue network dies, so it gets asserted rather than
assumed.

There are two layers here, and they fail for different reasons:

  * THE MACHINERY (runs now, costs nothing). Drive the real graph with a
    scripted model, record every reply through `RecordingLLM`, then run the SAME
    case again with `FakeLLM` over those cassettes and require the two
    RunRecords to match. This proves record and replay agree about the cassette
    key, that the recorded text validates on the way back in, and that a replayed
    run reaches the same verdict by the same route. If `cassette_key` were ever
    reimplemented instead of imported, this is where it breaks.

  * THE RECORDING (skips until task C4 runs). The same assertion against the
    cassettes committed from real Bedrock and the verdicts recorded with them.

A cassette key is a hash of the system prompt, so editing any SYSTEM_PROMPT after
the recording run changes the key and replay fails with "No cassette". That is
intentional -- see tests/test_prompts.py, which is the other half of that guard.
"""
from __future__ import annotations

import json
from collections import deque
from pathlib import Path

import pytest

from repro.contracts import (
    ClientReport,
    ExecutionResult,
    Handover,
    Hypothesis,
    Patch,
    ReportFacts,
    RunRecord,
    Verdict,
)

# Aliased: pytest tries to COLLECT any imported name starting with "Test",
# and warns that it cannot because the model has an __init__.
from repro.contracts import TestArtifact as TestFile
from repro.graph.build import run
from repro.llm.bedrock import RecordingLLM
from repro.llm.fake import FakeLLM, ScriptedLLM
from repro.sandbox.fake import green_result, red_result
from repro.settings import settings

# The class is named TestArtifact, so pytest tries to collect it as a test class
# wherever it is imported, alias or not. This says it is not one.
TestFile.__test__ = False

REPO_ROOT = Path(__file__).resolve().parents[1]

#: What task C4 commits: the cassettes, and the verdicts they were recorded with.
CASSETTE_DIR = REPO_ROOT / "src" / "repro" / "llm" / "cassettes"
RECORDED_RUNS = REPO_ROOT / "eval" / "recorded_runs.json"

#: Differs between two runs of the same case for reasons that are not fidelity:
#: wall_clock_s is a stopwatch, and usage is what THIS provider charged.
NOT_FIDELITY = {"wall_clock_s", "usage"}


def comparable(record: RunRecord) -> str:
    """The part of a RunRecord that replay must reproduce byte for byte."""
    payload = record.model_dump(mode="json")
    for key in NOT_FIDELITY:
        payload.pop(key, None)
    return json.dumps(payload, sort_keys=True, indent=2)


# ---------------------------------------------------------------------------
# A sandbox that reaches a verdict without running anything
# ---------------------------------------------------------------------------
class ScriptedSandbox:
    """The Sandbox protocol, driven by prepared results (task B4's constructors).

    Deterministic on purpose: replay fidelity is a claim about the MODEL layer,
    so everything below it has to return the same thing both times or the test
    would be measuring the sandbox instead.
    """

    def __init__(self, test_results: list[ExecutionResult]) -> None:
        self.test_results = deque(test_results)
        self.written: list[TestFile] = []
        self.applied: list[Patch] = []

    def search(self, query: str, k: int = 8) -> list[tuple[str, str, float]]:
        return [("shopcart/pricing.py", "def shipping_for(subtotal):\n    ...", 6.0)]

    def rank_candidates(self, hits, k: int) -> list[Hypothesis]:
        return []

    def write_test(self, test: TestFile) -> None:
        self.written.append(test)

    def run_test(self, test_path: str) -> ExecutionResult:
        return self.test_results.popleft()

    def run_suite(self) -> ExecutionResult:
        return green_result(passed=6)

    def apply_patch(self, patch: Patch) -> tuple[bool, str]:
        self.applied.append(patch)
        return True, "applied"

    def revert_patch(self, patch: Patch) -> None:
        return None


DIFF = (
    "--- a/shopcart/pricing.py\n"
    "+++ b/shopcart/pricing.py\n"
    "@@ -44,3 +44,3 @@\n"
    "     discounted = apply_promo(subtotal, promo)\n"
    "-    return round(discounted + shipping_for(discounted), 2)\n"
    "+    return round(discounted + shipping_for(subtotal), 2)\n"
)


def script() -> list:
    """One reply per node, in the order the graph asks for them."""
    return [
        ReportFacts(
            observed_behaviour="charged postage on an order over $50",
            expected_behaviour="free postage over $50",
            steps=["tried to buy stuff"],
            entrypoint_hint="checkout",
            missing=[],
            confidence=0.9,
        ),
        Hypothesis(
            file_path="shopcart/pricing.py",
            symbol="shipping_for",
            rationale="The client was 'charged me postage'. shipping_for sees the discounted total.",
            confidence=0.85,
        ),
        TestFile(
            path="tests/test_free_shipping.py",
            source="from shopcart.pricing import Line, total\n\n"
            "def test_free_shipping_uses_pre_discount_total():\n"
            '    assert total([Line("A", 55.0, 1)], promo="SAVE10") == 49.50\n',
        ),
        Patch(
            unified_diff=DIFF,
            files_touched=["shopcart/pricing.py"],
            rationale="shipping_for was passed the post-promo subtotal. It now sees the original.",
        ),
        Handover(
            dev_summary="## Free shipping used the discounted subtotal",
            client_reply="You were charged postage on an order over $50. We reproduced it.",
        ),
    ]


@pytest.fixture
def report() -> ClientReport:
    """ONE report, reused by both runs.

    It carries `received_at`, which goes into the user turn and therefore into
    the cassette key. Two reports built a millisecond apart would key to
    different cassettes and the replay would miss for a reason that has nothing
    to do with fidelity.
    """
    return ClientReport(
        run_id="fidelity-1",
        raw_text="i was charged postage even though the site says free postage over $50",
        repo_path=str(REPO_ROOT / "fixtures" / "demo_repos" / "shopcart"),
    )


def record_then_replay(report: ClientReport, cassette_dir: Path) -> tuple[RunRecord, RunRecord]:
    recorder = RecordingLLM(ScriptedLLM(script()), cassette_dir=cassette_dir, enabled=True)
    recorded = run(report, recorder, sandbox=ScriptedSandbox([red_result(), green_result()]))

    replayed = run(
        report,
        FakeLLM(cassette_dir),
        sandbox=ScriptedSandbox([red_result(), green_result()]),
    )
    return recorded, replayed


# ---------------------------------------------------------------------------
# Layer 1: the machinery. Free, offline, runs in CI on every push.
# ---------------------------------------------------------------------------
def test_a_recorded_run_replays_byte_identically(report, tmp_path):
    recorded, replayed = record_then_replay(report, tmp_path / "cassettes")

    assert comparable(replayed) == comparable(recorded)


def test_the_replayed_run_reaches_the_same_verdict(report, tmp_path):
    recorded, replayed = record_then_replay(report, tmp_path / "cassettes")

    assert recorded.verdict == Verdict.REPRODUCED_AND_FIXED
    assert replayed.verdict == recorded.verdict


def test_every_accepted_patch_is_identical(report, tmp_path):
    """The patch is the thing a human merges. It is the one that must not drift."""
    recorded, replayed = record_then_replay(report, tmp_path / "cassettes")

    recorded_patches = [f.patch.unified_diff for f in recorded.fix_attempts if f.accepted]
    replayed_patches = [f.patch.unified_diff for f in replayed.fix_attempts if f.accepted]

    assert recorded_patches, "the recorded run accepted no patch, so this proves nothing"
    assert replayed_patches == recorded_patches


def test_replay_needs_no_model_and_no_network(report, tmp_path):
    """FakeLLM reads files. Nothing in the replay path can reach a provider."""
    cassette_dir = tmp_path / "cassettes"
    record_then_replay(report, cassette_dir)

    fake = FakeLLM(cassette_dir)
    replayed = run(report, fake, sandbox=ScriptedSandbox([red_result(), green_result()]))

    assert replayed.verdict == Verdict.REPRODUCED_AND_FIXED
    assert len(fake.calls) == 5, "one call per node: intake, localise, repro, fix, report"


def test_one_cassette_per_distinct_prompt(report, tmp_path):
    cassette_dir = tmp_path / "cassettes"
    recorder = RecordingLLM(ScriptedLLM(script()), cassette_dir=cassette_dir, enabled=True)
    run(report, recorder, sandbox=ScriptedSandbox([red_result(), green_result()]))

    written = sorted(p.name for p in cassette_dir.glob("*.json"))
    assert len(written) == 5
    assert len(set(recorder.written)) == 5, "two nodes collided on one cassette key"


def test_a_changed_prompt_breaks_replay_loudly(report, tmp_path):
    """The failure mode C4 warns about, asserted rather than described.

    A cassette key hashes the system prompt, so editing one after the recording
    run makes replay miss. It must fail with a message naming the cause, not
    fall back to a plausible-looking reply.
    """
    from repro.llm.base import SchemaValidationError
    from repro.llm.fake import cassette_key

    cassette_dir = tmp_path / "cassettes"
    record_then_replay(report, cassette_dir)

    key = cassette_key("a prompt nobody recorded", "some user turn", "ReportFacts")
    with pytest.raises(SchemaValidationError, match="No cassette"):
        FakeLLM(cassette_dir).structured(
            system="a prompt nobody recorded", user="some user turn", schema=ReportFacts
        )
    assert not (cassette_dir / f"{key}.json").exists()


def test_recording_is_off_unless_asked_for(report, tmp_path):
    """A normal `LLM_PROVIDER=bedrock` run must not overwrite the cassettes."""
    cassette_dir = tmp_path / "cassettes"
    recorder = RecordingLLM(ScriptedLLM(script()), cassette_dir=cassette_dir, enabled=False)
    record = run(report, recorder, sandbox=ScriptedSandbox([red_result(), green_result()]))

    assert record.verdict == Verdict.REPRODUCED_AND_FIXED
    assert not cassette_dir.exists()
    assert settings().record is False, "REPRO_RECORD leaked into the test environment"


# ---------------------------------------------------------------------------
# Layer 2: the committed recording. Skips until task C4 has been run.
# ---------------------------------------------------------------------------
def _recorded_runs() -> list[dict]:
    if not RECORDED_RUNS.is_file():
        pytest.skip(
            f"{RECORDED_RUNS.relative_to(REPO_ROOT)} does not exist yet: task C4's recording "
            "run has not happened. It needs live Bedrock credentials, all 8 eval cases "
            "(task E1) and the eval harness (task E2)."
        )
    return json.loads(RECORDED_RUNS.read_text())["runs"]


def test_the_committed_cassettes_exist():
    runs = _recorded_runs()
    assert CASSETTE_DIR.is_dir(), "recorded runs are committed but the cassettes are not"
    assert list(CASSETTE_DIR.glob("*.json")), "the cassette directory is empty"
    assert runs, "recorded_runs.json lists no runs"


def test_every_recorded_case_replays_to_its_recorded_verdict():
    """`LLM_PROVIDER=fake make eval` must reproduce the recording, offline."""
    for recorded in _recorded_runs():
        report = ClientReport.model_validate(recorded["report"])
        replayed = run(report, FakeLLM(CASSETTE_DIR))

        assert replayed.verdict.value == recorded["verdict"], (
            f"{report.run_id}: replayed {replayed.verdict.value}, "
            f"recorded {recorded['verdict']}"
        )
        accepted = [f.patch.unified_diff for f in replayed.fix_attempts if f.accepted]
        assert accepted == recorded["accepted_patches"], f"{report.run_id}: patch drift"
