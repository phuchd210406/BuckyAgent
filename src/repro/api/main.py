"""FastAPI surface. OWNER: Engineer D.

`POST /runs` invokes the real graph through `repro.graph.build.run` on a worker
thread, and every node transition it reports becomes a StreamEvent on the SSE
stream. `MOCK=1` swaps the graph for a fixture replay at the same pacing and
through the same event vocabulary: the rehearsal path when something upstream
is broken, and the reason the UI can be worked on with no AWS and no repo.

A run takes EITHER a `repo_url` -- any GitHub repository, which is cloned here
before the graph starts -- or a `repo_path` under an allowed root, which is how
the seeded demo repo is still reachable. The clone is the reason this API can be
pointed at a client's actual project instead of a fixture.

`GET /config` says which provider and model a run would really use, because a
UI that shows "Claude Haiku" while the backend is replaying cassettes is lying
to the person watching it.

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
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from pydantic import BaseModel, Field
from sse_starlette.sse import EventSourceResponse

from repro.clients import credential_for, llm_for, resolve_provider
from repro.contracts import MAX_RUN_USD, ClientReport, RunRecord, StreamEvent
from repro.graph import events
from repro.graph.build import enforce_invariants
from repro.graph.build import run as run_graph
from repro.llm.base import SchemaValidationError
from repro.sandbox import deps
from repro.sandbox.github import (
    DEFAULT_CACHE_ROOT,
    RepoFetchError,
    RepoRef,
    fetch_repo,
    parse_repo_ref,
)
from repro.settings import load_env, model_id_for, model_label

# Before anything below reads the environment. `uvicorn repro.api.main:app` does
# not run the CLI, so without this a key sitting in .env would never be seen and
# every run would quietly replay cassettes instead of calling a model.
load_env()

LOG = logging.getLogger("repro.api")

app = FastAPI(title="Repro", version="0.1.0")

#: The deployed frontend (Vercel) and the backend (a laptop behind a tunnel) are
#: different origins, so the browser needs to be told this is allowed. Exact
#: origins from the environment, plus every vercel.app preview URL, because each
#: deploy gets its own hostname and pinning one would break the next.
CORS_ORIGINS = [
    origin.strip()
    for origin in os.getenv(
        "REPRO_CORS_ORIGINS", "http://localhost:5173,http://127.0.0.1:5173"
    ).split(",")
    if origin.strip()
]
CORS_ORIGIN_REGEX = os.getenv("REPRO_CORS_ORIGIN_REGEX", r"https://.*\.vercel\.app")

app.add_middleware(
    CORSMiddleware,
    allow_origins=CORS_ORIGINS,
    allow_origin_regex=CORS_ORIGIN_REGEX,
    # No cookies and no auth header: nothing here is credentialed, and asking
    # for credentials would forbid the wildcard-ish regex above.
    allow_credentials=False,
    allow_methods=["GET", "POST", "OPTIONS"],
    allow_headers=["content-type"],
)

#: Multiplies every scripted delay on the mock path. 1.0 is demo pacing.
TIME_SCALE = float(os.getenv("REPRO_MOCK_SPEED", "1.0"))

#: Under tests/ because it is test data the UI borrows, not shipped code.
FIXTURE_PATH = Path(
    os.getenv(
        "REPRO_MOCK_FIXTURE",
        Path(__file__).resolve().parents[3] / "tests" / "fixtures" / "mock_run_shopcart.json",
    )
)

#: Where the seeded demo repositories live. Offered by GET /config so the UI
#: does not hard-code a path that only exists in this checkout.
DEMO_REPO_ROOT = Path(__file__).resolve().parents[3] / "fixtures" / "demo_repos"

#: Real runs copy this path and run pytest inside the copy. The endpoint is
#: unauthenticated by design, so it will only do that for repos underneath an
#: allowed root -- otherwise "repo_path" is an arbitrary read of this machine.
#: A cloned repository is always allowed: WE chose that path, not the caller.
#: Set REPRO_ALLOWED_REPOS="" to turn the check off.
ALLOWED_REPO_ROOT = os.getenv("REPRO_ALLOWED_REPOS", str(DEMO_REPO_ROOT))


def allowed_roots() -> list[Path]:
    """Directories a caller-supplied `repo_path` may sit under."""
    roots = [Path(part.strip()) for part in ALLOWED_REPO_ROOT.split(",") if part.strip()]
    return [root.expanduser().resolve() for root in [*roots, DEFAULT_CACHE_ROOT]]


def demo_repos() -> list[dict]:
    """Every seeded repo in the fixtures directory, newest listing on each call."""
    if not DEMO_REPO_ROOT.is_dir():
        return []
    return [
        {"path": str(path), "name": path.name}
        for path in sorted(DEMO_REPO_ROOT.iterdir())
        if path.is_dir()
    ]


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
    """What the web form posts. The rest of ClientReport we fill in.

    One of `repo_url` and `repo_path`, never both and never neither. They are
    two ways to name the same thing -- a project on this machine -- and the URL
    is the one a person actually has to hand on a Monday morning.
    """

    raw_text: str = Field(min_length=1, description="The complaint, verbatim.")
    repo_url: Optional[str] = Field(
        None,
        description="A GitHub repository: https://github.com/owner/repo, owner/repo, "
        "or a link to a branch. Cloned before the run starts.",
    )
    repo_path: Optional[str] = Field(
        None, description="Path to a local checkout, under an allowed root."
    )
    repo_ref: Optional[str] = Field(
        None, description="Branch, tag or commit. Overrides any ref in repo_url."
    )
    reporter_name: Optional[str] = None


class RunAccepted(BaseModel):
    run_id: str


# ---------------------------------------------------------------------------
# A run, from the stream's point of view
# ---------------------------------------------------------------------------

#: Sentinel: the run is over, close the stream.
_DONE = object()


class NoModelCredentials(RuntimeError):
    """The run needed a model and the environment only had recorded answers.

    Raised in place of the cassette-miss error, which is written for whoever
    records cassettes and reads, to everybody else, like the agent broke. The
    cause is nearly always a missing key, and the fix is one line of .env.
    """


class ExpiredCredentials(RuntimeError):
    """The provider had a credential and the provider rejected it."""


#: Substrings that mean "your keys, not your code". The AWS sandbox lease lasts
#: twelve hours, so this is the single most likely failure of a real run, and
#: `ExpiredTokenException` in a stack trace does not tell anyone to re-paste it.
_EXPIRED_MARKERS = (
    "expiredtoken",
    "security token included in the request is expired",
    "unrecognizedclientexception",
    "invalidclienttokenid",
    "authentication_error",
    "invalid x-api-key",
)


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

    def __init__(self, report: ClientReport, repo_ref: RepoRef | None = None) -> None:
        super().__init__(report)
        #: Set when the caller gave a GitHub URL: the repo is fetched here,
        #: before the graph starts, and `report.repo_path` is filled in from it.
        self.repo_ref = repo_ref

    async def play(self) -> None:
        loop = asyncio.get_running_loop()

        def emit_from_thread(event: StreamEvent) -> None:
            # run() calls this from the worker thread, and asyncio.Queue is not
            # thread-safe. Hop back onto the loop before touching a subscriber.
            loop.call_soon_threadsafe(self._emit, event)

        try:
            if self.repo_ref is not None:
                # Minutes of network on a big repository, so it happens on a
                # worker thread with the timeline showing what it is waiting on.
                self._emit(events.prepare_started(self.run_id, f"Fetching {self.repo_ref}"))
                path = await asyncio.to_thread(fetch_repo, self.repo_ref)
                self.report.repo_path = str(path)
                self._emit(
                    events.prepare_finished(
                        self.run_id,
                        f"{self.repo_ref} cloned",
                        repo=str(self.repo_ref),
                        repo_path=str(path),
                    )
                )

            record = await asyncio.to_thread(
                lambda: run_graph(self.report, llm_for(), on_event=emit_from_thread)
            )
        except RepoFetchError as exc:
            # A repository we could not fetch is the caller's problem to fix, and
            # it deserves the message git gave us rather than a traceback.
            self.fail(exc)
        except SchemaValidationError as exc:
            self.fail(_explain_schema_failure(exc))
        except Exception as exc:  # noqa: BLE001 - one bad run must not kill the server
            self.fail(_explain_credentials(exc))
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


def _explain_schema_failure(exc: SchemaValidationError) -> BaseException:
    """Name the real cause when the model was never called in the first place."""
    if resolve_provider() != "fake":
        return exc
    return NoModelCredentials(
        "This run needed a model and there are no credentials, so it fell back to "
        "replaying recorded answers -- which only exist for the seeded demo repo and "
        "its recorded complaint. Set ANTHROPIC_API_KEY (or refresh the AWS keys and set "
        "LLM_PROVIDER=bedrock) in .env and restart the API."
    )


def _explain_credentials(exc: BaseException) -> BaseException:
    """Say "re-paste your keys" when that is what happened, and nothing otherwise."""
    text = f"{type(exc).__name__}: {exc}".lower()
    if not any(marker in text for marker in _EXPIRED_MARKERS):
        return exc
    provider = resolve_provider()
    if provider == "bedrock":
        return ExpiredCredentials(
            "AWS rejected these credentials. Sandbox keys last 12 hours: re-paste all "
            "three of AWS_ACCESS_KEY_ID, AWS_SECRET_ACCESS_KEY and AWS_SESSION_TOKEN "
            "into .env together, then restart the API. `make check-bedrock` proves "
            "they work before you spend a run on them."
        )
    return ExpiredCredentials(
        f"The {provider} credentials were rejected. Check ANTHROPIC_API_KEY in .env "
        "and restart the API."
    )


@lru_cache(maxsize=1)
def load_fixture(path: str | None = None) -> dict:
    return json.loads(Path(path or FIXTURE_PATH).read_text(encoding="utf-8"))


def fixture_repo() -> str:
    """The repository the recorded run is OF, for a mock run to name.

    A mock run replays one recording, so whatever repository the caller asked
    for is not the one on screen: the header said `pallets/flask` while every
    hypothesis under it said `shopcart/pricing.py`. The badge does say it is a
    rehearsal, but a screen that contradicts itself reads as broken rather than
    as replayed. The fixture names its own repository so a future recording of
    something else needs no change here.
    """
    try:
        recorded = str(load_fixture().get("repo") or "").strip()
    except (OSError, ValueError):  # a broken fixture is MockRun's to report
        recorded = ""
    return recorded or str(DEMO_REPO_ROOT / "shopcart")


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
    roots = allowed_roots()
    candidate = Path(repo_path).expanduser().resolve()
    if not any(candidate.is_relative_to(root) for root in roots):
        raise HTTPException(
            status_code=400,
            detail=(
                "repo_path must be inside one of "
                + ", ".join(str(root) for root in roots)
                + ". This endpoint has no auth, so it will not copy an arbitrary "
                "directory off this machine. To investigate a project that is not "
                "there, pass repo_url and let the API clone it."
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


@app.get("/config")
def config() -> dict:
    """What a run started right now would ACTUALLY do.

    The UI reads this to name the model on screen. It is computed from the same
    `resolve_provider` the run itself calls, so the badge cannot say "Haiku"
    while the backend quietly replays a cassette -- which is the difference
    between a demo and a lie.
    """
    provider = "mock" if mock_enabled() else resolve_provider()
    model = model_id_for(provider)
    return {
        "mode": "mock" if mock_enabled() else "live",
        "provider": provider,
        "model_id": model,
        "model": model_label(model) if model else "recorded replies (no model is called)",
        # `fake` is not a real run: it can only answer prompts somebody recorded.
        "live_model": provider in {"anthropic", "bedrock"},
        "credentials": {name: credential_for(name) for name in ("anthropic", "bedrock")},
        "github_token": bool(os.environ.get("GITHUB_TOKEN") or os.environ.get("GH_TOKEN")),
        "install_deps": deps.install_mode(),
        "max_run_usd": MAX_RUN_USD,
        "demo_repos": demo_repos(),
    }


@app.post("/runs", status_code=201, response_model=RunAccepted)
async def create_run(body: NewRun) -> RunAccepted:
    """Start a run and hand back its id. The work happens off this request.

    The repository is only PARSED here, never fetched: a clone is minutes of
    network and this response is what the browser needs before it can open the
    stream. A typo still fails fast, with a 400 that names what is accepted.
    """
    repo_ref = None
    repo_path = (body.repo_path or "").strip()

    if body.repo_url and repo_path:
        raise HTTPException(status_code=400, detail="pass repo_url or repo_path, not both")
    if body.repo_url:
        try:
            repo_ref = parse_repo_ref(body.repo_url)
        except RepoFetchError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc
        if body.repo_ref:
            # An explicitly given ref wins over one embedded in the URL: the
            # person filled in a second box, which is a later decision.
            repo_ref = RepoRef(
                owner=repo_ref.owner, repo=repo_ref.repo, ref=body.repo_ref.strip()
            )
        # Filled in for real once the clone lands; the placeholder is what the
        # timeline shows in the meantime, and it is the thing the user typed.
        repo_path = str(repo_ref)
    elif not repo_path:
        raise HTTPException(
            status_code=400,
            detail="a run needs a repository: pass repo_url (a GitHub repo) or repo_path.",
        )

    if mock_enabled():
        # Nothing is fetched or read on this path, and the recording is of one
        # particular repository -- so that is the one the screen names.
        repo_path = fixture_repo()

    report = ClientReport(
        run_id=uuid.uuid4().hex,
        raw_text=body.raw_text,
        repo_path=repo_path,
        reporter_name=body.reporter_name,
    )
    if mock_enabled():
        run: RunStream = MockRun(report)
    elif repo_ref is not None:
        run = RealRun(report, repo_ref=repo_ref)
    else:
        check_repo_path(repo_path)  # only a real run touches the filesystem
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
