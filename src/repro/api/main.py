"""FastAPI surface. OWNER: Engineer D.

MOCK RUNS ONLY, for now. `POST /runs` replays a fixture on a background task
at the pacing a real run has -- intake ~2s, localise ~3s, each repro attempt
~8s -- so the UI is built against real waiting, real ordering and a real retry
rather than against instant responses. Nothing here imports `repro.graph`; when
the graph is wired in, `MockRun.play()` is the only thing that changes.
"""
from __future__ import annotations

import asyncio
import json
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

from repro.contracts import ClientReport, RunRecord, StreamEvent

app = FastAPI(title="Repro", version="0.1.0")

#: Multiplies every scripted delay. 1.0 is demo pacing; tests set it to 0.
TIME_SCALE = float(os.getenv("REPRO_MOCK_SPEED", "1.0"))

#: Under tests/ because it is test data the UI borrows, not shipped code.
FIXTURE_PATH = Path(
    os.getenv(
        "REPRO_MOCK_FIXTURE",
        Path(__file__).resolve().parents[3] / "tests" / "fixtures" / "mock_run_shopcart.json",
    )
)

#: run_id -> MockRun. In-memory on purpose: runs are minutes long, not days,
#: and the durable copy is the RunRecord JSON under runs/.
RUNS: dict[str, "MockRun"] = {}


# ---------------------------------------------------------------------------
# Request/response bodies
# ---------------------------------------------------------------------------


class NewRun(BaseModel):
    """What the web form posts. The rest of ClientReport we fill in."""

    raw_text: str = Field(min_length=1, description="The complaint, verbatim.")
    repo_path: str = Field(min_length=1, description="Absolute path to a local checkout.")
    reporter_name: Optional[str] = None


class RunAccepted(BaseModel):
    run_id: str


# ---------------------------------------------------------------------------
# The mock run
# ---------------------------------------------------------------------------


@lru_cache(maxsize=1)
def load_fixture(path: str | None = None) -> dict:
    return json.loads(Path(path or FIXTURE_PATH).read_text(encoding="utf-8"))


class MockRun:
    """Replays the fixture as if a real graph were producing it.

    Subscribers get every event, including the ones that happened before they
    connected: a UI that opens the stream a second after POSTing must still see
    the whole story, and a UI that connects after the run finished gets the
    replay and an immediate close.
    """

    def __init__(self, report: ClientReport) -> None:
        self.run_id = report.run_id
        self.report = report
        self.events: list[StreamEvent] = []
        self.record: RunRecord | None = None
        self.subscribers: list[asyncio.Queue] = []
        self.task: asyncio.Task | None = None
        self.finished = False

    # --- writing ------------------------------------------------------------
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
                        payload=scripted.get("payload", {}),
                    )
                )
                # The record is readable from the moment the verdict lands.
                if scripted["type"] == "verdict":
                    self.record = self._build_record(fixture)
        except asyncio.CancelledError:  # pragma: no cover - shutdown
            raise
        except Exception as exc:  # noqa: BLE001 - a broken fixture must not hang the UI
            self._emit(
                StreamEvent(
                    type="error",
                    run_id=self.run_id,
                    label=f"The mock run failed: {type(exc).__name__}",
                    payload={"error": str(exc)},
                )
            )
        finally:
            self._emit_sentinel()

    def _build_record(self, fixture: dict) -> RunRecord:
        return RunRecord.model_validate(
            {
                **fixture["record"],
                "run_id": self.run_id,
                "report": self.report.model_dump(mode="json"),
            }
        )

    def _emit(self, event: StreamEvent) -> None:
        self.events.append(event)
        for queue in self.subscribers:
            queue.put_nowait(event)

    def _emit_sentinel(self) -> None:
        """Wake every open stream so it can close itself."""
        self.finished = True
        for queue in self.subscribers:
            queue.put_nowait(_DONE)

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


#: Sentinel: the run is over, close the stream.
_DONE = object()


async def stream(run: MockRun, request: Request) -> AsyncIterator[dict]:
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


def _get_run(run_id: str) -> MockRun:
    run = RUNS.get(run_id)
    if run is None:
        raise HTTPException(status_code=404, detail=f"unknown run: {run_id}")
    return run


# ---------------------------------------------------------------------------
# Routes
# ---------------------------------------------------------------------------


@app.get("/healthz")
def healthz() -> dict:
    return {"ok": True}


@app.post("/runs", status_code=201, response_model=RunAccepted)
async def create_run(body: NewRun) -> RunAccepted:
    """Start a run and hand back its id. The work happens on a background task."""
    report = ClientReport(
        run_id=uuid.uuid4().hex,
        raw_text=body.raw_text,
        repo_path=body.repo_path,
        reporter_name=body.reporter_name,
    )
    run = MockRun(report)
    RUNS[run.run_id] = run
    run.task = asyncio.create_task(run.play())
    return RunAccepted(run_id=run.run_id)


@app.get("/runs/{run_id}/events")
async def run_events(run_id: str, request: Request) -> EventSourceResponse:
    """Server-Sent Events: one StreamEvent per message, closing on the verdict."""
    run = _get_run(run_id)
    return EventSourceResponse(stream(run, request))


@app.get("/runs/{run_id}")
async def get_run(run_id: str):
    """The full RunRecord, once the run is terminal. 202 while it is still going."""
    run = _get_run(run_id)
    if run.record is None:
        return JSONResponse(
            {"run_id": run_id, "status": "running", "events_so_far": len(run.events)},
            status_code=202,
        )
    return run.record
