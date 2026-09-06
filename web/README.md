# Repro — web

One screen. No router, no auth, no state library: the whole app is `App.jsx`
plus a reducer that turns the SSE event log into an ordered list of cards.

## Run it

Two terminals, from the repo root:

```sh
make api                               # FastAPI on :8000 — REAL runs
cd web && npm install && npm run dev   # Vite on :5173
```

`make api` invokes the real graph. For UI work, and for rehearsal when
something upstream is broken, replay the fixture instead:

```sh
MOCK=1 make api                        # no AWS, no repo, no cost
MOCK=1 REPRO_MOCK_SPEED=0.1 make api   # ...and ten times faster
```

Both paths emit through `repro.graph.events`, so the browser cannot tell them
apart. `GET /healthz` reports which one is running.

Open http://localhost:5173 and press **Run**. Vite proxies `/runs` and
`/healthz` to :8000, so the browser stays on one origin and the API needs no
CORS. A mock run takes ~33 seconds, which is deliberate: it is the pacing of a real
run.

## What the screen has to get right

* **A failed repro attempt stays on screen, with the retry below it.** That
  visible retry is the agentic story. `buildCards` appends a new card per
  attempt and never reopens a finished one.
* **The two green badges on the fix card are the loudest thing on the page.**
  They are the product's safety property: a patch is accepted only when the
  repro test went red→green *and* the existing suite stayed green.
* **Red and green never carry meaning alone** — every badge has an icon, a
  word, and a border weight, because the video is compressed and judges may be
  colour-blind.
* **No spinner-only states.** Every moment names its step, including the gaps
  between nodes where the routers run.
