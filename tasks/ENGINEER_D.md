# Engineer D — API & Web Application

**You own the surface a judge actually looks at. You build against a mock until
hour 16 and are therefore never blocked.**

The video is 20% of the grade on its own, and the demo carries "effectiveness"
and "presentation" too. A correct agent with an unwatchable UI scores worse than
a decent agent whose reasoning you can *see*. Your job is to make the agent's
thinking legible: plan, act, adapt, visible on screen.

| | |
|---|---|
| **Files you own** | `src/repro/api/*`, `web/**`, `tests/test_api.py` |
| **Files you may READ but never EDIT** | `src/repro/contracts.py`, `src/repro/graph/build.py` |
| **You depend on** | Engineer A's `run()` at hour 16 only — mock it until then |
| **People depend on you for** | nothing. You are a leaf. |

Branch `feat/web`. Because nobody depends on you, you absorb schedule risk best:
if the team is behind at hour 22, your polish gets cut, not someone else's core.

---

## D1 · Mock API with the real event stream (2 h)

```
Read src/repro/contracts.py, in particular StreamEvent and RunRecord.

Build the FastAPI app in src/repro/api/main.py:

  POST /runs        body {raw_text, repo_path, reporter_name?} -> {run_id}
  GET  /runs/{id}/events   Server-Sent Events, streaming StreamEvent JSON
  GET  /runs/{id}          the full RunRecord once terminal
  GET  /healthz

For now, POST /runs must start a MOCK run: a background task that emits a
realistic, correctly-timed sequence of StreamEvents from a fixture file, then a
terminal RunRecord. Include the realistic delays (intake ~2s, localise ~3s,
each repro attempt ~8s) so the UI is built against real pacing, not instant
responses.

Put the fixture in tests/fixtures/mock_run_shopcart.json with a full sequence
including a FAILED first repro attempt and a successful second, because that
retry is the moment that proves the system is agentic and the UI must handle it.

Write tests/test_api.py using fastapi.testclient: POST returns a run_id, the SSE
stream yields events in order and terminates, GET on an unknown id returns 404.

Do not import repro.graph — you are not wired to the real graph yet.
```

**Done when:** `curl -N localhost:8000/runs/<id>/events` streams a believable run.

---

## D2 · The timeline UI (3 h)

```
Build the React + Vite app in web/. One screen, no router, no auth, no state
library. Vertical timeline that fills in live as SSE events arrive.

Left panel: a textarea pre-filled with the shopcart client complaint, a repo
picker (dropdown of the demo repos), and a Run button.

Right panel, the timeline. Each node appears as a card as its event arrives:
  - INTAKE       -> the extracted facts as labelled fields, with anything the
                    client did NOT say shown greyed out as "not stated"
  - LOCALISE     -> the ranked hypotheses with confidence bars
  - REPRO ATTEMPT 1 -> the generated test in a code block, and a big RED or
                    GREEN badge with the pytest tail
  - REPRO ATTEMPT 2 -> same, appended below, NOT replacing attempt 1
  - FIX          -> the unified diff, syntax highlighted, plus TWO badges side by
                    side: "repro test: GREEN" and "existing suite: GREEN"
  - VERDICT      -> the two handover panes, dev PR body and client reply

Design rules that matter for the video:
- a failed repro attempt must stay on screen, visibly, with the retry below it.
  That visible retry IS the agentic story. Do not collapse or hide it.
- the two green badges on the fix card are the safety property made visual. Make
  them the most prominent thing on the screen.
- red/green must be distinguishable without colour (icons + text), because the
  video will be compressed and judges may be colour-blind.
- no spinner-only states: always name the step in progress, e.g. "running
  pytest in sandbox (attempt 2 of 3)".

Keep it plain and legible: system font stack, generous whitespace, one accent
colour. Do not spend time on a design system.
```

**Done when:** a full mock run plays end to end and is readable at 720p, which
is what your screen recording will be after compression.

---

## D3 · The client-reply panel (60 min)

```
Add a dedicated, visually distinct panel for handover.client_reply, styled like
an email reply rather than a developer tool: sender line, greeting, plain
paragraphs, no monospace.

This is the differentiator against every other bug-fixing agent, and it is the
last thing the judge sees before the video ends. Give it room.

Add a "Copy reply" button. Small touch, but it says the artefact is meant to be
used, not just displayed.
```

**Done when:** you can read the client reply on screen from two metres away.

---

## D4 · Wire to the real graph (60 min, hour 16 — the integration checkpoint)

```
Engineer A's run() is now available. Replace the mock background task in
POST /runs with a real invocation:

    from repro.graph.build import run

Run it in a thread or a background task and translate graph node transitions
into StreamEvents. Keep the mock path behind a MOCK=1 env var — you will want it
back the moment something upstream breaks during rehearsal.

Add the invariant gate: before returning a RunRecord over the API, call
check_invariants(). If non-empty, return the record with the patch stripped and
an explicit "verification failed" event in the stream. The API must never serve
a patch that failed its own invariants, even if the graph somehow produced one.

Add a test that a record with violated invariants is served without a patch.
```

**Done when:** the real agent drives the real UI, and `MOCK=1` still works.

---

## D5 · Demo hardening (60 min, hour 26)

```
Three things, in this order of importance:

1. Reset button that clears state without a page reload, so you can re-record a
   take in ten seconds instead of restarting the server.
2. A visible cost counter in the corner: tokens and USD for the current run,
   live. "This run cost $0.019" on screen during the video is worth a slide.
3. Error states that do not look like crashes: if the backend dies mid-stream,
   show "connection lost — the run is still recorded, reload to see it" rather
   than a blank page.

Then deploy the frontend to Vercel (free tier, keeps it off the AWS budget) and
verify it works against a local backend over ngrok. Do this BEFORE recording
day, not on it.
```

**Done when:** you can complete three consecutive clean takes without touching a
terminal.
