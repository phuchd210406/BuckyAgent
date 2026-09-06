# Deployment — step by step

Written against `AWS_setup.md`. The budget is **US$20 for the whole team, once,
non-renewable**. Access is revoked at $20 and the account is destroyed at $30.
Every instruction below is shaped by that.

## Rule zero: the cost model

Your only meaningful AWS cost should be Bedrock tokens. At Haiku's $1/$5 per
million and ~15k tokens per agent run, **a run costs roughly two cents**. You
could run the agent a thousand times and still be under budget.

What actually kills teams is leaving something running. Re-read the AWS deck's
"Pick your services well" slide and note that not one of the forbidden services
appears in our architecture. That is not a coincidence — it was a design input.

**Two people must never both be experimenting against Bedrock at once without
saying so.** Nominate one person as budget owner; they check the lease bar in
the Innovation Sandbox portal every three hours and post the number in the team
channel. Remember the bar lags several hours, so treat $12 as your real ceiling.

---

## Stage 0 — Before anyone writes agent code (30 minutes, hour 0)

Do this **first**. It is the single most common way a hackathon team loses six
hours.

1. **Lease the account.** One person only. Access portal →
   `https://d-9667b91afb.awsapps.com/start`, username
   `hackathon2026,<leader-email>` (note the comma, no spaces). Set up the
   authenticator app, then **share the 2FA secret key, the password, and the
   username with all five teammates** so nobody is blocked on one phone.
2. **Request the lease** — Applications tab → Innovation Sandbox Ignite
   Hackathon Application → Request a new lease → template `Hackathon 2026` →
   accept ToS → Submit. Approval can take up to 2 working days, so **this
   happens before anything else, ideally days ahead.**
3. **Enable model access.** Bedrock console → Model access → Claude Haiku 4.5.
   Do it in *both* candidate regions if the console lets you.
4. **Settle the region question empirically:**
   ```bash
   pip install -r requirements.txt
   python scripts/check_bedrock.py
   ```
   The two decks disagree — Session 1 says model access is in `ap-southeast-1`,
   the AWS deck's troubleshooting slide says make sure you are in `us-east-1`.
   `check_bedrock.py` tries both and tells you which one actually answers. Put
   the winner in `.env` and **post it in the team channel**. A whole afternoon
   can disappear into `AccessDeniedException` that is only ever a region.
5. **Warn everyone about the 12-hour key expiry.** Sandbox keys die every 12
   hours. In a 30-hour build every engineer will re-paste the three `export`
   lines at least twice. When someone says "it broke and I changed nothing",
   this is the first thing to check.

---

## Stage 1 — Local development (hours 1–24, costs nothing)

This is where 90% of the work happens, and almost all of it needs no AWS at all.

```bash
git clone https://github.com/<your-org>/repro.git && cd repro
python -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt
cp .env.example .env          # leave LLM_PROVIDER=fake

make test                     # contract + invariant tests
make demo                     # full agent run, replayed, $0.00
make api                      # http://localhost:8000
```

`LLM_PROVIDER=fake` is the default on purpose. Only Engineer C, and only while
recording cassettes, should ever have `bedrock` in their `.env`.

### Recording cassettes (hour ~20, costs ~$0.50 total)

```bash
export AWS_ACCESS_KEY_ID=...      # fresh from the access portal
export AWS_SECRET_ACCESS_KEY=...
export AWS_SESSION_TOKEN=...
REPRO_RECORD=1 LLM_PROVIDER=bedrock make record
git add src/repro/llm/cassettes && git commit -m "record cassettes"
```

Commit the cassettes. They are your demo insurance policy.

---

## Stage 2 — Local AgentCore contract (hour 25, still free)

Session 2 §6 blesses local-first. Prove the deployment contract without paying:

```bash
make serve                          # starts src/repro/agentcore/agent.py on :8080
curl -X POST http://localhost:8080/invocations \
  -H 'Content-Type: application/json' \
  -d '{"report": {"run_id":"demo","raw_text":"checkout charges postage even though it says free over $50","repo_path":"fixtures/demo_repos/shopcart"}}'
curl http://localhost:8080/ping
```

If both respond, the deployed version will behave identically. The decorator is
the whole integration.

---

## Stage 3 — Deploy to AgentCore Runtime (hour 26, ~30 min, billable)

Do this **once**, screen-record it while it happens, and tear it down the same
hour. You are deploying to satisfy the technical-quality criterion and to have
real deployment footage, not to run production. Read the whole stage before you
start: one of the two deployment types silently disables the agent's own safety
check, and which one you want depends on what the footage has to show.

**Run every command from the repository root.** `agentcore configure` uses your
current directory as the build context and derives what it runs from the
entrypoint path *relative to that directory*. Run from `src/repro/agentcore` it
packages two files, `agent.py` and `__init__.py`, and nothing else: no
dependency file (the CLI stops and asks for one), and no `repro` package.
Answering that prompt does not save you — the build then SUCCEEDS and the
runtime dies with `ModuleNotFoundError: No module named 'repro'`, after the
billable build.

```bash
pip install 'bedrock-agentcore-starter-toolkit==0.3.12'   # also pinned in requirements.txt
cd "$(git rev-parse --show-toplevel)"      # the build context. Not src/repro/agentcore.

agentcore configure --entrypoint src/repro/agentcore/agent.py --requirements-file requirements.txt --region us-east-1
```

It is interactive. The answer that matters is the first one:

```
Select deployment type:
  1. Direct Code Deploy (recommended) - Python only, no Docker required
  2. Container - For custom runtimes or complex dependencies
```

Everything else can take its default (auto-create the execution role and the S3
bucket, IAM auth, short-term memory). It will also tell you Python 3.14 is not
supported and pick 3.11, which is fine — `requires-python` is `>=3.11`.

Then check what it wrote. This replaces looking for a Dockerfile, which only
exists on the Container path:

```bash
grep -E 'entrypoint:|source_path:|deployment_type:' .bedrock_agentcore.yaml
#   source_path: must be the REPOSITORY ROOT. That is the whole check.
#   entrypoint:  must be .../src/repro/agentcore/agent.py
```

`configure` also prints `Expanding build context to include dependencies:
.../src/repro/agentcore -> .../BuckyAgent`. That line is it doing the right
thing.

### Choose the deployment type with your eyes open

Decide before you answer that prompt: **the choice is one-way per agent.** The
toolkit refuses to change it later —

```
❌ Cannot change deployment type from 'direct_code_deploy' to 'container'
   for existing agent 'src_repro_agentcore_agent'.
```

— and the two types differ in what they can verify for free, which matters more
than it looks. Check first:

```bash
command -v docker >/dev/null && docker info >/dev/null 2>&1 \
  && echo "docker usable"  || echo "no docker: Container cannot be dry-run locally"
command -v uv >/dev/null && echo "uv present"  || echo "no uv: Direct Code Deploy cannot be dry-run locally"
```

| | Direct Code Deploy | Container |
|---|---|---|
| Cloud build | code zip, uploaded | ARM64 via CodeBuild — **no local Docker needed** |
| Free local dry run | needs `uv` | needs **Docker** |
| `fixtures/.../shopcart/tests` ships | **no** (see below) | yes, with your own `.dockerignore` |
| Cloud endpoint can return `reproduced_and_fixed` | **no** | yes |

The trap is the second row. On a machine without Docker, Container still
deploys fine — but you lose the free dry run, so the first time you discover a
wrong `PYTHONPATH` is ten minutes into a **billable** CodeBuild. On a ~30-minute
billable budget that is the worse risk, so prefer Direct Code Deploy unless the
cloud endpoint genuinely has to produce a fix, and if it does, install Docker
first rather than flying blind.

**Direct Code Deploy** ships a code zip. Its file list is filtered by a
`dockerignore.template` **bundled inside the toolkit** — not by any
`.dockerignore` you write — and that template excludes `tests/` at *every*
depth, pruning the directory during the walk. Run the toolkit's own packager
logic over this repo and the demo project arrives like this:

```
shopcart files that ship:
    fixtures/demo_repos/shopcart/shopcart/__init__.py
    fixtures/demo_repos/shopcart/shopcart/pricing.py
  shopcart TESTS shipped: NONE
```

That breaks the product's safety property in a way that still looks like it
works. `run_suite()` returns `no tests ran` (exit 5) on arrival; once the agent
writes its own repro test, the "existing suite" it verifies a patch against
consists solely of the test it just wrote. `must_not_break` from
`fixtures/BUGS.md` stops being checked by anything, and the run still reports a
verdict. **A deployed Direct Code Deploy agent cannot honestly return
`reproduced_and_fixed`.**

That is acceptable if Stage 3 is for footage — record the deploy, invoke it,
tear it down, and demo the real red→green from `make demo` or the local API,
which have the whole fixture. Do not claim on camera that the cloud endpoint
produced the fix.

**Container** builds an image and *does* honour a `.dockerignore` you write
(the toolkit only generates one if none exists). Take this path if the cloud
endpoint has to do a real end-to-end run:

```bash
TOOLKIT=$(python -c 'import bedrock_agentcore_starter_toolkit as t, os; print(os.path.dirname(t.__file__))')
cp "$TOOLKIT/utils/runtime/templates/dockerignore.template" .dockerignore
sed -i '/^tests\/$/d' .dockerignore      # keep fixtures/demo_repos/shopcart/tests
grep -c '^tests/$' .dockerignore          # must print 0

# then re-run configure and choose 2. Container
```

Its cloud build runs on CodeBuild, so no local Docker is required to *deploy* —
only to dry-run it first. Budget about fifteen extra minutes.

If you already configured this agent as Direct Code Deploy, configure will
refuse to switch. Clear the local state first — but **check that nothing was
provisioned before you do**, or you will orphan live resources that keep
costing money with no config left pointing at them:

```bash
grep -E 'agent_id:|agent_arn:|execution_role:|s3_path:|ecr_repository:|memory_id:' \
  .bedrock_agentcore.yaml
#   every one must be `null`. If any is not, use `agentcore destroy` instead.

rm .bedrock_agentcore.yaml
rm -rf .bedrock_agentcore/
```

`configure` only ever says "*Will* auto-create" — nothing exists until `launch`,
so a config you never launched is safe to delete outright.

### Prove it locally before you pay for it

```bash
agentcore launch --local --env PYTHONPATH=src --env LLM_PROVIDER=stub

curl -s localhost:8080/ping
curl -s -X POST localhost:8080/invocations -H 'Content-Type: application/json' \
  -d '{"report":{"run_id":"local","raw_text":"charged postage","repo_path":"fixtures/demo_repos/shopcart"}}'
```

`LLM_PROVIDER=stub` needs no AWS and no cassettes (see `agent.py`), so this is
free and offline, and it is where a broken `PYTHONPATH` costs two minutes
instead of a billable build. Do not skip it.

It has a prerequisite that differs by deployment type: Direct Code Deploy runs
the script through **`uv`**, Container builds and runs the image through
**Docker**. If the one you chose is missing, this step does not degrade — it
refuses, and your first real verification becomes the billable launch. On the
Container path, also confirm the fixture survived:

```bash
docker run --rm --entrypoint ls <image> fixtures/demo_repos/shopcart/tests
```

### Then the billable launch

`launch` was renamed `deploy` in starter-toolkit 0.3.x (`agentcore deploy`,
`agentcore deploy --local`). Both names work today; the toolkit prints a
deprecation notice for the old one, alongside a louder notice that the starter
toolkit itself is superseded by the `@aws/agentcore` npm CLI. Do not switch CLIs
at hour 26 — set `AGENTCORE_SUPPRESS_RECOMMENDATION=1` and keep moving.

```bash
agentcore launch --env PYTHONPATH=src --env LLM_PROVIDER=bedrock
#   uploads source, builds, waits for READY
#   ~10 minutes. Billing starts here. Record your screen for this.

agentcore status
agentcore invoke '{"report": {"run_id":"live","raw_text":"...","repo_path":"fixtures/demo_repos/shopcart"}}'
```

Both `--env` flags are load-bearing on **both** deployment types, and neither
can be dropped:

* `PYTHONPATH=src` — `repro` is deliberately not pip-installed (see the Makefile
  header), and neither deployment type puts the repo root on `sys.path`:
  Container runs `python -m src.repro.agentcore.agent`, Direct Code Deploy runs
  the script by path, so `sys.path[0]` is `src/repro/agentcore/`. Both fail on
  `from repro import clients` without it. It cannot be fixed through the
  requirements file either — the generated Dockerfile copies *only* the
  dependency file and installs it BEFORE `COPY . .`, so a `.` entry fails with
  no `pyproject.toml` in the image yet, and pointing `--requirements-file` at
  `pyproject.toml` installs `repro` but none of the runtime dependencies,
  because that file has no `[project.dependencies]`.
* `LLM_PROVIDER=bedrock` — `agent.py` defaults to `fake`, which replays
  cassettes. Deployed without this you get "No cassette", not a real run.

Then, **the same hour, without fail:**

```bash
agentcore destroy
```

Teardown is not complete — S3 buckets, ECR repositories and CloudWatch log
groups can survive `destroy`. Check the console and delete the ECR repo and the
S3 bucket by hand. A stale ECR repository is small money but it is money that
keeps ticking after the hackathon ends.

---

## Stage 4 — The frontend (free, off the AWS bill entirely)

```bash
cd web && npm install && npm run build
npx vercel --prod
```

Point `VITE_API_BASE` at your local FastAPI (via `ngrok` for the recording) or
at the AgentCore endpoint. Vercel's free tier keeps the entire UI outside the
$20, which is why the frontend is not on AWS.

---

## Stage 5 — Submission packaging (hour 28)

```
repro-submission/
├── repro/                 # git archive of main, .env EXCLUDED, .venv EXCLUDED
├── README.md              # run instructions + file-by-file overview
├── deck.pdf               # 10 slides
└── demo.mp4               # 5 minutes
```

```bash
git archive --format=zip --output ../repro-src.zip HEAD    # honours .gitignore
```

Checklist against the deck's "Project Files — Key Points":

- [ ] README covers **how to run** and **what each script/file is for**
- [ ] `requirements.txt` present
- [ ] Secrets in `.env`, `.env` gitignored, `.env.example` committed
- [ ] Python ✔
- [ ] The solution runs **exactly as shown in the video** on a clean clone
- [ ] Inline documentation where the methodology shows at code level — the
      docstrings in `contracts.py` and the invariant checker are where a judge
      will look to see whether your slides are true of your code
- [ ] Testing/evaluation covered in the slides (see `EVALUATION.md`)
- [ ] Under 5 GB, one submission

## Failure playbook

| Symptom | Cause | Fix |
|---|---|---|
| `AccessDeniedException` on Bedrock | wrong region, or model access off | `python scripts/check_bedrock.py` |
| Worked an hour ago, now `NoCredentialsError` | 12h key expiry | re-paste the three exports |
| `ValidationException: model id` | built the id in code | ids live in `settings.py` only |
| Budget bar jumped | someone looped without a cap | check `MAX_TOTAL_LLM_CALLS`, kill it |
| Lease expired / account frozen | budget hit $20 | **no second lease.** Fall back to `LLM_PROVIDER=fake` and demo from cassettes. This is exactly why they exist. |
