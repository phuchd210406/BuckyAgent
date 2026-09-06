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
apart from the stream alone — which is why `GET /config` reports which one is
running and the page shows it as a banner. A mock run replays ONE recording, of
the seeded shopcart repo, so it ignores whatever repository the form asked for
and names shopcart instead; a header saying `pallets/flask` above hypotheses in
`shopcart/pricing.py` reads as broken rather than as replayed.

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


## Deploying the frontend

Vercel free tier, so the UI stays off the AWS bill entirely. The project lives
in `web/`, and `vercel.json` sets the build; Vercel detects Vite on its own.

```sh
cd web
vercel login          # once
vercel link           # once, from web/ — this directory is the project root
vercel --prod
```

### The one thing that will catch you out

`VITE_API_BASE` is baked in **at build time, by Vercel's builder**, not read at
runtime. Setting it locally does nothing: Vercel rebuilds from source and your
local `dist/` is ignored. It has to be a project environment variable:

```sh
vercel env add VITE_API_BASE production   # e.g. https://your-tunnel.ngrok-free.app
vercel --prod                             # a redeploy is required; a refresh is not enough
```

With it unset the app calls its own origin, gets Vercel's 404 HTML back, and
shows "Cannot reach the backend" — which is at least honest, but it is not what
you want thirty seconds into a take.

Anonymous `vercel deploy --temporary` deployments **ignore `--build-env`**, so
that path cannot be pointed at a backend at all. Log in first.

### Backend, over a tunnel

The backend stays on a laptop; only the frontend is deployed.

```sh
make api                     # or MOCK=1 make api to rehearse
ngrok http 8000              # or: ssh -R 80:localhost:8000 nokey@localhost.run
```

Then set `VITE_API_BASE` to the tunnel URL and redeploy. CORS is already handled
for `*.vercel.app` — including preview URLs, which get a fresh hostname per
deploy. Other origins go in `REPRO_CORS_ORIGINS` (comma-separated).

### Check it before recording day, not on it

```sh
bash scripts/verify_deploy.sh                       # cross-origin, all local
bash scripts/verify_tunnel.sh https://you.vercel.app # through a real public tunnel
```

Both assert the preflight, the cross-origin POST, the SSE headers, the event
count, the verdict and the cost figures. `verify_deploy.sh` also greps the built
bundle to confirm the API base actually reached it.
