"""FastAPI surface. OWNER: Engineer D.

`POST /runs` invokes the real graph through `repro.graph.build.run` on a worker
thread, and every node transition it reports becomes a StreamEvent on the SSE
stream. `MOCK=1` swaps the graph for a fixture replay at the same pacing and
through the same event vocabulary: the rehearsal path when something upstream
is broken, and the reason the UI can be worked on with no AWS and no repo.

Nothing that leaves this module has skipped `check_invariants()`.
"""
from __future__ import annotations

import asyncio
import json
import logging
import os
import uuid
from datetime import datetime, timezone
from functools import lru_cache
from pathlib import Path
from typing import AsyncIterator, Optional

from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import JSONResponse
from pydantic import BaseModel, Field
from sse_starlette.sse import EventSourceResponse

from repro.clients import llm_for
from repro.contracts import ClientReport, RunRecord, StreamEvent
from repro.graph import events
from repro.graph.build import enforce_invariants
from repro.graph.build import run as run_graph

LOG = logging.getLogger("repro.api")

app = FastAPI(title="Repro", version="0.1.0")

#: Multiplies every scripted delay on the mock path. 1.0 is demo pacing.
TIME_SCALE = float(os.getenv("REPRO_MOCK_SPEED", "1.0"))

#: Under tests/ because it is test data the UI borrows, not shipped code.
FIXTURE_PATH = Path(
    os.getenv(
        "REPRO_MOCK_FIXTURE",
        Path(__file__).resolve().parents[3] / "tests" / "fixtures" / "mock_run_shopcart.json",
    )
)

#: Real runs copy this path and run pytest inside the copy. The endpoint is
#: unauthenticated by design, so it will only do that for repos underneath an
#: allowed root -- otherwise "repo_path" is an arbitrary read of this machine.
#: Set REPRO_ALLOWED_REPOS="" to turn the check off.
ALLOWED_REPO_ROOT = os.getenv(
    "REPRO_ALLOWED_REPOS",
    str(Path(__file__).resolve().parents[3] / "fixtures" / "demo_repos"),
)

#: run_id -> the run. In memory on purpose: runs are minutes long, not days,
#: and the durable copy is the RunRecord JSON under runs/.
RUNS: dict[str, "RunStream"] = {}


def mock_enabled() -> bool:
    """Read at call time, so a test or a rehearsal can flip it without a restart."""
    return os.getenv("MOCK", "0").strip().lower() in {"1", "true", "yes"}


# ---------------------------------------------------------------------------
# Request/response bodies
# ---------------------------------------------------------------------------


class NewRun(BaseModel):
    """What the web form posts. The rest of ClientReport we fill in."""

    raw_text: str = Field(min_length=1, description="The complaint, verbatim.")
    repo_path: str = Field(min_length=1, description="Path to a local checkout.")
    reporter_name: Optional[str] = None


class RunAccepted(BaseModel):
    run_id: str


# ---------------------------------------------------------------------------
# A run, from the stream's point of view
# ---------------------------------------------------------------------------

#: Sentinel: the run is over, close the stream.
_DONE = object()


class RunStream:
    """Shared plumbing: fan out events, hold the record, gate the invariants.

    Subscribers get every event, including the ones from before they connected:
    a UI that opens the stream a second after POSTing must still see the whole
    story, and one that connects after the run finished gets the replay and an
    immediate close.
    """

    def __init__(self, report: ClientReport) -> None:
        self.run_id = report.run_id
        self.report = report
        self.events: list[StreamEvent] = []
        self.record: RunRecord | None = None
        self.error: str | None = None
        self.subscribers: list[asyncio.Queue] = []
        self.task: asyncio.Task | None = None
        self.finished = False

    # --- writing ------------------------------------------------------------
    def _emit(self, event: StreamEvent) -> None:
        self.events.append(event)
        for queue in self.subscribers:
            queue.put_nowait(event)

    def _emit_sentinel(self) -> None:
        self.finished = True
        for queue in self.subscribers:
            queue.put_nowait(_DONE)

    def publish(self, record: RunRecord) -> None:
        """The last gate before a record is readable over HTTP.

        `run()` already enforces this, and the fixture is sound by
        construction. It is checked again here anyway, because "the graph would
        never" is not a property the API can rely on: a patch that failed its
        own verification must not reach a human who trusts this endpoint.
        """
        violations = record.check_invariants()
        if violations:
            LOG.error(
                "run %s: serving a record that violated %d invariant(s); patches withheld",
                record.run_id,
                len(violations),
            )
            self._emit(events.verification_failed(record.run_id, violations))
            record = enforce_invariants(record)
        self.record = record

    def fail(self, exc: BaseException) -> None:
        LOG.exception("run %s failed", self.run_id)
        self.error = f"{type(exc).__name__}: {exc}"
        self._emit(events.run_failed(self.run_id, exc))

    # --- reading ------------------------------------------------------------
    def subscribe(self) -> asyncio.Queue:
        """Queue pre-loaded with the backlog. No awaits: nothing can interleave."""
        queue: asyncio.Queue = asyncio.Queue()
        for event in self.events:
            queue.put_nowait(event)
        if self.finished:
            queue.put_nowait(_DONE)
        self.subscribers.append(queue)
        return queue

    def unsubscribe(self, queue: asyncio.Queue) -> None:
        if queue in self.subscribers:
            self.subscribers.remove(queue)


class RealRun(RunStream):
    """The actual graph, on a worker thread."""

    async def play(self) -> None:
        loop = asyncio.get_running_loop()

        def emit_from_thread(event: StreamEvent) -> None:
            # run() calls this from the worker thread, and asyncio.Queue is not
            # thread-safe. Hop back onto the loop before touching a subscriber.
            loop.call_soon_threadsafe(self._emit, event)

        try:
            record = await asyncio.to_thread(
                lambda: run_graph(self.report, llm_for(), on_event=emit_from_thread)
            )
        except Exception as exc:  # noqa: BLE001 - one bad run must not kill the server
            self.fail(exc)
        else:
            self.publish(record)
        finally:
            self._emit_sentinel()


class MockRun(RunStream):
    """Replays the fixture at the pacing a real run has. MOCK=1."""

    async def play(self) -> None:
        fixture = load_fixture()
        try:
            for scripted in fixture["events"]:
                await asyncio.sleep(float(scripted["after_s"]) * TIME_SCALE)
                self._emit(
                    StreamEvent(
                        type=scripted["type"],
                        run_id=self.run_id,
                        at=datetime.now(timezone.utc),
                        label=scripted["label"],
                        payload=self._payload_for(scripted),
                    )
                )
                if scripted["type"] == "verdict":
                    self.publish(self._build_record(fixture))
        except asyncio.CancelledError:  # pragma: no cover - shutdown
            raise
        except Exception as exc:  # noqa: BLE001 - a broken fixture must not hang the UI
            self.fail(exc)
        finally:
            self._emit_sentinel()

    def _payload_for(self, scripted: dict) -> dict:
        """The scripted payload, plus anything only this run knows.

        The client reply is addressed to whoever reported the bug, and the
        fixture cannot know their name -- it comes off the report that started
        the run.
        """
        payload = dict(scripted.get("payload", {}))
        if scripted["type"] == "run_started":
            payload["repo_path"] = self.report.repo_path
        if scripted["type"] == "verdict":
            payload["reporter_name"] = self.report.reporter_name
        return payload

    def _build_record(self, fixture: dict) -> RunRecord:
        return RunRecord.model_validate(
            {
                **fixture["record"],
                "run_id": self.run_id,
                "report": self.report.model_dump(mode="json"),
            }
        )


@lru_cache(maxsize=1)
def load_fixture(path: str | None = None) -> dict:
    return json.loads(Path(path or FIXTURE_PATH).read_text(encoding="utf-8"))


# ---------------------------------------------------------------------------
# Streaming
# ---------------------------------------------------------------------------


async def stream(run: RunStream, request: Request) -> AsyncIterator[dict]:
    queue = run.subscribe()
    index = 0
    try:
        while True:
            event = await queue.get()
            if event is _DONE:
                return
            yield {"id": str(index), "event": event.type, "data": event.model_dump_json()}
            index += 1
            if event.type == "verdict":
                return  # terminal: the run is over, so is the stream
            if await request.is_disconnected():  # pragma: no cover - client hangup
                return
    finally:
        run.unsubscribe(queue)


def _get_run(run_id: str) -> RunStream:
    run = RUNS.get(run_id)
    if run is None:
        raise HTTPException(status_code=404, detail=f"unknown run: {run_id}")
    return run


def check_repo_path(repo_path: str) -> None:
    """A real run copies this directory. Refuse anything outside the allow-list."""
    if not ALLOWED_REPO_ROOT:
        return  # deliberately disabled
    root = Path(ALLOWED_REPO_ROOT).resolve()
    candidate = Path(repo_path).expanduser().resolve()
    if not candidate.is_relative_to(root):
        raise HTTPException(
            status_code=400,
            detail=(
                f"repo_path must be inside {root}. This endpoint has no auth, so it "
                f"will not copy an arbitrary directory off this machine."
            ),
        )
    if not candidate.is_dir():
        raise HTTPException(status_code=400, detail=f"no such repository: {candidate}")


# ---------------------------------------------------------------------------
# Routes
# ---------------------------------------------------------------------------


@app.get("/healthz")
def healthz() -> dict:
    return {"ok": True, "mode": "mock" if mock_enabled() else "live"}


@app.post("/runs", status_code=201, response_model=RunAccepted)
async def create_run(body: NewRun) -> RunAccepted:
    """Start a run and hand back its id. The work happens off this request."""
    report = ClientReport(
        run_id=uuid.uuid4().hex,
        raw_text=body.raw_text,
        repo_path=body.repo_path,
        reporter_name=body.reporter_name,
    )
    if mock_enabled():
        run: RunStream = MockRun(report)
    else:
        check_repo_path(body.repo_path)  # only a real run touches the filesystem
        run = RealRun(report)

    RUNS[run.run_id] = run
    run.task = asyncio.create_task(run.play())
    return RunAccepted(run_id=run.run_id)


@app.get("/runs/{run_id}/events")
async def run_events(run_id: str, request: Request) -> EventSourceResponse:
    """Server-Sent Events: one StreamEvent per message, closing on the verdict."""
    return EventSourceResponse(stream(_get_run(run_id), request))


@app.get("/runs/{run_id}")
async def get_run(run_id: str):
    """The full RunRecord, once the run is terminal. 202 while it is still going."""
    run = _get_run(run_id)
    if run.error is not None:
        return JSONResponse({"run_id": run_id, "error": run.error}, status_code=500)
    if run.record is None:
        return JSONResponse(
            {"run_id": run_id, "status": "running", "events_so_far": len(run.events)},
            status_code=202,
        )
    return run.record
