# The slides

Task E4 in `tasks/ENGINEER_E.md`. Session 3's sample flow, one message per
slide, every number carrying its source in the footer rail of the slide itself.

Eleven slides, not ten: "Technical architecture" is split into **05, the agent
graph** and **06, the guardrails**, because the graph is the slide a technical
judge reads rather than listens to and it needs the whole canvas. Slide 05 is a
hand-authored inline SVG redrawing of the diagram in `docs/ARCHITECTURE.md`,
laid out from `graph/build.py` — `build_graph:352-376` and the three routers —
so the branches, both self-loops and all three no-patch exits are the ones the
code actually takes.

**Published (with speaker notes and a present mode):**
https://claude.ai/code/artifact/2d1d50a5-cd28-4c21-a40e-ee6ad7d842df

`deck.html` is the source. The three `__IMG1__`/`__IMG2__`/`__IMG3__` tokens are
placeholders for the screenshots, inlined as base64 `data:` URIs at publish time
because the artifact CSP blocks external images:

```bash
python3 - <<'PY'
import base64, pathlib
html = pathlib.Path("docs/deck/deck.html").read_text()
for token, path in {
    "__IMG1__": "docs/deck/screenshots/01-first-repro-attempt-failed.png",
    "__IMG2__": "docs/deck/screenshots/02-two-green-badges.png",
    "__IMG3__": "docs/deck/screenshots/03-client-reply.png",
}.items():
    raw = pathlib.Path(path).read_bytes()
    html = html.replace(token, "data:image/png;base64," + base64.b64encode(raw).decode())
pathlib.Path("/tmp/deck-inlined.html").write_text(html)
PY
```

Open the result in a browser, or print to PDF — the print stylesheet puts one
slide per page.

## The screenshots

`screenshots/capture.mjs` drives the running UI in headless Chrome over the
DevTools protocol and captures the three moments slide 8 needs. No dependencies:
Node 24 ships a global `WebSocket`.

```bash
MOCK=1 REPRO_MOCK_SPEED=0.2 REPRO_CORS_ORIGINS=http://localhost:5199 \
  PYTHONPATH=src .venv/bin/python -m uvicorn repro.api.main:app --port 8010 &
cd web && VITE_API_BASE=http://127.0.0.1:8010 npx vite --port 5199 &
google-chrome --headless=new --remote-debugging-port=9333 \
  --user-data-dir=/tmp/deck-chrome --window-size=1440,1000 about:blank &
node docs/deck/screenshots/capture.mjs /tmp/shots
```

The committed images are from **rehearsal replay** (`MOCK=1`), which replays the
recorded shopcart run at its real pacing and calls no model. That is stated on
the slide. Retake them from a live run before recording the video.

## Where the deck and `docs/ARCHITECTURE.md` disagree, and why the deck is right

The slides were checked line by line against the code. Two things ARCHITECTURE.md
describes are not built, so the deck does not claim them:

* **Escalating to Sonnet on the final attempt.** Both clients fix their model at
  construction (`llm/bedrock.py:116`, `llm/anthropic_api.py:59`); nothing anywhere
  selects `settings.SONNET`, which appears only in `PRICES` and `MODEL_LABELS`.
  The deck says "one model per run", and slide 5's speaker notes carry a
  DO NOT CLAIM warning.
* **Three clarifying questions.** `agents/clarify.py:48` asks for one
  `ClarifyingQuestion` and returns `{"questions": [question]}`. The module's own
  docstring flags the divergence and leaves the choice to the lead. The deck says
  one question.

Either fix the doc or build the feature — but until one of those happens, the
slides match the code and the doc does not.

Two more corrections made in the same pass: the sandbox seam is **seven** methods
expressing four capabilities (`graph/sandbox_seam.py:85-98`), not four methods;
and `RunRecord` is at `contracts.py:260`.

## Two things still owed before this deck is final

1. **Slide 2's interview quote.** `docs/PROBLEM_STATEMENT.md` says the two
   interviews have not happened. The slide carries an amber box with the exact
   question to ask rather than an invented quote. If the interviews do not
   happen, cut the box — do not fill it.
2. **Slide 7's live numbers.** The six-metric table is scored over the committed
   `RunRecord` fixtures, and `eval/results.md` says in as many words not to put
   that table on a slide as though it were measured. The slide says so too, and
   names the one figure that *is* measured — $0.014147, from
   `eval/recorded_runs.json`. Run `make eval-live` and replace the table.
