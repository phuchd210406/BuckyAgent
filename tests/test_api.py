"""API tests. OWNER: Engineer D.

The API is on a mock run until the graph is wired in, so these assert the
CONTRACT the UI is built against: an id back immediately, an ordered stream
that ends, and a record that only exists once the run is terminal.
"""
from __future__ import annotations

import json

import pytest
from fastapi.testclient import TestClient

from repro.api import main
from repro.contracts import RunRecord, StreamEvent, Verdict

NEW_RUN = {
    "raw_text": "I was charged twice for one order.",
    "repo_path": "/tmp/shopcart",
    "reporter_name": "A. Client",
}


@pytest.fixture(autouse=True)
def _instant(monkeypatch):
    """Real pacing is 33 seconds. Tests assert the ordering, not the waiting."""
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


def test_healthz(client):
    assert client.get("/healthz").json() == {"ok": True}


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
