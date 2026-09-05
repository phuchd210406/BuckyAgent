# Architecture

## One picture

```
  client complaint (free text)  +  repo path
              |
              v
  +-----------------------------------------------------------------+
  |                      LangGraph  ·  typed state                   |
  |                                                                  |
  |   [INTAKE]  extract ReportFacts ------> confidence < 0.6         |
  |      |                                  or missing critical      |
  |      |                                          |                |
  |      |                                          v                |
  |      |                                   [CLARIFY]  <= 1 round   |
  |      |                                   3 plain-English Qs      |
  |      |                                   --> PAUSE, ask human    |
  |      v                                                           |
  |   [LOCALISE]  code search + git log ---> <=5 typed Hypotheses    |
  |      |                                   (paths + 2-line reason, |
  |      |                                    never file contents)   |
  |      v                                                           |
  |   [REPRO] write failing test -> RUN IT IN SANDBOX               |
  |      |         |                                                 |
  |      |         +-- test went GREEN? we did NOT reproduce.        |
  |      |             revise hypothesis, loop  ---------+           |
  |      |             (hard cap 3, counter in state)    |           |
  |      |                                               |           |
  |      +-- test went RED for the right reason ---------|--+        |
  |                                                      |  |        |
  |              +---------------------------------------+  |        |
  |              v                                          v        |
  |        [REPORT] verdict=NOT_REPRODUCED            [FIX] patch    |
  |              ^                                          |        |
  |              |                                     RUN TWICE:    |
  |              |                                  1. repro test    |
  |              |                                     must go GREEN |
  |              |                                  2. existing suite|
  |              |                                     must stay GRN |
  |              |                                          |        |
  |              +-- 3 attempts, still not both green ------+        |
  |                                                         |        |
  |        [REPORT] dev PR body + plain-language client reply        |
  +-----------------------------------------------------------------+
              |
              v
      RunRecord (full audit trail, token cost, replayable)
```

## The five nodes

| Node | Agent class (Session 3 taxonomy) | Input | Output | Model |
|---|---|---|---|---|
| `intake` | Extraction · Parse & Transform | raw complaint | `ReportFacts` | Haiku |
| `clarify` | Information · Answer & Advise | `ReportFacts.missing` | ≤3 `ClarifyingQuestion` | Haiku |
| `localise` | Decision-Support · Guide & Recommend | facts + search hits | ≤5 `Hypothesis` | Haiku |
| `repro` | Transaction · Do & Automate | hypotheses | `TestArtifact` + real `ExecutionResult` | Haiku, Sonnet on attempt 3 |
| `fix` | Transaction · Do & Automate | red test + hypothesis | `Patch` + two `ExecutionResult`s | Haiku, Sonnet on attempt 3 |
| `report` | Creative/Generative · Create & Draft | whole `RunRecord` | `Handover` (two audiences) | Haiku |

Escalating to Sonnet only on the final attempt is a deliberate cost/capability
trade: Haiku at $1/$5 per million handles most runs, and we pay the $3/$15 rate
only for the ~15% of runs that are about to fail anyway.

## The four things that keep it alive at turn fifty

Straight from Session 3's "Building Agents That Hold Up":

1. **Context rot** — each node is single-purpose and exits. Nothing accumulates
   a fifty-turn transcript. The state carries typed objects, not chat history.
2. **Bound every loop** — `MAX_REPRO_ATTEMPTS`, `MAX_FIX_ATTEMPTS`,
   `MAX_CLARIFY_ROUNDS`, `MAX_TOTAL_LLM_CALLS`, `MAX_RUN_USD`. All in
   `contracts.py`, all read by routers in plain Python, none of them consulting
   the model's opinion about whether it is nearly done.
3. **Descriptions are the interface** — every `Field(description=...)` in
   `contracts.py` is prompt text. Review them as prompts.
4. **Keep payloads small** — the model never sees a whole file. `search()`
   returns capped snippets; `ExecutionResult` carries tails only
   (`SANDBOX_MAX_OUTPUT_CHARS = 4000`), because a pytest traceback dump would be
   re-read on every subsequent turn of the loop.

## Guardrails

- **Sandbox.** The target repo is copied to `/tmp/repro-workspaces/<run_id>`.
  The original is never written to. Path traversal outside the workspace raises.
  `subprocess` with a hard 60s timeout and no network.
- **Allow-list, not deny-list.** The agent has exactly four capabilities:
  read a file, write a file under `tests/`, run pytest, apply a unified diff.
  There is no shell tool. There is no `pip install`. There is no network tool.
- **Never auto-merge.** The output is a patch and a PR body. A human presses
  merge. `compliance.human_in_the_loop` is the design, not a setting.
- **Budget kill-switch.** `MAX_RUN_USD` is checked after every model call. The
  run aborts with `Verdict.ABORTED_BUDGET` rather than discovering the overrun
  on the AWS console two days later (Cost Explorer lags ~48h).

## Stack, and why each piece was chosen against the constraints

| Layer | Choice | Why |
|---|---|---|
| Model | Bedrock Claude Haiku 4.5 via `converse` | Session 1's stated default. $1/$5 per Mtok. |
| Orchestration | LangGraph | Session 2 §4. Flowchart control is exactly right when a wrong branch means shipping a bad patch. |
| Schemas | Pydantic v2 | Session 1: typed object back, not a string to parse that breaks the day a model wraps JSON in a code fence. |
| Sandbox | `subprocess` + `tempfile` | Zero infra. No Docker dependency on the demo laptop. |
| Retrieval | Local BM25-ish scoring over the workspace | **Deliberately not S3 Vectors or OpenSearch.** The AWS deck: OpenSearch minimum always-on capacity costs hundreds a month. Our corpus is one repo, in a temp dir, for sixty seconds. A vector DB here is a budget bonfire and an architecture smell. |
| API | FastAPI + SSE | Streams `StreamEvent`s so the judge watches the agent think. |
| Frontend | Vite + React on **Vercel free tier** | Keeps the entire UI off the AWS bill. The USD 20 buys tokens and nothing else. |
| Storage | JSON files under `runs/` | DynamoDB on-demand would also be near-free, but files are one fewer thing to break at hour 27, and the audit trail is git-diffable. |
| Deploy | AgentCore Runtime, once, recorded, then `destroy` | Session 2 §6. Serverless, one microVM per session, no Dockerfile to write. |

## What we are explicitly NOT building

Saying this out loud on a slide is a strength, not a weakness — it is the
difference between "partially functional" and "functional prototype with minor
work needed" on the technical-quality rubric.

- Not multi-language. Python + pytest only.
- Not multi-repo, not GitHub App, not webhooks. One repo path, one run.
- No dependency installation in the sandbox. Demo repos are stdlib-only.
- No fine-tuning, no RAG over external docs, no vector database.
- No auto-merge, ever.
