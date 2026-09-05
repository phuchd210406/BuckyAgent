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
real deployment footage, not to run production.

```bash
pip install bedrock-agentcore-starter-toolkit
cd src/repro/agentcore

agentcore configure --entrypoint agent.py --region <the region check_bedrock chose>
#   writes .bedrock_agentcore.yaml, creates the IAM execution role, provisions S3

agentcore launch
#   uploads source, builds the image, pushes to ECR, waits for READY
#   ~10 minutes. Billing starts here. Record your screen for this.

agentcore status
agentcore invoke '{"report": {"run_id":"live","raw_text":"...","repo_path":"..."}}'
```

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
