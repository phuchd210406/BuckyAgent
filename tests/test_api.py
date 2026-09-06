"""API tests. OWNER: Engineer D.

The API is on a mock run until the graph is wired in, so these assert the
CONTRACT the UI is built against: an id back immediately, an ordered stream
that ends, and a record that only exists once the run is terminal.
"""
from __future__ import annotations

import json
import pathlib

import pytest
from fastapi.testclient import TestClient

from repro.api import main
from repro.contracts import (
    ClientReport,
    ExecutionResult,
    FixAttempt,
    Handover,
    Patch,
    RunRecord,
    StreamEvent,
    Verdict,
)

NEW_RUN = {
    "raw_text": "I was charged twice for one order.",
    "repo_path": "/tmp/shopcart",
    "reporter_name": "A. Client",
}


@pytest.fixture(autouse=True)
def _instant(monkeypatch):
    """Mock path, no waiting. Real pacing is 33s; tests assert order, not time."""
    monkeypatch.setenv("MOCK", "1")
    monkeypatch.setattr(main, "TIME_SCALE", 0.0)
    main.RUNS.clear()


@pytest.fixture
def client():
    with TestClient(main.app) as test_client:
        yield test_client


def read_events(client, run_id: str) -> list[StreamEvent]:
    """Consume the SSE stream to its end and parse it back into StreamEvents."""
    events: list[StreamEvent] = []
    with client.stream("GET", f"/runs/{run_id}/events") as response:
        assert response.status_code == 200
        assert response.headers["content-type"].startswith("text/event-stream")
        for line in response.iter_lines():
            if line.startswith("data:"):
                events.append(StreamEvent.model_validate_json(line[len("data:") :].strip()))
    return events


# --- the three routes ---------------------------------------------------------


def test_healthz_says_which_mode_it_is_in(client):
    # Which mode matters during a demo: "why is it instant?" has one answer.
    assert client.get("/healthz").json() == {"ok": True, "mode": "mock"}


def test_post_runs_returns_a_run_id(client):
    response = client.post("/runs", json=NEW_RUN)

    assert response.status_code == 201
    run_id = response.json()["run_id"]
    assert run_id and isinstance(run_id, str)
    # The report the UI posted is the report the run carries.
    assert main.RUNS[run_id].report.raw_text == NEW_RUN["raw_text"]
    assert main.RUNS[run_id].report.reporter_name == "A. Client"


def test_post_runs_rejects_an_empty_body(client):
    assert client.post("/runs", json={}).status_code == 422
    assert client.post("/runs", json={"raw_text": "", "repo_path": "/tmp"}).status_code == 422


def test_the_stream_yields_every_event_in_order_and_then_ends(client):
    run_id = client.post("/runs", json=NEW_RUN).json()["run_id"]

    events = read_events(client, run_id)  # returns only because the stream closed

    assert [e.type for e in events][:2] == ["run_started", "node_started"]
    assert events[-1].type == "verdict"
    assert all(e.run_id == run_id for e in events)
    assert [e.at for e in events] == sorted(e.at for e in events)
    # Ordering that matters to the UI: nodes announce themselves before they finish.
    types = [e.type for e in events]
    assert types.index("node_started") < types.index("node_finished")
    assert types.index("hypothesis") < types.index("repro_attempt")
    assert types.index("repro_attempt") < types.index("fix_attempt")


def test_the_stream_shows_a_failed_first_repro_then_a_successful_retry(client):
    # This is the sequence that proves the thing is agentic rather than a
    # one-shot prompt, and the UI has to render it.
    run_id = client.post("/runs", json=NEW_RUN).json()["run_id"]

    attempts = [e for e in read_events(client, run_id) if e.type == "repro_attempt"]

    assert [a.payload["attempt_no"] for a in attempts] == [1, 2]
    assert [a.payload["reproduced"] for a in attempts] == [False, True]
    assert attempts[0].payload["reasoning"]  # the UI shows WHY attempt 1 did not count


def test_the_verdict_event_says_who_the_reply_is_addressed_to(client):
    # The reply is a letter to a person; the fixture cannot know their name, so
    # it comes off the report the run was started from.
    run_id = client.post("/runs", json={**NEW_RUN, "reporter_name": "Sarah Whitfield"}).json()[
        "run_id"
    ]

    verdict = read_events(client, run_id)[-1]

    assert verdict.type == "verdict"
    assert verdict.payload["reporter_name"] == "Sarah Whitfield"
    assert verdict.payload["handover"]["client_reply"]


def test_the_verdict_event_carries_no_name_when_none_was_given(client):
    anonymous = {key: value for key, value in NEW_RUN.items() if key != "reporter_name"}
    run_id = client.post("/runs", json=anonymous).json()["run_id"]

    assert read_events(client, run_id)[-1].payload["reporter_name"] is None


def test_a_late_subscriber_still_gets_the_whole_story(client):
    run_id = client.post("/runs", json=NEW_RUN).json()["run_id"]
    client.get(f"/runs/{run_id}")  # let the run finish first

    events = read_events(client, run_id)

    assert events[0].type == "run_started"
    assert events[-1].type == "verdict"


def test_get_run_returns_the_record_once_terminal(client):
    run_id = client.post("/runs", json=NEW_RUN).json()["run_id"]
    read_events(client, run_id)

    response = client.get(f"/runs/{run_id}")

    assert response.status_code == 200
    record = RunRecord.model_validate(response.json())
    assert record.run_id == run_id
    assert record.report.raw_text == NEW_RUN["raw_text"]
    assert record.verdict == Verdict.REPRODUCED_AND_FIXED
    assert [a.reproduced for a in record.repro_attempts] == [False, True]
    assert [f.accepted for f in record.fix_attempts] == [True]
    assert record.handover is not None
    # The API must never hand out a record that contradicts itself.
    assert record.check_invariants() == []


def test_get_run_is_202_while_the_run_is_still_going(client):
    # Built by hand rather than raced against: a run that has not been played.
    run = main.MockRun(
        main.ClientReport(run_id="in-flight", raw_text="x", repo_path="/tmp/x")
    )
    main.RUNS["in-flight"] = run

    response = client.get("/runs/in-flight")

    assert response.status_code == 202
    assert response.json()["status"] == "running"
    assert "verdict" not in response.json()


def test_unknown_run_is_404(client):
    assert client.get("/runs/nope").status_code == 404
    assert client.get("/runs/nope/events").status_code == 404
    assert "nope" in client.get("/runs/nope").json()["detail"]


# --- the fixture is the UI's spec, so it gets asserted too --------------------


def test_the_fixture_paces_like_a_real_run():
    """Asserted on the fixture, not on the clock.

    TestClient buffers a streaming response and hands the whole body over at
    the end, so wall-clock timing cannot be measured through it -- verified
    against a real uvicorn server instead, where the events arrive spread out
    (intake +1.7s, localise +3.0s, repro attempts +8s each, verdict +32.9s).
    """
    fixture = main.load_fixture()
    delay = {}
    for event in fixture["events"]:
        node = event.get("payload", {}).get("node")
        key = (event["type"], node, event.get("payload", {}).get("attempt_no"))
        delay[key] = event["after_s"]

    assert delay[("node_finished", "intake", None)] == pytest.approx(2.0, abs=0.5)
    # localise: the hypotheses stream in, then the node closes.
    localise = sum(
        e["after_s"]
        for e in fixture["events"]
        if e["type"] == "hypothesis" or e.get("payload", {}).get("node") == "localise"
    )
    assert localise == pytest.approx(3.0, abs=0.5)
    assert delay[("repro_attempt", None, 1)] == pytest.approx(8.0, abs=0.5)
    assert delay[("repro_attempt", None, 2)] == pytest.approx(8.0, abs=0.5)
    total = sum(e["after_s"] for e in fixture["events"])
    assert 25.0 <= total <= 45.0, f"a demo run should feel like ~30s, not {total}s"


def test_the_fixture_record_is_a_valid_sound_runrecord():
    fixture = main.load_fixture()
    record = RunRecord.model_validate(
        {
            **fixture["record"],
            "run_id": "fixture",
            "report": {"run_id": "fixture", "raw_text": "x", "repo_path": "/tmp/x"},
        }
    )

    assert record.check_invariants() == []
    assert record.usage.usd > 0


def test_every_fixture_event_is_a_valid_stream_event():
    for scripted in main.load_fixture()["events"]:
        StreamEvent(
            type=scripted["type"],
            run_id="fixture",
            label=scripted["label"],
            payload=scripted.get("payload", {}),
        )
    # And the payloads are JSON, because that is what goes down the wire.
    json.dumps(main.load_fixture())


# --- the invariant gate --------------------------------------------------------


def _unsound_record(run_id: str) -> RunRecord:
    """A patch accepted on a run that never reproduced anything. Forbidden."""
    green = ExecutionResult(exit_code=0, stdout_tail="", stderr_tail="", duration_s=0.1)
    return RunRecord(
        run_id=run_id,
        report=ClientReport(run_id=run_id, raw_text="x", repo_path="/tmp/x"),
        repro_attempts=[],
        fix_attempts=[
            FixAttempt(
                attempt_no=1,
                patch=Patch(
                    unified_diff="--- a/shopcart/pricing.py\n+++ b/shopcart/pricing.py\n+DANGER\n",
                    files_touched=["shopcart/pricing.py"],
                    rationale="Fabricated. Never verified.",
                ),
                target_test=green,
                suite=green,
                accepted=True,
                reasoning="fabricated",
            )
        ],
        verdict=Verdict.REPRODUCED_AND_FIXED,
        handover=Handover(dev_summary="apply this patch", client_reply="we fixed it"),
    )


def test_a_record_with_violated_invariants_is_served_without_a_patch(client, monkeypatch):
    """The API is the last gate, and it does not trust the graph to have held.

    A patch that failed its own verification must never reach a human who
    trusts this endpoint -- so the check is repeated here even though run()
    already did it.
    """
    monkeypatch.delenv("MOCK", raising=False)
    monkeypatch.setattr(main, "llm_for", lambda *a, **k: None)
    monkeypatch.setattr(main, "check_repo_path", lambda path: None)

    def graph_that_lies(report, llm, *, on_event=None, **kwargs):
        return _unsound_record(report.run_id)

    monkeypatch.setattr(main, "run_graph", graph_that_lies)

    run_id = client.post("/runs", json=NEW_RUN).json()["run_id"]
    stream_events = read_events(client, run_id)
    response = client.get(f"/runs/{run_id}")

    assert response.status_code == 200
    served = response.json()
    # Not one byte of the patch survives.
    assert "DANGER" not in json.dumps(served)
    for attempt in served["fix_attempts"]:
        assert attempt["patch"]["unified_diff"] == ""
        assert attempt["patch"]["files_touched"] == []
        assert attempt["accepted"] is False
    assert served["verdict"] == Verdict.ABORTED_BUDGET.value
    assert served["handover"] is None
    # And the record that IS served is sound.
    assert RunRecord.model_validate(served).check_invariants() == []

    # The stream says so out loud, rather than quietly serving less.
    failures = [event for event in stream_events if event.type == "error"]
    assert failures, "the UI must be told the verification failed"
    assert failures[0].payload["error"] == "verification failed"
    assert any(
        "accepted without a reproduction" in violation
        for violation in failures[0].payload["violations"]
    )


def test_a_sound_record_passes_the_gate_untouched(client, monkeypatch):
    monkeypatch.delenv("MOCK", raising=False)
    monkeypatch.setattr(main, "llm_for", lambda *a, **k: None)
    monkeypatch.setattr(main, "check_repo_path", lambda path: None)
    sound = _unsound_record("placeholder").model_copy(
        update={"verdict": Verdict.NOT_REPRODUCED, "fix_attempts": []}
    )
    monkeypatch.setattr(main, "run_graph", lambda report, llm, **kw: sound)

    run_id = client.post("/runs", json=NEW_RUN).json()["run_id"]
    read_events(client, run_id)

    assert client.get(f"/runs/{run_id}").json()["verdict"] == Verdict.NOT_REPRODUCED.value


# --- mock vs live ---------------------------------------------------------------


def test_mock_is_off_by_default_and_on_with_the_env_var(monkeypatch):
    monkeypatch.delenv("MOCK", raising=False)
    assert main.mock_enabled() is False
    monkeypatch.setenv("MOCK", "1")
    assert main.mock_enabled() is True
    assert client_mode() == "mock"


def client_mode() -> str:
    with TestClient(main.app) as probe:
        return probe.get("/healthz").json()["mode"]


def test_a_real_run_will_not_copy_an_arbitrary_directory(client, monkeypatch, tmp_path):
    # The endpoint has no auth. Without this, `repo_path` is a read of any
    # directory on the machine, and the model sees whatever is in it.
    monkeypatch.delenv("MOCK", raising=False)
    monkeypatch.setattr(main, "ALLOWED_REPO_ROOT", str(tmp_path / "demo_repos"))

    response = client.post("/runs", json={**NEW_RUN, "repo_path": "/etc"})

    assert response.status_code == 400
    assert "no auth" in response.json()["detail"]


def test_a_repo_that_does_not_exist_is_rejected_at_post_time(client, monkeypatch, tmp_path):
    monkeypatch.delenv("MOCK", raising=False)
    (tmp_path / "demo_repos").mkdir()
    monkeypatch.setattr(main, "ALLOWED_REPO_ROOT", str(tmp_path / "demo_repos"))

    response = client.post(
        "/runs", json={**NEW_RUN, "repo_path": str(tmp_path / "demo_repos" / "nope")}
    )

    assert response.status_code == 400
    assert "no such repository" in response.json()["detail"]


# --- the real graph, end to end -------------------------------------------------


def _demo_repo(tmp_path) -> pathlib.Path:
    """A tiny but real Python project, with a real bug and a real test suite."""
    repo = tmp_path / "demo_repos" / "shopcart"
    (repo / "shopcart").mkdir(parents=True)
    (repo / "tests").mkdir()
    (repo / "shopcart" / "__init__.py").write_text("")
    (repo / "shopcart" / "pricing.py").write_text(
        "FREE_SHIPPING_THRESHOLD = 50.0\nFLAT_POSTAGE = 4.99\n\n\n"
        "def shipping_cost(subtotal):\n"
        "    return 0.0 if subtotal >= FREE_SHIPPING_THRESHOLD else FLAT_POSTAGE\n\n\n"
        "def total(subtotal):\n"
        "    return round(subtotal + FLAT_POSTAGE, 2)  # the bug\n"
    )
    (repo / "tests" / "test_pricing.py").write_text(
        "from shopcart.pricing import shipping_cost\n\n\n"
        "def test_threshold():\n    assert shipping_cost(60) == 0.0\n"
    )
    return repo


def test_a_real_run_streams_real_events_and_serves_a_real_record(
    client, monkeypatch, tmp_path
):
    """No mock: the real graph, a real workspace copy, real pytest subprocesses.

    The scripted model keeps writing a test that passes, so nothing reproduces
    and the run ends at the cap -- which is the one full path available before
    Engineer B's patcher lands, and it exercises everything else.
    """
    from repro.contracts import Handover, Hypothesis, ReportFacts, TestArtifact
    from repro.llm.fake import ScriptedLLM

    repo = _demo_repo(tmp_path)
    monkeypatch.delenv("MOCK", raising=False)
    monkeypatch.setattr(main, "ALLOWED_REPO_ROOT", str(tmp_path / "demo_repos"))

    def passing_test(n):
        return TestArtifact(
            path=f"tests/test_repro_{n}.py",
            source="from shopcart.pricing import shipping_cost\n\n\n"
            f"def test_attempt_{n}():\n    assert shipping_cost(62.5) == 0.0\n",
        )

    script = [
        ReportFacts(observed_behaviour="charged postage over $50", confidence=0.8),
        Hypothesis(file_path="shopcart/pricing.py", symbol="total",
                   rationale="Adds postage. Always.", confidence=0.7),
        passing_test(1), passing_test(2), passing_test(3),
        Handover(dev_summary="Could not reproduce.", client_reply="We could not reproduce it."),
    ]
    monkeypatch.setattr(main, "llm_for", lambda *a, **k: ScriptedLLM(script))

    run_id = client.post(
        "/runs",
        json={"raw_text": "charged postage over $50", "repo_path": str(repo),
              "reporter_name": "Sarah Whitfield"},
    ).json()["run_id"]
    stream_events = read_events(client, run_id)

    kinds = [event.type for event in stream_events]
    assert kinds[0] == "run_started"
    assert kinds[-1] == "verdict"
    # Three real pytest runs, each one green, so none of them reproduced.
    attempts = [e for e in stream_events if e.type == "repro_attempt"]
    assert [a.payload["reproduced"] for a in attempts] == [False, False, False]
    assert all(a.payload["passed"] == 1 for a in attempts)
    assert all(a.payload["test_source"] for a in attempts)
    # Retrieval really ran: it ranked candidates out of the copied workspace.
    assert [e for e in stream_events if e.type == "hypothesis"]
    # The reply card needs to know who to address.
    assert stream_events[-1].payload["reporter_name"] == "Sarah Whitfield"

    served = client.get(f"/runs/{run_id}").json()
    record = RunRecord.model_validate(served)
    assert record.verdict == Verdict.NOT_REPRODUCED
    assert len(record.repro_attempts) == 3
    assert record.check_invariants() == []
    assert record.usage.calls == 6

    # The sandbox is a copy: the project on disk is exactly as it was.
    assert "the bug" in (repo / "shopcart" / "pricing.py").read_text()
    assert not (repo / "tests" / "test_repro_1.py").exists()


def test_a_run_that_dies_says_so_terminally_rather_than_just_going_quiet(
    client, monkeypatch
):
    """A dead run must announce itself.

    Without a terminal marker the stream simply ends with no verdict, the
    browser's EventSource calls that a dropped connection, and the UI blames
    the network for something the run already explained.
    """
    monkeypatch.delenv("MOCK", raising=False)
    monkeypatch.setattr(main, "llm_for", lambda *a, **k: None)
    monkeypatch.setattr(main, "check_repo_path", lambda path: None)

    def graph_that_dies(report, llm, **kwargs):
        raise RuntimeError("the sandbox caught fire")

    monkeypatch.setattr(main, "run_graph", graph_that_dies)

    run_id = client.post("/runs", json=NEW_RUN).json()["run_id"]
    stream_events = read_events(client, run_id)

    last = stream_events[-1]
    assert last.type == "error"
    assert last.payload["terminal"] is True
    assert "the sandbox caught fire" in last.payload["error"]

    failed = client.get(f"/runs/{run_id}")
    assert failed.status_code == 500
    assert "the sandbox caught fire" in failed.json()["error"]


def test_verification_failure_is_not_terminal_because_a_verdict_still_follows(
    client, monkeypatch
):
    monkeypatch.delenv("MOCK", raising=False)
    monkeypatch.setattr(main, "llm_for", lambda *a, **k: None)
    monkeypatch.setattr(main, "check_repo_path", lambda path: None)
    monkeypatch.setattr(
        main, "run_graph", lambda report, llm, **kw: _unsound_record(report.run_id)
    )

    run_id = client.post("/runs", json=NEW_RUN).json()["run_id"]
    stream_events = read_events(client, run_id)

    failure = next(event for event in stream_events if event.type == "error")
    assert failure.payload["terminal"] is False


# ---------------------------------------------------------------------------
# Real repositories, and telling the truth about the model
#
# The app is only useful if it can be pointed at a client's actual project, so
# these pin the contract the form is built against: a URL is parsed at POST time
# (fast feedback for a typo) and fetched inside the run (minutes of network,
# with the timeline showing what it is waiting on).
# ---------------------------------------------------------------------------


@pytest.fixture
def live(monkeypatch):
    """Off the mock path, without letting a test touch the network or a model."""
    monkeypatch.setenv("MOCK", "0")
    main.RUNS.clear()


def test_a_github_url_is_accepted_and_parsed_at_post_time(client, live):
    response = client.post(
        "/runs",
        json={"raw_text": "checkout is broken", "repo_url": "https://github.com/psf/requests"},
    )

    assert response.status_code == 201
    run = main.RUNS[response.json()["run_id"]]
    assert (run.repo_ref.owner, run.repo_ref.repo) == ("psf", "requests")
    # Nothing has been cloned yet: the timeline shows what was asked for.
    assert run.report.repo_path == "psf/requests"


def test_an_explicit_ref_beats_the_one_in_the_url(client, live):
    run_id = client.post(
        "/runs",
        json={
            "raw_text": "broken",
            "repo_url": "https://github.com/pallets/flask/tree/3.0.x",
            "repo_ref": "main",
        },
    ).json()["run_id"]

    assert main.RUNS[run_id].repo_ref.ref == "main"


def test_a_typo_in_the_url_fails_immediately_with_something_actionable(client, live):
    response = client.post("/runs", json={"raw_text": "broken", "repo_url": "gitlab.com/a/b"})

    assert response.status_code == 400
    assert "github.com/owner/repo" in response.json()["detail"]


def test_a_run_needs_a_repository_and_will_not_guess_one(client, live):
    response = client.post("/runs", json={"raw_text": "broken"})

    assert response.status_code == 400
    assert "repo_url" in response.json()["detail"]


def test_a_url_and_a_path_together_are_refused_rather_than_ranked(client, live):
    response = client.post(
        "/runs",
        json={"raw_text": "broken", "repo_url": "psf/requests", "repo_path": "/tmp/x"},
    )

    assert response.status_code == 400


def test_a_failed_clone_ends_the_run_with_the_reason_git_gave(client, live, monkeypatch):
    """Not a traceback: the person pasted something and needs to know what to fix."""
    from repro.sandbox.github import RepoFetchError

    def refuse(ref):
        raise RepoFetchError("GitHub has no repository acme/nope, or it is not visible to us.")

    monkeypatch.setattr(main, "fetch_repo", refuse)

    run_id = client.post(
        "/runs", json={"raw_text": "broken", "repo_url": "acme/nope"}
    ).json()["run_id"]
    events = read_events(client, run_id)

    assert events[-1].type == "error"
    assert events[-1].payload["terminal"] is True
    assert "acme/nope" in events[-1].payload["error"]


def test_the_cloned_repo_cache_is_always_an_allowed_root(monkeypatch, tmp_path):
    """We chose that path, so a caller pointing at it is not pointing anywhere new."""
    monkeypatch.setattr(main, "ALLOWED_REPO_ROOT", str(tmp_path / "demo_repos"))
    monkeypatch.setattr(main, "DEFAULT_CACHE_ROOT", tmp_path / "clones")
    cloned = tmp_path / "clones" / "psf__requests"
    cloned.mkdir(parents=True)

    main.check_repo_path(str(cloned))  # does not raise


# --- GET /config --------------------------------------------------------------


def test_config_names_the_model_that_would_actually_answer(client, live, monkeypatch):
    monkeypatch.setenv("LLM_PROVIDER", "anthropic")

    config = client.get("/config").json()

    assert config["provider"] == "anthropic"
    assert config["live_model"] is True
    assert "Haiku" in config["model"]
    assert config["max_run_usd"] > 0


def test_config_admits_when_no_model_will_be_called(client, live, monkeypatch):
    """A page that showed a model name here would be lying about a replayed run."""
    monkeypatch.setenv("LLM_PROVIDER", "fake")

    config = client.get("/config").json()

    assert config["provider"] == "fake"
    assert config["live_model"] is False
    assert config["model_id"] == ""


def test_config_reports_mock_mode_as_mock(client, monkeypatch):
    monkeypatch.setenv("MOCK", "1")

    config = client.get("/config").json()

    assert config["mode"] == "mock"
    assert config["live_model"] is False


def test_config_lists_the_demo_repos_that_actually_exist(client, live, monkeypatch, tmp_path):
    (tmp_path / "shopcart").mkdir()
    (tmp_path / "notes.md").write_text("not a repo")
    monkeypatch.setattr(main, "DEMO_REPO_ROOT", tmp_path)

    names = [repo["name"] for repo in client.get("/config").json()["demo_repos"]]

    assert names == ["shopcart"], "only directories, and only ones on this machine"


# --- failures a person can act on ---------------------------------------------


def test_an_expired_aws_lease_says_to_re_paste_the_keys(monkeypatch):
    """`ExpiredTokenException` in a stack trace tells nobody what to do about it."""
    monkeypatch.setenv("LLM_PROVIDER", "bedrock")

    explained = main._explain_credentials(
        RuntimeError("An error occurred (ExpiredTokenException) calling Converse")
    )

    assert isinstance(explained, main.ExpiredCredentials)
    assert "12 hours" in str(explained)


def test_an_unrelated_failure_is_passed_through_untouched(monkeypatch):
    monkeypatch.setenv("LLM_PROVIDER", "bedrock")
    original = RuntimeError("the sandbox refused this patch")

    assert main._explain_credentials(original) is original


def test_a_cassette_miss_is_reported_as_the_missing_key_it_usually_is(monkeypatch):
    from repro.llm.base import SchemaValidationError

    monkeypatch.setenv("LLM_PROVIDER", "fake")

    explained = main._explain_schema_failure(SchemaValidationError("No cassette abc123"))

    assert isinstance(explained, main.NoModelCredentials)
    assert "ANTHROPIC_API_KEY" in str(explained)


def test_a_real_provider_keeps_the_schema_error_it_actually_got(monkeypatch):
    """With a live model, a schema failure IS a model failure and must not be relabelled."""
    from repro.llm.base import SchemaValidationError

    monkeypatch.setenv("LLM_PROVIDER", "anthropic")
    original = SchemaValidationError("ReportFacts did not validate after one re-prompt")

    assert main._explain_schema_failure(original) is original


def test_a_mock_run_names_the_repository_the_recording_is_of(client):
    """Not the one the caller asked for: nothing was fetched, and the fixture is
    a recording of shopcart. A header saying `pallets/flask` above hypotheses in
    `shopcart/pricing.py` reads as broken rather than as replayed."""
    run_id = client.post(
        "/runs",
        json={"raw_text": "password reset never arrives", "repo_url": "pallets/flask"},
    ).json()["run_id"]

    events = read_events(client, run_id)

    assert events[0].payload["repo_path"] == "fixtures/demo_repos/shopcart"
    assert main.RUNS[run_id].report.repo_path == "fixtures/demo_repos/shopcart"
    # And the record served afterwards agrees with what was on screen.
    assert client.get(f"/runs/{run_id}").json()["report"]["repo_path"] == (
        "fixtures/demo_repos/shopcart"
    )


def test_the_repository_a_mock_run_names_comes_from_the_fixture_itself(client, monkeypatch):
    """So a recording of something else needs no code change to be labelled right."""
    main.load_fixture.cache_clear()
    monkeypatch.setattr(
        main, "load_fixture", lambda path=None: {"repo": "fixtures/demo_repos/ledger"}
    )

    assert main.fixture_repo() == "fixtures/demo_repos/ledger"
