# Deploying the backend to Render — from zero

`docs/DEPLOY.md` covers the AWS/AgentCore path, which is a **one-off, billable,
tear-it-down-the-same-hour** recording. This is the other thing: a URL that
stays up, costs nothing, and that the Vercel frontend can point at.

What gets deployed is `src/repro/api/main.py` — FastAPI, `POST /runs`, the SSE
stream, `GET /healthz`. Nothing else. The frontend stays on Vercel.

---

## Stage 0 — What you need before you start (10 min)

1. **A GitHub account with this repo pushed to it.** Render deploys *from a git
   repo*; there is no "upload a zip". The remote here is already
   `github.com/phuchd210406/BuckyAgent` — confirm your work is pushed:

   ```bash
   git status            # clean?
   git push origin main
   ```

   A private repo is fine. You will authorise Render to read it in Stage 1.

2. **The cassettes must be committed.** The deployed service runs
   `LLM_PROVIDER=fake`, which replays recorded model responses from
   `src/repro/llm/cassettes/`. They are tracked; verify before you deploy,
   because an empty cassette dir deploys perfectly and then fails on the first
   run:

   ```bash
   git ls-files src/repro/llm/cassettes | wc -l     # must be > 0
   ```

3. **The demo fixture must be committed** — `check_repo_path` only lets a run
   touch `fixtures/demo_repos/`, and that is also the only repo the cassettes
   know about:

   ```bash
   git ls-files fixtures/demo_repos | wc -l         # must be > 0
   ```

4. **No AWS anything.** No account, no keys, no `.env`. That is the point of
   `LLM_PROVIDER=fake`, and it is why this deployment can outlive the sandbox
   lease.

---

## Stage 1 — Create the Render account (5 min)

1. Go to <https://render.com> → **Get Started**.
2. Choose **Sign up with GitHub**. Signing up with GitHub rather than
   email/password saves you a separate connect step later, and Render only asks
   for repo read access.
3. GitHub shows an authorisation screen. Under **Repository access**, either:
   - *All repositories*, or
   - *Only select repositories* → pick **BuckyAgent**.

   Prefer the second. You can add repos later at
   GitHub → Settings → Applications → Render.
4. Verify your email when the message arrives.
5. Render asks for a workspace name and a plan. Pick the **Hobby / free** plan.
   It does **not** ask for a credit card on the free plan — if a card prompt
   appears, you are on the wrong plan; back out and choose Hobby.

---

## Stage 2 — Create the web service (10 min)

There are two ways. The Blueprint is fewer clicks and it is version-controlled;
the manual path is easier to debug the first time. Both produce the same thing.

### Option A — Blueprint (uses the committed `render.yaml`)

```bash
git add render.yaml && git commit -m "deploy: render blueprint for the API" && git push
```

Then Dashboard → **Blueprints** → **New Blueprint Instance** → select the
**BuckyAgent** repo → Render reads `render.yaml` and shows one service,
`repro-api`. It will prompt for the one variable marked `sync: false`:

- `REPRO_CORS_ORIGINS` — you do not know the Vercel URL yet. Put
  `http://localhost:5173` for now and fix it in Stage 4.

**Apply**.

### Option B — By hand from the Dashboard

Dashboard → **New +** → **Web Service** → connect the **BuckyAgent** repo, then
fill in exactly this:

| Field | Value |
|---|---|
| Name | `repro-api` |
| Language / Runtime | **Python 3** |
| Branch | `main` |
| Root Directory | *(leave empty — the repo root IS the build context)* |
| Build Command | `pip install -r requirements.txt` |
| Start Command | `uvicorn repro.api.main:app --host 0.0.0.0 --port $PORT` |
| Instance Type | **Free** |
| Region | Singapore (or whichever is nearest you) |

Then **Advanced** → **Add Environment Variable**, four times:

| Key | Value |
|---|---|
| `PYTHONPATH` | `src` |
| `PYTHON_VERSION` | `3.12.3` |
| `LLM_PROVIDER` | `fake` |
| `REPRO_WORKSPACE` | `/tmp/repro-workspaces` |

and **Health Check Path** → `/healthz`.

**Create Web Service**.

### Why each of those four is load-bearing

* **`PYTHONPATH=src`** — `repro` is deliberately not pip-installed (see the
  Makefile header), and Render's start command runs from the repo root, so
  `import repro` fails without it. It fails *at import time*, before uvicorn
  binds `$PORT`, so the symptom Render shows you is the misleading
  **"Port scan timeout: no open ports detected"**, not an ImportError. If you
  see that message, this variable is the first thing to check.
* **`PYTHON_VERSION`** — `pyproject.toml` says `requires-python >=3.11`. Pin it
  instead of inheriting whatever Render defaults to this month.
* **`LLM_PROVIDER=fake`** — replays cassettes. `bedrock` would need AWS keys
  that expire every 12 hours, and would spend real money on every page visit.
* **`REPRO_WORKSPACE=/tmp/...`** — a real run copies the demo repo and runs
  pytest inside the copy. `/tmp` is writable on a free instance and is wiped on
  restart, which is exactly what a disposable workspace wants.

`--host 0.0.0.0` and `--port $PORT` are not optional either: Render assigns the
port at runtime and only routes traffic to a process listening on all
interfaces.

---

## Stage 3 — Watch the first deploy and verify it (5 min)

The **Logs** tab streams the build. Expect roughly:

```
==> Cloning from https://github.com/phuchd210406/BuckyAgent
==> Using Python version 3.12.3
==> Running build command 'pip install -r requirements.txt'
    ... (~2-4 min: langgraph, boto3, fastapi, bedrock-agentcore)
==> Running 'uvicorn repro.api.main:app --host 0.0.0.0 --port 10000'
    INFO:     Uvicorn running on http://0.0.0.0:10000
==> Your service is live 🎉
```

Your URL is at the top of the service page:
`https://repro-api-XXXX.onrender.com`.

Verify it in that order — health, then a real run:

```bash
API=https://repro-api-XXXX.onrender.com

curl -s $API/healthz
#   {"ok":true,"mode":"live"}

# The cassettes were recorded against ONE exact complaint. Send anything else
# and the run starts, then dies with "No cassette ... for schema ReportFacts".
REPORT="hi, i tried to buy stuff this morning and it charged me postage even though the site says free postage over \$50. my basket was definitely more than \$50. can you sort it out, we have customers complaining"

RUN=$(curl -s -X POST $API/runs -H 'content-type: application/json' \
  -d "{\"raw_text\":\"$REPORT\",\"repo_path\":\"fixtures/demo_repos/shopcart\"}" \
  | python3 -c 'import sys,json;print(json.load(sys.stdin)["run_id"])')

curl -s -N $API/runs/$RUN/events | head -40      # the SSE stream, live
curl -s $API/runs/$RUN | python3 -m json.tool | head -20
```

`202` means still running; poll again. A `verdict` field means it finished.

---

## Stage 4 — Point the frontend at it (10 min)

Two edits, one on each side, and **both need a rebuild** — `VITE_API_BASE` is
baked in at build time, not read at page load.

1. **Vercel** → your `repro` project → Settings → Environment Variables:

   ```
   VITE_API_BASE = https://repro-api-XXXX.onrender.com
   ```

   No trailing slash. Then **Redeploy** (a refresh is not enough).

2. **Render** → `repro-api` → Environment → set `REPRO_CORS_ORIGINS` to your
   production Vercel URL, e.g. `https://repro.vercel.app`. Saving triggers a
   redeploy on its own.

   Preview deploys already work without this: `main.py` allows every
   `https://*.vercel.app` origin by regex. This variable is for the production
   hostname and any custom domain.

Open the Vercel URL, submit the demo complaint, and watch the timeline fill in.

---

## What "free" actually costs you

| | |
|---|---|
| Spin-down | after **15 minutes** of no requests |
| Cold start | **~50 s** on the next request |
| RAM | 512 MB |
| Build minutes | limited per month; each push to `main` spends some |
| Bandwidth | 100 GB/month — irrelevant here |

The cold start is the one that bites during a demo. The first click after a
quiet spell hangs for the better part of a minute and looks like a hang, not a
wait. **Warm it up before you present:**

```bash
curl -s https://repro-api-XXXX.onrender.com/healthz
```

Do that thirty seconds before you share your screen. Do not "solve" this with a
cron job that pings every 10 minutes — it keeps the instance up permanently,
burns your free hours, and Render treats it as abuse of the free tier.

---

## Two modes worth knowing

| | `LLM_PROVIDER=fake` (default here) | `MOCK=1` |
|---|---|---|
| What runs | the real graph, replayed model calls | a scripted fixture replay |
| Real pytest in a sandbox | yes | no |
| Works for any complaint text | **no** — one recorded wording only | yes, any text, any `repo_path` |
| Verdict reached | `reproduced_not_fixed` (patch cassettes have drifted) | `reproduced_and_fixed` |
| Honest to demo as "the agent ran" | yes | **no** — it is a recording |

Both were verified on this code against the exact Render start command.

Set `MOCK=1` in the Render environment if you want a URL that always tells the
whole story for any input — but say on camera that it is a replay. The
defensible demo of the real red→green is `make demo` locally, or the `fake` path
above with the recorded complaint.

---

## Failure playbook

| Symptom | Cause | Fix |
|---|---|---|
| `Port scan timeout: no open ports detected` | app crashed at import, usually a missing `PYTHONPATH=src` | check Logs for the traceback above the timeout |
| `ModuleNotFoundError: No module named 'repro'` | same | `PYTHONPATH=src` |
| Deploy live, but `/runs` 500s with `No cassette ... ReportFacts` | complaint text is not the recorded one | send the exact wording from Stage 3 |
| `/runs` returns 400 `repo_path must be inside ...` | `repo_path` outside `fixtures/demo_repos` | use `fixtures/demo_repos/shopcart` |
| Browser console: CORS blocked | `REPRO_CORS_ORIGINS` unset or wrong host | set it to the exact Vercel origin, no trailing slash |
| Frontend still calls `localhost` | `VITE_API_BASE` is baked at build time | set it in Vercel, then **redeploy** |
| First request of the day hangs ~50 s | free-tier spin-down | warm with `/healthz` first |
| Build fails on `bedrock-agentcore` | Python too old | `PYTHON_VERSION=3.12.3` |
